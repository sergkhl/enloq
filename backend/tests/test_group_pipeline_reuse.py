"""Authored group stage reuse, frozen Retry and durable recovery, offline only."""

import asyncio
import copy
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings, private_write
from enloq.domain.models import Chat, MediaInfo, Message, Refused
from enloq.inference import audio
from enloq.inference.audio import WHISPER, LocalWhisper
from enloq.storage.store import RETENTION, WINDOW
from PIL import Image

from backend.tests.group_test_support import known_non_ads

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC).timestamp()
KEY, DAY = "1/channel:42", "2026-10-11"
INTERVAL = 4 * 3600


def attach_model(rig):
    if rig.classify_non_ads:
        known_non_ads(rig.app)
    original = rig.app.engine.generate
    # Only an in-process authored engine runs. A separate name exercises normal
    # reservation/unknown-cost accounting without contacting any provider.
    rig.app.engine.name = "authored-local-model"
    rig.calls = []
    rig.model_hook = None

    async def generate(role, packet):
        rig.calls.append((role, copy.deepcopy(packet)))
        if rig.model_hook:
            await rig.model_hook(role, packet)
        value = await original(role, packet)
        if role == "group_briefing_chunk":
            # A concise authored model answer keeps reusable notes bounded.
            # This qualifies citation/coverage mechanics, not summary quality.
            return dict(
                contract_version=2,
                outcome="briefing",
                topics=[
                    dict(
                        title="Discussion",
                        text="Authored participants discussed the listed points.",
                        citations=packet["covered_ids"],
                        participant_source_ids=[packet["covered_ids"][0]],
                    )
                ],
            )
        return value

    rig.app.engine.generate = generate


def create(tmp_path, *, first_enabled=NOW, classify_non_ads=True, check=False):
    settings = Settings(data=tmp_path / "authored-app", timezone="UTC")
    manager = AccountManager(settings)
    now = [first_enabled]
    for runtime in manager.runtimes.values():
        runtime.assistant.store.clock = lambda: now[0]
    manager.set_processing_threshold(0)
    app = manager.get(1)
    app.store.put_chat(
        Chat(account_id=1, peer="channel:42", title="Authored group", kind="group"),
        activity=now[0],
    )
    app.store.groups.observe(KEY, access="available", reason="authored_native")
    app.store.groups.scanned(
        KEY,
        epoch=app.store.groups.intake(KEY).epoch,
        before=0,
        top=0,
        cutoff=first_enabled - WINDOW,
        oldest=None,
        count=0,
        complete=True,
    )
    app.set_group_processing(KEY, True)
    # Stage-by-stage tests publish at the final; test_citation_check runs the
    # citation check stage (ADR 34).
    app.briefings.check_citations = check
    rig = SimpleNamespace(
        manager=manager,
        app=app,
        now=now,
        settings=settings,
        tmp_path=tmp_path,
        classify_non_ads=classify_non_ads,
    )
    attach_model(rig)
    return rig


@pytest.fixture
async def rig(tmp_path):
    value = create(tmp_path)
    try:
        yield value
    finally:
        await value.manager.close()


def source(rig, identity, **changes):
    message = Message(
        account_id=1,
        peer="channel:42",
        message_id=identity,
        revision=1,
        date=rig.now[0],
        text=f"Authored point {identity}.",
        author="Alex",
        author_peer="user:7",
    ).model_copy(update=changes)
    rig.app.sync.ingest(message, group_epoch=rig.app.store.groups.intake(KEY).epoch)
    return rig.app.store.message(KEY, identity)


def multiple_packets(rig):
    for identity in range(1, 9):
        source(rig, identity, text=f"Authored point {identity}. " + "A" * 3480)


def photo(rig, identity):
    image = io.BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(image, "PNG")
    value = source(
        rig, identity, text="", media="image", attachment_id=f"photo-{identity}"
    )
    rig.app.transport.media[value.peer, identity] = image.getvalue()
    return value


def speech(rig, identity):
    value = source(
        rig,
        identity,
        text="",
        media="audio",
        attachment_id=f"speech-{identity}",
        media_info=MediaInfo(voice=True, duration=1),
    )
    rig.app.transport.media[value.peer, identity] = b"OggS-authored-test-data"
    return value


