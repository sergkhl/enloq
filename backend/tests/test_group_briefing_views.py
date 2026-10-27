"""GB6 cached daily presentation and exact navigation, over authored sources."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.application import group_context
from enloq.application.accounts import AccountManager
from enloq.application.conversations import Conversations
from enloq.config import Settings
from enloq.domain.briefing_contract import NO_SUBSTANCE
from enloq.domain.models import (
    Chat,
    MediaInfo,
    Message,
    ProcessingPaused,
    Refused,
)
from enloq.inference.base import EngineResponse
from enloq.inference.guidance import briefing_fingerprint
from enloq.inference.work import ROUTES
from enloq.storage.store import RETENTION, Store
from enloq.transport.fake import FakeTransport
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC).timestamp()
KEY, DAY = "1/channel:42", "2026-10-11"


def authored(manager):
    now = [NOW]
    for runtime in manager.runtimes.values():
        runtime.assistant.store.clock = lambda: now[0]
    manager.set_processing_threshold(0)
    app = manager.get(1)
    app.store.put_chat(
        Chat(account_id=1, peer="channel:42", title="Authored group", kind="group"),
        activity=NOW,
    )
    app.store.groups.observe(KEY, access="available", reason="authored_native")
    app.set_group_processing(KEY, True)
    app.briefings.check_citations = False
    intake = app.store.groups.intake(KEY)
    app.store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=0,
        top=0,
        count=0,
        cutoff=NOW - 7 * 86400,
        oldest=None,
        complete=True,
    )
    return SimpleNamespace(
        manager=manager,
        app=app,
        store=app.store,
        storage=app.store.group_briefings,
        view=app.briefings,
        now=now,
    )


@pytest.fixture
def rig(tmp_path):
    value = authored(AccountManager(Settings(data=tmp_path)))
    yield value
    for runtime in value.manager.runtimes.values():
        runtime.assistant.store.close()


def source(rig, identity=1, **values):
    message = Message(
        account_id=1,
        peer="channel:42",
        message_id=identity,
        revision=1,
        date=rig.now[0],
        text=f"Authored participant update {identity}.",
        author="Alex",
        author_peer="user:7",
    ).model_copy(update=values)
    rig.app.sync.ingest(message, group_epoch=rig.store.groups.intake(KEY).epoch)
    return rig.store.message(KEY, identity)


def freeze(rig, ids, *, day=DAY, omissions=()):
    packet = group_context.packet(
        rig.store, KEY, day, [rig.store.message(KEY, identity) for identity in ids]
    )
    return rig.storage.freeze(
        KEY,
        day,
        [packet],
        intended_ids=ids,
        omissions=omissions,
        guidance_hash=briefing_fingerprint(
            rig.app.rewriting.work.guidance(),
            role="group_briefing",
            route=ROUTES["group_briefing"],
        ),
        route=ROUTES["group_briefing"],
    )


def output(*ids, text="Authored discussion reached a decision."):
    return dict(outcome="briefing", topics=[dict(text=text, citations=list(ids))])


def complete(rig, ids, *, day=DAY, answer=None):
    attempt = rig.storage.create_attempt(freeze(rig, ids, day=day))
    final = rig.storage.publish(attempt.id, answer or output(*ids), covered_ids=ids)
    rig.storage.finish_batch(attempt.batch_id)
    return final


def failed(rig, ids):
    attempt = rig.storage.create_attempt(freeze(rig, ids))
    rig.storage.fail(attempt.id, "Authored format failure")
    rig.storage.finish_batch(attempt.batch_id, state="failed")
    return rig.storage.attempt(attempt.id)


def current(rig):
    return next(day for day in rig.view.view(KEY) if day["day"] == DAY)


def test_days_follow_application_timezone_and_cached_retention_without_work(rig):
    before = datetime(2026, 10, 10, 16, 59, tzinfo=UTC).timestamp()
    source(rig, 1, date=before)
    source(rig, 2, date=before + 60)
    source(rig, 3, date=NOW - RETENTION - 1)
    source(rig, 4, text="", media="sticker")
    source(rig, 5, service=True)
    source(rig, 6, media="audio", text="")
    before_jobs = rig.store.rows("SELECT * FROM jobs")
    before_run = rig.storage.run_state(KEY)
    days = rig.view.view(KEY)
    assert [day["day"] for day in days] == ["2026-10-10", DAY]
    assert days[0]["source_ids"] == [1]
    assert days[1]["source_ids"] == [2, 6]
    assert all(not day["analyzing"] and day["final"] is None for day in days)
    assert rig.store.rows("SELECT * FROM jobs") == before_jobs
    assert rig.storage.run_state(KEY) == before_run
    assert not rig.app.engine.calls and not rig.app.transport.exact_reads


def test_final_coverage_keeps_later_outgoing_originals_and_exact_draft(rig):
    source(rig, 1)
    final = complete(rig, [1])
    source(rig, 2, outgoing=True, author_peer="user:1", author="Owner")
    rig.store.mutate_draft(
        KEY, 0, lambda value: value.update(text="Exact authored draft")
    )
    value = rig.app.conversation(KEY)
    day = value["briefings"][0]
    assert day["final"]["id"] == final.id
    assert day["covered_ids"] == [1] and day["uncovered_ids"] == [2]
    assert day["partial"] and not day["analyzing"]
    assert value["messages"][1]["outgoing"]
    assert value["draft"]["text"] == "Exact authored draft"
    assert value["blocks"] == []
    assert rig.app.conversation("1/user:101")["briefings"] == []
    assert not rig.app.engine.calls


def test_only_final_prefix_is_exposed_without_provisional_coverage_or_citations(rig):
    source(rig, 1)
    final = complete(rig, [1])
    source(rig, 2)
    attempt = rig.storage.create_attempt(freeze(rig, [1, 2]))
    rig.storage.store_chunk(attempt.id, [2], output(2, text="Hidden chunk note."))
    assert "Hidden chunk" not in json.dumps(current(rig))
    assert rig.storage.publish_prefix(attempt.id, "A new usable prefix")
    day = current(rig)
    assert day["stream"] == {"attempt": attempt.id, "text": "A new usable prefix"}
    assert day["analyzing"] and day["final"]["id"] == final.id
    assert day["covered_ids"] == [1] and day["uncovered_ids"] == [2]
    assert set(day["stream"]) == {"attempt", "text"}
    with pytest.raises(Refused, match="validated briefing citation"):
        rig.view.sources(KEY, DAY, final.id, 2)


def test_ephemeral_prefix_changes_wake_the_chat_without_storing_text(rig):
    source(rig)
    attempt = rig.storage.create_attempt(freeze(rig, [1]))
    _, before = rig.store.changes()
    assert not rig.storage.publish_prefix(attempt.id, "  ")
    assert not rig.storage.publish_prefix(attempt.id, chr(0))
    assert not rig.storage.publish_prefix(attempt.id, chr(0xD800))
    assert rig.store.changes(before) == ([], before)
    assert rig.storage.publish_prefix(attempt.id, "Usable prefix")
    scopes, first = rig.store.changes(before)
    assert scopes == [KEY] and first > before
    assert rig.storage.publish_prefix(attempt.id, "Usable prefix")
    assert rig.store.changes(first) == ([], first)
    assert rig.storage.publish_prefix(attempt.id, "Usable prefix grows")
    assert rig.store.changes(first)[0] == [KEY]
    assert "Usable prefix" not in json.dumps(
        rig.store.rows("SELECT body FROM group_briefing_attempts")
    )
    assert not rig.store.rows("SELECT * FROM group_briefing_results")


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_stream_restores_previous_current_final_and_retry_state(
    rig, interrupted
):
    source(rig, 1)
    final = complete(rig, [1])
    source(rig, 2)
    attempt = rig.storage.create_attempt(freeze(rig, [1, 2]))
    rig.storage.publish_prefix(attempt.id, "Discard this incomplete update")
    _, before = rig.store.changes()
    rig.storage.fail(attempt.id, "Authored interrupted stream", interrupted=interrupted)
    rig.storage.finish_batch(attempt.batch_id, state="failed")
    day = current(rig)
    assert day["stream"] is None and not day["analyzing"]
    assert day["final"]["id"] == final.id and day["uncovered_ids"] == [2]
    assert day["attempt"]["state"] == ("interrupted" if interrupted else "failed")
    assert day["retry"] == {"attempt": attempt.id, "enabled": True, "reason": None}
    assert rig.store.changes(before)[0] == [KEY]


def test_recovery_discards_prefix_and_restores_valid_final_without_generation(rig):
    source(rig, 1)
    final = complete(rig, [1])
    source(rig, 2)
    attempt = rig.storage.create_attempt(freeze(rig, [1, 2]))
    rig.storage.publish_prefix(attempt.id, "Never persisted as completed")
    path, shared = rig.store.path, rig.store.shared_path
    rig.store.close()
    store = Store(path, account_id=1, clock=lambda: rig.now[0], shared_path=shared)
    store.recover()
    app = Conversations(store, FakeTransport())
    day = app.briefings.view(KEY)[0]
    assert day["stream"] is None and day["final"]["id"] == final.id
    assert day["attempt"]["state"] == "interrupted" and day["retry"]["enabled"]
    assert not app.engine.calls and not app.transport.exact_reads
    store.close()


@pytest.mark.parametrize("change", ["edit", "delete", "speaker", "proof"])
def test_material_change_withdraws_final_and_prefix_without_accessible_cache_fallback(
    rig, change
):
    source(rig, 1)
    complete(rig, [1])
    source(rig, 2)
    attempt = rig.storage.create_attempt(freeze(rig, [1, 2]))
    rig.storage.publish_prefix(attempt.id, "Stale provisional wording")
    if change == "proof":
        rig.store.groups.withdraw_source(f"{KEY}/1")
    else:
        values = {"revision": 2}
        values.update(
            {"deleted": True}
            if change == "delete"
            else {"author_peer": "user:8"}
            if change == "speaker"
            else {"text": "An observed changed decision."}
        )
        source(rig, 1, **values)
    day = current(rig)
    assert day["final"] is None and day["stream"] is None
    assert not day["cached"] and not day["analyzing"] and day["retry"] is None
    assert day["attempt"]["state"] == "superseded"
    assert not rig.storage.publish_prefix(attempt.id, "Late stale output")


def test_lost_access_retains_safe_cached_final_and_exact_source_read_only(rig):
    source(rig, 1, media="image", media_info=MediaInfo(size=42, mime="image/png"))
    final = complete(rig, [1])
    rig.app.set_group_processing(KEY, False)
    rig.store.groups.observe(KEY, access="lost", reason="authored_left")
    day = current(rig)
    assert day["cached"] and day["final"]["id"] == final.id
    assert not day["analyzing"] and day["stream"] is None and day["retry"] is None
    window = rig.view.sources(KEY, DAY, final.id, 1)
    assert window["messages"][0]["media"] == "image"
    assert not window["before"] and not window["after"]
    assert not rig.app.transport.exact_reads and not rig.app.transport.downloads
    assert not rig.store.groups.get(KEY).processing_enabled


def test_lost_access_does_not_restore_changed_or_expired_cached_wording(rig):
    source(rig, 1)
    final = complete(rig, [1])
    source(rig, 1, revision=2, text="The retained source was observed edited.")
    rig.store.groups.observe(KEY, access="lost", reason="authored_left")
    assert current(rig)["final"] is None
    with pytest.raises(Refused, match="changed or expired"):
        rig.view.sources(KEY, DAY, final.id, 1)
    rig.now[0] += RETENTION + 1
    assert rig.view.view(KEY) == []


def test_no_substance_remains_validated_coverage_and_exact_wording(rig):
    source(rig, 1)
    final = complete(rig, [1], answer={"outcome": "no_substance", "topics": []})
    day = current(rig)
    assert day["final"] == {
        "id": final.id,
        "text": NO_SUBSTANCE,
        "outcome": "no_substance",
        "topics": [],
        "covered_ids": [1],
        "covered_start": NOW,
        "covered_end": NOW,
        "excluded_ids": [],
        "ad_ids": [],
        "local_outcome": None,
    }
    assert day["covered_ids"] == [1] and not day["partial"] and day["retry"] is None
    with pytest.raises(Refused, match="validated briefing citation"):
        rig.view.sources(KEY, DAY, final.id, 1)


@pytest.mark.parametrize("outcome", ["briefing", "no_substance"])
def test_older_day_originals_are_readable_outside_the_loaded_snapshot(rig, outcome):
    old = "2026-10-10"
    source(rig, 1, date=NOW - 86400)
    source(rig, 2, date=NOW - 86400, media="image")
    answer = (
        output(1, 2) if outcome == "briefing" else {"outcome": outcome, "topics": []}
    )
    final = complete(rig, [1, 2], day=old, answer=answer)
    for identity in range(3, 103):
        source(rig, identity)
    conversation = rig.app.conversation(KEY)
    assert [message["message_id"] for message in conversation["messages"]] == list(
        range(3, 103)
    )
    window = rig.view.originals(KEY, old, final.id)
    assert window["source"] == 1 and window["day"] == old
    assert [message["message_id"] for message in window["messages"]] == [1, 2]
    assert window["messages"][1]["media"] == "image"
    assert not window["before"] and not window["after"]
    assert not rig.app.transport.exact_reads and not rig.app.engine.calls


def test_no_substance_originals_keep_safe_cache_access_after_loss(rig):
    source(rig, 1)
    final = complete(rig, [1], answer={"outcome": "no_substance", "topics": []})
    rig.store.groups.observe(KEY, access="lost", reason="authored_left")
    assert rig.view.originals(KEY, DAY, final.id)["source"] == 1
    rig.now[0] += RETENTION + 1
    with pytest.raises(Refused, match="changed or expired"):
        rig.view.originals(KEY, DAY, final.id)


@pytest.mark.parametrize("around", [True, 0, 2, 3])
def test_day_originals_refuse_missing_wrong_day_and_invalid_targets(rig, around):
    source(rig, 1)
    source(rig, 3, date=NOW - 86400)
    final = complete(rig, [1])
    with pytest.raises(Refused):
        rig.view.originals(KEY, DAY, final.id, around=around)
    assert not rig.app.transport.exact_reads and not rig.app.engine.calls


def test_original_window_rechecks_final_when_source_changes_during_annotation(rig):
    source(rig, 1)
    final = complete(rig, [1])
    annotate = rig.store.history.annotate

    def changed(messages, **options):
        result = annotate(messages, **options)
        source(rig, 1, revision=2, text="Observed update during cached rendering.")
        return result

    rig.store.history.annotate = changed
    with pytest.raises(Refused, match="changed or expired"):
        rig.view.originals(KEY, DAY, final.id)
    assert not rig.app.transport.exact_reads


def test_restored_access_does_not_display_old_native_proof_through_cached_fallback(rig):
    source(rig, 1)
    final = complete(rig, [1])
    rig.store.groups.observe(KEY, access="lost", reason="authored_left")
    assert rig.storage.state(KEY, DAY, cached_access=True).cached
    rig.store.groups.observe(KEY, access="available", reason="authored_restored")
    day = rig.storage.state(KEY, DAY, cached_access=True)
    assert day.final is None and not day.cached
    assert current(rig)["final"] is None
    with pytest.raises(Refused, match="changed or expired"):
        rig.view.originals(KEY, DAY, final.id)


def test_citation_window_is_same_day_bounded_and_keeps_author_and_attachment_metadata(
    rig,
):
    for identity in range(1, 141):
        source(rig, identity)
    source(rig, 141, date=NOW - 86400)
    source(rig, 142, date=NOW + 86400)
    final = complete(rig, [50])
    window = rig.view.sources(KEY, DAY, final.id, 50)
    ids = [message["message_id"] for message in window["messages"]]
    assert ids == list(range(25, 125)) and len(ids) == 100
    assert window["before"] and window["after"]
    assert window["source"] == 50 and window["result"] == final.id
    assert all(message["author_peer"] == "user:7" for message in window["messages"])
    assert not rig.app.transport.exact_reads and not rig.app.engine.calls


@pytest.mark.parametrize(
    "key,day,identity,source_id",
    [
        ("1/channel:99", DAY, None, 1),
        ("2/channel:42", DAY, None, 1),
        ("1/user:101", DAY, None, 1),
        (KEY, "2026-10-10", None, 1),
        (KEY, "2026-1-1", None, 1),
        (KEY, DAY, "0" * 32, 1),
        (KEY, DAY, None, 2),
        (KEY, DAY, None, True),
    ],
)
def test_source_navigation_refuses_wrong_scope_uncited_and_invalid_targets(
    rig, key, day, identity, source_id
):
    source(rig, 1)
    source(rig, 2)
    final = complete(rig, [1])
    with pytest.raises(Refused):
        rig.view.sources(key, day, identity or final.id, source_id)
    assert not rig.app.transport.exact_reads and not rig.app.engine.calls


@pytest.mark.parametrize("gate", ["group_off", "account_off", "budget", "connection"])
def test_retry_permission_reports_current_gate_without_new_jobs_or_clock_changes(
    rig, gate
):
    source(rig, 1)
    attempt = failed(rig, [1])
    if gate == "group_off":
        rig.app.set_group_processing(KEY, False)
    elif gate == "account_off":
        rig.manager.set_processing(1, False)
    elif gate == "budget":
        rig.manager.registry.set_budget(0, family="group_briefing")
    else:
        rig.app.transport.connected = False

        def disconnected():
            # Model the manager's pure personal-mode gate without entering the
            # account registry during the planner's held transaction.
            raise ProcessingPaused("Waiting for this account's Telegram connection")

        rig.app.group_runtime_admit = disconnected
    before = rig.storage.run_state(KEY)
    day = current(rig)
    assert day["retry"]["attempt"] == attempt.id
    assert not day["retry"]["enabled"] and day["retry"]["reason"]
    assert not day["analyzing"]
    with pytest.raises(Refused):
        rig.view.retry_for(KEY, attempt.id)
    assert rig.storage.run_state(KEY) == before and not rig.store.rows(
        "SELECT * FROM jobs"
    )


def test_retry_is_exact_failed_intent_despite_append_and_threshold_and_never_completed(
    rig,
):
    source(rig, 1)
    attempt = failed(rig, [1])
    source(rig, 2)
    rig.manager.set_processing_threshold(10000)
    before = rig.storage.run_state(KEY).last_auto_started
    assert current(rig)["retry"]["enabled"]
    new_id = rig.view.retry_for(KEY, attempt.id)
    new = rig.storage.attempt(new_id)
    assert new.retry_of == attempt.id and new.snapshot.intended_ids == [1]
    assert rig.storage.run_state(KEY).last_auto_started == before
    assert current(rig)["retry"] is None
    with pytest.raises(Refused):
        rig.view.retry_for("1/channel:99", attempt.id)
    final = rig.storage.publish(new.id, output(1), covered_ids=[1])
    rig.storage.finish_batch(new.batch_id)
    assert current(rig)["final"]["id"] == final.id
    with pytest.raises(Refused, match="Completed briefing"):
        rig.view.retry_for(KEY, attempt.id)


async def test_started_stream_finishes_after_settings_change_without_a_waiting_status(
    rig,
):
    source(rig, 1)
    rig.now[0] += 31
    await rig.view.process_due()  # Classify cached originals first.
    await rig.view.process_due()  # Cached preparation queues the final stage.
    entered, finish = asyncio.Event(), asyncio.Event()

    async def generate_stream(role, packet, on_text):
        assert role == "group_briefing"
        on_text("The authored participants reached")
        entered.set()
        await finish.wait()
        answer = output(*packet["covered_ids"])
        answer["contract_version"] = 2
        answer["topics"][0].update(
            title="Discussion", participant_source_ids=packet["covered_ids"]
        )
        return EngineResponse(answer, actual_nanos=0)

    rig.app.engine.generate_stream = generate_stream
    task = asyncio.create_task(rig.view.process_due())
    await asyncio.wait_for(entered.wait(), 2)
    assert current(rig)["analyzing"] and current(rig)["stream"]
    rig.manager.set_processing_threshold(10000)
    rig.manager.set_processing(1, False)
    rig.manager.registry.set_budget(0, family="group_briefing")
    assert current(rig)["analyzing"] and current(rig)["stream"]
    finish.set()
    await task
    day = current(rig)
    assert day["final"] and day["stream"] is None and not day["analyzing"]
    assert day["covered_ids"] == [1]


async def test_threshold_held_queued_stage_has_no_analyzing_or_waiting_status(rig):
    source(rig, 1)
    rig.now[0] += 31
    await rig.view.process_due()
    before = rig.storage.run_state(KEY)
    rig.manager.set_processing_threshold(10000)
    day = current(rig)
    assert day["attempt"]["state"] == "active"
    assert not day["analyzing"] and day["stream"] is None and day["retry"] is None
    assert "waiting" not in json.dumps(day).lower()
    assert rig.storage.run_state(KEY) == before and not rig.app.engine.calls


def test_authenticated_http_sources_and_retry_are_account_and_peer_bound(tmp_path):
    server = create_app(Settings(data=tmp_path))
    manager = server.state.accounts
    manager._create(manager.registry.register(2, "authored-second.session"))
    rig = authored(manager)
    source(rig, 1)
    final = complete(rig, [1])
    source(rig, 2)
    attempt = failed(rig, [1, 2])
    base = "/api/accounts/1/chats/channel:42/briefings"
    client = TestClient(server, base_url="http://127.0.0.1:8765")
    assert (
        client.get(
            f"{base}/{DAY}/sources", params={"result": final.id, "source": 1}
        ).status_code
        == 401
    )
    trust_browser(client, tmp_path)
    result = client.get(
        f"{base}/{DAY}/sources", params={"result": final.id, "source": 1}
    )
    assert result.status_code == 200 and result.json()["source"] == 1
    originals = client.get(f"{base}/{DAY}/originals", params={"result": final.id})
    assert originals.status_code == 200 and originals.json()["source"] == 1
    uncited = client.get(
        f"{base}/{DAY}/originals", params={"result": final.id, "around": 2}
    )
    assert uncited.status_code == 200 and uncited.json()["source"] == 2
    assert (
        client.get(
            f"{base}/{DAY}/sources", params={"result": final.id, "source": 2}
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"{base}/{DAY}/sources", params={"result": final.id, "source": 0}
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"{base}/{DAY}/sources", params={"result": "bad", "source": 1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/accounts/2/chats/channel:42/briefings/{attempt.id}/retry", json={}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/accounts/1/chats/channel:99/briefings/{attempt.id}/retry", json={}
        ).status_code
        == 400
    )
    retried = client.post(f"{base}/{attempt.id}/retry", json={})
    assert retried.status_code == 200
    next_id = retried.json()["attempt"]
    assert (
        isinstance(next_id, str) and rig.storage.attempt(next_id).retry_of == attempt.id
    )
    assert client.post(f"{base}/{attempt.id}/retry", json={}).status_code == 400
    assert not rig.app.transport.exact_reads and not rig.app.engine.calls
    client.close()
    for runtime in manager.runtimes.values():
        runtime.assistant.store.close()
