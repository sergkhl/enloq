"""Authored deletion consequence recovery and late-stage currentness (ADR 31)."""

import asyncio
import json
import sqlite3
import uuid

import pytest
from enloq.domain.models import Refused
from enloq.inference.base import EngineResponse
from enloq.inference.fake import choice
from enloq.storage.store import RETENTION
from enloq.transport.mtproto import PREPARATION

from backend.tests.test_memory import conversation as memory_conversation
from backend.tests.test_memory import fold
from backend.tests.test_memory_takeaways import retained
from backend.tests.test_reactions import snapshot
from backend.tests.test_rebuild import edit, fixture, message
from backend.tests.test_suggestion_state import OPTIONS, prepare, ready

KEY, PEER = "1/user:42", "user:42"


def rig(tmp_path):
    now = [2_000_000_000.0]
    return fixture(tmp_path, clock=lambda: now[0]), now


def pending(app):
    return app.store.rows("SELECT * FROM cursors WHERE key LIKE 'deletion-effects:%'")


async def deletion(app, identities):
    app.transport.messages = app.store.messages(KEY, limit=1000)
    return await app.deletion.submit(
        KEY,
        uuid.uuid4().hex,
        [
            {"message_id": i, "revision": app.store.message(KEY, i).revision}
            for i in identities
        ],
        browser="authored-browser",
    )


async def test_native_observed_hook_failure_replays_after_restart_without_duplicate_work(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1))
    app.sync.ingest(message(app, 2, text="Retain this second point"))
    retained(app, "generated", "Generated cited point", [1])
    retained(app, "owner", "Owner's separate note", [1], "owner")
    original = app.sync.changed

    def fail(message, **kwargs):
        raise RuntimeError("Authored hook interruption after tombstone")

    app.sync.changed = fail
    with pytest.raises(RuntimeError):
        app.sync.deleted(PEER, [1])
    assert app.store.message(KEY, 1).deleted and pending(app)
    assert {e["id"] for e in app.memory.entries(KEY)} == {"generated", "owner"}
    assert json.loads(app.store.rows("SELECT sources FROM blocks")[0]["sources"]) == [
        1,
        2,
    ]
    app.sync.changed = original
    restarted = fixture(tmp_path, clock=lambda: now[0])
    restarted.sync.recover_deletions()
    assert not pending(restarted)
    assert {e["id"] for e in restarted.memory.entries(KEY)} == {"owner"}
    assert json.loads(
        restarted.store.rows("SELECT sources FROM blocks")[0]["sources"]
    ) == [2]
    jobs = restarted.store.rows("SELECT * FROM jobs")
    version = restarted.store.history.rows(KEY, 1)
    restarted.sync.deleted(PEER, [1])
    restarted.sync.recover_deletions()
    assert restarted.store.rows("SELECT * FROM jobs") == jobs
    assert restarted.store.history.rows(KEY, 1) == version and len(version) == 1
    assert not restarted.engine.calls and not restarted.transport.exact_reads


async def test_replay_after_partially_completed_hook_is_idempotent(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app, 1))
    app.sync.ingest(message(app, 2))
    retained(app, "generated", "Generated cited point", [1])
    actual = app.sync.changed

    def partial(message, **kwargs):
        actual(message, **kwargs)
        raise RuntimeError("Authored late hook failure")

    app.sync.changed = partial
    with pytest.raises(RuntimeError):
        app.sync.deleted(PEER, [1])
    jobs = app.store.rows("SELECT * FROM jobs")
    memory_state = app.memory.state(KEY)
    app.sync.changed = actual
    app.sync.recover_deletions()
    assert app.store.rows("SELECT * FROM jobs") == jobs
    assert app.memory.state(KEY) == memory_state and not pending(app)
    assert not app.engine.calls


