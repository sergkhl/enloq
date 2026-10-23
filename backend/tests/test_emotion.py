import asyncio
import json

import pytest
from enloq.application.context import projection
from enloq.domain.emotion import LIFETIME
from enloq.inference.base import EngineResponse
from enloq.inference.openrouter import output_schema
from enloq.inference.work import InvalidOutput, validate_output

from backend.tests.test_rebuild import fixture, message

# ADR 30's decision after the published rewrite; calming (ADR 53), which
# decides first, is covered by test_calming.
pytestmark = pytest.mark.usefixtures("calming_off")

KEY = "1/user:42"


def result(value):
    return validate_output(
        "incoming_rewrite", EngineResponse(value), {"messages": [{"text": "Authored"}]}
    )


def test_the_incoming_rewrite_no_longer_carries_emotion():
    """ADR 30: emotion is a separate decision, outside MiMo's contract."""
    schema = output_schema("incoming_rewrite")
    assert list(schema["properties"]) == ["parts", "image_context"]
    assert schema["required"] == ["parts", "image_context"]
    assert result({"text": "Preserved", "image_context": []}).text == "Preserved"
    for body in [
        {"text": "Preserved", "image_context": [], "emotion": "sad"},
        {"text": "", "image_context": []},
        {"text": "Ok", "image_context": [{"message_id": 9, "text": "x"}]},
        {"text": "Ok", "image_context": [], "extra": 1},
        '{"text":"ok","text":"duplicate","image_context":[]}',
        '{"text":"ok","image_context":[]',
    ]:
        with pytest.raises(InvalidOutput):
            result(body)


def capture(app, ids=(1,), *, context_ids=()):
    sources = [app.store.message(KEY, i) for i in ids]
    context = [app.store.message(KEY, i) for i in context_ids]
    packet = {
        "messages": [projection(m) for m in sources],
        "inputs": {m.key: m.revision for m in sources + context},
        "attribution_inputs": {
            m.key: [getattr(m, "forwarded", None), getattr(m, "forwarded_from", None)]
            for m in sources + context
        },
    }
    return app.emotion.capture(
        {"id": sources[0].key, "chat_key": KEY}, packet, "authored-attempt"
    )


def publish(app, record=None, code="worried"):
    with app.store.transaction() as db:
        app.emotion.publish(db, KEY, record, code, 1)


def test_native_clock_twelve_hour_boundary_and_restart_reuse(tmp_path):
    now = [2_000_000_000]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app))
    record = capture(app)
    now[0] += 300
    publish(app, record)
    calls = app.store.rows("SELECT count(*) AS n FROM jobs")[0]["n"]
    now[0] = record["source_time"] + LIFETIME - 0.01
    assert app.emotion.view(KEY).code == "worried"
    restarted = fixture(tmp_path, clock=lambda: now[0])
    assert restarted.emotion.view(KEY).code == "worried"
    now[0] += 0.01
    assert restarted.emotion.view(KEY).reason == "expired"
    now[0] += 1
    assert restarted.emotion.view(KEY).code is None
    assert app.store.rows("SELECT count(*) AS n FROM jobs")[0]["n"] == calls
    assert not app.store.rows("SELECT * FROM reservations")


def test_new_incoming_non_ai_tie_order_old_completion_and_no_resurrection(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app))
    old = capture(app)
    publish(app, old)
    app.sync.ingest(message(app, 2, outgoing=True))
    assert app.emotion.view(KEY).code == "worried"
    app.sync.ingest(message(app, 3, text="", media="sticker"))
    assert app.emotion.view(KEY).code is None
    publish(app, old, code="positive")
    assert app.emotion.view(KEY).code is None
    app.sync.ingest(message(app, 3, text="", media="sticker", deleted=True, revision=2))
    publish(app, old, code="positive")
    assert app.emotion.view(KEY).code is None
    # A new deliberate assessment captures the deletion barrier.
    publish(app, capture(app), code="neutral")
    assert app.emotion.view(KEY).code == "neutral"


