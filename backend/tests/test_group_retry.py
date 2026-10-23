"""ADR 31: current failure Retry is separate from first-time processing.

All messages, provider answers and stores are authored and isolated.
"""

import asyncio
import json

import pytest
from enloq.application.blocks import reconcile_unread
from enloq.domain.models import ProcessingPaused, Refused
from enloq.inference.base import EngineResponse
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import RETENTION, WINDOW

from backend.tests.test_rebuild import fixture, message

KEY, IDENTITY = "1/user:42", "1/user:42/1"
DAY = 86400


def rig(tmp_path):
    now = [2_000_000_000.0]
    return fixture(tmp_path, clock=lambda: now[0]), now


async def failed(tmp_path, *, age=0):
    app, now = rig(tmp_path)
    generate = app.engine.generate

    async def invalid(role, packet):
        await generate(role, packet)
        return EngineResponse({"wrong": "shape"})

    app.engine.generate = invalid
    app.sync.ingest(message(app, date=now[0] - age * DAY), target=not age)
    if age:
        app.sync.ingest(message(app, 100, outgoing=True))
        assert app.rewriting.process_older(KEY) == 1
    else:
        now[0] += 31
    await app.process_due()
    block = app.rewriting.block(IDENTITY)
    assert block["state"] == "failed" and not block["rendered"]
    app.engine.generate = generate
    return app, now, block


async def test_originals_cannot_retry_but_earlier_new_changed_and_unread_work_remain(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app), target=False)
    original = app.rewriting.block(IDENTITY)
    with pytest.raises(Refused, match="failed or interrupted"):
        app.rewriting.retry(IDENTITY, revision=original["revision"])
    assert not app.store.rows("SELECT 1 FROM jobs") and not app.engine.calls
    assert app.rewriting.process_older(KEY) == 1
    await app.process_due()
    await app.process_due()  # Emotion follows the completed rewrite.

    app.sync.ingest(message(app, 2, text="A new authored point"))
    queued = app.rewriting.block(IDENTITY)
    waiting = app.store.rows("SELECT * FROM jobs")
    with pytest.raises(Refused, match="failed or interrupted"):
        app.rewriting.retry(IDENTITY, revision=queued["revision"])
    assert app.store.rows("SELECT * FROM jobs") == waiting
    now[0] += 31
    await app.process_due()
    await app.process_due()

    app.sync.ingest(message(app, 2, revision=2, text="An edited authored point"))
    now[0] += 31
    await app.process_due()
    await app.process_due()
    app.sync.ingest(message(app, 3, outgoing=True))
    app.sync.ingest(message(app, 4, text="Cached unread point"), target=False)
    assert app.rewriting.block(f"{KEY}/4")["state"] == "original"
    reconcile_unread(app.store)
    now[0] += 31
    await app.process_due()
    await app.process_due()
    assert [role for role, _ in app.engine.calls] == ["incoming_rewrite"] * 4
    draft = app.store.mutate_draft(KEY, 0, lambda d: d.update(text="Owner draft"))
    await app.rewriting.outgoing(KEY, draft["revision"])
    assert app.engine.calls[-1][0] == "outgoing_rewrite"
    assert not app.transport.exact_reads and not app.transport.deliveries


async def test_failed_retry_accepts_one_new_attempt_and_does_not_repeat_after_success(
    tmp_path,
):
    app, _, block = await failed(tmp_path)
    prior = app.store.rows("SELECT id FROM jobs WHERE role='incoming_rewrite'")[0]["id"]
    attempt = app.rewriting.retry(IDENTITY, revision=block["revision"])
    assert attempt != prior
    queued = app.store.rows("SELECT * FROM jobs WHERE id=?", (attempt,))[0]
    assert json.loads(queued["payload"])["explicit_retry"]
    with pytest.raises(Refused):
        app.rewriting.retry(IDENTITY, revision=block["revision"])
    assert len(app.store.rows("SELECT id FROM jobs WHERE role='incoming_rewrite'")) == 2
    await app.process_due()
    await app.process_due()
    assert app.rewriting.block(IDENTITY)["state"] == "ready"
    with pytest.raises(Refused, match="already rewritten"):
        app.rewriting.retry(IDENTITY, revision=block["revision"])
    await app.process_due()
    assert len(app.engine.calls) == 2
    assert (
        len(
            app.store.rows(
                "SELECT r.id FROM reservations r JOIN jobs j ON j.id=r.id WHERE j.role='incoming_rewrite'"
            )
        )
        == 2
    )