async def test_tombstone_and_pending_marker_are_atomic(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app))
    with app.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER fail_marker BEFORE INSERT ON cursors WHEN NEW.key LIKE 'deletion-effects:%' BEGIN SELECT RAISE(ABORT, 'authored marker failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        app.sync.deleted(PEER, [1])
    assert not app.store.message(KEY, 1).deleted
    assert not app.store.history.rows(KEY, 1) and not pending(app)


async def test_unknown_chat_native_deletion_does_not_admit_source_or_effects(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.deleted("user:777", [1])
    app.sync.deleted("channel:777", [1])
    assert not pending(app) and not app.store.rows("SELECT 1 FROM messages")
    assert not app.engine.calls and not app.transport.exact_reads


async def test_acknowledged_operation_repairs_tombstone_before_failed_hook(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app))
    operation = await deletion(app, [1])
    actual = app.sync.changed
    app.sync.changed = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("Authored saved tombstone/hook failure")
    )
    completed = await app.deletion.dispatch(operation["id"])
    assert completed["state"] == "completed"
    assert app.store.message(KEY, 1).deleted and app.store.rows("SELECT 1 FROM blocks")
    assert (
        pending(app)
        and app.store.deletion_operations.get(operation["id"])["needs_reconcile"]
    )
    app.sync.changed = actual
    checked = await app.deletion.check(KEY, operation["id"])
    assert checked["state"] == "completed" and not app.store.rows(
        "SELECT 1 FROM blocks"
    )
    assert (
        not pending(app)
        and not app.store.deletion_operations.get(operation["id"])["needs_reconcile"]
    )
    assert (
        len(app.transport.deletion_calls) == 1
        and len(app.store.history.rows(KEY, 1)) == 1
    )


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_incoming_stream_late_completion_cannot_restore_deleted_source(tmp_path):
    app, now = rig(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    class Engine:
        name, model, provider = "offline", "authored", "local"

        async def generate_stream(self, role, packet, on_text):
            on_text("Visible authored prefix")
            entered.set()
            await release.wait()
            on_text("Late deleted wording")
            return EngineResponse(
                {"text": "Late deleted wording", "image_context": []}, actual_nanos=7
            )

    app.engine = Engine()
    app.sync.ingest(message(app))
    now[0] += 31
    running = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    assert app.conversation(KEY)["blocks"][0]["stream"]
    operation = await deletion(app, [1])
    await app.deletion.dispatch(operation["id"])
    assert app.conversation(KEY)["blocks"] == []
    release.set()
    await running
    assert app.conversation(KEY)["blocks"] == [] and app.store.message(KEY, 1).deleted
    assert not app.store.rows("SELECT 1 FROM rewrite_streams")
    assert not app.store.rows("SELECT 1 FROM emotion_state")
    assert not app.store.rows("SELECT 1 FROM analyzed")
    assert app.store.rows("SELECT status,actual FROM reservations") == [
        {"status": "receipted", "actual": 7}
    ]


async def test_deleting_outgoing_boundary_regroups_with_quiet_wait_and_no_stale_render(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1, text="First incoming point"))
    app.sync.ingest(message(app, 2, outgoing=True, text="Owner boundary"), target=False)
    app.sync.ingest(message(app, 3, text="Second incoming point"))
    now[0] += 31
    await app.process_due()
    await app.process_due()
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='ready',rendered='Prior distinct text',rendered_revision=revision"
        )
    original_draft = edit(app, "Keep authored draft", reply_to=2)
    app.sync.deleted(PEER, [2])
    [block] = app.store.rows("SELECT * FROM blocks")
    assert json.loads(block["sources"]) == [1, 3] and block["rendered"] is None
    assert block["state"] == "queued" and app.store.draft(KEY) == original_draft
    [queued] = app.store.rows(
        "SELECT due FROM jobs WHERE scope=? AND state='queued' AND role='incoming_rewrite'",
        (block["id"],),
    )
    assert queued["due"] == now[0] + 30
    jobs = app.store.rows("SELECT * FROM jobs")
    app.sync.deleted(PEER, [2])
    assert app.store.rows("SELECT * FROM jobs") == jobs


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_deleted_input_rejects_late_emotion_classification_and_suggestions(
    tmp_path,
):
    app, now = rig(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    app.sync.ingest(message(app))
    now[0] += 31
    await app.process_due()

    async def held(role, packet):
        entered.set()
        await release.wait()
        return {
            "emotion": choice("worried", 0.9),
            "hostility": {"type": "noul", "noul": 0.0},
            "suggestions_useful": {"type": "noul", "noul": 0.9},
        }

    app.decider.generate = held
    running = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    app.sync.deleted(PEER, [1])
    release.set()
    await running
    assert app.emotion.view(KEY).code is None
    assert app.suggestions.view(KEY).options == []
    assert not app.store.rows("SELECT 1 FROM jobs WHERE role='response_suggestions'")
    assert (
        app.store.rows("SELECT state FROM jobs WHERE role='emotion_classification'")[0][
            "state"
        ]
        == "superseded"
    )


async def test_deleted_reaction_target_rejects_late_assessment_and_unseen_dot(tmp_path):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1), target=False)
    app.sync.ingest(message(app, 2, outgoing=True), target=False)
    added = now[0] + 1
    now[0] += 2
    app.sync.reactions(PEER, 2, snapshot(peer=[("👎", added)], observed=now[0]))
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(role, packet):
        entered.set()
        await release.wait()
        return {
            "emotion": choice("worried", 0.9),
            "suggestions_useful": {"type": "noul", "noul": 0.9},
        }

    app.decider.generate = held
    now[0] += 31
    running = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    app.sync.deleted(PEER, [2])
    release.set()
    await running
    assert (
        app.emotion.view(KEY).code is None and app.suggestions.view(KEY).options == []
    )
    with app.store.transaction() as db:
        assert app.store.viewing.ready_reactions(db, KEY) == []
    assert app.previews.opening(KEY) is None