@pytest.mark.parametrize("change", ["edit", "delete", "transcript", "image"])
def test_used_supporting_sources_invalidate_and_other_chats_do_not(tmp_path, change):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, outgoing=True))
    app.sync.ingest(message(app, 2))
    publish(app, capture(app, (2,), context_ids=(1,)))
    if change in ("edit", "delete"):
        app.sync.ingest(
            message(
                app,
                1,
                outgoing=True,
                revision=2,
                text="changed",
                deleted=change == "delete",
            )
        )
    else:
        body = (
            {"transcript": {"text": "changed", "language": "en"}}
            if change == "transcript"
            else {"image_context": "changed"}
        )
        with app.store.transaction() as db:
            db.execute(
                "INSERT INTO derivatives VALUES (?,?,?)",
                (f"{KEY}/1", 1, json.dumps(body)),
            )
    assert app.emotion.view(KEY).code is None
    other = fixture(tmp_path, account=2)
    assert other.emotion.view("2/user:42").reason == "unavailable"


def test_legacy_schema_and_retention_are_additive(tmp_path):
    now = [2_000_000_000]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app))
    with app.store.transaction() as db:
        db.execute("DROP TABLE emotion_state")
        db.execute("PRAGMA user_version=20")
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="Keep authored words"))
    app = fixture(tmp_path, clock=lambda: now[0])
    assert app.emotion.view(KEY).code is None
    assert app.store.draft(KEY)["text"] == "Keep authored words"
    publish(app, capture(app))
    now[0] += 31 * 86400
    app.store.expire()
    assert not app.store.rows("SELECT * FROM emotion_state")


@pytest.mark.parametrize("change_before_publication", [False, True])
async def test_conflicting_attribution_withdraws_emotion_without_new_work(
    tmp_path, change_before_publication
):
    from enloq.application.context import incoming

    app = fixture(tmp_path)
    app.sync.ingest(message(app, forwarded=True, forwarded_from="Authored origin"))
    block = app.store.rows("SELECT * FROM blocks")[0]
    packet = await incoming(app, block)
    record = app.emotion.capture(block, packet, "attribution-attempt")
    publish(app, record)
    assert app.emotion.view(KEY).code == "worried"
    jobs = app.store.rows("SELECT * FROM jobs")
    # A conflicting known value, without a content or native revision change.
    with app.store.transaction() as db:
        db.execute(
            "UPDATE messages SET body=json_set(body,'$.forwarded_from','Another origin') WHERE key=?",
            (KEY + "/1",),
        )
        if change_before_publication:
            db.execute("DELETE FROM emotion_state")
    if change_before_publication:
        publish(app, record)
        assert not app.store.rows("SELECT * FROM emotion_state")
    else:
        assert app.emotion.view(KEY).reason == "changed"
    assert app.store.rows("SELECT * FROM jobs") == jobs
    assert not app.store.rows("SELECT * FROM reservations")


@pytest.mark.parametrize(
    "initial,learned",
    [
        ({}, {"forwarded": True, "forwarded_from": "Authored origin"}),
        ({}, {"forwarded": False}),
        ({"forwarded": True}, {"forwarded": True, "forwarded_from": "Authored origin"}),
    ],
)
async def test_filled_in_attribution_keeps_emotion_current(tmp_path, initial, learned):
    """ADR 27: learning previously unknown forward state or a missing name,
    with the header's date, signature and imported flag, is a fill-in."""
    from enloq.application.context import incoming

    app = fixture(tmp_path)
    app.sync.ingest(message(app, **initial))
    block = app.store.rows("SELECT * FROM blocks")[0]
    packet = await incoming(app, block)
    record = app.emotion.capture(block, packet, "fill-in-attempt")
    publish(app, record)
    jobs = app.store.rows("SELECT * FROM jobs")
    details = (
        {
            "forward_date": app.store.clock() - 86400,
            "forward_signature": "Authored author",
            "imported": False,
        }
        if learned["forwarded"]
        else {}
    )
    app.store.learn_forwarding(message(app, **learned, **details))
    stored = app.store.message(KEY, 1)
    assert [stored.forwarded, stored.forwarded_from] == [
        learned["forwarded"],
        learned.get("forwarded_from"),
    ]
    assert app.emotion.view(KEY).code == "worried"
    assert app.store.rows("SELECT * FROM jobs") == jobs


