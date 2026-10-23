"""Authored group media, fake probe/worker and temporary caches only (GB4)."""

import asyncio
import copy
import io
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.application.group_context import current
from enloq.application.group_context import packet as group_packet
from enloq.application.media import AUDIO_BYTES, IMAGE_BYTES, Media
from enloq.config import private_write
from enloq.domain.models import (
    Chat,
    MediaInfo,
    Message,
    ProcessingPaused,
    Refused,
    Unavailable,
)
from enloq.inference import audio
from enloq.inference.audio import WHISPER, LocalWhisper
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import RETENTION, Store
from enloq.transport.fake import FakeTransport
from PIL import Image

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC).timestamp()


def png():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(output, "PNG")
    return output.getvalue()


def rig(tmp_path, *, peer="channel:42", account=1):
    now = [NOW]
    registry = AccountRegistry(tmp_path)
    registry.register(account, f"authored-{account}.session")
    store = Store(
        tmp_path / str(account) / "rewriting.sqlite3",
        account_id=account,
        clock=lambda: now[0],
        shared_path=registry.path,
    )
    key = f"{account}/{peer}"
    store.put_chat(Chat(account_id=account, peer=peer, title="Authored", kind="group"))
    store.groups.observe(key, access="available", reason="authored_access")
    store.groups.set_processing_enabled(key, True)
    transport = FakeTransport()
    transport.account_id = account
    media = Media(store, transport)
    controls = dict(budget=True, threshold=True, valid=True)

    def guard():
        if not registry.processing(account)["ai_enabled"]:
            raise ProcessingPaused("Account AI is Off")
        if not store.groups.get(key).processing_enabled:
            raise ProcessingPaused("Group AI is Off")
        if not controls["budget"]:
            raise Refused("Group briefing budget reached")
        if not controls["threshold"]:
            raise Refused("Processing threshold no longer met")
        state, intake = store.groups.get(key), store.groups.intake(key)
        if not state.supported or state.access != "available" or intake.replay_pending:
            raise Refused("Group unavailable for preparation")

    return SimpleNamespace(
        registry=registry,
        store=store,
        transport=transport,
        media=media,
        controls=controls,
        key=key,
        now=now,
        guard=guard,
        valid=lambda: controls["valid"],
    )


def source(r, identity, **values):
    message = Message(
        account_id=r.store.account_id,
        peer=r.key.split("/", 1)[1],
        message_id=identity,
        revision=0,
        date=r.now[0],
        author="Authored participant",
        author_peer="user:7",
        text="",
    ).model_copy(update=values)
    r.store.apply_message(message, group_epoch=r.store.groups.intake(r.key).epoch)
    # Sync would clear this after its durable source-change effects. The fixture
    # has no pipeline or notifications and records only the resulting proof.
    with r.store.transaction() as db:
        db.execute("DELETE FROM cursors WHERE key=?", ("group-effects:" + message.key,))
    return r.store.message(r.key, identity)


def projection(message):
    return dict(
        id=message.message_id,
        revision=message.revision,
        text=message.text,
        media=message.media,
        reply_to=message.reply_to,
    )


def packet(r, targets, supporting=()):
    return dict(
        messages=[projection(m) for m in targets],
        context=[projection(m) for m in supporting],
        inputs={m.key: m.revision for m in tuple(targets) + tuple(supporting)},
        direct_reply=None,
    )


def derivative(r, message, **values):
    with r.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (message.key, message.revision, json.dumps(values)),
        )


def local_worker(
    r, tmp_path, monkeypatch, *, duration="23.5", probe_hook=None, worker_hook=None
):
    root = tmp_path / "authored-whisper"
    private_write(
        root / ".local/models/whisper/provenance.json",
        json.dumps(dict(model=WHISPER, revision="authored-model-revision")),
    )
    binary = root / "audio/.venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.touch()
    calls = []

    async def fake_process(argv, **kwargs):
        stage = "probe" if argv[0].endswith("ffprobe") else "worker"
        calls.append(stage)
        if stage == "probe":
            if probe_hook:
                await probe_hook()
            return json.dumps(dict(format=dict(duration=duration))).encode(), b""
        if worker_hook:
            await worker_hook()
        return b'{"text":"Authored local transcript","language":"en"}', b""

    monkeypatch.setattr(audio, "bounded_process", fake_process)
    r.media.transcriber = LocalWhisper(root)
    r.media.transcription_lock = r.media.transcriber.lock
    return calls


