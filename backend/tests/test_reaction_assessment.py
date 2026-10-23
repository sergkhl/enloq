"""P4: Reaction Assessment and its emotion/suggestion currentness (ADR 26;
Adaptive left with ADR 53). Authored messages and reactions; fake engine and transport only."""

import asyncio

from enloq.domain.emotion import LIFETIME
from enloq.inference.fake import choice
from enloq.inference.guidance import DEFAULTS_REVISION

from backend.tests.test_reactions import snapshot
from backend.tests.test_rebuild import fixture, message

PEER = "user:42"
KEY = "1/user:42"
START = 2_000_000_000.0


def rig(tmp_path, *, assessment=None):
    now = [START]
    app = fixture(tmp_path, clock=lambda: now[0])
    if assessment:
        app.decider.assessment = assessment
    return app, now


def calls(app, role="reaction_assessment"):
    engine = app.decider if role == "reaction_assessment" else app.engine
    return [packet for name, packet in engine.calls if name == role]


async def settle(app, now, seconds=31):
    now[0] += seconds
    for _ in range(6):
        await app.process_due()


async def conversation(app, now):
    """An incoming message, already rewritten, then the owner's reply."""
    app.sync.ingest(message(app, 1, text="Can you send the plan?", date=now[0]))
    await settle(app, now)
    app.sync.ingest(
        message(app, 2, text="Here is the plan.", outgoing=True, date=now[0]),
        target=False,
    )
    assert calls(app, "incoming_rewrite") and not calls(app)


async def test_a_correspondent_trigger_is_assessed_after_quiet_and_becomes_current(
    tmp_path,
):
    app, now = rig(
        tmp_path, assessment={"emotion": "positive", "suggestions_useful": False}
    )
    await conversation(app, now)
    added = now[0] + 5
    now[0] += 6
    app.sync.reactions(PEER, 2, snapshot(peer=[("👍", added)], observed=now[0]))
    view = app.emotion.view(KEY)
    assert (view.source, view.reason, view.reaction, view.target_id) == (
        "reaction",
        "pending",
        "👍",
        2,
    )
    # Thirty quiet seconds pass before any call.
    await settle(app, now, 10)
    assert not calls(app)
    await settle(app, now, 25)
    [packet] = calls(app)
    # One decision state: the conversation as it stood and the reaction on
    # the owner's exact message; no memory, images or other fields.
    assert set(packet) == {"state", "guidance"}
    assert packet["state"] == {
        "conversation": [
            {"id": 1, "from": "recipient", "text": "Can you send the plan?"},
            {"id": 2, "from": "owner", "text": "Here is the plan."},
        ],
        "reaction": {"by": "recipient", "emoji": "👍", "on_message": 2},
    }
    view = app.emotion.view(KEY)
    assert (view.code, view.reason, view.source_time) == (
        "positive",
        "available",
        added,
    )
    assert view.expires_at == added + LIFETIME
    record = app.assessment.record(KEY)
    assert (record["guidance"], record["defaults"]) == (0, DEFAULTS_REVISION)
    # Repeated identical observations, refreshes and restarts never resample.
    app.sync.reactions(PEER, 2, snapshot(peer=[("👍", added)], observed=now[0] + 1))
    app.store.recover()
    await settle(app, now, 60)
    assert len(calls(app)) == 1
    assert not app.transport.downloads


async def test_own_other_baseline_and_undated_reactions_never_assess(tmp_path):
    app, now = rig(tmp_path)
    await conversation(app, now)
    now[0] += 5
    app.sync.reactions(PEER, 2, snapshot(own=["👍"], observed=now[0]))
    app.sync.reactions(PEER, 1, snapshot(peer=[("❤️", now[0])], observed=now[0]))
    # Present before the feature existed: a display-only baseline.
    app.sync.ingest(message(app, 3, text="Earlier", date=START - 50))
    app.sync.reactions(PEER, 3, snapshot(peer=[("👍", START - 10)], observed=now[0]))
    await settle(app, now, 60)
    assert not calls(app)
    assert app.emotion.view(KEY).source == "incoming"
    assert not app.store.rows(
        "SELECT 1 FROM jobs WHERE role='reaction_assessment' AND state='queued'"
    )


