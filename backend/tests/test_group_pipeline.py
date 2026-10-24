"""Authored GB5 pipeline races and caps, with no native/provider/audio work."""

import asyncio
import copy
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings, private_write
from enloq.domain.models import Chat, MediaInfo, Message
from enloq.inference import audio
from enloq.inference.audio import WHISPER, LocalWhisper
from enloq.inference.base import EngineResponse, ProviderRefused
from enloq.inference.guidance import briefing_fingerprint
from enloq.inference.work import FLAT_UNKNOWN, ROUTES
from PIL import Image

from backend.tests.group_test_support import briefing_output, known_non_ads

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC).timestamp()
DAY = "2026-10-11"


class AuthoredEngine:
    """A controlled fake receipt and short cited answer over exact frozen IDs."""

    name, model, provider = "mimo", "authored-only-mimo", "xiaomi"

    def __init__(self, app):
        self.app = app
        self.calls, self.prepared = [], []
        self.route_entered, self.provider_entered = asyncio.Event(), asyncio.Event()
        self.release_route, self.finish = asyncio.Event(), asyncio.Event()
        self.release_route.set()
        self.finish.set()
        self.refusal, self.outcome = None, "valid"
        self.fail_at = None

    async def prepare(self, packet):
        self.prepared.append(copy.deepcopy(packet))
        self.route_entered.set()
        await self.release_route.wait()
        if self.refusal == "route":
            raise ProviderRefused("Authored exact route unavailable", retry_after=900)

    async def generate(self, role, packet):
        active = self.app.store.rows(
            "SELECT id FROM group_briefing_attempts WHERE state='active'"
        )
        assert len(active) == 1
        attempt = self.app.store.group_briefings.attempt(active[0]["id"])
        self.calls.append(
            dict(
                role=role,
                packet=copy.deepcopy(packet),
                attempt=attempt.id,
                common_hash=attempt.snapshot.guidance_hash,
                role_hash=briefing_fingerprint(
                    packet["guidance"], role=role, route=ROUTES[role]
                ),
            )
        )
        self.provider_entered.set()
        await self.finish.wait()
        if self.refusal == "dispatch":
            raise ProviderRefused("Authored zero-work provider refusal")
        outcome = (
            self.outcome
            if self.fail_at is None or len(self.calls) == self.fail_at
            else "valid"
        )
        if outcome == "unknown":
            raise ConnectionError("Authored uncertain result")
        if outcome == "format":
            return EngineResponse({"topics": []}, actual_nanos=250_000)
        if role == "group_briefing_image":
            answer = {
                "image_context": [
                    {
                        "message_id": image["message_id"],
                        "text": "An authored progress chart.",
                    }
                    for image in packet["images"]
                ]
            }
        else:
            answer = briefing_output(packet["covered_ids"])
        return EngineResponse(answer, actual_nanos=250_000)

    async def generate_stream(self, role, packet, on_text):
        response = await self.generate(role, packet)
        if self.outcome == "valid":
            on_text("The authored participants reported progress.")
        return response


@pytest.fixture
async def rig(tmp_path):
    manager = AccountManager(Settings(data=tmp_path, timezone="UTC"))
    manager.registry.set_concurrency(1)
    manager.set_processing_threshold(0)
    now = [NOW]
    app = manager.get(1)
    app.store.clock = lambda: now[0]
    key = "1/channel:42"
    app.store.put_chat(
        Chat(account_id=1, peer="channel:42", title="Authored team", kind="group"),
        activity=now[0],
    )
    app.store.groups.observe(key, access="available", reason="authored_native")
    app.set_group_processing(key, True)
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


def source(r, identity, *, text="Authored substantive progress.", **values):
    message = Message(
        account_id=1,
        peer="channel:42",
        message_id=identity,
        revision=1,
        date=r.now[0],
        author="Authored participant",
        author_peer="user:7",
        text=text,
    ).model_copy(update=values)
    r.app.sync.ingest(message, group_epoch=r.store.groups.intake(r.key).epoch)
    return r.store.message(r.key, identity)


def photo(r, identity=1):
    message = source(
        r, identity, text="", media="image", attachment_id=f"authored-{identity}"
    )
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(output, "PNG")
    r.app.transport.media[message.peer, identity] = output.getvalue()
    return message