def local_worker(rig, monkeypatch, *, duration=23.5):
    root = rig.tmp_path / "authored-whisper"
    private_write(
        root / ".local/models/whisper/provenance.json",
        json.dumps(dict(model=WHISPER, revision="authored-model-revision")),
    )
    binary = root / "audio/.venv/bin/python"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    events = []

    async def process(argv, **kwargs):
        stage = "probe" if argv[0].endswith("ffprobe") else "worker"
        events.append(stage)
        if stage == "probe":
            return json.dumps(dict(format=dict(duration=str(duration)))).encode(), b""
        return b'{"text":"Authored local speech point.","language":"en"}', b""

    monkeypatch.setattr(audio, "bounded_process", process)
    rig.app.media.transcriber = LocalWhisper(root)
    rig.app.media.transcription_lock = rig.app.media.transcriber.lock
    return events


async def step(rig, *, expected=None):
    job = rig.app.briefings.next_job()
    assert job is not None, "An authored admitted stage should be runnable"
    assert rig.app.briefings.claim(job)
    payload = json.loads(job["payload"])
    if expected:
        assert payload["stage"] == expected
    await rig.app.briefings.run(job)
    return payload


async def settle(rig, *, maximum=30):
    stages = []
    for _ in range(maximum):
        if rig.app.briefings.next_job() is None:
            return stages
        stages.append((await step(rig))["stage"])
    pytest.fail("Authored briefing stages did not settle within the bounded check")


def attempts(rig):
    return [
        rig.app.store.group_briefings.attempt(row["id"])
        for row in rig.app.store.rows(
            "SELECT id FROM group_briefing_attempts ORDER BY rowid"
        )
    ]


def batches(rig):
    return [
        rig.app.store.group_briefings.batch(row["id"])
        for row in rig.app.store.rows(
            "SELECT id FROM group_briefing_batches ORDER BY rowid"
        )
    ]


async def restart(rig):
    await rig.manager.close()
    rig.manager = AccountManager(rig.settings)
    for runtime in rig.manager.runtimes.values():
        runtime.assistant.store.clock = lambda: rig.now[0]
    rig.app = rig.manager.get(1)
    rig.app.store.recover()
    rig.app.briefings.reconcile()
    attach_model(rig)


async def test_later_update_reuses_valid_final_and_cached_image_speech_after_restart(
    rig, monkeypatch
):
    multiple_packets(rig)
    photo(rig, 9)
    spoken = speech(rig, 10)
    worker = local_worker(rig, monkeypatch)
    rig.now[0] += 31
    await settle(rig)
    first = rig.app.store.group_briefings.state(KEY, DAY).final
    assert first and set(first.covered_ids) == set(range(1, 11))
    assert {role for role, _ in rig.calls} == {
        "group_briefing_image",
        "group_briefing_chunk",
        "group_briefing",
    }
    initial = batches(rig)[0]
    assert initial.source_ids == list(range(1, 11))
    assert initial.speech_seconds == 23.5 and worker == ["probe", "worker"]
    transcript = rig.app.media.cached(spoken)["transcript"]
    await restart(rig)
    rig.now[0] += INTERVAL + 31
    source(rig, 11)
    rig.now[0] += 31
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and set(final.covered_ids) == set(range(1, 12))
    assert [role for role, _ in rig.calls] == ["group_briefing"]
    request = rig.calls[0][1]
    assert [value["id"] for value in request["messages"]] == [11]
    assert set(request["chunks"][0]["covered_ids"]) == set(range(1, 11))
    latest = batches(rig)[-1]
    assert latest.source_ids == [11] and latest.requests == 1
    assert latest.speech_seconds == 0 and worker == ["probe", "worker"]
    assert (
        rig.app.media.cached(rig.app.store.message(KEY, 10))["transcript"] == transcript
    )
    assert not rig.app.transport.downloads and not rig.app.transport.exact_reads


async def failed_after_chunk(rig):
    multiple_packets(rig)

    async def interrupt(role, packet):
        if role == "group_briefing":
            raise ConnectionError("Authored unknown final outcome")

    rig.model_hook = interrupt
    rig.now[0] += 31
    await settle(rig)
    attempt = attempts(rig)[-1]
    assert attempt.state == "failed"
    chunks = rig.app.store.rows("SELECT id FROM group_briefing_chunks WHERE valid=1")
    assert chunks
    assert batches(rig)[-1].state == "failed"
    return attempt, [row["id"] for row in chunks]