def voice(r, identity=1, *, kind="audio", metadata_duration=1):
    message = source(
        r,
        identity,
        media=kind,
        media_info=MediaInfo(voice=kind == "audio", duration=metadata_duration),
        attachment_id=f"authored-speech-{identity}",
    )
    r.transport.media[message.peer, identity] = b"OggS-authored-audio-fixture"
    return message


def image(r, identity=1, **values):
    message = source(
        r, identity, media="image", attachment_id=f"image-{identity}", **values
    )
    r.transport.media[message.peer, identity] = png()
    return message


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_cached_derivatives_are_free_and_need_no_fresh_stage_admission(
    tmp_path, peer
):
    r = rig(tmp_path, peer=peer)
    photo, speech = image(r), voice(r, 2)
    transcript = dict(text="Authored cached speech", language="en")
    derivative(r, photo, image_context="Authored cached image meaning")
    derivative(r, speech, transcript=transcript)
    r.registry.set_processing(1, False)
    r.store.groups.set_processing_enabled(r.key, False)
    r.controls.update(budget=False, threshold=False)
    r.store.groups.begin_replay(r.key, epoch=r.store.groups.intake(r.key).epoch)
    charges = []
    value = await r.media.enrich(
        packet(r, [photo, speech]),
        r.key,
        guard=r.guard,
        valid=r.valid,
        on_speech_start=charges.append,
    )
    assert value["messages"][0]["image_context"] == "Authored cached image meaning"
    assert value["messages"][1]["transcript"] == transcript
    assert not value["nothing"] and value["images"] == []
    assert not charges and not r.transport.downloads and not r.transport.exact_reads
    assert not r.store.rows("SELECT * FROM jobs")


async def test_cached_transcript_is_free_despite_untrusted_duration_metadata(tmp_path):
    r = rig(tmp_path)
    speech = voice(r, metadata_duration=601)
    derivative(r, speech, transcript=dict(text="Previously validated local speech"))
    value = await r.media.enrich(packet(r, [speech]), r.key, valid=r.valid)
    assert (
        value["messages"][0]["transcript"]["text"]
        == "Previously validated local speech"
    )
    assert not value["unanalysed"] and not r.transport.downloads


@pytest.mark.parametrize("kind", ["image", "audio", "video_note"])
async def test_fresh_group_media_requires_call_scoped_admission(tmp_path, kind):
    r = rig(tmp_path)
    message = image(r) if kind == "image" else voice(r, kind=kind)
    with pytest.raises(Refused, match="stage admission"):
        await r.media.enrich(packet(r, [message]), r.key)
    assert not r.transport.downloads and not r.store.rows("SELECT * FROM jobs")


async def test_fresh_group_speech_requires_duration_callback_before_acquisition(
    tmp_path,
):
    r = rig(tmp_path)
    message = voice(r)
    with pytest.raises(Refused, match="duration admission"):
        await r.media.enrich(packet(r, [message]), r.key, guard=r.guard)
    assert not r.transport.downloads and not r.store.rows("SELECT * FROM jobs")


async def test_group_preparation_never_exact_fetches_missing_replied_media(tmp_path):
    r = rig(tmp_path)
    message = source(r, 1, text="Authored target")
    value = packet(r, [message])
    value["context"] = [dict(id=999, revision=0, text="", media="image")]
    value["direct_reply"] = 999
    with pytest.raises(Refused, match="source changed"):
        await r.media.enrich(value, r.key, guard=r.guard, valid=r.valid)
    assert not r.transport.exact_reads and not r.transport.downloads


async def test_strict_cached_revision_cannot_supply_an_old_meaning(tmp_path):
    r = rig(tmp_path)
    first = image(r)
    derivative(r, first, image_context="Previous revision only")
    edited = source(
        r, 1, media="image", revision=1, attachment_id="new-image", text="Edited"
    )
    derivative(r, first, image_context="Old surviving diagnostic derivative")
    r.transport.media[edited.peer, 1] = png()
    value = await r.media.enrich(
        packet(r, [edited]), r.key, guard=r.guard, valid=r.valid
    )
    assert len(value["images"]) == 1
    assert "image_context" not in value["messages"][0]
    assert len(r.transport.downloads) == 1


