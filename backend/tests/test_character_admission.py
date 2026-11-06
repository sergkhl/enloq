"""ADR 35 and ADR 62 Q21: the Processing Character Threshold governs group
briefings only; private text is held for its decision at any length, and only
an image, a voice note or a Video Circle admits a block for rewriting.
Authored cached sources only; no native operations or provider calls."""

import json

import pytest
from enloq.application.accounts import AccountManager
from enloq.application.blocks import reconcile_admission, reconcile_unread
from enloq.application.conversations import Conversations
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from enloq.inference.base import EngineResponse
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import WINDOW, Store
from enloq.transport.fake import FakeTransport

from backend.tests.test_rebuild import message, picture

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


@pytest.mark.parametrize("threshold", [0, 200, 10_000])
async def test_private_text_is_held_at_any_length_whatever_the_threshold(
    rig, threshold
):
    app, now, registry = rig
    registry.set_processing_threshold(threshold)
    app.notifications.unavailable = lambda: True
    app.sync.ingest(message(app, text="а" * 10_000), live=True)
    assert not pending(app) and len(held(app)) == 1
    assert block(app)["state"] == "original"
    now[0] += 31
    await app.process_due()
    # One decision from the originals, and no rewrite (ADR 49 Q3, ADR 54).
    assert not app.engine.calls and len(held(app)) == 1
    assert [role for role, _ in app.decider.calls] == ["emotion_classification"]
    # A Save of the threshold releases nothing.
    registry.set_processing_threshold(0)
    reconcile_admission(app.store)
    await app.process_due()
    assert not app.engine.calls and block(app)["state"] == "original"


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


async def test_cached_unread_catchup_defers_without_scanning_already_read_history(rig):
    app, now, _ = rig
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"read_through": 1}))
    app.sync.ingest(message(app, 1, text="а" * 200), target=False)
    app.sync.ingest(message(app, 2, outgoing=True), target=False)
    app.sync.ingest(message(app, 3, text="б" * 199), target=False)
    assert not held(app) and not pending(app)
    reconcile_unread(app.store)
    assert len(held(app)) == 1 and held(app)[0]["scope"] == KEY + "/3"
    now[0] += 31
    await app.process_due()
    assert not app.engine.calls
    state = app.decider.calls[0][1]["state"]["conversation"]
    assert [e["id"] for e in state if e.get("latest_block")] == [3]
    assert block(app)["state"] == "original"
    assert not app.transport.reads and not app.transport.exact_reads


async def test_source_delete_shrinks_block_and_expired_hold_never_backfills(rig):
    app, now, _ = rig
    app.sync.ingest(message(app, text="а" * 100))
    app.sync.ingest(message(app, 2, text="б" * 100))
    app.sync.deleted("user:42", [2])
    assert not pending(app) and len(held(app)) == 1
    assert json.loads(block(app)["sources"]) == [1]
    now[0] += WINDOW + 1
    await app.process_due()
    assert not pending(app) and not held(app) and not app.engine.calls
    assert not app.transport.reads and not app.transport.exact_reads


async def test_unchanged_failure_settings_and_restart_do_not_retry_and_explicit_retry_is_exempt(
    rig,
):
    app, now, registry = rig
    app.sync.ingest(picture(app, text="а" * 200))
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


async def test_explicit_earlier_processing_and_manual_suggestions_are_exempt(rig):
    app, _, registry = rig
    registry.set_processing_threshold(10_000)
    app.sync.ingest(message(app, text="Authored short point"))
    assert len(held(app)) == 1
    # Rewrite earlier messages offers only blocks with media (ADR 54).
    assert app.rewriting.process_older(KEY) == 0
    app.sync.ingest(message(app, 2, outgoing=True), target=False)
    app.sync.ingest(picture(app, 3, text="Authored photo"), target=False)
    assert app.rewriting.process_older(KEY) == 1
    await app.process_due()
    assert block(app, KEY + "/3")["state"] == "ready"
    draft = app.store.mutate_draft(
        KEY, 0, lambda value: value.update(text="My short draft")
    )
    app.suggestions.generate(KEY, draft["revision"])
    for _ in range(4):
        await app.process_due()
    assert [role for role, _ in app.engine.calls] == [
        "incoming_rewrite",
        "response_suggestions",
    ]
    assert block(app)["state"] == "original"


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
    second.sync.ingest(picture(second, text="б" * 200))
    assert not manager.memory_idle()
    with second.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0")
    assert second.rewriting.next_job()["scope"] == "2/user:42/1"
    # A threshold Save changes no private admission.
    manager.set_processing_threshold(10_000)
    assert second.rewriting.next_job()["scope"] == "2/user:42/1"
    assert not app.rewriting.next_job()