def speech(r, identity):
    message = source(
        r,
        identity,
        text="",
        media="audio",
        attachment_id=f"authored-speech-{identity}",
        media_info=MediaInfo(voice=True, duration=1),
    )
    r.app.transport.media[message.peer, identity] = b"OggS-authored-audio-fixture"
    return message


def next_stage(r, expected=None):
    job = r.pipeline.next_job()
    assert job is not None, "Expected an admitted authored stage"
    assert r.pipeline.claim(job)
    if expected is not None:
        assert json.loads(job["payload"])["stage"] == expected
    return job


async def finish(r, *, stages=50):
    for _ in range(stages):
        job = r.pipeline.next_job()
        if job is None:
            return
        assert r.pipeline.claim(job)
        await r.pipeline.run(job)
    pytest.fail("Authored pipeline exceeded the bounded stage check")


def batch(r):
    rows = r.store.rows("SELECT id FROM group_briefing_batches ORDER BY rowid DESC")
    assert rows
    return r.storage.batch(rows[0]["id"])


def save_guidance(r):
    r.registry.set_guidance(
        {"group_briefing": "Preserve each speaker's authored progress separately."}
    )
    return briefing_fingerprint(r.registry.guidance(), route=ROUTES["group_briefing"])


def assert_dispatched_guidance(calls):
    for call in calls:
        actual = briefing_fingerprint(
            call["packet"]["guidance"], route=ROUTES["group_briefing"]
        )
        assert call["common_hash"] == actual
        assert call["role_hash"] == briefing_fingerprint(
            call["packet"]["guidance"],
            role=call["role"],
            route=ROUTES[call["role"]],
        )


def control(r, name, enabled):
    if name == "master":
        r.manager.set_processing(1, enabled)
    elif name == "group":
        r.app.set_group_processing(r.key, enabled)
    elif name == "threshold":
        r.manager.set_processing_threshold(0 if enabled else 10_000)
    else:
        r.registry.set_budget(
            2_000_000_000 if enabled else 0,
            clock=r.store.clock,
            family="group_briefing",
        )