async def test_concurrent_retry_clicks_accept_exactly_one_attempt(tmp_path):
    app, _, block = await failed(tmp_path)
    results = await asyncio.gather(
        *(
            asyncio.to_thread(app.rewriting.retry, IDENTITY, revision=block["revision"])
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, str) for result in results) == 1
    assert sum(isinstance(result, Refused) for result in results) == 1
    assert len(app.store.rows("SELECT id FROM jobs WHERE state='queued'")) == 1
    assert len(app.engine.calls) == 1


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
@pytest.mark.parametrize("restart", [False, True])
async def test_interrupted_partial_is_retryable_with_a_new_attempt(tmp_path, restart):
    app, now = rig(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    class Streaming:
        name, model, provider = "offline", "authored", "local"

        async def generate_stream(self, role, packet, publish):
            publish("Incomplete authored prefix")
            entered.set()
            await release.wait()
            return EngineResponse(
                {"text": "Validated authored result", "image_context": []}
            )

    app.engine = Streaming()
    app.sync.ingest(message(app))
    now[0] += 31
    task = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    working = app.rewriting.block(IDENTITY)
    assert app.rewriting.partial(working)["text"] == "Incomplete authored prefix"
    with pytest.raises(Refused):
        app.rewriting.retry(IDENTITY, revision=working["revision"])
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    if restart:
        app = fixture(tmp_path, clock=lambda: now[0])
        app.store.recover()
    interrupted = app.rewriting.block(IDENTITY)
    assert interrupted["state"] == "unknown" and not interrupted["rendered"]
    assert app.rewriting.partial(interrupted) is None
    retry = app.rewriting.retry(IDENTITY, revision=interrupted["revision"])
    release.set()
    await app.process_due()
    assert app.rewriting.block(IDENTITY)["state"] == "ready"
    assert (
        app.store.rows("SELECT state FROM jobs WHERE id=?", (retry,))[0]["state"]
        == "done"
    )
    assert len(app.store.rows("SELECT id FROM reservations")) == 2


@pytest.mark.parametrize("state", ["failed", "unknown"])
async def test_failure_metadata_without_an_incoming_attempt_cannot_start_first_work(
    tmp_path, state
):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app), target=False)
    with app.store.transaction() as db:
        db.execute("UPDATE blocks SET state=?,error='Authored local status'", (state,))
    with pytest.raises(Refused, match="current failed or interrupted attempt"):
        app.rewriting.retry(IDENTITY)
    assert not app.store.rows("SELECT 1 FROM jobs") and not app.engine.calls


@pytest.mark.parametrize("state", ["failed", "unknown"])
async def test_usable_completed_text_survives_failed_or_interrupted_metadata(
    tmp_path, state
):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app))
    now[0] += 31
    await app.process_due()
    complete = app.rewriting.block(IDENTITY)
    jobs = app.store.rows("SELECT * FROM jobs")
    with app.store.transaction() as db:
        db.execute("UPDATE blocks SET state=?,error='Authored local status'", (state,))
    with pytest.raises(Refused, match="already rewritten"):
        app.rewriting.retry(IDENTITY, revision=complete["revision"])
    assert app.rewriting.block(IDENTITY)["rendered"] == complete["rendered"]
    assert app.store.rows("SELECT * FROM jobs") == jobs and len(app.engine.calls) == 1


async def test_stale_reviewed_revision_refuses_before_queueing(tmp_path):
    app, _, block = await failed(tmp_path)
    jobs = app.store.rows("SELECT * FROM jobs")
    with pytest.raises(Refused, match="group changed"):
        app.rewriting.retry(IDENTITY, revision="f" * 64)
    assert app.store.rows("SELECT * FROM jobs") == jobs
    assert app.rewriting.block(IDENTITY) == block and len(app.engine.calls) == 1


async def test_saved_source_edit_before_dependency_hook_refuses_retry(tmp_path):
    app, _, block = await failed(tmp_path)
    app.store.apply_message(message(app, revision=2, text="Edited before hook"))
    assert app.rewriting.block(IDENTITY)["revision"] == block["revision"]
    jobs = app.store.rows("SELECT * FROM jobs")
    with pytest.raises(Refused, match="Source expired or changed"):
        app.rewriting.retry(IDENTITY, revision=block["revision"])
    assert app.store.rows("SELECT * FROM jobs") == jobs and len(app.engine.calls) == 1


@pytest.mark.parametrize("flag", ["outgoing", "service"])
async def test_changed_native_source_role_cannot_retry_as_incoming(tmp_path, flag):
    app, _, block = await failed(tmp_path)
    app.store.apply_message(message(app, **{flag: True}))
    jobs = app.store.rows("SELECT * FROM jobs")
    with pytest.raises(Refused, match="Source expired or changed"):
        app.rewriting.retry(IDENTITY, revision=block["revision"])
    assert app.store.rows("SELECT * FROM jobs") == jobs and len(app.engine.calls) == 1