async def test_retry_reuses_chunk_after_restart_and_never_expands_to_appended_source(
    rig,
):
    previous, chunk_ids = await failed_after_chunk(rig)
    began = batches(rig)[0].started
    await restart(rig)
    source(rig, 9, text="Appended after the failed frozen intent.")
    rig.manager.set_processing_threshold(10000)
    job = rig.app.briefings.retry(previous.id)
    retried = attempts(rig)[-1]
    assert retried.retry_of == previous.id and not retried.automatic
    assert retried.snapshot.intended_ids == previous.snapshot.intended_ids
    assert retried.snapshot.packets == previous.snapshot.packets
    assert rig.app.briefings.claim(job)
    await rig.app.briefings.run(job)
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and final.covered_ids == list(range(1, 9))
    assert set(chunk_ids) <= set(final.chunk_ids)
    assert [role for role, _ in rig.calls] == ["group_briefing"]
    assert 9 not in rig.calls[0][1]["covered_ids"]
    assert batches(rig)[-1].requests == 1
    assert batches(rig)[-1].source_ids == list(range(7, 9))
    assert rig.app.store.group_briefings.run_state(KEY).last_auto_started == began
    assert rig.app.store.group_briefings.state(KEY, DAY).uncovered_ids == [9]
    with pytest.raises(Refused):
        rig.app.briefings.retry(retried.id)


async def test_failed_final_retry_reuses_cached_image_and_local_transcript(
    rig, monkeypatch
):
    multiple_packets(rig)
    photo(rig, 9)
    spoken = speech(rig, 10)
    worker = local_worker(rig, monkeypatch)

    async def unknown(role, packet):
        if role == "group_briefing":
            raise ConnectionError("Authored unknown final after media preparation")

    rig.model_hook = unknown
    rig.now[0] += 31
    await settle(rig)
    previous = attempts(rig)[0]
    assert previous.state == "failed"
    assert worker == ["probe", "worker"]
    assert batches(rig)[0].speech_seconds == 23.5
    kept = rig.app.media.cached(spoken)["transcript"]
    rig.model_hook = None
    rig.calls.clear()
    rig.app.transport.downloads.clear()
    job = rig.app.briefings.retry(previous.id)
    assert rig.app.briefings.claim(job)
    await rig.app.briefings.run(job)
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and final.covered_ids == list(range(1, 11))
    assert [role for role, _ in rig.calls] == ["group_briefing"]
    assert batches(rig)[-1].requests == 1
    assert batches(rig)[-1].speech_seconds == 0
    assert worker == ["probe", "worker"]
    assert rig.app.media.cached(spoken)["transcript"] == kept
    assert not rig.app.transport.downloads and not rig.app.transport.exact_reads


@pytest.mark.parametrize("disabled", ["account", "group", "access", "budget"])
async def test_failed_retry_requires_current_switches_access_and_budget(rig, disabled):
    previous, _ = await failed_after_chunk(rig)
    rig.model_hook = None
    rig.calls.clear()
    old_batches = len(batches(rig))
    if disabled == "account":
        rig.manager.set_processing(1, False)
    elif disabled == "group":
        rig.app.set_group_processing(KEY, False)
    elif disabled == "access":
        rig.app.store.groups.observe(KEY, access="lost", reason="authored_loss")
    else:
        rig.manager.registry.set_budget(0, family="group_briefing")
    with pytest.raises(Refused):
        rig.app.briefings.retry(previous.id)
    assert len(batches(rig)) == old_batches
    assert rig.calls == [] and not rig.app.transport.exact_reads


async def test_retry_uses_30_day_cache_boundary_without_automatic_seven_day_backfill(
    rig,
):
    previous, _ = await failed_after_chunk(rig)
    rig.model_hook = None
    rig.calls.clear()
    rig.now[0] = NOW + WINDOW + 3600
    assert rig.app.briefings.next_job() is None
    job = rig.app.briefings.retry(previous.id)
    assert rig.app.briefings.claim(job)
    await rig.app.briefings.run(job)
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and final.covered_ids == list(range(1, 9))
    assert [role for role, _ in rig.calls] == ["group_briefing"]
    assert not rig.app.transport.exact_reads
    with pytest.raises(Refused):
        rig.app.briefings.retry("unavailable-authored-attempt")