# ---- Emotion Classification after the rewrite (ADR 30, D2) ------------------


def classification(app):
    rows = app.store.rows(
        "SELECT * FROM jobs WHERE role='emotion_classification' ORDER BY rowid"
    )
    return rows[-1] if rows else None


async def rewritten(app, now, identity=1, **fields):
    app.sync.ingest(message(app, identity, **fields))
    now[0] += 30
    await app.process_due()


def rig(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    return app, now


async def test_classification_follows_publication_and_readiness_never_waits(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1, outgoing=True, text="Owner earlier words"))
    await rewritten(app, now, 2, text="I am worried about tomorrow.")
    block = app.conversation(KEY)["blocks"][-1]
    assert block["state"] == "ready" and not app.decider.calls
    assert app.emotion.view(KEY).reason == "pending"
    job = classification(app)
    assert job["state"] == "queued" and job["scope"] == KEY
    assert json.loads(job["payload"]) == {"class": "new", "block": f"{KEY}/2"}
    app.decider.emotion = "worried"
    await app.process_due()
    view = app.emotion.view(KEY)
    assert (view.code, view.reason) == ("worried", "available")
    [(role, packet)] = app.decider.calls
    assert role == "emotion_classification" and set(packet) == {"state", "guidance"}
    assert packet["state"] == {
        "conversation": [
            {"id": 1, "from": "owner", "text": "Owner earlier words"},
            {
                "id": 2,
                "from": "recipient",
                "text": "I am worried about tomorrow.",
                "latest_block": True,
            },
        ]
    }
    record = app.emotion.record(KEY)
    assert record["state"] == "ready" and "prepared" not in record
    assert record["decision"]["probability"] == 0.9
    assert record["decision"]["choice"] == "worried"
    # Reads, refreshes and restarts start nothing new. The suggestions that
    # follow ask only for their labels (ADR 49 Q19).
    app.conversation(KEY)
    app.store.recover()
    await app.process_due()
    assert [role for role, _ in app.decider.calls if role != "suggestion_labels"] == [
        "emotion_classification"
    ]
    assert "probability" not in view.model_dump()


async def test_a_selected_probability_below_the_floor_is_uncertain(tmp_path):
    app, now = rig(tmp_path)
    app.decider.emotion, app.decider.probability = "angry", 0.34
    await rewritten(app, now)
    await app.process_due()
    view = app.emotion.view(KEY)
    assert (view.code, view.reason) == ("unknown", "uncertain")
    assert app.emotion.record(KEY)["decision"]["choice"] == "angry"


async def test_failure_is_unavailable_with_a_diagnostic_and_pauses_nothing(tmp_path):
    from enloq.storage.accounts import AccountRegistry

    app, now = rig(tmp_path)
    registry = AccountRegistry(tmp_path)
    app.rewriting.work.on_format = registry.record_format
    app.decider.emotion = None  # No answer at all.
    for identity in (1, 2, 3, 4):
        await rewritten(app, now, identity, text=f"Authored message {identity}")
        await app.process_due()
        record = app.emotion.record(KEY)
        assert (record["state"], record["failure"]) == ("failed", "missing_answer")
        assert app.emotion.view(KEY).reason == "invalid"
    # Four failures: no role pauses, the rewrite never reruns, no retry.
    assert not registry.paused_roles()
    assert "emotion_classification" not in registry.format_failures()
    assert len(app.engine.calls) == 4 and len(app.decider.calls) == 4
    assert not app.store.rows("SELECT id FROM jobs WHERE state='queued'")
    receipt = app.store.rows(
        "SELECT usage FROM receipts WHERE role='emotion_classification'"
    )[-1]
    assert json.loads(receipt["usage"])["format_failure"] == "missing_answer"