async def test_a_burst_returning_to_the_processed_state_makes_no_call(tmp_path):
    app, now = rig(tmp_path)
    await conversation(app, now)
    before = app.emotion.view(KEY)
    added = now[0] + 1
    app.sync.reactions(PEER, 2, snapshot(peer=[("👍", added)], observed=now[0] + 2))
    assert app.emotion.view(KEY).source == "reaction"
    # Removed within the quiet window, before any assessment began.
    app.sync.reactions(PEER, 2, snapshot(observed=now[0] + 10))
    await settle(app, now, 45)
    assert not calls(app)
    assert app.emotion.view(KEY) == before


async def test_replacement_assesses_the_new_reaction_and_removal_revives_nothing(
    tmp_path,
):
    app, now = rig(
        tmp_path, assessment={"emotion": "positive", "suggestions_useful": False}
    )
    await conversation(app, now)
    first = now[0] + 1
    app.sync.reactions(PEER, 2, snapshot(peer=[("👍", first)], observed=now[0] + 1))
    await settle(app, now, 35)
    assert app.emotion.view(KEY).code == "positive"
    app.decider.assessment = {"emotion": "frustrated", "suggestions_useful": False}
    second = now[0] + 2
    app.sync.reactions(PEER, 2, snapshot(peer=[("👎", second)], observed=now[0] + 2))
    assert app.emotion.view(KEY).reason == "pending"
    await settle(app, now, 35)
    assert len(calls(app)) == 2
    assert calls(app)[-1]["state"]["reaction"]["emoji"] == "👎"
    assert app.emotion.view(KEY).code == "frustrated"
    # Removing the current reaction withdraws its result; neither the earlier
    # reaction's nor the incoming block's estimate comes back.
    app.sync.reactions(PEER, 2, snapshot(observed=now[0] + 3))
    view = app.emotion.view(KEY)
    assert (view.source, view.reason, view.code) == ("reaction", "changed", None)
    await settle(app, now, 60)
    assert len(calls(app)) == 2


async def test_newer_incoming_wins_and_late_assessment_cannot_publish(tmp_path):
    app, now = rig(tmp_path)
    await conversation(app, now)
    release, started = asyncio.Event(), asyncio.Event()
    original = app.decider.generate

    async def held(role, packet):
        if role == "reaction_assessment":
            started.set()
            await release.wait()
            return {
                "emotion": choice("angry", 0.9),
                "suggestions_useful": {"type": "noul", "noul": 0.9},
            }
        return await original(role, packet)

    app.decider.generate = held
    added = now[0] + 1
    app.sync.reactions(PEER, 2, snapshot(peer=[("👎", added)], observed=now[0] + 1))
    now[0] += 31
    task = asyncio.create_task(app.process_due())
    await asyncio.wait_for(started.wait(), 5)
    app.sync.ingest(message(app, 3, text="Never mind, all good.", date=now[0] + 5))
    release.set()
    await task
    record = app.assessment.record(KEY)
    assert record["state"] == "pending" and record["code"] is None
    assert (
        app.store.rows(
            "SELECT state FROM jobs WHERE role='reaction_assessment' ORDER BY rowid DESC LIMIT 1"
        )[0]["state"]
        == "superseded"
    )
    assert app.emotion.view(KEY).source == "incoming"
    assert not app.store.rows("SELECT 1 FROM jobs WHERE role='response_suggestions'")


async def test_the_twelve_hours_count_from_native_addition_not_observation(tmp_path):
    app, now = rig(
        tmp_path, assessment={"emotion": "positive", "suggestions_useful": False}
    )
    await conversation(app, now)
    added = now[0] + 1
    # Observed two hours later, for example after a gap.
    now[0] += 7200
    app.sync.reactions(PEER, 2, snapshot(peer=[("👌", added)], observed=now[0]))
    await settle(app, now, 31)
    assert app.emotion.view(KEY).expires_at == added + LIFETIME
    now[0] = added + LIFETIME
    assert app.emotion.view(KEY).reason == "expired"


async def test_useful_assessment_prepares_suggestions_answered_by_a_later_send(
    tmp_path,
):
    app, now = rig(
        tmp_path, assessment={"emotion": "worried", "suggestions_useful": True}
    )
    await conversation(app, now)
    added = now[0] + 1
    app.sync.reactions(PEER, 2, snapshot(peer=[("👎", added)], observed=now[0] + 1))
    await settle(app, now, 35)
    [packet] = calls(app, "response_suggestions")
    assert packet["reaction"] == {"emoji": "👎", "target_id": 2, "by": "recipient"}
    assert packet["messages"][0]["id"] == 2 and packet["recipient_emotion"] == "worried"
    assert "memory" not in packet
    view = app.suggestions.view(KEY)
    assert view.state == "ready" and view.source_time == added
    # The owner's own reaction does not answer; native outgoing text does.
    app.sync.reactions(PEER, 1, snapshot(own=["👍"], observed=now[0] + 1))
    assert app.suggestions.view(KEY).state == "ready"
    app.sync.ingest(
        message(app, 4, text="I will fix it.", outgoing=True, date=now[0] + 2),
        target=False,
    )
    assert app.suggestions.view(KEY).reason == "answered"
    # Emotion stays: an answer clears suggestions, not the signal.
    assert app.emotion.view(KEY).code == "worried"