async def test_expired_retry_refuses_without_creating_batch_or_fetching(rig):
    previous, _ = await failed_after_chunk(rig)
    rig.now[0] = NOW + RETENTION + 1
    before = len(batches(rig))
    rig.calls.clear()
    with pytest.raises(Refused):
        rig.app.briefings.retry(previous.id)
    assert len(batches(rig)) == before
    assert rig.calls == [] and not rig.app.transport.exact_reads


async def test_guidance_change_keeps_native_retry_frozen_but_recomputes_old_chunks(rig):
    previous, old_chunks = await failed_after_chunk(rig)
    rig.model_hook = None
    rig.calls.clear()
    guidance = rig.manager.registry.guidance()
    values = dict(guidance["fields"])
    values["group_briefing"] += "\nUse the newly authored topic guidance."
    rig.manager.registry.set_guidance(values)
    job = rig.app.briefings.retry(previous.id)
    retried = attempts(rig)[-1]
    assert retried.snapshot.intended_ids == previous.snapshot.intended_ids
    assert retried.snapshot.guidance_hash != previous.snapshot.guidance_hash
    assert rig.app.briefings.claim(job)
    await rig.app.briefings.run(job)
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and not set(old_chunks) & set(final.chunk_ids)
    assert [role for role, _ in rig.calls] == [
        "group_briefing_chunk",
        "group_briefing",
    ]


async def test_current_route_never_reuses_chunk_from_an_older_exact_route(rig):
    multiple_packets(rig)
    rig.now[0] += 31
    assert rig.app.briefings.claim(rig.app.briefings.next_job())
    attempt = attempts(rig)[0]
    storage = rig.app.store.group_briefings
    historical = storage.refresh_guidance(
        attempt.id,
        guidance_hash=attempt.snapshot.guidance_hash,
        route="authored/older-model@older-exact-provider",
    )
    targets = historical.snapshot.packets[0]["covered_ids"]
    old_chunk = storage.store_chunk(
        historical.id,
        targets,
        dict(
            outcome="briefing",
            topics=[dict(text="Historical authored notes.", citations=targets)],
        ),
    )
    storage.fail(historical.id, "Historical authored failed final")
    storage.finish_batch(historical.batch_id, state="failed")
    source(rig, 9, text="New authored source changes the native intent.")
    rig.now[0] += INTERVAL + 31
    await settle(rig)
    final = storage.state(KEY, DAY).final
    assert final and final.covered_ids == list(range(1, 10))
    assert old_chunk.id not in final.chunk_ids
    assert final.snapshot.route != old_chunk.snapshot.route
    assert final.snapshot.guidance_hash == old_chunk.snapshot.guidance_hash
    assert [role for role, _ in rig.calls] == [
        "group_briefing_chunk",
        "group_briefing",
    ]
    assert batches(rig)[-1].source_ids == list(range(1, 10))


async def test_source_append_during_frozen_attempt_stays_uncovered_until_later_batch(
    rig,
):
    source(rig, 1)
    rig.now[0] += 31
    prospective = rig.app.briefings.next_job()
    assert rig.app.briefings.claim(prospective)
    attempt = attempts(rig)[0]
    began = batches(rig)[0].started
    source(rig, 2, text="Later authored original.")
    assert rig.app.store.group_briefings.current(attempt)
    assert rig.app.briefings.next_job() is None
    rig.now[0] += 31
    # The claimed prepare is already owned, so resume that exact claim.
    await rig.app.briefings.run(prospective)
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and final.covered_ids == [1]
    assert rig.app.store.group_briefings.state(KEY, DAY).uncovered_ids == [2]
    assert rig.app.store.group_briefings.run_state(KEY).last_auto_started == began
    rig.now[0] = began + INTERVAL
    rig.calls.clear()
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and final.covered_ids == [1, 2]
    assert batches(rig)[-1].source_ids == [2]