async def test_suggestions_late_response_cannot_revive_deleted_original(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app), target=False)
    record = await prepare(app)
    with app.store.transaction() as db:
        app.suggestions.enqueue(db, KEY, record, record["attempt"], app.store.clock())
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(role, packet):
        entered.set()
        await release.wait()
        return {"options": [option.model_dump() for option in OPTIONS]}

    app.engine.generate = held
    running = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    app.sync.deleted(PEER, [1])
    release.set()
    await running
    assert app.suggestions.view(KEY).options == []
    assert (
        app.store.rows("SELECT state FROM jobs WHERE role='response_suggestions'")[0][
            "state"
        ]
        == "superseded"
    )


async def test_deleting_owner_answer_never_revives_prepared_suggestions(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app, 1), target=False)
    await ready(app)
    assert app.suggestions.view(KEY).state == "ready"
    app.sync.ingest(message(app, 2, outgoing=True), target=False)
    assert app.suggestions.view(KEY).reason == "answered"
    app.sync.deleted(PEER, [2])
    app.sync.recover_deletions()
    assert (
        app.suggestions.view(KEY).reason == "answered"
        and app.suggestions.view(KEY).options == []
    )
    assert not app.engine.calls


@pytest.mark.usefixtures("remember_on")
async def test_memory_deletion_preserves_owner_and_rejects_inflight_generated_takeaway(
    tmp_path,
):
    app, now = rig(tmp_path)
    memory_conversation(app, now)
    retained(app, "owner", "Owner-authored memory", [1], "owner")
    retained(app, "generated", "Old generated memory", [1])
    app.memory.record(KEY, [f"{KEY}/1"])
    assert app.memory.schedule(KEY)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(role, packet):
        entered.set()
        await release.wait()
        return {
            "person": {
                "add": [{"text": "Late generated takeaway", "citations": [1]}],
                "remove": [],
                "outdated": [],
            },
            "account": {"add": [], "remove": [], "outdated": []},
        }

    app.engine.generate = held
    running = asyncio.create_task(fold(app))
    await asyncio.wait_for(entered.wait(), 5)
    app.sync.deleted(PEER, [1])
    release.set()
    job = await running
    assert {e["id"] for e in app.memory.entries(KEY)} == {"owner"}
    assert (
        app.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))[0]["state"]
        == "superseded"
    )
    assert not app.store.rows("SELECT 1 FROM analyzed WHERE key=?", (f"{KEY}/1",))