async def test_neutral_or_not_useful_assessments_request_no_suggestions(tmp_path):
    for index, result in enumerate(
        (
            {"emotion": "neutral", "suggestions_useful": True},
            {"emotion": "angry", "suggestions_useful": False},
        )
    ):
        app, now = rig(tmp_path / str(index), assessment=result)
        await conversation(app, now)
        app.sync.reactions(
            PEER, 2, snapshot(peer=[("👍", now[0] + 1)], observed=now[0] + 1)
        )
        await settle(app, now, 35)
        assert len(calls(app)) == 1 and not calls(app, "response_suggestions")


async def test_ineligible_chats_and_old_targets_are_never_assessed(tmp_path):
    app, now = rig(tmp_path)
    await conversation(app, now)
    # A cached target more than seven days older than the reaction.
    app.sync.ingest(message(app, 5, text="Old", date=now[0] - 8 * 86400), target=False)
    app.sync.reactions(
        PEER, 5, snapshot(peer=[("👍", now[0] + 1)], observed=now[0] + 1)
    )
    await settle(app, now, 35)
    assert not calls(app) and app.emotion.view(KEY).source == "incoming"
    chat = app.store.chat(KEY)
    app.store.put_chat(chat.model_copy(update={"ai_eligible": False}))
    app.sync.reactions(
        PEER, 2, snapshot(peer=[("👍", now[0] + 2)], observed=now[0] + 2)
    )
    await settle(app, now, 35)
    assert not calls(app)


async def test_failed_format_is_not_resampled_and_restart_keeps_outcomes(tmp_path):
    app, now = rig(tmp_path, assessment={"emotion": "elated", "suggestions_useful": 1})
    await conversation(app, now)
    app.sync.reactions(
        PEER, 2, snapshot(peer=[("🤝", now[0] + 1)], observed=now[0] + 1)
    )
    await settle(app, now, 35)
    assert len(calls(app)) == 1
    record = app.assessment.record(KEY)
    assert record["state"] == "failed" and record["failure"] == "unknown_option"
    assert app.emotion.view(KEY).reason == "unavailable"
    app.store.recover()
    await settle(app, now, 120)
    assert len(calls(app)) == 1


async def test_a_dispatched_assessment_interrupted_by_restart_stays_unknown(tmp_path):
    app, now = rig(tmp_path)
    await conversation(app, now)
    app.sync.reactions(
        PEER, 2, snapshot(peer=[("👍", now[0] + 1)], observed=now[0] + 1)
    )
    started = asyncio.Event()

    async def hang(role, packet):
        started.set()
        await asyncio.Event().wait()

    app.decider.generate = hang
    now[0] += 31
    task = asyncio.create_task(app.process_due())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    app.store.recover()
    record = app.assessment.record(KEY)
    assert record["state"] == "failed" and record["failure"] == "interrupted"
    assert app.emotion.view(KEY).reason == "unavailable"


async def test_account_off_waits_without_a_call(tmp_path):
    from enloq.domain.models import ProcessingPaused

    app, now = rig(tmp_path)
    await conversation(app, now)

    def off():
        raise ProcessingPaused("AI processing is Off for this account")

    app.admit_ai = off
    app.sync.reactions(
        PEER, 2, snapshot(peer=[("👍", now[0] + 1)], observed=now[0] + 1)
    )
    now[0] += 40
    job = app.rewriting.next_job()
    assert job and job["role"] == "reaction_assessment"
    assert app.rewriting.claim(job)
    await app.rewriting.run(job)
    assert not calls(app)
    assert (
        app.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))[0]["state"]
        == "queued"
    )
    assert app.emotion.view(KEY).reason == "pending"