async def test_another_failed_model_role_does_not_unlock_incoming_retry(tmp_path):
    app, _, block = await failed(tmp_path)
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET role='emotion_classification'")
    jobs = app.store.rows("SELECT * FROM jobs")
    with pytest.raises(Refused, match="current failed or interrupted attempt"):
        app.rewriting.retry(IDENTITY, revision=block["revision"])
    assert app.store.rows("SELECT * FROM jobs") == jobs and len(app.engine.calls) == 1


async def test_new_source_after_retry_acceptance_supersedes_it_before_dispatch(
    tmp_path,
):
    app, now, block = await failed(tmp_path)
    retry = app.rewriting.retry(IDENTITY, revision=block["revision"])
    queued = app.store.rows("SELECT * FROM jobs WHERE id=?", (retry,))[0]
    app.sync.ingest(message(app, revision=2, text="Current changed wording"))
    await app.rewriting.run(queued)
    assert not app.store.rows("SELECT 1 FROM reservations WHERE id=?", (retry,))
    assert (
        app.store.rows("SELECT state FROM jobs WHERE id=?", (retry,))[0]["state"]
        == "superseded"
    )
    now[0] += 31
    await app.process_due()
    assert app.rewriting.block(IDENTITY)["rendered"] == "Current changed wording"
    assert len(app.engine.calls) == 2


async def test_old_cached_failure_remains_retryable_while_chat_is_recent(tmp_path):
    app, _, block = await failed(tmp_path, age=12)
    assert not app.rewriting.eligible(block) and app.rewriting.eligible(
        block, explicit=True
    )
    app.rewriting.retry(IDENTITY, revision=block["revision"])
    await app.process_due()
    assert app.rewriting.block(IDENTITY)["state"] == "ready"
    assert len(app.engine.calls) == 2 and not app.transport.exact_reads


@pytest.mark.parametrize(
    "loss", ["off", "ineligible", "inactive", "expired", "deleted"]
)
async def test_failure_retry_preserves_account_target_and_source_gates(tmp_path, loss):
    app, now, block = await failed(tmp_path)
    if loss == "off":

        def off():
            raise ProcessingPaused("AI is Off")

        app.admit_ai = off
    elif loss == "ineligible":
        app.store.put_chat(
            app.store.chat(KEY).model_copy(update={"ai_eligible": False})
        )
    elif loss == "inactive":
        now[0] += WINDOW + 1
    elif loss == "expired":
        now[0] += RETENTION + 1
        app.sync.ingest(message(app, 100, outgoing=True))
    else:
        app.sync.deleted("user:42", [1])
    jobs = app.store.rows("SELECT * FROM jobs")
    with pytest.raises(Refused):
        app.rewriting.retry(IDENTITY, revision=block["revision"])
    assert app.store.rows("SELECT * FROM jobs") == jobs and len(app.engine.calls) == 1


async def test_failure_retry_obeys_shared_budget_at_dispatch_without_an_extra_call(
    tmp_path,
):
    app, now, block = await failed(tmp_path)
    app.engine.name = "authored-counted"
    registry = AccountRegistry(tmp_path)
    registry.set_budget(0)
    retry = app.rewriting.retry(IDENTITY, revision=block["revision"])
    await app.process_due()
    assert len(app.engine.calls) == 1
    assert not app.store.rows("SELECT 1 FROM reservations WHERE id=?", (retry,))
    assert app.rewriting.block(IDENTITY)["state"] == "queued"
    with pytest.raises(Refused):
        app.rewriting.retry(IDENTITY, revision=block["revision"])
    registry.set_budget(FLAT_UNKNOWN)
    now[0] += 61
    await app.process_due()
    assert len(app.engine.calls) == 2
    assert app.rewriting.block(IDENTITY)["state"] == "ready"
    assert registry.budget(clock=app.store.clock)["reached"]


async def test_format_pause_allows_only_the_deliberate_failed_attempt(tmp_path):
    app, _, block = await failed(tmp_path)
    app.rewriting.paused_roles = lambda: {"incoming_rewrite"}
    app.sync.ingest(message(app, 2, outgoing=True))
    app.sync.ingest(message(app, 3))
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0 WHERE state='queued'")
    assert app.rewriting.next_job() is None
    retry = app.rewriting.retry(IDENTITY, revision=block["revision"])
    assert app.rewriting.next_job()["id"] == retry
    await app.process_due()
    assert app.rewriting.block(IDENTITY)["state"] == "ready"
    assert len(app.engine.calls) == 2
