"""Authored cache-only navigation retains owner work without native discovery."""

import json

import pytest
from enloq.domain.models import Chat, Message, Refused
from enloq.storage.store import RETENTION, WINDOW

from backend.tests.test_group_deletion import submit
from backend.tests.test_rebuild import fixture


def group(app, peer="chat:42"):
    key = f"{app.store.account_id}/{peer}"
    app.store.put_chat(
        Chat(
            account_id=app.store.account_id,
            peer=peer,
            title="Authored retained group",
            kind="group",
        ),
        activity=app.store.clock(),
    )
    app.store.groups.observe(key, access="available", reason="authored_native")
    return key


def rig(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    return app, now, group(app)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"text": "Exact authored draft"}, "draft"),
        ({"text": "  "}, "draft"),
        ({"attachments": ["authored-image"]}, "draft"),
        ({"reply_to": 123}, "draft"),
        ({"undo": "Original authored words"}, "draft"),
        ({"selected_media": {"id": "a" * 32}}, "selected_media"),
    ],
)
def test_saved_draft_forms_enter_retained_only_after_the_active_boundary(
    tmp_path, change, reason
):
    app, now, key = rig(tmp_path)
    saved = app.store.mutate_draft(key, 0, lambda draft: draft.update(change))
    now[0] += WINDOW
    assert app.retained.entries() == []
    assert app.retained.cache_read(key).key == key
    with pytest.raises(Refused):
        app.retained.assert_open(key)
    now[0] += 0.1
    entry = app.retained.entries()[0]
    assert entry["chat"]["key"] == key and entry["reasons"] == [reason]
    assert entry["draft"] is (reason == "draft")
    assert entry["selected_media"] is (reason == "selected_media")
    assert entry["send_count"] == entry["deletion_count"] == 0
    assert "Exact authored draft" not in json.dumps(entry)
    assert app.retained.assert_open(key).key == key
    assert app.retained.cache_read(key).key == key
    assert app.store.draft(key) == saved
    assert app.store.recent_chats("group", cached_access=True) == []
    assert not app.store.groups.get(key).processing_enabled
    assert not app.engine.calls and not app.transport.reads


def test_old_empty_groups_and_private_chats_do_not_become_retained(tmp_path):
    app, now, key = rig(tmp_path)
    app.store.mutate_draft("1/user:42", 0, lambda d: d.update(text="Private draft"))
    # Persisting a blank draft record alone does not create saved work.
    app.store.mutate_draft(key, 0, lambda d: None)
    now[0] += WINDOW + 1
    assert app.retained.entries() == []
    for candidate in (key, "1/user:42", "2/chat:42", "1/chat:99"):
        with pytest.raises(Refused):
            app.retained.cache_read(candidate)


@pytest.mark.parametrize("access", ["lost", "unknown", "available"])
def test_exact_cached_peer_survives_access_loss_without_enabling_ai(tmp_path, access):
    app, now, key = rig(tmp_path)
    saved = app.store.mutate_draft(key, 0, lambda d: d.update(text="Kept words"))
    now[0] += WINDOW + 1
    app.store.groups.observe(key, access=access, reason="authored_evidence")
    before = app.store.groups.get(key)
    activity = app.store.rows("SELECT activity FROM chats WHERE key=?", (key,))
    assert app.retained.entries()[0]["chat"]["key"] == key
    assert app.retained.cache_read(key).peer == "chat:42"
    assert app.conversation(key)["draft"]["text"] == "Kept words"
    assert app.store.groups.get(key) == before
    assert app.store.draft(key) == saved
    assert app.store.rows("SELECT activity FROM chats WHERE key=?", (key,)) == activity
    assert app.store.rows("SELECT * FROM jobs") == []
    assert app.engine.calls == app.transport.reads == []


@pytest.mark.parametrize("reason", ["protected", "forum", "broadcast"])
def test_unsupported_peer_is_not_exposed_by_retained_navigation(tmp_path, reason):
    app, now, key = rig(tmp_path)
    app.store.mutate_draft(key, 0, lambda d: d.update(text="Kept owner draft"))
    now[0] += WINDOW + 1
    app.store.groups.observe(key, access="lost", supported=False, reason=reason)
    assert app.retained.entries() == []
    with pytest.raises(Refused):
        app.retained.cache_read(key)
    assert app.store.draft(key)["text"] == "Kept owner draft"


