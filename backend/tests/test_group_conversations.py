"""Group list/reading/control contracts over authored account-local caches."""

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat, Message, Refused
from enloq.storage.store import RETENTION, Store
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser


def populate(
    app, peer="chat:42", *, author="Alice", author_peer="user:7", outgoing=False
):
    store = app.store
    store.put_chat(
        Chat(
            account_id=store.account_id, peer=peer, title="Authored group", kind="group"
        )
    )
    key = f"{store.account_id}/{peer}"
    store.groups.observe(key, access="available", reason="authored_native_evidence")
    app.sync.ingest(
        Message(
            account_id=store.account_id,
            peer=peer,
            message_id=1,
            revision=1,
            date=store.clock(),
            text="A native original",
            author=author,
            author_peer=author_peer,
            outgoing=outgoing,
        )
    )
    return key


def refuse(*args, **kwargs):
    raise AssertionError("Group reading must not enter a private AI projection")


def test_group_projection_is_native_only_and_reads_do_not_opt_in(tmp_path):
    app = create_app(Settings(data=tmp_path)).state.accounts.get(1)
    key = populate(app)
    app.emotion.view = app.suggestions.view = app.stages = refuse
    app.rewriting.older = app.rewriting.older_notice = refuse
    before = app.store.groups.get(key)
    value = app.conversation(key)
    assert value["chat"]["kind"] == "group"
    assert value["messages"][0]["author_peer"] == "user:7"
    assert value["emotion"] is value["suggestions"] is value["progress"] is None
    assert value["blocks"] == [] and value["older"] == {"available": 0, "notice": None}
    assert not value["group_processing"]["processing_enabled"]
    assert app.store.groups.get(key) == before
    assert not app.engine.calls and not app.transport.exact_reads


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_group_reading_history_and_exact_sources_are_retained_cache_only(
    tmp_path, peer
):
    app = create_app(Settings(data=tmp_path)).state.accounts.get(1)
    key = populate(app, peer)
    for identity in range(2, 122):
        app.sync.ingest(
            Message(
                account_id=1,
                peer=peer,
                message_id=identity,
                revision=1,
                date=app.store.clock(),
                text=f"Authored {identity}",
            )
        )
    app.store.groups.observe(key, access="lost", reason="authored_loss")
    page = await app.history(key, before=90)
    assert [m["message_id"] for m in page] == list(range(89, 39, -1))
    assert (await app.direct(key, 89)).text == "Authored 89"
    assert await app.direct(key, 122) is None
    assert len(app.previews.window(key, 89)) == 58
    app.schedule_refresh(key)
    assert not app.tasks and not app.transport.exact_reads
    app.store.mutate_draft(key, 0, lambda d: d.update(text="Retained authored draft"))
    app.store.clock = lambda: page[0]["date"] + RETENTION + 1
    assert await app.history(key, before=90) == []
    assert await app.direct(key, 89) is None
    assert app.conversation(key)["messages"] == []
    assert app.store.draft(key)["text"] == "Retained authored draft"


def test_group_preview_uses_native_author_not_outgoing_flag_or_private_rewrite(
    tmp_path,
):
    app = create_app(Settings(data=tmp_path)).state.accounts.get(1)
    key = populate(app, author="Team channel", author_peer="channel:42", outgoing=True)
    source = app.store.message(key, 1)
    app.sync.ingest(source.model_copy(update={"author_signature": "Moderator"}))
    row = app.previews.recent("group")[0]
    assert (
        row.preview.original
        == row.preview.ai
        == "Team channel · Moderator: A native original"
    )
    source = app.store.message(key, 1)
    app.sync.ingest(
        source.model_copy(
            update={"author": "", "author_peer": None, "author_signature": None}
        )
    )
    assert (
        app.previews.recent("group")[0].preview.ai
        == "Unknown participant: A native original"
    )
    app.sync.ingest(
        app.store.message(key, 1).model_copy(update={"author_peer": "user:1"})
    )
    assert app.previews.recent("group")[0].preview.ai == "You: A native original"


def test_cached_recent_group_remains_readable_after_loss_without_native_sync(tmp_path):
    app = create_app(Settings(data=tmp_path)).state.accounts.get(1)
    key = populate(app)
    app.store.mutate_draft(key, 0, lambda d: d.update(text="Keep this authored draft"))
    app.store.groups.observe(key, access="lost", reason="authored_loss")
    assert not app.store.recent_chats("group")
    assert key not in {c["key"] for c in app.store.sync_targets()}
    assert [c.key for c in app.previews.recent("group")] == [key]
    value = app.conversation(key)
    assert value["draft"]["text"] == "Keep this authored draft"
    assert value["messages"][0]["text"] == "A native original"
    assert value["group_processing"]["access"] == "lost"
    assert not value["group_processing"]["effective"]
    assert not app.engine.calls and not app.transport.exact_reads


