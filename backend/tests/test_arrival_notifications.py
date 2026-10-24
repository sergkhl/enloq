"""Arrival, explicit hostility, fail-open and silent burst semantics (ADR 20)."""

import asyncio

import pytest
from enloq.inference.base import ProviderRefused

from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    return fixture(tmp_path, clock=lambda: now[0]), now


def arrival(app, identity=1, **changes):
    app.sync.ingest(message(app, identity, **changes), live=True)


def candidates(app):
    app.notifications.advance()
    return [
        value
        for row in app.notifications.rows()
        if (value := app.notifications.candidate(row))
    ]


async def test_clean_alert_before_quiet_without_emotion_memory_or_suggestions(rig):
    app, now = rig
    arrival(app, text="Authored clean text")
    assert candidates(app) == []
    await app.process_due()
    event = candidates(app)[0]
    assert event["text"] == "Authored clean text" and not event["silent"]
    assert [role for role, _ in app.decider.calls] == ["hostility_screen"]
    assert not app.engine.calls and app.suggestions.record(KEY) is None
    assert now[0] == 2_000_000_000


async def test_hostile_waits_for_valid_softening_and_clean_later_message_does_not(rig):
    app, now = rig
    app.decider.hostility = 1.0
    arrival(app, text="Authored hostile wording")
    await app.process_due()
    assert candidates(app) == []
    now[0] += 1
    app.decider.hostility = 0.0
    arrival(app, 2, text="Authored clean follow-up")
    await app.process_due()
    first = candidates(app)[0]
    assert first["text"] == "Authored clean follow-up"
    app.notifications.finish(app.notifications.rows()[0])
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='ready',calmed=1,rendered='Authored softened block',rendered_revision=revision"
        )
    updates = candidates(app)
    assert not updates or updates[0]["silent"]


def test_five_second_queue_deadline_uses_original_and_cancels_undispatched_work(rig):
    app, now = rig
    arrival(app)
    now[0] += 4.99
    assert candidates(app) == []
    now[0] += 0.01
    assert candidates(app)[0]["text"] == "Authored request, not a promise."
    assert not app.store.rows("SELECT id FROM reservations")
    assert (
        app.store.rows("SELECT state FROM jobs WHERE role='hostility_screen'")[0][
            "state"
        ]
        == "cancelled"
    )


@pytest.mark.parametrize("reason", ["off", "budget", "media"])
def test_known_bypass_notifies_at_arrival_without_model_request(rig, reason):
    app, _ = rig
    if reason == "off":
        app.calming.set(KEY, False)
    elif reason == "budget":
        app.notifications.unavailable = lambda: True
    arrival(
        app,
        text="" if reason == "media" else "Authored original",
        media="audio" if reason == "media" else None,
    )
    assert candidates(app)
    assert not app.store.rows("SELECT id FROM jobs WHERE role='hostility_screen'")


async def test_burst_updates_silent_and_later_burst_can_alert(rig):
    app, now = rig
    arrival(app)
    await app.process_due()
    first = candidates(app)[0]
    app.notifications.finish(app.notifications.rows()[0])
    now[0] += 1
    arrival(app, 2)
    await app.process_due()
    second = candidates(app)[0]
    assert second["id"] != first["id"] and second["group"] == first["group"]
    assert second["silent"] and second["count"] == 2
    app.notifications.finish(app.notifications.rows()[0])
    now[0] += 31
    arrival(app, 3)
    # Earlier block decisions may precede the new screening job.
    for _ in range(6):
        await app.process_due()
    third = candidates(app)[0]
    assert third["group"] != second["group"] and not third["silent"]


async def test_screen_uses_cached_direct_reply_and_discards_changed_context(rig):
    app, now = rig
    prior = message(app, 1, text="Authored joke context", date=now[0] - 8 * 86400)
    app.sync.ingest(prior, target=False)
    entered, release = asyncio.Event(), asyncio.Event()
    packets = []

    async def held(role, packet):
        assert role == "hostility_screen"
        packets.append(packet)
        entered.set()
        await release.wait()
        return {"hostility": {"type": "noul", "noul": 1.0}}

    app.decider.generate = held
    arrival(app, 2, text="Authored target", reply_to=1)
    running = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    assert [m["id"] for m in packets[0]["state"]["conversation"]] == [1, 2]
    app.sync.ingest(prior.model_copy(update={"text": "Changed context"}), target=False)
    release.set()
    await running
    assert candidates(app)[0]["text"] == "Authored target"
    assert not app.transport.exact_reads


@pytest.mark.parametrize("failure", ["format", "provider", "refused", "late"])
async def test_screen_failure_or_late_result_falls_back_without_resampling(
    rig, failure
):
    app, now = rig
    calls = []

    async def answer(role, packet):
        calls.append(role)
        if failure == "provider":
            raise ConnectionError("Authored failure")
        if failure == "refused":
            raise ProviderRefused("Authored no-work refusal")
        if failure == "late":
            now[0] += 5
        return (
            {} if failure == "format" else {"hostility": {"type": "noul", "noul": 1.0}}
        )

    app.decider.generate = answer
    arrival(app)
    await app.process_due()
    assert candidates(app)[0]["text"] == "Authored request, not a promise."
    assert calls == ["hostility_screen"]
    assert not app.store.rows(
        "SELECT id FROM jobs WHERE role='hostility_screen' AND state='queued'"
    )


async def test_hostile_screen_followed_by_clean_block_decision_releases_original(rig):
    app, now = rig
    app.decider.hostility = 1.0
    arrival(app)
    await app.process_due()
    assert not candidates(app)
    app.decider.hostility = 0.0
    now[0] += 31
    for _ in range(6):
        await app.process_due()
    assert candidates(app)[0]["text"] == "Authored request, not a promise."