@pytest.mark.parametrize(
    "change",
    ["account_off", "group_off", "budget", "threshold", "access", "source", "valid"],
)
async def test_image_completion_rechecks_admission_and_material_validity(
    tmp_path, change
):
    r = rig(tmp_path)
    photo = image(r)
    entered, release = asyncio.Event(), asyncio.Event()
    original = r.transport.download

    async def held_download(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    r.transport.download = held_download
    task = asyncio.create_task(
        r.media.enrich(packet(r, [photo]), r.key, guard=r.guard, valid=r.valid)
    )
    await entered.wait()
    if change == "account_off":
        r.registry.set_processing(1, False)
    elif change == "group_off":
        r.store.groups.set_processing_enabled(r.key, False)
    elif change in ("budget", "threshold", "valid"):
        r.controls[change] = False
    elif change == "access":
        r.store.groups.observe(r.key, access="lost", reason="authored_loss")
    else:
        source(r, 1, media="image", revision=1, attachment_id="changed")
    release.set()
    with pytest.raises(Refused):
        await task
    assert r.media.cache.usage()["files"] == 0
    assert not list(r.media.workspace.path.glob("*.bin"))
    assert not r.store.rows("SELECT * FROM derivatives")


async def test_image_guard_is_rechecked_after_cache_lock_before_download(tmp_path):
    r = rig(tmp_path)
    photo = image(r)
    lock = r.media.cache.locks.setdefault(r.media.cache.entry(photo), asyncio.Lock())
    await lock.acquire()
    task = asyncio.create_task(
        r.media.enrich(packet(r, [photo]), r.key, guard=r.guard, valid=r.valid)
    )
    await asyncio.sleep(0)
    r.controls["budget"] = False
    lock.release()
    with pytest.raises(Refused, match="budget"):
        await task
    assert not r.transport.downloads


async def test_no_second_image_stage_after_switch_turns_off(tmp_path):
    r = rig(tmp_path)
    photos = [image(r, i) for i in (1, 2)]
    original = r.media.image

    async def turn_off_after_first(message, **kwargs):
        value = await original(message, **kwargs)
        r.registry.set_processing(1, False)
        return value

    r.media.image = turn_off_after_first
    with pytest.raises(ProcessingPaused):
        await r.media.enrich(packet(r, photos), r.key, guard=r.guard, valid=r.valid)
    assert [d[1] for d in r.transport.downloads] == [1]


async def test_cached_image_plus_four_fresh_images_and_explicit_fifth_omission(
    tmp_path,
):
    r = rig(tmp_path)
    photos = [image(r, i) for i in range(1, 7)]
    derivative(r, photos[0], image_context="Authored previously understood image")
    value = await r.media.enrich(packet(r, photos), r.key, guard=r.guard, valid=r.valid)
    assert len(value["images"]) == 4 and [d[1] for d in r.transport.downloads] == [
        2,
        3,
        4,
        5,
    ]
    assert value["unanalysed"] == [
        dict(id=6, kind="image", reason="more than four images")
    ]
    assert not value["nothing"]


@pytest.mark.parametrize(
    "rejection", ["declared_size", "actual_size", "unsupported", "decode"]
)
async def test_rejected_image_is_an_explicit_omission_not_substantive_input(
    tmp_path, rejection
):
    r = rig(tmp_path)
    photo = image(
        r,
        media_info=MediaInfo(size=IMAGE_BYTES + 1)
        if rejection == "declared_size"
        else None,
    )
    if rejection == "actual_size":
        r.transport.media[photo.peer, 1] = b"x" * (IMAGE_BYTES + 1)
    elif rejection == "unsupported":
        r.transport.media[photo.peer, 1] = b"not an authored image"
    elif rejection == "decode":
        r.transport.media[photo.peer, 1] = png()[:25]
    value = await r.media.enrich(
        packet(r, [photo]), r.key, guard=r.guard, valid=r.valid
    )
    assert value["nothing"] and not value["images"]
    assert value["unanalysed"] == [
        dict(id=1, kind="image", reason="exceeds image limits or cannot be decoded")
    ]
    assert r.media.cache.usage()["files"] == 0
    if rejection == "declared_size":
        assert not r.transport.downloads


@pytest.mark.parametrize("kind", ["audio", "video_note"])
async def test_measured_speech_duration_is_charged_before_worker_and_cached_free(
    tmp_path, monkeypatch, kind
):
    r = rig(tmp_path)
    speech = voice(r, kind=kind, metadata_duration=1)
    charges = []

    async def before_worker():
        assert charges == [23.5]
        assert r.media.transcription_lock.locked()
        assert r.store.rows("SELECT state FROM jobs") == [dict(state="running")]

    calls = local_worker(r, tmp_path, monkeypatch, worker_hook=before_worker)
    value = await r.media.enrich(
        packet(r, [speech]),
        r.key,
        guard=r.guard,
        valid=r.valid,
        on_speech_start=charges.append,
    )
    transcript = value["messages"][0]["transcript"]
    assert transcript["duration_seconds"] == 23.5 and transcript["model"] == WHISPER
    assert calls == ["probe", "worker"] and not value["images"]
    assert "OggS" not in json.dumps(value)
    assert r.store.rows("SELECT state FROM jobs") == [dict(state="done")]
    r.registry.set_processing(1, False)
    assert (
        await r.media.transcript(
            speech, guard=r.guard, valid=r.valid, on_speech_start=charges.append
        )
        == transcript
    )
    assert charges == [23.5] and calls == ["probe", "worker"]


@pytest.mark.parametrize(
    "change", ["account_off", "group_off", "budget", "threshold", "valid"]
)
async def test_speech_waiting_for_shared_capacity_creates_no_attempt_or_charge(
    tmp_path, monkeypatch, change
):
    r = rig(tmp_path)
    speech = voice(r)
    calls = local_worker(r, tmp_path, monkeypatch)
    charges = []
    await r.media.transcription_lock.acquire()
    task = asyncio.create_task(
        r.media.transcript(
            speech, guard=r.guard, valid=r.valid, on_speech_start=charges.append
        )
    )
    await asyncio.sleep(0)
    if change == "account_off":
        r.registry.set_processing(1, False)
    elif change == "group_off":
        r.store.groups.set_processing_enabled(r.key, False)
    else:
        r.controls[change] = False
    r.media.transcription_lock.release()
    with pytest.raises(Refused):
        await task
    assert not calls and not charges and not r.transport.downloads
    assert not r.store.rows("SELECT * FROM jobs")


async def test_last_stage_admission_is_after_probe_before_duration_charge(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    speech = voice(r)
    charges = []

    async def after_probe():
        r.controls["threshold"] = False

    calls = local_worker(r, tmp_path, monkeypatch, probe_hook=after_probe)
    with pytest.raises(Refused, match="threshold"):
        await r.media.transcript(
            speech, guard=r.guard, valid=r.valid, on_speech_start=charges.append
        )
    assert calls == ["probe"] and not charges and not r.store.rows("SELECT * FROM jobs")


async def test_durable_duration_refusal_creates_no_attempt_or_worker(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    speech = voice(r)
    calls = local_worker(r, tmp_path, monkeypatch)
    observed = []

    def exhausted(duration):
        observed.append(duration)
        raise Refused("Fresh speech work limit reached")

    with pytest.raises(Refused, match="speech work limit"):
        await r.media.transcript(
            speech, guard=r.guard, valid=r.valid, on_speech_start=exhausted
        )
    assert observed == [23.5] and calls == ["probe"]
    assert not r.store.rows("SELECT * FROM jobs") and not r.store.rows(
        "SELECT * FROM derivatives"
    )


async def test_callback_refusal_in_supporting_speech_holds_preparation(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    target = source(r, 1, text="Authored substantive target")
    supporting = voice(r, 2)
    calls = local_worker(r, tmp_path, monkeypatch)

    def hold(duration):
        raise Refused("Fresh speech work limit reached")

    with pytest.raises(Refused, match="speech work limit"):
        await r.media.enrich(
            packet(r, [target], [supporting]),
            r.key,
            guard=r.guard,
            valid=r.valid,
            on_speech_start=hold,
        )
    assert calls == ["probe"] and not r.store.rows("SELECT * FROM jobs")


@pytest.mark.parametrize(
    "duration",
    [None, True, False, "NaN", "Infinity", "-1", "0", "600.001", "unmeasured", {}, []],
)
async def test_invalid_probe_duration_never_charges_or_starts_worker(
    tmp_path, monkeypatch, duration
):
    r = rig(tmp_path)
    speech = voice(r)
    calls = local_worker(r, tmp_path, monkeypatch, duration=duration)
    charges = []
    with pytest.raises(Refused):
        await r.media.transcript(
            speech, guard=r.guard, valid=r.valid, on_speech_start=charges.append
        )
    assert calls == ["probe"] and not charges
    assert not r.store.rows("SELECT * FROM jobs")


@pytest.mark.parametrize("switch", ["account", "group"])
async def test_started_speech_may_finish_off_but_next_fresh_stage_waits(
    tmp_path, monkeypatch, switch
):
    r = rig(tmp_path)
    first, second = voice(r), voice(r, 2)
    entered, release = asyncio.Event(), asyncio.Event()
    charges = []

    async def hold_worker():
        entered.set()
        await release.wait()

    calls = local_worker(r, tmp_path, monkeypatch, worker_hook=hold_worker)
    task = asyncio.create_task(
        r.media.enrich(
            packet(r, [first, second]),
            r.key,
            guard=r.guard,
            valid=r.valid,
            on_speech_start=charges.append,
        )
    )
    await entered.wait()
    if switch == "account":
        r.registry.set_processing(1, False)
    else:
        r.store.groups.set_processing_enabled(r.key, False)
    release.set()
    with pytest.raises(ProcessingPaused):
        await task
    assert r.media.cached(first)["transcript"]["text"] == "Authored local transcript"
    assert r.media.cached(second) == {} and charges == [23.5]
    assert calls == ["probe", "worker"] and [d[1] for d in r.transport.downloads] == [1]
    assert r.store.rows("SELECT state FROM jobs") == [dict(state="done")]


@pytest.mark.parametrize(
    "change", ["edit", "delete", "loss", "gap", "expiry", "valid", "authorization"]
)
async def test_started_speech_charge_survives_material_invalidation_without_cache_write(
    tmp_path, monkeypatch, change
):
    r = rig(tmp_path)
    speech = voice(r)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold_worker():
        entered.set()
        await release.wait()

    calls = local_worker(r, tmp_path, monkeypatch, worker_hook=hold_worker)
    charges = []
    task = asyncio.create_task(
        r.media.transcript(
            speech, guard=r.guard, valid=r.valid, on_speech_start=charges.append
        )
    )
    await entered.wait()
    if change in ("edit", "delete"):
        source(
            r,
            1,
            media="audio",
            attachment_id="changed",
            revision=1,
            deleted=change == "delete",
        )
    elif change == "loss":
        r.store.groups.observe(r.key, access="lost", reason="authored_loss")
    elif change == "gap":
        r.store.groups.mark_gap(r.key)
    elif change == "expiry":
        r.now[0] += RETENTION + 1
    elif change == "valid":
        r.controls["valid"] = False
    else:
        r.media.authorized = lambda: False
    release.set()
    with pytest.raises(Refused, match="transcription failed"):
        await task
    assert charges == [23.5] and calls == ["probe", "worker"]
    assert not r.store.rows("SELECT * FROM derivatives")
    assert r.store.rows("SELECT state FROM jobs") == [dict(state="failed")]


async def test_worker_failure_and_deliberate_retry_each_charge_once(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    speech = voice(r)
    charges = []

    async def fail_worker():
        raise Unavailable("Authored worker failed")

    calls = local_worker(r, tmp_path, monkeypatch, worker_hook=fail_worker)
    for retry in (False, False, True):
        with pytest.raises(Refused):
            await r.media.transcript(
                speech,
                retry=retry,
                guard=r.guard,
                valid=r.valid,
                on_speech_start=charges.append,
            )
    assert charges == [23.5, 23.5] and calls == ["probe", "worker", "probe", "worker"]
    assert r.store.rows("SELECT state FROM jobs") == [
        dict(state="failed"),
        dict(state="failed"),
    ]


async def test_cancelled_started_worker_retains_charge_and_requires_explicit_retry(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    speech = voice(r)
    entered = asyncio.Event()

    async def hold_worker():
        entered.set()
        await asyncio.Event().wait()

    calls = local_worker(r, tmp_path, monkeypatch, worker_hook=hold_worker)
    charges = []
    task = asyncio.create_task(
        r.media.transcript(
            speech, guard=r.guard, valid=r.valid, on_speech_start=charges.append
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(Refused, match="explicitly retry"):
        await r.media.transcript(
            speech, guard=r.guard, valid=r.valid, on_speech_start=charges.append
        )
    assert charges == [23.5] and calls == ["probe", "worker"]
    assert r.store.rows("SELECT state FROM jobs") == [dict(state="failed")]
    assert not list(r.media.workspace.path.glob("*.bin"))


async def test_routine_replay_does_not_invalidate_a_started_transcript(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    speech = voice(r)

    async def routine_replay():
        r.store.groups.begin_replay(r.key, epoch=r.store.groups.intake(r.key).epoch)

    calls = local_worker(r, tmp_path, monkeypatch, worker_hook=routine_replay)
    result = await r.media.transcript(
        speech, guard=r.guard, valid=r.valid, on_speech_start=lambda _: None
    )
    assert result["text"] == "Authored local transcript"
    assert r.media.cached(speech)["transcript"] == result and calls == [
        "probe",
        "worker",
    ]


async def test_derivative_write_rechecks_group_proof_on_the_held_transaction(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    speech = voice(r)
    calls = local_worker(r, tmp_path, monkeypatch)
    original = r.store.transaction
    stage = dict(returned=False, changed=False)

    def valid():
        stage["returned"] = "worker" in calls
        return True

    @contextmanager
    def before_transaction():
        # The first material check after the worker marks this state. Trigger
        # native loss once immediately before that check acquires the DB.
        if stage["returned"] and not stage["changed"]:
            stage["changed"] = True
            r.store.transaction = original
            r.store.groups.observe(r.key, access="lost", reason="authored_race")
        with original() as db:
            yield db

    monkeypatch.setattr(r.store, "transaction", before_transaction)
    charges = []
    with pytest.raises(Refused):
        await r.media.transcript(
            speech, guard=r.guard, valid=valid, on_speech_start=charges.append
        )
    assert stage["changed"] and not r.store.rows("SELECT * FROM derivatives")
    assert charges == [23.5]


async def test_real_group_packet_material_callback_allows_current_transcript_write(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    speech = voice(r)
    value = group_packet(r.store, r.key, "2026-10-11", [speech])
    calls = local_worker(r, tmp_path, monkeypatch)

    def material():
        return current(r.store, value)

    prepared = await r.media.enrich(
        copy.deepcopy(value),
        r.key,
        guard=r.guard,
        valid=material,
        on_speech_start=lambda _: None,
    )
    assert calls == ["probe", "worker"] and current(r.store, prepared)
    assert prepared["transcript_inputs"] and not prepared["nothing"]


async def test_service_and_non_ai_media_never_enter_fresh_preparation(tmp_path):
    r = rig(tmp_path)
    service = source(r, 1, text="Authored membership service text", service=True)
    other = [
        source(r, i, media=kind)
        for i, kind in enumerate(
            ("video", "document", "sticker", "gif", "contact", "poll"), 2
        )
    ]
    value = await r.media.enrich(packet(r, [service] + other), r.key, valid=r.valid)
    assert value["messages"][0]["text"] == "" and value["nothing"]
    assert len(value["unanalysed"]) == 6 and not value["images"]
    assert not r.transport.downloads and not r.transport.exact_reads
    assert not r.store.rows("SELECT * FROM jobs")


@pytest.mark.parametrize("field", ["size", "duration"])
async def test_untrusted_oversized_speech_metadata_is_a_free_explicit_omission(
    tmp_path, field
):
    r = rig(tmp_path)
    message = source(
        r,
        1,
        media="audio",
        attachment_id="speech",
        media_info=MediaInfo(**{field: AUDIO_BYTES + 1 if field == "size" else 601}),
    )
    value = await r.media.enrich(packet(r, [message]), r.key, valid=r.valid)
    assert value["nothing"] and value["unanalysed"] == [
        dict(id=1, kind="audio", reason="exceeds transcription limits")
    ]
    assert not r.transport.downloads and not r.store.rows("SELECT * FROM jobs")


async def test_measured_durations_stop_at_durable_twenty_minute_allowance(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    sources = [voice(r, i, metadata_duration=1) for i in (1, 2, 3)]
    calls = local_worker(r, tmp_path, monkeypatch, duration="600")
    charged = []

    def charge(duration):
        if sum(charged) + duration > 1200:
            raise Refused("Fresh speech work limit reached")
        charged.append(duration)

    with pytest.raises(Refused, match="speech work limit"):
        await r.media.enrich(
            packet(r, sources),
            r.key,
            guard=r.guard,
            valid=r.valid,
            on_speech_start=charge,
        )
    assert charged == [600, 600]
    assert calls == ["probe", "worker", "probe", "worker", "probe"]
    assert [j["state"] for j in r.store.rows("SELECT state FROM jobs")] == [
        "done",
        "done",
    ]
    assert r.media.cached(sources[0])["transcript"]
    assert r.media.cached(sources[1])["transcript"]
    assert not r.media.cached(sources[2])
    # Reusing the completed sources consumes none of the held update's allowance.
    await r.media.enrich(
        packet(r, sources[:2]),
        r.key,
        guard=r.guard,
        valid=r.valid,
        on_speech_start=charge,
    )
    assert charged == [600, 600] and len(calls) == 5


async def test_group_and_private_speech_share_lock_without_leaking_guards(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    group_source = voice(r)
    private_key = "1/user:42"
    r.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored private", kind="private")
    )
    private_source = Message(
        account_id=1,
        peer="user:42",
        message_id=1,
        revision=0,
        date=NOW,
        media="audio",
        attachment_id="private-speech",
    )
    r.store.apply_message(private_source)
    r.transport.media[private_source.peer, 1] = b"OggS-authored-private-audio"
    entered, release = asyncio.Event(), asyncio.Event()
    admissions, charges = [], []

    async def hold_private_worker():
        if not entered.is_set():
            entered.set()
            await release.wait()

    calls = local_worker(r, tmp_path, monkeypatch, worker_hook=hold_private_worker)

    def private_admission():
        admissions.append("private")

    r.media.admit = private_admission
    private_task = asyncio.create_task(r.media.transcript(private_source))
    await entered.wait()
    group_task = asyncio.create_task(
        r.media.transcript(
            group_source,
            guard=r.guard,
            valid=r.valid,
            on_speech_start=charges.append,
        )
    )
    await asyncio.sleep(0)
    assert calls == ["probe", "worker"] and not charges
    r.store.groups.set_processing_enabled(r.key, False)
    release.set()
    private_result = await private_task
    with pytest.raises(ProcessingPaused):
        await group_task
    assert private_result["text"] == "Authored local transcript"
    assert r.store.message(private_key, 1).revision == private_source.revision
    assert r.media.admit is private_admission and admissions
    assert calls == ["probe", "worker"] and not charges
    assert [j["scope"] for j in r.store.rows("SELECT scope FROM jobs")] == [
        private_source.key
    ]


async def test_combined_image_bytes_remain_bounded_with_valid_individual_images(
    tmp_path,
):
    r = rig(tmp_path)
    photos = [image(r, i) for i in (1, 2, 3, 4)]
    content = png()
    padded = content + bytes(IMAGE_BYTES - len(content))
    for photo in photos:
        r.transport.media[photo.peer, photo.message_id] = padded
    value = await r.media.enrich(packet(r, photos), r.key, guard=r.guard, valid=r.valid)
    assert [i["message_id"] for i in value["images"]] == [1, 2, 3]
    assert value["unanalysed"] == [
        dict(id=4, kind="image", reason="combined image limit")
    ]
    assert not value["nothing"] and len(r.transport.downloads) == 4


async def test_local_whisper_callback_observes_probe_then_worker_order(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    calls = local_worker(r, tmp_path, monkeypatch, duration="12.75")
    path = tmp_path / "authored-local-audio.bin"
    path.write_bytes(b"authored-local-audio")
    observed = []

    def start(duration):
        assert calls == ["probe"]
        observed.append(duration)

    result = await r.media.transcriber.transcribe(path, on_start=start)
    assert observed == [12.75] and calls == ["probe", "worker"]
    assert result["duration_seconds"] == 12.75
    # The existing private call form remains valid without the callback.
    await r.media.transcriber.transcribe(path)
    assert calls == ["probe", "worker", "probe", "worker"]


async def test_local_model_is_never_started_for_unavailable_or_oversized_file(
    tmp_path, monkeypatch
):
    r = rig(tmp_path)
    calls = local_worker(r, tmp_path, monkeypatch)
    path = tmp_path / "authored-audio.bin"
    path.write_bytes(b"authored")
    charged = []
    r.media.transcriber.python.unlink()
    with pytest.raises(Unavailable):
        await r.media.transcriber.transcribe(path, on_start=charged.append)
    r.media.transcriber.python.touch()
    with path.open("wb") as output:
        output.truncate(AUDIO_BYTES + 1)
    with pytest.raises(Refused):
        await r.media.transcriber.transcribe(path, on_start=charged.append)
    assert not charged and not calls