def test_cached_group_list_requires_retained_work_and_keeps_seven_day_activity(
    tmp_path,
):
    app = create_app(Settings(data=tmp_path)).state.accounts.get(1)
    now = [app.store.clock()]
    app.store.clock = lambda: now[0]
    key = "1/chat:43"
    app.store.put_chat(
        Chat(
            account_id=1,
            peer="chat:43",
            title="Authored unknown-access group",
            kind="group",
        ),
        activity=now[0],
    )
    assert not app.previews.recent("group")
    blank = app.store.mutate_draft(key, 0, lambda d: None)
    assert not app.previews.recent("group")
    app.store.mutate_draft(
        key, blank["revision"], lambda d: d.update(text="Retained authored text")
    )
    assert [row.key for row in app.previews.recent("group")] == [key]
    assert not app.store.recent_chats("group")
    now[0] += 7 * 86400 + 1
    assert not app.previews.recent("group")
    assert app.store.draft(key)["text"] == "Retained authored text"
    assert not app.engine.calls and not app.transport.exact_reads


def test_group_control_is_durable_separate_from_master_and_typed_account_peers(
    tmp_path,
):
    server = create_app(Settings(data=tmp_path))
    manager = server.state.accounts
    manager._create(manager.registry.register(2, "second.synthetic.session"))
    now = [2_000_000_000.0]
    for identity in (1, 2):
        manager.get(identity).store.clock = lambda: now[0]
        for peer in ("chat:42", "channel:42"):
            populate(manager.get(identity), peer)
    app = manager.get(1)
    key = "1/chat:42"
    _, sequence = app.store.changes(0)
    assert app.set_group_processing(key, True).effective
    first = app.store.groups.get(key).first_enabled_at
    assert first == now[0]
    assert not app.group_processing("1/channel:42").processing_enabled
    assert not manager.get(2).group_processing("2/chat:42").processing_enabled
    manager.set_processing(1, False)
    held = app.group_processing(key)
    assert (
        held.processing_enabled
        and not held.account_processing_enabled
        and not held.effective
    )
    app.store.groups.observe(key, access="lost", reason="authored_loss")
    manager.set_processing(1, True)
    assert (
        app.group_processing(key).processing_enabled
        and not app.group_processing(key).effective
    )
    app.store.groups.observe(key, access="available", reason="authored_return")
    assert app.group_processing(key).effective
    now[0] += 5 * 3600
    app.set_group_processing(key, False)
    app.set_group_processing(key, True)
    assert app.store.groups.get(key).first_enabled_at == first
    reopened = Store(app.store.path, account_id=1, shared_path=manager.registry.path)
    assert reopened.groups.get(key).processing_enabled
    assert reopened.groups.get(key).first_enabled_at == first
    scopes, _ = app.store.changes(sequence)
    assert key in scopes


def test_authenticated_group_api_kind_controls_and_cached_sources(tmp_path):
    settings = Settings(data=tmp_path)
    server = create_app(settings)
    server.state.accounts._create(
        server.state.accounts.registry.register(2, "second.synthetic.session")
    )
    app = server.state.accounts.get(1)
    key = populate(app)
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(server, base_url=origin["Origin"]) as client:
        assert client.get("/api/accounts/1/chats?kind=group").status_code == 401
        trust_browser(client, settings.data)
        assert all(
            c["kind"] == "private" for c in client.get("/api/accounts/1/chats").json()
        )
        rows = client.get("/api/accounts/1/chats?kind=group").json()
        assert [c["key"] for c in rows] == [key]
        assert client.get("/api/accounts/1/chats?kind=broadcast").status_code == 422
        route = "/api/accounts/1/chats/chat:42/processing"
        before = client.get("/api/settings").json()
        assert not client.get(route).json()["processing_enabled"]
        saved = client.post(route, json={"enabled": True}, headers=origin)
        assert saved.status_code == 200 and saved.json()["effective"]
        after = client.get("/api/settings").json()
        assert (
            before["budget"] == after["budget"]
            and before["processing_threshold"] == after["processing_threshold"]
        )
        originals = client.post(
            "/api/accounts/1/chats/chat:42/originals/current",
            json={"identities": [1, 2]},
            headers=origin,
        )
        assert originals.status_code == 200 and originals.json()["missing"] == [2]
        assert originals.json()["messages"][0]["author"] == "Alice"
        assert (
            client.get("/api/accounts/1/chats/user:101/processing").status_code == 400
        )
        assert client.get("/api/accounts/2/chats/chat:42").status_code == 400


@pytest.mark.parametrize("enabled", [1, 0, "true", None, [], {}])
def test_group_switch_refuses_coerced_values_without_changing_policy(tmp_path, enabled):
    settings = Settings(data=tmp_path)
    server = create_app(settings)
    app = server.state.accounts.get(1)
    key = populate(app)
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, settings.data)
        result = client.post(
            "/api/accounts/1/chats/chat:42/processing",
            json={"enabled": enabled},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert result.status_code == 422
    assert not app.store.groups.get(key).processing_enabled
    assert app.store.groups.get(key).first_enabled_at is None
    with pytest.raises(Refused):
        app.group_processing("1/user:101")
