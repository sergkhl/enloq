"""Independent GB5 race regressions over authored offline manager state."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from enloq.application import group_context
from enloq.application.accounts import AccountManager
from enloq.config import Settings
from enloq.domain.models import Chat, Message, Refused
from enloq.inference.work import ROUTES
from enloq.storage.store import WINDOW

from backend.tests.group_test_support import known_non_ads
from backend.tests.test_group_pipeline import (
    DAY,
    NOW,
    AuthoredEngine,
    next_stage,
    source,
    speech,
)


@pytest.fixture
async def review_rig(tmp_path, request):
    manager = AccountManager(Settings(data=tmp_path, timezone="UTC"))
    manager.registry.set_concurrency(1)
    manager.set_processing_threshold(0)
    now = [getattr(request, "param", NOW)]
    app = manager.get(1)
    app.store.clock = lambda: now[0]
    key = "1/channel:42"
    app.store.put_chat(
        Chat(account_id=1, peer="channel:42", title="Authored review", kind="group"),
        activity=now[0],
    )
    app.store.groups.observe(key, access="available", reason="authored_native")
    app.set_group_processing(key, True)
    app.briefings.check_citations = False
    known_non_ads(app)
    engine = AuthoredEngine(app)
    app.engine = engine
    value = SimpleNamespace(
        manager=manager,
        registry=manager.registry,
        app=app,
        store=app.store,
        pipeline=app.briefings,
        storage=app.store.group_briefings,
        engine=engine,
        now=now,
        key=key,
    )
    try:
        yield value
    finally:
        await manager.close()


@pytest.mark.parametrize("review_rig", [NOW - WINDOW + 10], indirect=True)
async def test_started_local_speech_caches_when_automatic_window_expires(review_rig):
    r = review_rig
    message = speech(r, 1)
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldMeasuredTranscriber:
        async def transcribe(self, path, *, on_start):
            on_start(60)
            entered.set()
            await release.wait()
            return {
                "text": "Authored completed local transcript.",
                "language": "en",
                "duration_seconds": 60,
                "model_revision": "authored-only",
            }

    r.app.media.transcriber = HeldMeasuredTranscriber()
    r.now[0] = NOW
    job = next_stage(r, "prepare")
    attempt = r.storage.attempt(json.loads(job["payload"])["attempt"])
    started_clock = r.storage.run_state(r.key).last_auto_started
    task = asyncio.create_task(r.pipeline.run(job))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert r.storage.batch(attempt.batch_id).speech_seconds == 60
        assert r.store.rows("SELECT state FROM jobs WHERE role='transcript'") == [
            {"state": "running"}
        ]
        r.now[0] += 20
        assert r.pipeline.next_job() is None
        assert r.storage.attempt(attempt.id).state == "active"
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=2)
    assert r.app.media.cached(message)["transcript"]["text"]
    assert r.store.rows("SELECT state FROM jobs WHERE role='transcript'") == [
        {"state": "done"}
    ]
    assert r.pipeline.next_job() is None
    assert r.storage.attempt(attempt.id).state == "interrupted"
    batch = r.storage.batch(attempt.batch_id)
    assert batch.state == "interrupted"
    assert batch.speech_seconds == 60 and batch.requests == 0
    assert r.storage.run_state(r.key).active_batch is None
    assert r.storage.run_state(r.key).last_auto_started == started_clock
    assert not r.engine.calls and not r.store.rows("SELECT id FROM receipts")
    assert r.storage.current(attempt.snapshot)
    assert r.pipeline.retry(attempt.id)
    retry = r.storage.state(r.key, group_context.calendar_day(r.store, message)).attempt
    assert retry.retry_of == attempt.id


@pytest.mark.parametrize("review_rig", [NOW - 86400], indirect=True)
async def test_retry_cannot_replace_automatic_batch_between_days(review_rig):
    r = review_rig
    old_day = "2026-10-10"
    message = source(r, 1)
    packet = group_context.packet(r.store, r.key, old_day, [message])
    snapshot = r.storage.freeze(
        r.key,
        old_day,
        [packet],
        intended_ids=[1],
        guidance_hash=r.pipeline._guide(),
        route=ROUTES["group_briefing"],
    )
    previous = r.storage.create_attempt(snapshot, automatic=True)
    r.storage.fail(previous.id, "Authored uncertain attempt")
    r.storage.finish_batch(previous.batch_id, state="failed")
    r.now[0] = NOW
    source(r, 2)
    source(r, 3, date=NOW - 86400)
    r.now[0] += 31
    await r.pipeline.run(next_stage(r, "prepare"))
    await r.pipeline.run(next_stage(r, "final"))
    assert r.storage.state(r.key, DAY).final.covered_ids == [2]
    run = r.storage.run_state(r.key)
    batch = r.storage.batch(run.active_batch)
    assert batch.state == "active" and batch.automatic
    before_attempts = r.store.rows("SELECT id FROM group_briefing_attempts")
    before_batches = r.store.rows("SELECT id,body FROM group_briefing_batches")
    before_jobs = r.store.rows("SELECT id,payload FROM jobs WHERE state='queued'")
    assert len(before_jobs) == 1
    assert json.loads(before_jobs[0]["payload"])["stage"] == "start_day"
    with pytest.raises(Refused, match="already active"):
        r.pipeline.retry(previous.id)
    assert r.storage.run_state(r.key) == run
    assert r.store.rows("SELECT id FROM group_briefing_attempts") == before_attempts
    assert r.store.rows("SELECT id,body FROM group_briefing_batches") == before_batches
    assert (
        r.store.rows("SELECT id,payload FROM jobs WHERE state='queued'") == before_jobs
    )
    await r.pipeline.run(next_stage(r, "start_day"))
    await r.pipeline.run(next_stage(r, "prepare"))
    await r.pipeline.run(next_stage(r, "final"))
    final = r.storage.state(r.key, old_day).final
    assert final.covered_ids == [1, 3]
    assert r.storage.batch(batch.id).state == "completed"
    assert r.storage.run_state(r.key).last_auto_started == run.last_auto_started


async def test_started_group_call_keeps_memory_busy_after_group_ai_off(review_rig):
    r = review_rig
    source(r, 1)
    r.now[0] += 31
    await r.pipeline.run(next_stage(r, "prepare"))
    r.engine.finish.clear()
    task = asyncio.create_task(r.pipeline.run(next_stage(r, "final")))
    try:
        await asyncio.wait_for(r.engine.provider_entered.wait(), timeout=2)
        r.app.set_group_processing(r.key, False)
        assert r.store.rows(
            "SELECT state FROM jobs WHERE role='group_briefing' AND state='running'"
        ) == [{"state": "running"}]
        assert not r.pipeline.planner.has_pending()
        assert not r.pipeline.local_idle()
        assert not r.manager.memory_idle()
    finally:
        r.engine.finish.set()
        await asyncio.wait_for(task, timeout=2)
    assert r.storage.state(r.key, DAY).final.covered_ids == [1]
    assert r.pipeline.local_idle() and r.manager.memory_idle()


async def test_off_attempt_and_retry_only_prefix_do_not_block_idle_memory(review_rig):
    r = review_rig
    first = source(r, 1, text="", media="audio", attachment_id="authored-speech")
    for identity in range(2, 201):
        source(r, identity, text="Authored update.")
    # The retained transcript fits local Whisper's 32k byte ceiling while its
    # whole source exceeds one 24k group input. The omission must stay explicit.
    with r.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (
                first.key,
                first.revision,
                json.dumps(
                    {
                        "transcript": {
                            "text": "😀" * 7000,
                            "model": "authored-only-whisper",
                            "language": "en",
                            "duration_seconds": 1,
                            "model_revision": "authored-only",
                        }
                    }
                ),
            ),
        )
    r.engine.outcome = "unknown"
    r.now[0] += 31
    await r.pipeline.run(next_stage(r, "prepare"))
    await r.pipeline.run(next_stage(r, "chunk"))
    failed = r.storage.state(r.key, DAY).attempt
    assert failed.state == "failed" and len(failed.snapshot.intended_ids) == 199
    assert [item.message_id for item in failed.snapshot.omissions] == [1]
    r.engine.outcome = "valid"
    source(r, 201)
    r.now[0] += 4 * 3600 + 31
    prospective = r.pipeline.planner.pending(include_deferred=True)
    assert len(prospective) == 1 and r.pipeline._freeze_days(prospective) == []
    other_key = "1/chat:42"
    r.store.put_chat(
        Chat(account_id=1, peer="chat:42", title="Authored held group", kind="group"),
        activity=r.store.clock(),
    )
    r.store.groups.observe(other_key, access="available", reason="authored_native")
    r.app.set_group_processing(other_key, True)
    r.app.sync.ingest(
        Message(
            account_id=1,
            peer="chat:42",
            message_id=1,
            revision=1,
            date=r.now[0],
            text="Authored held update.",
            author_peer="user:7",
        ),
        group_epoch=r.store.groups.intake(other_key).epoch,
    )
    r.now[0] += 31
    await r.pipeline.run(next_stage(r, "prepare"))
    r.app.set_group_processing(other_key, False)
    assert r.storage.state(other_key, DAY).attempt.state == "active"
    assert r.pipeline.planner.has_pending()
    assert not r.pipeline.next_job()
    assert r.pipeline.local_idle() and r.manager.memory_idle()