def test_migration_keeps_typed_peers_separate_and_both_opt_ins_off(tmp_path):
    app, now, old_key = rig(tmp_path)
    new_key = group(app, "channel:42")
    app.store.mutate_draft(old_key, 0, lambda d: d.update(text="Old exact peer"))
    app.store.mutate_draft(new_key, 0, lambda d: d.update(text="New exact peer"))
    app.store.groups.observe(
        old_key,
        access="lost",
        reason="migrated",
        migrated_to="channel:42",
    )
    now[0] += WINDOW + 1
    assert {entry["chat"]["key"] for entry in app.retained.entries()} == {
        old_key,
        new_key,
    }
    assert app.retained.assert_open(old_key).peer == "chat:42"
    assert app.retained.assert_open(new_key).peer == "channel:42"
    assert not app.store.groups.get(old_key).processing_enabled
    assert not app.store.groups.get(new_key).processing_enabled
    foreign = fixture(tmp_path, account=2, clock=lambda: now[0])
    assert foreign.retained.entries() == []
    with pytest.raises(Refused):
        foreign.retained.cache_read(old_key)


async def test_send_work_enters_retained_and_cancellation_preserves_its_content(
    tmp_path,
):
    app, now, key = rig(tmp_path)
    saved = app.store.mutate_draft(key, 0, lambda d: d.update(text="Exact send words"))
    operation = await app.delivery.submit(key, saved["revision"])
    now[0] += WINDOW + 1
    app.store.groups.observe(key, access="lost", reason="authored_loss")
    entry = app.retained.entries()[0]
    assert entry["reasons"] == ["send"] and entry["send_count"] == 1
    assert app.retained.cache_read(key).key == key
    assert "Exact send words" not in json.dumps(entry)
    assert not app.transport.deliveries
    cancelled = app.delivery.operations.cancel(operation["id"])
    assert cancelled["text"] == "Exact send words"
    entry = app.retained.entries()[0]
    assert entry["reasons"] == ["cancelled_send"]
    assert entry["send_count"] == 0 and entry["cancelled_count"] == 1
    assert app.retained.cache_read(key).key == key
    assert app.conversation(key)["operations"][0]["text"] == "Exact send words"


async def test_unresolved_deletion_keeps_status_after_sources_expire(tmp_path):
    app, now, key = rig(tmp_path)
    source = Message(
        account_id=1,
        peer="chat:42",
        message_id=1,
        revision=1,
        date=now[0],
        text="Authored native text that expires",
        author_peer="user:1",
    )
    app.sync.ingest(source)
    app.transport.messages = [source]
    operation = await submit(app, key)
    app.transport.deletion_failure = TimeoutError()
    await app.deletion.dispatch(operation["id"])
    now[0] += RETENTION + 1
    app.store.expire()
    entry = app.retained.entries()[0]
    assert entry["reasons"] == ["deletion"] and entry["deletion_count"] == 1
    assert "Authored native text" not in json.dumps(entry)
    assert app.retained.assert_open(key).key == key
    view = app.conversation(key)
    assert view["messages"] == []
    assert view["deletions"][0]["targets"][0]["preview"] == ""
    reads = list(app.transport.reads)
    assert await app.history(key) == []
    assert await app.direct(key, 1) is None
    assert app.transport.reads == reads
    assert app.store.deletion_operations.get(operation["id"])["state"] == "uncertain"


def test_revoked_account_cannot_open_retained_work(tmp_path):
    app, now, key = rig(tmp_path)
    app.store.mutate_draft(key, 0, lambda d: d.update(text="Saved owner words"))
    now[0] += WINDOW + 1
    app.authorized = lambda: False
    for action in (app.retained.entries, lambda: app.retained.cache_read(key)):
        with pytest.raises(Refused):
            action()