@pytest.mark.parametrize("change", ["edit", "delete", "access"])
async def test_material_change_during_final_rejects_late_publication(rig, change):
    original = source(rig, 1)
    rig.now[0] += 31
    await step(rig, expected="prepare")
    started, release = asyncio.Event(), asyncio.Event()

    async def hold(role, packet):
        if role == "group_briefing":
            started.set()
            await release.wait()

    rig.model_hook = hold
    job = rig.app.briefings.next_job()
    assert rig.app.briefings.claim(job)
    running = asyncio.create_task(rig.app.briefings.run(job))
    await asyncio.wait_for(started.wait(), 2)
    batch = batches(rig)[0]
    try:
        if change in {"edit", "delete"}:
            source(
                rig,
                1,
                revision=original.revision + 1,
                text="Materially changed authored text.",
                deleted=change == "delete",
            )
        else:
            rig.app.store.groups.observe(KEY, access="lost", reason="authored_loss")
    finally:
        release.set()
        await running
    assert rig.app.store.group_briefings.state(KEY, DAY).final is None
    assert not rig.app.store.rows("SELECT id FROM group_briefing_results")
    assert attempts(rig)[0].state == "superseded"
    after = rig.app.store.group_briefings.batch(batch.id)
    assert after.requests == 1 and after.state != "active"
    assert (
        rig.app.store.group_briefings.run_state(KEY).last_auto_started == batch.started
    )
    assert not rig.app.transport.exact_reads


async def test_editing_later_chunk_keeps_earlier_successful_chunk_reusable(rig):
    multiple_packets(rig)
    rig.now[0] += 31
    await step(rig, expected="prepare")
    await step(rig, expected="chunk")
    first = rig.app.store.rows("SELECT id FROM group_briefing_chunks WHERE valid=1")
    assert len(first) == 1
    attempt = attempts(rig)[0]
    # The last source is outside the first packet's target and Supporting Context.
    original = rig.app.store.message(KEY, 8)
    source(rig, 8, revision=2, text="A changed later source. " + "B" * 3480)
    assert attempts(rig)[0].state == "superseded"
    assert rig.app.store.rows(
        "SELECT valid FROM group_briefing_chunks WHERE id=?", (first[0]["id"],)
    ) == [dict(valid=1)]
    assert not rig.app.briefings.next_job()
    rig.now[0] += INTERVAL + 31
    rig.calls.clear()
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and first[0]["id"] in final.chunk_ids
    assert len([role for role, _ in rig.calls if role == "group_briefing_chunk"]) == 0
    assert final.snapshot.guidance_hash == attempt.snapshot.guidance_hash
    assert original.revision != rig.app.store.message(KEY, 8).revision


async def test_multi_day_updates_share_one_durable_batch_and_newest_day_is_first(
    tmp_path,
):
    rig = create(tmp_path, first_enabled=NOW - 2 * 86400)
    try:
        for day in range(3):
            rig.now[0] = NOW - (2 - day) * 86400
            source(rig, day + 1)
        rig.now[0] += 31
        await settle(rig)
        assert [packet["day"] for role, packet in rig.calls] == [
            "2026-10-11",
            "2026-10-10",
            "2026-10-09",
        ]
        saved = batches(rig)
        assert len(saved) == 1 and saved[0].requests == 3
        assert saved[0].source_ids == [1, 2, 3] and saved[0].speech_seconds == 0
        assert len({value.batch_id for value in attempts(rig)}) == 1
        assert (
            rig.app.store.group_briefings.run_state(KEY).last_auto_started
            == saved[0].started
        )
        await restart(rig)
        assert batches(rig) == saved and rig.app.briefings.next_job() is None
        assert not rig.app.transport.exact_reads
    finally:
        await rig.manager.close()


async def test_auto_expiry_closes_orphan_without_erasing_old_batch_or_replaying_it(
    rig,
):
    source(rig, 1)
    rig.now[0] += 31
    await step(rig, expected="prepare")
    old = batches(rig)[0]
    rig.app.set_group_processing(KEY, False)
    rig.now[0] = NOW + WINDOW + 3600
    source(rig, 2, text="Current day after a long pause.")
    rig.app.set_group_processing(KEY, True)
    rig.now[0] += 31
    await settle(rig)
    saved = batches(rig)
    assert len(saved) == 2
    assert saved[0].id == old.id and saved[0].state == "interrupted"
    assert saved[0].source_ids == [1] and saved[0].requests == 0
    assert saved[1].source_ids == [2] and saved[1].requests == 1
    assert [packet["covered_ids"] for role, packet in rig.calls] == [[2]]
    assert rig.app.store.group_briefings.state(KEY, DAY).final is None
    assert not rig.app.transport.exact_reads