async def test_observed_history_transcript_and_cached_bytes_keep_original_expiry(
    tmp_path,
):
    app, now = rig(tmp_path)
    original = message(app, media="audio", text="Original caption")
    app.sync.ingest(original, target=False, arrival=False)
    raw = b"authored retained media bytes"
    app.transport.media[PEER, 1] = raw
    first = await app.media.cache.fetch(original, limit=1024, priority=PREPARATION)
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES (?,?,?)",
            (
                original.key,
                original.revision,
                json.dumps(
                    {
                        "transcript": {
                            "text": "Authored observed transcript",
                            "language": "en",
                        }
                    }
                ),
            ),
        )
    app.sync.deleted(PEER, [1])
    [version] = app.store.history.rows(KEY, 1)
    prior = app.store.history.version(KEY, 1, version["id"])
    assert prior.text == "Original caption" and not prior.deleted
    assert json.loads(version["transcript"])["text"] == "Authored observed transcript"
    assert app.media.cache.get(prior)["path"].read_bytes() == raw
    assert first["path"].is_file() and not app.store.rows("SELECT 1 FROM derivatives")
    assert len(app.transport.downloads) == 1
    now[0] += RETENTION + 1
    app.store.expire()
    app.media.cache.collect()
    assert not app.store.history.rows(KEY, 1) and app.media.cache.usage()["files"] == 0
    assert not first["path"].exists()


@pytest.mark.parametrize("change", ["deletion", "expiry"])
async def test_download_finishing_after_deletion_or_expiry_caches_no_stale_bytes(
    tmp_path, change
):
    app, now = rig(tmp_path)
    original = message(app, media="image", text="")
    app.sync.ingest(original, target=False, arrival=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def download(peer, identity, path, **kwargs):
        path.write_bytes(b"authored late download")
        entered.set()
        await release.wait()

    app.transport.download = download
    downloading = asyncio.create_task(
        app.media.cache.fetch(original, limit=1024, priority=PREPARATION)
    )
    await asyncio.wait_for(entered.wait(), 5)
    if change == "deletion":
        app.sync.deleted(PEER, [1])
    else:
        now[0] += RETENTION + 1
        app.store.expire()
    release.set()
    with pytest.raises(Refused):
        await downloading
    assert not app.store.rows("SELECT 1 FROM media_files")
    assert not list(app.media.cache.directory.iterdir())


@pytest.mark.parametrize("change", ["deletion", "expiry"])
async def test_transcription_finishing_after_deletion_or_expiry_creates_no_derivative(
    tmp_path, change
):
    app, now = rig(tmp_path)
    original = message(app, media="audio", text="")
    app.sync.ingest(original, target=False, arrival=False)
    entered, release = asyncio.Event(), asyncio.Event()

    class Transcriber:
        async def transcribe(self, path):
            entered.set()
            await release.wait()
            return {"text": "Late recognized authored words", "language": "en"}

    app.media.transcriber = Transcriber()
    path = tmp_path / "authored-source.bin"
    path.write_bytes(b"authored local transcription input")
    running = asyncio.create_task(app.media._transcribe(original, path))
    await asyncio.wait_for(entered.wait(), 5)
    if change == "deletion":
        app.sync.deleted(PEER, [1])
    else:
        now[0] += RETENTION + 1
        app.store.expire()
    release.set()
    with pytest.raises(Refused):
        await running
    assert not app.store.rows("SELECT 1 FROM derivatives")


async def test_deletion_clears_local_unseen_opening_and_preview_without_native_receipts(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app), live=True)
    now[0] += 31
    await app.process_due()
    app.unseen.advance()
    assert app.previews.opening(KEY) == 1 and app.previews.recent()[0].unseen
    app.sync.deleted(PEER, [1])
    row = app.previews.recent()[0]
    assert row.opening_source is None and not row.unseen
    # ADR 52 Q10: what it last said, marked deleted, never its rewrite.
    assert row.preview.original == row.preview.ai == message(app).text
    assert row.preview.deleted
    assert app.store.chat(KEY).read_through == 0
    assert not app.transport.reads and not app.transport.deliveries