async def test_refusals_back_off_three_times_then_leave_emotion_unavailable(
    tmp_path,
):
    from enloq.inference.base import ProviderRefused

    app, now = rig(tmp_path)
    refusals = [4]

    async def prepare(packet):
        if refusals[0]:
            refusals[0] -= 1
            raise ProviderRefused("Exact Decider / Perplexity route unavailable")

    app.decider.prepare = prepare
    await rewritten(app, now)
    for delay in (30, 60, 120):
        await app.process_due()
        job = classification(app)
        assert job["state"] == "queued" and job["due"] == now[0] + delay
        assert app.emotion.view(KEY).reason == "pending"
        now[0] = job["due"]
    await app.process_due()
    record = app.emotion.record(KEY)
    assert (record["state"], record["failure"]) == ("failed", "refused")
    assert app.emotion.view(KEY).reason == "invalid"
    assert not app.decider.calls
    # Only the incoming rewrite reserved anything; refusals did no work.
    assert len(app.store.rows("SELECT * FROM reservations")) == 1


async def test_ai_off_holds_and_restart_recovery_never_resamples(tmp_path):
    from enloq.domain.models import ProcessingPaused

    app, now = rig(tmp_path)
    await rewritten(app, now)
    job = classification(app)

    def off():
        raise ProcessingPaused("AI processing is Off for this account")

    app.admit_ai = off
    assert app.rewriting.claim(job)
    await app.rewriting.run(job)
    assert classification(app)["state"] == "queued" and not app.decider.calls
    assert app.emotion.view(KEY).reason == "pending"
    app.admit_ai = lambda: None
    # A claimed, undispatched attempt runs again after a restart.
    assert app.rewriting.claim(job)
    app.store.recover()
    assert classification(app)["state"] == "queued"
    hold = asyncio.Event()

    async def hang(role, packet):
        hold.set()
        await asyncio.Event().wait()

    app.decider.generate = hang
    task = asyncio.create_task(app.process_due())
    await asyncio.wait_for(hold.wait(), 5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # A dispatched attempt interrupted by a restart is never resampled.
    app.store.recover()
    record = app.emotion.record(KEY)
    assert (record["state"], record["failure"]) == ("failed", "interrupted")
    assert app.emotion.view(KEY).reason == "invalid"
    assert not app.store.rows("SELECT id FROM jobs WHERE state='queued'")


async def test_newer_incoming_supersedes_and_late_output_never_publishes(tmp_path):
    from enloq.inference.fake import FakeDecider

    app, now = rig(tmp_path)
    await rewritten(app, now)
    release, started = asyncio.Event(), asyncio.Event()
    authored = FakeDecider()
    authored.emotion = "angry"

    async def held(role, packet):
        started.set()
        await release.wait()
        return await authored.generate(role, packet)

    app.decider.generate = held
    task = asyncio.create_task(app.process_due())
    await asyncio.wait_for(started.wait(), 5)
    app.sync.ingest(message(app, 2, text="Never mind, all good."))
    release.set()
    await task
    assert classification(app)["state"] == "superseded"
    assert app.emotion.record(KEY)["state"] == "superseded"
    view = app.emotion.view(KEY)
    assert view.code is None and view.reason == "pending"
    # The receipted cost of the late call is kept.
    assert app.store.rows(
        "SELECT status FROM reservations WHERE id=?", (classification(app)["id"],)
    ) == [{"status": "receipted"}]


async def test_image_meaning_and_attribution_reach_the_state_never_raw_images(
    tmp_path,
):
    from backend.tests.test_rebuild import image_bytes

    app, now = rig(tmp_path)
    app.transport.media["user:42", 1] = image_bytes()
    app.sync.ingest(message(app, 1, text="Look at this.", media="image"))
    app.sync.ingest(
        message(
            app,
            2,
            text="Furious customer words.",
            forwarded=True,
            forwarded_from="Authored origin",
        )
    )
    now[0] += 30
    await app.process_due()
    await app.process_due()
    [(_, packet)] = app.decider.calls
    entries = packet["state"]["conversation"]
    assert entries[0]["image_meaning"] == "An authored synthetic image."
    assert entries[1]["forwarded_from"] == "Authored origin"
    assert all(e["latest_block"] for e in entries)
    assert "data:" not in json.dumps(packet)


async def test_schedule_failure_never_undoes_the_incoming_result(tmp_path, caplog):
    app, now = rig(tmp_path)

    def broken(*args, **kwargs):
        raise KeyError("authored scheduling failure")

    app.emotion._schedule = broken
    with caplog.at_level("WARNING", logger="enloq.emotion"):
        await rewritten(app, now)
    block = app.conversation(KEY)["blocks"][0]
    assert block["state"] == "ready" and block["rendered"]
    assert classification(app) is None
    assert app.emotion.view(KEY).reason == "unavailable"
    assert [r.msg for r in caplog.records] == ["emotion_schedule_unavailable"]


async def test_no_paid_call_for_a_block_past_the_twelve_hour_lifetime(tmp_path):
    app, now = rig(tmp_path)
    # Unread catch-up of a block already older than the display lifetime.
    app.sync.ingest(message(app, 1, date=now[0] - LIFETIME - 60))
    now[0] += 30
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0")
    await app.process_due()
    assert app.conversation(KEY)["blocks"][0]["state"] == "ready"
    assert classification(app) is None and not app.decider.calls
    assert app.emotion.view(KEY).reason == "expired"
    # A queued classification that expires before dispatch is not sent.
    app2, later = rig(tmp_path / "queued")
    await rewritten(app2, later)
    later[0] += LIFETIME
    await app2.process_due()
    assert classification(app2)["state"] == "superseded" and not app2.decider.calls
    assert app2.emotion.record(KEY)["state"] == "superseded"


async def test_a_chat_excluded_during_the_call_never_publishes(tmp_path):
    app, now = rig(tmp_path)
    await rewritten(app, now)
    original = app.decider.generate

    async def excluding(role, packet):
        chat = app.store.chat(KEY)
        app.store.put_chat(chat.model_copy(update={"ai_eligible": False}))
        return await original(role, packet)

    app.decider.generate = excluding
    app.decider.emotion = "sad"
    await app.process_due()
    assert len(app.decider.calls) == 1
    assert classification(app)["state"] == "superseded"
    assert app.emotion.view(KEY).code is None


async def test_changes_before_dispatch_supersede_without_a_failure(tmp_path):
    app, now = rig(tmp_path)
    await rewritten(app, now)
    job = classification(app)
    work = app.rewriting.work
    original = work.run

    async def changed_while_waiting(engine, job, packet, valid, **kwargs):
        # The block stops being current while the job waits for a slot.
        app.sync.ingest(message(app, 1, revision=2, text="Edited words"))
        return await original(engine, job, packet, valid, **kwargs)

    work.run = changed_while_waiting
    assert app.rewriting.claim(job)
    await app.rewriting.run(job)
    assert classification(app)["state"] == "superseded" and not app.decider.calls
    record = app.emotion.record(KEY)
    assert (record["state"], record["failure"]) == ("superseded", None)
    assert app.emotion.view(KEY).reason != "invalid"
    # A restart neither fails nor revives it.
    app.store.recover()
    assert app.emotion.record(KEY)["state"] == "superseded"


async def test_ending_an_older_run_stops_its_queued_classification(tmp_path):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1))
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET state='cancelled'")
        db.execute("UPDATE blocks SET state='original'")
    assert app.rewriting.process_older(KEY) == 1
    await app.process_due()
    job = classification(app)
    assert json.loads(job["payload"])["class"] == "older"
    app.rewriting.end_older(KEY, "stop")
    assert classification(app)["state"] == "cancelled"
    assert app.emotion.record(KEY)["state"] == "superseded"
    await app.process_due()
    assert not app.decider.calls
