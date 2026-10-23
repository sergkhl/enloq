"""ADR 35: authored cached sources only; no native operations or provider calls."""

import asyncio
import json

import pytest
from enloq.application import context
from enloq.application.accounts import AccountManager
from enloq.application.blocks import (
    original_characters,
    reconcile_admission,
    reconcile_unread,
)
from enloq.application.conversations import Conversations
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from enloq.inference.base import EngineResponse
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import WINDOW, Store
from enloq.transport.fake import FakeTransport

from backend.tests.test_rebuild import message

# These cases exercise the rewrite path with text (ADR 54 keeps it for
# voice notes, images and calming).
pytestmark = pytest.mark.usefixtures("text_rewriting")

KEY = "1/user:42"
IDENTITY = KEY + "/1"


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    registry = AccountRegistry(tmp_path)
    store = Store(
        tmp_path / "rewriting.sqlite3",
        account_id=1,
        clock=lambda: now[0],
        shared_path=registry.path,
    )
    store.put_chat(Chat(account_id=1, peer="user:42", title="Authored", kind="private"))
    return Conversations(store, FakeTransport()), now, registry


def pending(app):
    return app.store.rows(
        "SELECT * FROM jobs WHERE role='incoming_rewrite' "
        "AND state IN ('queued','claimed','running')"
    )


def held(app):
    return app.store.rows("SELECT * FROM incoming_deferred")


def block(app, identity=IDENTITY):
    return app.rewriting.block(identity)


def test_global_threshold_default_durability_validation_and_change_events(tmp_path):
    registry = AccountRegistry(tmp_path)
    assert registry.processing_threshold() == 200
    for accepted in (0, 200, 10_000):
        assert registry.set_processing_threshold(accepted) == accepted
        reopened = AccountRegistry(tmp_path)
        assert reopened.processing_threshold() == accepted
    _, latest = registry.changes()
    for invalid in (-1, 10_001, 2.0, "200", True, False, None):
        with pytest.raises(Refused, match="integer"):
            registry.set_processing_threshold(invalid)
        assert registry.processing_threshold() == 10_000
    assert registry.changes(latest) == ([], latest)
    registry.set_processing_threshold(199)
    scopes, latest = registry.changes(latest)
    assert scopes == ["processing_threshold"]
    registry.set_processing_threshold(199)
    assert registry.changes(latest) == ([], latest)


async def test_combined_199_then_200_with_quiet_period_and_no_worker_or_reservation(
    rig,
):
    app, now, registry = rig
    app.sync.ingest(message(app, text="а" * 100), live=True)
    now[0] += 10
    app.sync.ingest(message(app, 2, text="б" * 99), live=True)
    assert registry.processing_threshold() == 200
    assert not pending(app) and len(held(app)) == 1
    assert block(app)["state"] == "original"
    assert app.conversation(KEY)["blocks"][0]["stage"] is None
    assert app.conversation(KEY)["progress"] is None
    assert app.memory.local_idle()
    now[0] += 30
    await app.process_due()
    # ADR 49 Q3: still no rewrite, but one decision request from the originals.
    assert not app.engine.calls and len(held(app)) == 1
    assert [role for role, _ in app.decider.calls] == ["emotion_classification"]
    state = app.decider.calls[0][1]["state"]["conversation"]
    assert [e["id"] for e in state if e.get("latest_block")] == [1, 2]
    assert [r["engine"] for r in app.store.rows("SELECT * FROM reservations")] == [
        "offline"
    ]
    app.sync.ingest(message(app, 3, text="в"), live=True)
    assert len(pending(app)) == 1 and not held(app)
    now[0] += 29
    await app.process_due()
    assert not app.engine.calls
    now[0] += 1
    await app.process_due()
    assert len(app.engine.calls) == 1
    assert [m["id"] for m in app.engine.calls[0][1]["messages"]] == [1, 2, 3]
    assert block(app)["state"] == "ready"


async def test_original_count_ignores_metadata_context_derived_text_and_memory(rig):
    app, now, registry = rig
    # Emoji is one code point; the combining accent is a separate code point.
    assert (
        original_characters(
            [message(app, text=" \nЯ 😀\nа\u0301\t "), message(app, 2, text="  x  ")]
        )
        == 7
    )
    app.sync.ingest(message(app, 1, text="Context" * 100, outgoing=True))
    app.memory.add(KEY, 0, "Authored contextual fact")
    app.sync.ingest(message(app, 2, text=" \n" + "я" * 199 + "\t ", reply_to=1))
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES (?,?,?)",
            (KEY + "/2", 1, json.dumps({"image_context": "Authored context" * 100})),
        )
    now[0] += 31
    await app.process_due()
    assert not app.engine.calls and len(held(app)) == 1
    assert not app.transport.exact_reads
    registry.set_processing_threshold(199)
    await app.process_due()
    assert len(app.engine.calls) == 1
    assert app.engine.calls[0][1]["messages"][0]["text"].strip() == "я" * 199