@pytest.mark.parametrize("wait", ["media", "capacity", "route"])
async def test_guidance_save_during_unstarted_wait_binds_one_snapshot(rig, wait):
    r = rig
    photo(r)
    r.now[0] += 31
    if wait == "media":
        await r.pipeline.run(next_stage(r, "prepare"))
        entered, release = asyncio.Event(), asyncio.Event()
        original = r.app.media.enrich

        async def held_enrich(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        # The image model stage performs a second media preparation. Its common
        # and role hashes must have been captured together before this await.
        r.app.media.enrich = held_enrich
        task = asyncio.create_task(r.pipeline.run(next_stage(r, "image")))
        await asyncio.wait_for(entered.wait(), timeout=2)
        latest = save_guidance(r)
        release.set()
        await task
        r.app.media.enrich = original
    else:
        await r.pipeline.run(next_stage(r, "prepare"))
        job = next_stage(r, "image")
        if wait == "capacity":
            async with r.manager.capacity.slot("new"):
                task = asyncio.create_task(r.pipeline.run(job))
                for _ in range(20):
                    if any(
                        entry[2] == "group" for entry in r.manager.capacity._waiters
                    ):
                        break
                    await asyncio.sleep(0)
                else:
                    pytest.fail(
                        "The authored group stage did not reach its capacity wait"
                    )
                assert not r.engine.prepared
                latest = save_guidance(r)
            await task
        else:
            r.engine.release_route.clear()
            task = asyncio.create_task(r.pipeline.run(job))
            await asyncio.wait_for(r.engine.route_entered.wait(), timeout=2)
            latest = save_guidance(r)
            r.engine.release_route.set()
            await task
    assert not r.engine.calls and batch(r).requests == 0
    assert not r.store.rows("SELECT * FROM reservations")
    await finish(r)
    final = r.storage.state(r.key, DAY).final
    assert final and final.covered_ids == [1]
    assert final.snapshot.guidance_hash == latest
    assert [call["role"] for call in r.engine.calls] == [
        "group_briefing_image",
        "group_briefing",
    ]
    assert_dispatched_guidance(r.engine.calls)


@pytest.mark.parametrize("stage", ["image", "chunk"])
async def test_started_stage_finishes_old_guidance_next_call_uses_save(rig, stage):
    r = rig
    if stage == "image":
        photo(r)
    else:
        for identity in range(1, 7):
            source(r, identity, text="😀" * 1200)
    old = briefing_fingerprint(r.registry.guidance(), route=ROUTES["group_briefing"])
    r.now[0] += 31
    await r.pipeline.run(next_stage(r, "prepare"))
    job = next_stage(r, stage)
    r.engine.finish.clear()
    task = asyncio.create_task(r.pipeline.run(job))
    await asyncio.wait_for(r.engine.provider_entered.wait(), timeout=2)
    assert batch(r).requests == 1 and r.engine.calls[0]["common_hash"] == old
    latest = save_guidance(r)
    r.engine.finish.set()
    await task
    if stage == "image":
        assert r.app.media.cached(r.store.message(r.key, 1))["image_context"]
    else:
        assert r.store.rows("SELECT id FROM group_briefing_chunks WHERE valid=1")
    await finish(r)
    final = r.storage.state(r.key, DAY).final
    assert final and final.snapshot.guidance_hash == latest
    assert r.engine.calls[0]["common_hash"] == old
    assert all(call["common_hash"] == latest for call in r.engine.calls[1:])
    assert_dispatched_guidance(r.engine.calls)
    for row in r.store.rows("SELECT usage FROM receipts ORDER BY rowid"):
        assert json.loads(row["usage"])["guidance_version"] in {
            r.engine.calls[0]["packet"]["guidance"]["version"],
            r.registry.guidance()["version"],
        }


async def test_fresh_speech_cap_retains_two_transcripts_and_publishes_partial(
    rig, tmp_path, monkeypatch
):
    r = rig
    for identity in (1, 2, 3):
        speech(r, identity)
    root = tmp_path / "authored-whisper"
    private_write(
        root / ".local/models/whisper/provenance.json",
        json.dumps(dict(model=WHISPER, revision="authored-only")),
    )
    binary = root / "audio/.venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.touch()
    stages = []

    async def fake_process(argv, **kwargs):
        stage = "probe" if argv[0].endswith("ffprobe") else "worker"
        stages.append(stage)
        if stage == "probe":
            return b'{"format":{"duration":"600"}}', b""
        return b'{"text":"Authored local progress","language":"en"}', b""

    monkeypatch.setattr(audio, "bounded_process", fake_process)
    r.app.media.transcriber = LocalWhisper(root)
    r.app.media.transcription_lock = r.app.media.transcriber.lock
    r.now[0] += 31
    await finish(r)
    value = batch(r)
    assert value.speech_seconds == 1200 and value.requests == 1
    assert stages.count("worker") == 2
    assert stages.count("probe") == 3
    assert r.app.media.cached(r.store.message(r.key, 1))["transcript"]
    assert r.app.media.cached(r.store.message(r.key, 2))["transcript"]
    assert not r.app.media.cached(r.store.message(r.key, 3)).get("transcript")
    assert len(r.store.rows("SELECT id FROM jobs WHERE role='transcript'")) == 2
    state = r.storage.state(r.key, DAY)
    assert state.final and state.final.covered_ids == [1, 2]
    assert state.partial and state.uncovered_ids == [3]
    assert any(
        omission.message_id == 3 and "speech" in omission.reason.lower()
        for omission in state.final.omissions
    )
    assert r.engine.calls[0]["role"] == "group_briefing"
    for call in r.engine.calls:
        encoded = json.dumps(call["packet"])
        assert "OggS" not in encoded and "data:audio" not in encoded
        assert [message["id"] for message in call["packet"]["messages"]] == [1, 2]
        assert all(message.get("transcript") for message in call["packet"]["messages"])
    assert not r.app.transport.exact_reads


async def test_five_requests_reserve_a_partial_final_and_explicit_omissions(rig):
    r = rig
    for identity in range(1, 21):
        source(r, identity, text="Authored lengthy progress. " * 250)
    r.now[0] += 31
    await finish(r)
    value = batch(r)
    assert value.requests == 5 and len(r.engine.calls) == 5
    assert r.engine.calls[-1]["role"] == "group_briefing"
    assert all(call["role"] == "group_briefing_chunk" for call in r.engine.calls[:-1])
    assert len(r.store.rows("SELECT id FROM reservations")) == 5
    state = r.storage.state(r.key, DAY)
    assert state.final and state.partial and state.uncovered_ids
    assert set(state.final.covered_ids).isdisjoint(state.uncovered_ids)
    assert {omission.message_id for omission in state.final.omissions} >= set(
        state.uncovered_ids
    )
    assert not r.pipeline.next_job()
    assert_dispatched_guidance(r.engine.calls)


async def test_two_hundred_fresh_sources_do_not_silently_include_the_next(rig):
    r = rig
    for identity in range(1, 202):
        source(r, identity, text=f"Authored progress {identity}.")
    r.now[0] += 31
    await finish(r)
    value = batch(r)
    assert value.source_ids == list(range(1, 201)) and value.requests <= 5
    state = r.storage.state(r.key, DAY)
    assert state.final and state.final.covered_ids == list(range(1, 201))
    assert state.uncovered_ids == [201]
    assert any(
        omission.message_id == 201 and omission.reason == "source work limit"
        for omission in state.final.omissions
    )
    assert all(201 not in call["packet"]["covered_ids"] for call in r.engine.calls)
    assert r.store.message(r.key, 201).text == "Authored progress 201."
    assert not r.app.transport.exact_reads


@pytest.mark.parametrize("outcome", ["unknown", "format"])
async def test_fifth_request_failure_keeps_four_successful_chunks_and_cap(rig, outcome):
    r = rig
    for identity in range(1, 21):
        source(r, identity, text="Authored lengthy progress. " * 250)
    r.engine.outcome, r.engine.fail_at = outcome, 5
    r.now[0] += 31
    await finish(r)
    value = batch(r)
    assert value.requests == 5 and value.state == "failed"
    assert len(r.engine.calls) == 5
    assert r.engine.calls[-1]["role"] == "group_briefing"
    chunks = r.store.rows("SELECT id,body FROM group_briefing_chunks WHERE valid=1")
    assert len(chunks) == 4 and not r.storage.state(r.key, DAY).final
    assert len(r.store.rows("SELECT id FROM reservations")) == 5
    assert len(r.store.rows("SELECT id FROM receipts")) == 5
    before = [(row["id"], row["body"]) for row in chunks]
    r.now[0] += 4 * 3600 + 31
    await finish(r)
    assert batch(r).requests == 5 and len(r.engine.calls) == 5
    assert [
        (row["id"], row["body"])
        for row in r.store.rows(
            "SELECT id,body FROM group_briefing_chunks WHERE valid=1"
        )
    ] == before


@pytest.mark.parametrize("refusal", ["route", "dispatch"])
async def test_no_work_backoff_is_bounded_and_refusals_consume_requests(rig, refusal):
    r = rig
    source(r, 1)
    r.engine.refusal = refusal
    r.now[0] += 31
    for number in (1, 2, 3):
        await finish(r)
        assert batch(r).requests == number
        assert len(r.engine.prepared) == number
        if number < 3:
            assert batch(r).state == "active" and not r.pipeline.next_job()
            rows = r.store.rows(
                "SELECT due FROM jobs WHERE role LIKE 'group_briefing%' AND state='queued'"
            )
            assert len(rows) == 1
            wait = rows[0]["due"] - r.now[0]
            assert 30 <= wait <= 300
            r.now[0] = rows[0]["due"] - 0.01
            assert not r.pipeline.next_job()
            r.now[0] += 0.01
    assert batch(r).state == "failed"
    assert len(r.engine.calls) == (0 if refusal == "route" else 3)
    assert len(r.store.rows("SELECT id FROM reservations")) == (
        0 if refusal == "route" else 3
    )
    assert len(r.store.rows("SELECT id FROM receipts")) == (
        0 if refusal == "route" else 3
    )
    assert (
        r.registry.budget(
            clock=r.store.clock, timezone=r.store.timezone, family="group_briefing"
        )["spent"]
        == 0
    )
    r.now[0] += 4 * 3600
    save_guidance(r)
    assert not r.pipeline.next_job()
    assert batch(r).requests == 3


@pytest.mark.parametrize("outcome", ["unknown", "format"])
async def test_counted_failure_preserves_receipt_and_never_automatically_repeats(
    rig, outcome
):
    r = rig
    source(r, 1)
    r.engine.outcome = outcome
    r.now[0] += 31
    await finish(r)
    value = batch(r)
    assert value.requests == 1 and value.state == "failed"
    assert not r.storage.state(r.key, DAY).final
    reservations = r.store.rows("SELECT status,actual,family FROM reservations")
    assert len(reservations) == 1 and reservations[0]["family"] == "group_briefing"
    if outcome == "unknown":
        assert reservations[0]["status"] == "unknown"
        assert reservations[0]["actual"] is None
        expected = FLAT_UNKNOWN
    else:
        assert reservations[0]["status"] == "receipted"
        expected = 250_000
        assert json.loads(r.store.rows("SELECT usage FROM receipts")[0]["usage"])[
            "format_failure"
        ]
    assert (
        r.registry.budget(
            clock=r.store.clock, timezone=r.store.timezone, family="group_briefing"
        )["spent"]
        == expected
    )
    r.now[0] += 4 * 3600 + 31
    control(r, "master", False)
    control(r, "master", True)
    save_guidance(r)
    await finish(r)
    assert (
        len(r.engine.calls) == 1 and len(r.store.rows("SELECT id FROM receipts")) == 1
    )


@pytest.mark.parametrize("name", ["master", "group", "threshold", "budget"])
async def test_pause_during_route_wait_holds_unstarted_stage_without_request(rig, name):
    r = rig
    source(r, 1)
    r.now[0] += 31
    await r.pipeline.run(next_stage(r, "prepare"))
    clock = r.storage.run_state(r.key).last_auto_started
    r.engine.release_route.clear()
    task = asyncio.create_task(r.pipeline.run(next_stage(r, "final")))
    await asyncio.wait_for(r.engine.route_entered.wait(), timeout=2)
    control(r, name, False)
    r.engine.release_route.set()
    await task
    assert not r.engine.calls and batch(r).requests == 0
    assert batch(r).state == "active" and batch(r).source_ids == [1]
    assert not r.store.rows("SELECT id FROM reservations")
    assert not r.pipeline.next_job()
    assert r.storage.run_state(r.key).last_auto_started == clock
    control(r, name, True)
    await finish(r)
    assert r.storage.state(r.key, DAY).final
    assert batch(r).requests == 1 and len(r.engine.calls) == 1
    assert r.storage.run_state(r.key).last_auto_started == clock


@pytest.mark.parametrize("name", ["master", "group", "threshold", "budget"])
async def test_started_chunk_finishes_under_pause_and_resumes_without_repeating(
    rig, name
):
    r = rig
    for identity in range(1, 7):
        source(r, identity, text="😀" * 1200)
    r.now[0] += 31
    await r.pipeline.run(next_stage(r, "prepare"))
    clock = r.storage.run_state(r.key).last_auto_started
    r.engine.finish.clear()
    task = asyncio.create_task(r.pipeline.run(next_stage(r, "chunk")))
    await asyncio.wait_for(r.engine.provider_entered.wait(), timeout=2)
    control(r, name, False)
    r.engine.finish.set()
    await task
    rows = r.store.rows("SELECT id,body FROM group_briefing_chunks WHERE valid=1")
    assert len(rows) == 1 and batch(r).requests == 1
    before = batch(r).source_ids
    assert rows[0]["body"] and not r.pipeline.next_job()
    assert not r.storage.state(r.key, DAY).final
    assert r.storage.run_state(r.key).last_auto_started == clock
    control(r, name, True)
    await finish(r)
    final = r.storage.state(r.key, DAY).final
    assert final and final.covered_ids == list(range(1, 7))
    assert rows[0]["id"] in final.chunk_ids
    assert (
        r.store.rows(
            "SELECT body FROM group_briefing_chunks WHERE id=?", (rows[0]["id"],)
        )[0]["body"]
        == rows[0]["body"]
    )
    assert set(before) <= set(batch(r).source_ids)
    assert len(r.engine.calls) == batch(r).requests
    assert r.storage.run_state(r.key).last_auto_started == clock