async def test_unchanged_unknown_failure_does_not_resample_after_restart_or_guidance(
    rig,
):
    previous, _ = await failed_after_chunk(rig)
    counted = rig.manager.registry.budget(
        clock=rig.app.store.clock, family="group_briefing"
    )["spent"]
    assert counted > 0
    await restart(rig)
    rig.now[0] += INTERVAL + 31
    assert rig.app.briefings.next_job() is None
    current = rig.manager.registry.guidance()
    fields = dict(current["fields"])
    fields["group_briefing"] += "\nNew authored instructions."
    rig.manager.registry.set_guidance(fields)
    assert rig.app.briefings.next_job() is None
    assert len(attempts(rig)) == 1 and attempts(rig)[0].id == previous.id
    assert len(batches(rig)) == 1 and rig.calls == []
    assert (
        rig.manager.registry.budget(clock=rig.app.store.clock, family="group_briefing")[
            "spent"
        ]
        == counted
    )


async def test_beyond_cap_append_cannot_start_unchanged_failed_prefix_or_clock(rig):
    for identity in range(1, 201):
        source(rig, identity)

    async def unknown(role, packet):
        if role == "group_briefing":
            raise ConnectionError("Authored uncertain provider outcome")

    rig.model_hook = unknown
    rig.now[0] += 31
    await settle(rig)
    previous = attempts(rig)[0]
    assert previous.state == "failed"
    before = batches(rig)[0]
    source(rig, 201, text="An append outside the same oldest bounded prefix.")
    rig.now[0] += INTERVAL + 31
    rig.calls.clear()
    assert rig.app.briefings.next_job() is None
    assert batches(rig) == [before]
    assert (
        rig.app.store.group_briefings.run_state(KEY).last_auto_started == before.started
    )
    assert rig.calls == [] and len(attempts(rig)) == 1


async def test_restart_interrupts_owned_attempt_and_retry_keeps_successful_chunk(rig):
    multiple_packets(rig)
    rig.now[0] += 31
    await step(rig, expected="prepare")
    await step(rig, expected="chunk")
    old = attempts(rig)[0]
    chunks = rig.app.store.rows("SELECT id FROM group_briefing_chunks WHERE valid=1")
    batch = batches(rig)[0]
    assert batch.requests == 1
    await restart(rig)
    assert attempts(rig)[0].state == "interrupted"
    assert batches(rig)[0].requests == 1
    assert rig.app.briefings.next_job() is None
    job = rig.app.briefings.retry(old.id)
    assert rig.app.briefings.claim(job)
    await rig.app.briefings.run(job)
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and {row["id"] for row in chunks} <= set(final.chunk_ids)
    assert [role for role, _ in rig.calls] == ["group_briefing"]
    assert batches(rig)[-1].requests == 1
    assert batches(rig)[-1].source_ids == [7, 8]
    assert (
        rig.app.store.group_briefings.run_state(KEY).last_auto_started == batch.started
    )