@pytest.mark.parametrize("boundary", ["outgoing", "service", "day"])
async def test_no_character_accumulation_across_block_boundaries(rig, boundary):
    app, now, _ = rig
    app.sync.ingest(message(app, text="а" * 100))
    if boundary == "day":
        now[0] += 86400
    else:
        app.sync.ingest(message(app, 2, **{boundary: True}))
    app.sync.ingest(message(app, 3, text="б" * 100))
    now[0] += 31
    await app.process_due()
    assert not app.engine.calls and len(held(app)) == 2
    assert len(app.store.rows("SELECT * FROM blocks")) == 2


async def test_short_addition_to_successful_long_block_remains_eligible(rig):
    app, now, registry = rig
    app.sync.ingest(message(app, text="а" * 200))
    now[0] += 30
    await app.process_due()
    await app.process_due()  # Dependent Emotion Classification.
    cached = block(app)
    registry.set_processing_threshold(10_000)
    reconcile_admission(app.store)
    assert block(app) == cached and not held(app)
    registry.set_processing_threshold(200)
    app.sync.ingest(message(app, 2, text="б"))
    assert len(pending(app)) == 1
    now[0] += 30
    await app.process_due()
    assert len(app.engine.calls) == 2
    assert len(app.engine.calls[-1][1]["messages"]) == 2


@pytest.mark.parametrize("media", ["image", "audio", "video_note"])
async def test_analysable_media_exempts_target_under_existing_preparation(rig, media):
    app, now, registry = rig
    registry.set_processing_threshold(10_000)
    app.sync.ingest(message(app, text="", media=media))
    assert len(pending(app)) == 1 and not held(app)
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES (?,?,?)",
            (
                IDENTITY,
                1,
                json.dumps(
                    {"image_context": "Authored synthetic scene"}
                    if media == "image"
                    else {"transcript": {"text": "Authored prepared speech"}}
                ),
            ),
        )
    now[0] += 30
    await app.process_due()
    assert len(app.engine.calls) == 1 and block(app)["state"] == "ready"
    assert not app.transport.exact_reads


@pytest.mark.parametrize(
    "media", ["video", "gif", "sticker", "document", "poll", "link"]
)
async def test_textless_non_ai_media_does_not_call_model(rig, media):
    app, now, registry = rig
    registry.set_processing_threshold(0)
    app.sync.ingest(message(app, text="", media=media))
    now[0] += 31
    await app.process_due()
    assert block(app)["state"] == "nothing"
    assert not held(app) and not pending(app) and not app.engine.calls


async def test_lowering_honors_original_quiet_and_raising_before_claim_holds(rig):
    app, now, registry = rig
    app.sync.ingest(message(app, text="а" * 199))
    due = held(app)[0]["due"]
    now[0] += 20
    registry.set_processing_threshold(199)
    await app.process_due()
    assert not held(app) and pending(app)[0]["due"] == due
    assert not app.engine.calls
    now[0] += 10
    job = app.rewriting.next_job()
    registry.set_processing_threshold(200)
    assert not app.rewriting.claim(job)
    assert not pending(app) and held(app)[0]["due"] == due
    assert block(app)["state"] == "original" and app.memory.local_idle()
    registry.set_processing_threshold(199)
    await app.process_due()
    assert len(app.engine.calls) == 1


def test_raising_removes_deferred_scope_from_factual_active_run_progress(rig):
    app, _, registry = rig
    app.sync.ingest(message(app, text="а" * 200))
    app.sync.ingest(message(app, 2, outgoing=True))
    app.sync.ingest(message(app, 3, text="б" * 300))
    assert app.conversation(KEY)["progress"] == {
        "done": 0,
        "total": 2,
        "explicit": False,
    }
    registry.set_processing_threshold(201)
    reconcile_admission(app.store)
    assert app.conversation(KEY)["progress"] == {
        "done": 0,
        "total": 1,
        "explicit": False,
    }
    assert app.conversation(KEY)["blocks"][0]["stage"] is None


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_raise_during_preparation_holds_before_dispatch_and_releases_once(rig):
    app, now, registry = rig
    app.sync.ingest(message(app, text="а" * 200))
    now[0] += 30

    async def prepare(packet):
        registry.set_processing_threshold(201)

    app.engine.prepare = prepare
    await app.process_due()
    assert not app.engine.calls and not app.store.rows("SELECT * FROM reservations")
    assert block(app)["state"] == "original" and len(held(app)) == 1
    assert not pending(app) and app.memory.local_idle()
    del app.engine.prepare
    registry.set_processing_threshold(200)
    await app.process_due()
    await app.process_due()
    assert len(app.engine.calls) == 1 and block(app)["state"] == "ready"