async def test_an_unrelated_change_reuses_the_unchanged_current_outcome(tmp_path):
    app, now = rig(
        tmp_path, assessment={"emotion": "positive", "suggestions_useful": False}
    )
    await conversation(app, now)
    older, newer = now[0] + 1, now[0] + 2
    app.sync.reactions(PEER, 1, snapshot(peer=[("👌", older)], observed=now[0] + 1))
    app.sync.reactions(PEER, 2, snapshot(peer=[("👍", newer)], observed=now[0] + 2))
    await settle(app, now, 35)
    assert len(calls(app)) == 1
    assert calls(app)[0]["state"]["reaction"]["emoji"] == "👍"
    # Withdrawing the older, non-current reaction is a confirmed change, but
    # the current reaction and its context are unchanged: no second call.
    app.sync.reactions(PEER, 1, snapshot(observed=now[0] + 3))
    assert app.store.rows(
        "SELECT 1 FROM jobs WHERE role='reaction_assessment' AND state='queued'"
    )
    await settle(app, now, 35)
    assert len(calls(app)) == 1
    assert app.emotion.view(KEY).code == "positive"
    # An edit of a supporting message is changed evidence for a new pass.
    app.sync.ingest(
        message(app, 1, text="Can you send the final plan?", revision=2, date=START),
        target=False,
    )
    assert app.emotion.view(KEY).reason == "changed"
    await settle(app, now, 35)
    assert len(calls(app)) == 2
    assert app.emotion.view(KEY).code == "positive"


async def test_an_assessment_stopped_before_dispatch_runs_again(tmp_path):
    """Review finding: a pending record from an attempt that never reached the
    provider (budget wait, pause or restart) must not block a later run."""
    from enloq.domain.models import Refused

    app, now = rig(
        tmp_path, assessment={"emotion": "positive", "suggestions_useful": False}
    )
    await conversation(app, now)
    work = app.rewriting.work
    original = work.reserve
    blocked = []

    def reserve(job, engine, maximum=None, *, reserve=0):
        if job["role"] == "reaction_assessment" and not blocked:
            blocked.append(job["id"])
            raise Refused("Daily budget reached; waiting for the next budget day")
        return original(job, engine, maximum, reserve=reserve)

    work.reserve = reserve
    app.sync.reactions(
        PEER, 2, snapshot(peer=[("👍", now[0] + 1)], observed=now[0] + 1)
    )
    await settle(app, now, 35)
    assert blocked and not calls(app)
    assert app.emotion.view(KEY).reason == "pending"
    await settle(app, now, 70)
    assert len(calls(app)) == 1 and app.emotion.view(KEY).code == "positive"


async def test_a_restart_before_dispatch_keeps_the_assessment_runnable(tmp_path):
    app, now = rig(
        tmp_path, assessment={"emotion": "positive", "suggestions_useful": False}
    )
    await conversation(app, now)
    app.sync.reactions(
        PEER, 2, snapshot(peer=[("👍", now[0] + 1)], observed=now[0] + 1)
    )
    now[0] += 31
    started = asyncio.Event()
    original = app.rewriting.work.run

    async def interrupted(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    app.rewriting.work.run = interrupted
    job = app.rewriting.next_job()
    assert app.rewriting.claim(job)
    task = asyncio.create_task(app.rewriting.run(job))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # The service stopped with the job claimed and its record pending.
    app.store.recover()
    app.rewriting.work.run = original
    await settle(app, now, 5)
    assert len(calls(app)) == 1 and app.emotion.view(KEY).code == "positive"


async def test_the_decision_keeps_probabilities_and_applies_the_floor(tmp_path):
    app, now = rig(
        tmp_path, assessment={"emotion": "positive", "suggestions_useful": True}
    )
    # A selected probability below 0.35 is stored as unknown (ADR 30 Q6).
    app.decider.probability = 0.3
    await conversation(app, now)
    app.sync.reactions(
        PEER, 2, snapshot(peer=[("🤝", now[0] + 1)], observed=now[0] + 1)
    )
    await settle(app, now, 35)
    record = app.assessment.record(KEY)
    assert (record["state"], record["code"], record["useful"]) == (
        "ready",
        "unknown",
        True,
    )
    assert record["decision"]["choice"] == "positive"
    assert record["decision"]["probability"] == 0.3 and record["usefulness"] == 0.9
    view = app.emotion.view(KEY)
    assert (view.code, view.reason) == ("unknown", "uncertain")
    # Probabilities stay in the record; unknown never qualifies for options.
    assert "probability" not in view.model_dump()
    assert not calls(app, "response_suggestions")