async def test_pending_native_consequences_finish_after_source_expiry_without_backfill(
    tmp_path,
):
    app, now = rig(tmp_path)
    original = message(app, 1, date=now[0] - RETENTION + 10)
    app.sync.ingest(original, target=False)
    app.sync.ingest(message(app, 2, outgoing=True), target=False)
    retained(app, "generated", "Generated cited point", [1])
    retained(app, "owner", "Protected owner note", [1], "owner")
    actual = app.sync.changed
    app.sync.changed = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("Authored expired hook failure")
    )
    with pytest.raises(RuntimeError):
        app.sync.deleted(PEER, [1])
    now[0] += 11
    app.store.expire()
    assert app.store.message(KEY, 1) is None and pending(app)
    app.sync.changed = actual
    app.sync.recover_deletions()
    assert not pending(app) and {e["id"] for e in app.memory.entries(KEY)} == {"owner"}
    assert app.store.message(KEY, 1) is None and app.store.history.rows(KEY, 1) == []
    assert not app.transport.exact_reads and not app.engine.calls


async def test_receipt_after_source_expiry_removes_generated_citations_without_source_recreation(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1, date=now[0] - RETENTION + 10), target=False)
    app.sync.ingest(message(app, 2, outgoing=True), target=False)
    retained(app, "generated", "Generated cited point", [1])
    retained(app, "owner", "Protected owner note", [1], "owner")
    operation = await deletion(app, [1])
    native = app.transport.delete

    async def late_receipt(description, evidence):
        receipt = await native(description, evidence)
        now[0] += 11
        app.store.expire()
        return receipt

    app.transport.delete = late_receipt
    completed = await app.deletion.dispatch(operation["id"])
    assert (
        completed["state"] == "completed" and completed["counts"]["acknowledged"] == 1
    )
    assert {e["id"] for e in app.memory.entries(KEY)} == {"owner"}
    assert app.store.message(KEY, 1) is None and app.store.history.rows(KEY, 1) == []
    assert not pending(app) and len(app.transport.deletion_calls) == 1


@pytest.mark.parametrize("peer", [PEER, None])
async def test_one_replay_failure_preserves_marker_and_does_not_block_other_sources(
    tmp_path, peer
):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app, 1))
    app.sync.ingest(message(app, 2))
    actual = app.sync.changed
    app.sync.changed = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("Authored interrupted hooks")
    )
    with pytest.raises(RuntimeError):
        app.sync.deleted(peer, [1, 2])
    assert {row["key"] for row in pending(app)} == {
        f"deletion-effects:{KEY}/1",
        f"deletion-effects:{KEY}/2",
    }

    def first_blocked(message, **kwargs):
        if message.message_id == 1:
            raise RuntimeError("Authored first-source local blocker")
        return actual(message, **kwargs)

    app.sync.changed = first_blocked
    app.sync.recover_deletions()
    assert [row["key"] for row in pending(app)] == [f"deletion-effects:{KEY}/1"]
    app.sync.changed = actual
    app.sync.recover_deletions()
    assert not pending(app) and not app.engine.calls