async def test_manager_save_holds_claimed_work_without_late_failure_or_duplicate(
    tmp_path, monkeypatch
):
    manager = AccountManager(Settings(data=tmp_path))
    app = manager.get(1)
    now = [2_000_000_000.0]
    app.store.clock = lambda: now[0]
    app.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    )
    app.sync.ingest(message(app, text="а" * 200))
    now[0] += 30
    entered, release = asyncio.Event(), asyncio.Event()
    original = context.incoming

    async def preparing(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(context, "incoming", preparing)
    first = asyncio.create_task(app.process_due())
    await entered.wait()
    manager.set_processing_threshold(201)
    assert not pending(app) and block(app)["state"] == "original"
    manager.set_processing_threshold(200)
    assert len(pending(app)) == 1
    release.set()
    await first
    assert not app.engine.calls and block(app)["state"] == "queued"
    await app.process_due()
    assert len(app.engine.calls) == 1 and block(app)["state"] == "ready"


async def test_started_attempt_may_finish_after_saved_raise(rig):
    app, now, registry = rig
    app.sync.ingest(message(app, text="а" * 200))
    now[0] += 30
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.engine.generate

    async def running(role, packet):
        entered.set()
        await release.wait()
        return await original(role, packet)

    app.engine.generate = running
    task = asyncio.create_task(app.process_due())
    await entered.wait()
    registry.set_processing_threshold(201)
    reconcile_admission(app.store)
    assert pending(app)[0]["state"] == "running" and not held(app)
    release.set()
    await task
    assert block(app)["state"] == "ready" and len(app.engine.calls) == 1


async def test_initial_adoption_and_restart_reconcile_only_cached_queued_sources(rig):
    app, now, registry = rig
    registry.set_processing_threshold(0)
    app.sync.ingest(message(app, text="а" * 199))
    due = pending(app)[0]["due"]
    registry.set_processing_threshold(200)
    app.store.close()
    reopened = Store(
        app.store.path, account_id=1, clock=lambda: now[0], shared_path=registry.path
    )
    reopened.recover()
    fresh = Conversations(reopened, FakeTransport())
    reconcile_unread(reopened)
    reconcile_admission(reopened)
    assert not pending(fresh) and held(fresh)[0]["due"] == due
    for _ in range(2):
        reconcile_unread(reopened)
    assert held(fresh)[0]["due"] == due
    now[0] += 31
    registry.set_processing_threshold(199)
    await fresh.process_due()
    assert len(fresh.engine.calls) == 1
    assert not fresh.transport.exact_reads and not fresh.transport.reads


async def test_manager_startup_reconciles_pre_upgrade_pending_work_before_workers(
    tmp_path,
):
    manager = AccountManager(Settings(data=tmp_path))
    app = manager.get(1)
    app.store.clock = lambda: 2_000_000_000.0
    app.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    )
    manager.set_processing_threshold(0)
    app.sync.ingest(message(app, text="а" * 199))
    manager.registry.set_processing_threshold(200)
    try:
        await manager._start(1)
        assert not pending(app) and len(held(app)) == 1
        assert app.memory.local_idle() and block(app)["state"] == "original"
        assert not app.engine.calls and not app.transport.reads
    finally:
        await manager.close()


async def test_cached_unread_catchup_defers_without_scanning_already_read_history(rig):
    app, now, registry = rig
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"read_through": 1}))
    app.sync.ingest(message(app, 1, text="а" * 200), target=False)
    app.sync.ingest(message(app, 2, outgoing=True), target=False)
    app.sync.ingest(message(app, 3, text="б" * 199), target=False)
    assert not held(app) and not pending(app)
    reconcile_unread(app.store)
    assert len(held(app)) == 1 and held(app)[0]["scope"] == KEY + "/3"
    now[0] += 31
    registry.set_processing_threshold(199)
    await app.process_due()
    assert len(app.engine.calls) == 1
    assert [m["id"] for m in app.engine.calls[0][1]["messages"]] == [3]
    assert block(app)["state"] == "original"
    assert not app.transport.reads and not app.transport.exact_reads


async def test_source_shrink_withdraws_stale_wording_and_changed_hold_uses_current_sources(
    rig,
):
    app, now, _ = rig
    app.sync.ingest(message(app, text="а" * 200))
    now[0] += 30
    await app.process_due()
    await app.process_due()
    app.sync.ingest(message(app, revision=2, text="б" * 199))
    assert block(app)["state"] == "original" and not block(app)["rendered"]
    assert len(held(app)) == 1 and not pending(app)
    now[0] += 31
    await app.process_due()
    assert len(app.engine.calls) == 1
    app.sync.ingest(message(app, revision=3, text="в" * 200), target=False)
    assert len(pending(app)) == 1 and not held(app)
    now[0] += 30
    await app.process_due()
    assert app.engine.calls[-1][1]["messages"][0]["text"] == "в" * 200