async def test_retry_reuses_prior_final_raw_tail_before_spending_200_source_cap(rig):
    # V2 topic/participant metadata adds real bytes. Keep a bounded prior day
    # tail while still exercising the entire 200-new-source update allowance.
    prior_ids, fresh_ids = range(1, 151), range(151, 351)
    for identity in prior_ids:
        source(rig, identity)
    rig.now[0] += 31
    await settle(rig)
    previous_final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert previous_final and previous_final.covered_ids == list(prior_ids)
    assert previous_final.chunk_ids
    raw_tail = set(previous_final.covered_ids) - {
        identity
        for value in rig.app.briefings._chunks(previous_final.chunk_ids)
        for identity in value.covered_ids
    }
    assert raw_tail, "The prior validated final also covers unchunked native originals"
    rig.now[0] += INTERVAL + 31
    for identity in fresh_ids:
        source(rig, identity)

    async def unknown_first_chunk(role, packet):
        if role == "group_briefing_chunk":
            raise ConnectionError("Authored unknown first new chunk")

    rig.model_hook = unknown_first_chunk
    rig.now[0] += 31
    await settle(rig)
    failed = attempts(rig)[-1]
    assert failed.state == "failed"
    assert failed.snapshot.intended_ids == list(range(1, 351))
    rig.model_hook = None
    rig.calls.clear()
    prepared_targets = []
    original_enrich = rig.app.media.enrich

    async def enrich(packet, key, **options):
        prepared_targets.append(set(packet["covered_ids"]))
        return await original_enrich(packet, key, **options)

    rig.app.media.enrich = enrich
    job = rig.app.briefings.retry(failed.id)
    assert rig.app.briefings.claim(job)
    await rig.app.briefings.run(job)
    await settle(rig)
    final = rig.app.store.group_briefings.state(KEY, DAY).final
    assert final and set(fresh_ids) <= set(final.covered_ids)
    assert set(final.covered_ids) <= set(range(1, 351))
    # Native dependency metadata and notes for all 350 cannot fit one 32 KiB
    # request. Keep complete fresh coverage, with explicit older omissions,
    # rather than weakening the adopted whole-request bound.
    missing_earlier = set(prior_ids) - set(final.covered_ids)
    assert missing_earlier == {value.message_id for value in final.omissions}
    assert all(value.reason == "final request bound" for value in final.omissions)
    assert rig.app.store.group_briefings.current(previous_final)
    final_batch = batches(rig)[-1]
    assert final_batch.source_ids == list(fresh_ids)
    assert final_batch.requests <= 5 and final_batch.speech_seconds == 0
    assert all(
        set(message["id"] for message in packet["messages"]).isdisjoint(raw_tail)
        for role, packet in rig.calls
    )
    assert prepared_targets and all(
        targets.isdisjoint(raw_tail) for targets in prepared_targets
    )
    assert not rig.app.transport.exact_reads
    # Earlier still-valid output plus this fresh final completes the day even
    # when the newest final alone omits an older reusable chunk for size.
    old_batches = batches(rig)
    old_attempts = attempts(rig)
    old_jobs = rig.app.store.rows("SELECT id,state FROM jobs ORDER BY rowid")
    clock = rig.app.store.group_briefings.run_state(KEY).last_auto_started
    call_count = len(rig.calls)
    rig.now[0] += INTERVAL + 31
    assert rig.app.briefings.next_job() is None
    assert batches(rig) == old_batches and attempts(rig) == old_attempts
    assert rig.app.store.rows("SELECT id,state FROM jobs ORDER BY rowid") == old_jobs
    assert len(rig.calls) == call_count
    assert rig.app.store.group_briefings.run_state(KEY).last_auto_started == clock


async def test_newer_days_consume_one_shared_200_source_allowance(tmp_path):
    rig = create(tmp_path, first_enabled=NOW - 86400)
    try:
        for identity in range(1, 121):
            source(rig, identity)
        rig.now[0] = NOW
        for identity in range(121, 241):
            source(rig, identity)
        rig.now[0] += 31
        await settle(rig)
        saved = batches(rig)
        assert len(saved) == 1 and saved[0].requests <= 5
        assert saved[0].source_ids == list(range(1, 81)) + list(range(121, 241))
        assert saved[0].speech_seconds == 0
        today = rig.app.store.group_briefings.state(KEY, DAY)
        yesterday = rig.app.store.group_briefings.state(KEY, "2026-10-10")
        assert today.final and today.covered_ids == list(range(121, 241))
        assert yesterday.final and yesterday.covered_ids == list(range(1, 81))
        assert {value.message_id for value in yesterday.omissions} == set(
            range(81, 121)
        )
        days = [packet["day"] for role, packet in rig.calls]
        assert days == sorted(days, reverse=True)
        assert len({value.batch_id for value in attempts(rig)}) == 1
    finally:
        await rig.manager.close()


async def test_multi_day_speech_uses_same_measured_1200_second_allowance(
    tmp_path, monkeypatch
):
    rig = create(tmp_path, first_enabled=NOW - 86400)
    try:
        speech(rig, 1)
        rig.now[0] = NOW
        speech(rig, 2)
        speech(rig, 3)
        worker = local_worker(rig, monkeypatch, duration=600)
        rig.now[0] += 31
        await settle(rig)
        saved = batches(rig)
        assert len(saved) == 1 and saved[0].speech_seconds == 1200
        assert worker.count("worker") == 2
        today = rig.app.store.group_briefings.state(KEY, DAY)
        yesterday = rig.app.store.group_briefings.state(KEY, "2026-10-10")
        assert today.final and today.covered_ids == [2, 3]
        assert yesterday.final is None
        assert (
            rig.app.media.cached(rig.app.store.message(KEY, 1)).get("transcript")
            is None
        )
        assert [packet["day"] for role, packet in rig.calls] == [DAY]
        assert len({value.batch_id for value in attempts(rig)}) == 1
        assert (
            rig.app.store.group_briefings.run_state(KEY).last_auto_started
            == saved[0].started
        )
    finally:
        await rig.manager.close()