async def test_source_delete_shrinks_block_and_expired_hold_never_backfills(rig):
    app, now, registry = rig
    app.sync.ingest(message(app, text="а" * 100))
    app.sync.ingest(message(app, 2, text="б" * 100))
    app.sync.deleted("user:42", [2])
    assert not pending(app) and len(held(app)) == 1
    assert json.loads(block(app)["sources"]) == [1]
    now[0] += WINDOW + 1
    registry.set_processing_threshold(0)
    await app.process_due()
    assert not pending(app) and not held(app) and not app.engine.calls
    assert not app.transport.reads and not app.transport.exact_reads


async def test_unchanged_failure_settings_and_restart_do_not_retry_and_explicit_retry_is_exempt(
    rig,
):
    app, now, registry = rig
    app.sync.ingest(message(app, text="а" * 200))
    original = app.engine.generate

    async def invalid(role, packet):
        await original(role, packet)
        return EngineResponse({"wrong": "shape"})

    app.engine.generate = invalid
    now[0] += 30
    await app.process_due()
    failed = block(app)
    assert failed["state"] == "failed"
    for threshold in (201, 0, 10_000):
        registry.set_processing_threshold(threshold)
        reconcile_unread(app.store)
        app.store.recover()
        await app.process_due()
        assert block(app) == failed and len(app.engine.calls) == 1 and not held(app)
    app.engine.generate = original
    app.rewriting.retry(IDENTITY, revision=failed["revision"])
    await app.process_due()
    assert block(app)["state"] == "ready" and len(app.engine.calls) == 2


async def test_explicit_earlier_processing_and_composer_rewrite_are_exempt(rig):
    app, _, registry = rig
    registry.set_processing_threshold(10_000)
    app.sync.ingest(message(app, text="Authored short point"))
    assert len(held(app)) == 1
    assert app.rewriting.process_older(KEY) == 1
    assert not held(app)
    await app.process_due()
    assert block(app)["state"] == "ready"
    draft = app.store.mutate_draft(
        KEY, 0, lambda value: value.update(text="My short draft")
    )
    await app.rewriting.outgoing(KEY, draft["revision"])
    assert [role for role, _ in app.engine.calls] == [
        "incoming_rewrite",
        "outgoing_rewrite",
    ]


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_fallback_after_quiet_is_once_even_when_lowering_then_completing(rig):
    app, now, registry = rig
    app.sync.ingest(message(app, text="а" * 199), live=True)
    now[0] += 29
    app.unseen.advance()
    app.notifications.advance()
    assert app.store.rows("SELECT ready FROM source_views")[0]["ready"] is None
    now[0] += 1
    app.unseen.advance()
    app.notifications.advance()
    source_ready = app.store.rows("SELECT ready FROM source_views")[0]["ready"]
    notice = app.notifications.rows()[0]
    assert source_ready == now[0] and notice["kind"] == "original"
    assert app.notifications.candidate(notice)["text"] == "а" * 199
    assert app.notifications.finish(notice)
    registry.set_processing_threshold(199)
    await app.process_due()
    app.unseen.advance()
    app.notifications.advance()
    assert app.store.rows("SELECT ready FROM source_views")[0]["ready"] == source_ready
    assert app.notifications.rows() == []
    assert block(app)["state"] == "ready" and not app.transport.reads


def test_deferred_work_does_not_block_other_account_memory_idle_or_new_priority(
    tmp_path,
):
    manager = AccountManager(Settings(data=tmp_path))
    manager._create(manager.registry.register(2, "synthetic-second.session"))
    for account in (1, 2):
        app = manager.get(account)
        app.store.clock = lambda: 2_000_000_000.0
        app.store.put_chat(
            Chat(account_id=account, peer="user:42", title="Authored", kind="private")
        )
    app = manager.get(1)
    app.sync.ingest(message(app, text="а" * 199))
    assert manager.memory_idle() and not app.rewriting.next_job()
    second = manager.get(2)
    second.sync.ingest(message(second, text="б" * 200))
    assert not manager.memory_idle()
    with second.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0")
    assert second.rewriting.next_job()["scope"] == "2/user:42/1"
    manager.set_processing_threshold(201)
    assert manager.memory_idle() and not pending(second)
    manager.set_processing_threshold(200)
    assert second.rewriting.next_job()["scope"] == "2/user:42/1"
    assert not app.rewriting.next_job()
