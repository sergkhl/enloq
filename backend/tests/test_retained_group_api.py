"""GB9 trusted cache navigation for authored exact inactive group peers."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat, Message
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import RETENTION, WINDOW, Store
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser


@pytest.fixture(params=["chat:42", "channel:42"])
def retained_rig(tmp_path, request, monkeypatch):
    now = [2_000_000_000.0]
    directory = tmp_path / "app"
    registry = AccountRegistry(directory)
    store = Store(
        directory / "rewriting.sqlite3",
        account_id=1,
        clock=lambda: now[0],
        shared_path=registry.path,
    )
    peer, key = request.param, f"1/{request.param}"
    store.put_chat(
        Chat(account_id=1, peer=peer, title="Authored exact group", kind="group"),
        activity=now[0],
    )
    store.groups.observe(key, access="available", reason="authored_native")
    server = create_app(Settings(data=directory), store=store)
    app = server.state.accounts.get(1)
    server.state.accounts.set_processing(1, False)

    async def hold_worker():
        # These HTTP admission checks keep accepted work before native dispatch.
        # Dispatch and reviewed Resume have their own authored worker tests.
        await asyncio.Future()

    monkeypatch.setattr(app.delivery, "run", hold_worker)
    monkeypatch.setattr(app.deletion, "run", hold_worker)
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, directory)
        yield SimpleNamespace(
            app=app,
            client=client,
            server=server,
            directory=directory,
            now=now,
            peer=peer,
            key=key,
            base=f"/api/accounts/1/chats/{peer}",
            retained="/api/accounts/1/retained-groups",
        )


def authored_source(r, identity=1, **values):
    source = Message(
        account_id=1,
        peer=r.peer,
        message_id=identity,
        revision=1,
        date=r.now[0],
        text=f"Authored cached original {identity}.",
        author="Authored participant",
        author_peer="user:7",
    ).model_copy(update=values)
    r.app.sync.ingest(
        source,
        target=False,
        group_epoch=r.app.store.groups.intake(r.key).epoch,
    )
    r.app.transport.messages.append(source)
    return r.app.store.message(r.key, identity)


def save_draft(r, text="Exact authored saved draft", **values):
    return r.app.store.mutate_draft(
        r.key,
        r.app.store.draft(r.key)["revision"],
        lambda draft: draft.update(text=text, **values),
    )


def native_calls(r):
    transport = r.app.transport
    return {
        name: list(getattr(transport, name))
        for name in (
            "reads",
            "exact_reads",
            "downloads",
            "avatar_reads",
            "catalog_calls",
            "catalog_downloads",
            "deletion_calls",
            "group_send_reviews",
        )
    } | {"deliveries": dict(transport.deliveries)}


def test_retained_navigation_requires_trusted_browser_and_local_origin(retained_rig):
    r = retained_rig
    save_draft(r)
    r.now[0] += WINDOW + 1
    cookie = r.client.cookies.get(r.server.state.browser_auth.cookie)
    r.client.cookies.clear()
    refused = r.client.get(r.retained)
    assert refused.status_code == 401 and "Exact authored" not in refused.text
    wrong_origin = r.client.get(
        r.retained, headers={"Origin": "https://authored.invalid"}
    )
    assert wrong_origin.status_code == 403
    r.client.cookies.set(r.server.state.browser_auth.cookie, cookie)
    allowed = r.client.get(r.retained)
    assert allowed.status_code == 200 and allowed.headers["cache-control"] == "no-store"
    assert allowed.json()[0]["chat"]["key"] == r.key
    session = r.server.state.browser_auth.authenticate(cookie)
    r.server.state.browser_auth.revoke(session)
    revoked = r.client.get(r.retained)
    assert (
        revoked.status_code == 401 and revoked.headers["clear-site-data"] == '"cache"'
    )
    assert "Exact authored" not in revoked.text
    assert not any(native_calls(r).values()) and not r.app.engine.calls


def test_retained_index_exposes_reason_counts_without_draft_or_operation_content(
    retained_rig,
):
    r = retained_rig
    accepted = []
    for text in ("Authored cancelled submission", "Authored unresolved submission"):
        draft = save_draft(r, text)
        result = r.client.post(r.base + "/send", json={"revision": draft["revision"]})
        assert result.status_code == 200, result.text
        accepted.append(result.json()["operation"])
    cancelled = r.client.post(
        f"/api/accounts/1/operations/{accepted[0]['id']}/cancel", json={}
    )
    assert cancelled.status_code == 200
    saved = save_draft(r, "Private authored draft words", undo="Private authored Undo")
    r.app.store.groups.set_processing_enabled(r.key, True)
    before = r.app.store.groups.get(r.key)
    r.now[0] += WINDOW + 1
    active = r.client.get("/api/accounts/1/chats?kind=group")
    retained = r.client.get(r.retained)
    assert active.status_code == retained.status_code == 200
    assert active.json() == []
    assert len(retained.json()) == 1
    entry = retained.json()[0]
    assert entry["chat"]["peer"] == r.peer and entry["chat"]["key"] == r.key
    assert entry["reasons"] == ["draft", "send", "cancelled_send"]
    assert entry["draft"] is True and entry["selected_media"] is False
    assert entry["send_count"] == entry["cancelled_count"] == 1
    assert entry["deletion_count"] == 0
    encoded = json.dumps(entry)
    for private in (saved["text"], saved["undo"], *(op["text"] for op in accepted)):
        assert private not in encoded
    view = r.client.get(r.base)
    assert view.status_code == 200 and view.json()["retained"] is True
    assert view.json()["draft"]["text"] == saved["text"]
    assert {op["text"] for op in view.json()["operations"]} == {
        op["text"] for op in accepted
    }
    assert r.app.store.draft(r.key) == saved
    assert r.app.store.groups.get(r.key) == before
    assert not any(native_calls(r).values()) and not r.app.engine.calls


@pytest.mark.parametrize("access", ["available", "lost", "unknown", "migrated"])
def test_retained_current_window_history_and_exact_reads_never_backfill(
    retained_rig, access
):
    r = retained_rig
    for identity in (1, 2, 3):
        authored_source(r, identity)
    authored_source(r, 2, revision=2, text="Authored edited cached original.")
    saved = save_draft(r, reply_to=1, undo="Authored pre-rewrite words")
    r.now[0] += WINDOW + 1
    r.app.store.groups.observe(
        r.key,
        access="lost" if access == "migrated" else access,
        reason="authored_state",
        migrated_to="channel:84" if access == "migrated" else None,
    )
    # A native-only item must not appear or cause an exact/history read.
    r.app.transport.messages.append(
        Message(
            account_id=1,
            peer=r.peer,
            message_id=99,
            revision=1,
            date=r.now[0],
            text="Authored native-only item that must remain unread.",
        )
    )
    state = r.app.store.groups.get(r.key)
    activity = r.app.store.rows("SELECT activity FROM chats WHERE key=?", (r.key,))
    view = r.client.get(r.base)
    assert view.status_code == 200, view.text
    assert view.json()["retained"] is True
    assert [m["message_id"] for m in view.json()["messages"]] == [1, 2, 3]
    current = r.client.post(
        r.base + "/originals/current", json={"identities": [1, 2, 99]}
    )
    assert current.status_code == 200
    assert [m["message_id"] for m in current.json()["messages"]] == [1, 2]
    assert current.json()["missing"] == [99]
    window = r.client.get(r.base + "/window?around=2")
    assert window.status_code == 200
    assert [m["message_id"] for m in window.json()] == [1, 2, 3]
    history = r.client.get(r.base + "/history?before=3")
    assert history.status_code == 200
    assert [m["message_id"] for m in history.json()] == [2, 1]
    exact = r.client.get(r.base + "/originals/1")
    assert exact.status_code == 200 and exact.json()["message_id"] == 1
    missing = r.client.get(r.base + "/originals/99")
    assert missing.status_code == 200 and missing.json() is None
    versions = r.client.get(r.base + "/originals/2/versions")
    assert versions.status_code == 200
    assert versions.json()[0]["message"]["text"] == "Authored cached original 2."
    assert r.app.store.draft(r.key) == saved
    assert r.app.store.groups.get(r.key) == state
    assert (
        r.app.store.rows("SELECT activity FROM chats WHERE key=?", (r.key,)) == activity
    )
    assert not any(native_calls(r).values()) and not r.app.engine.calls
    assert not r.app.store.rows("SELECT * FROM reservations")


@pytest.mark.parametrize("supported", [True, False])
def test_inactive_no_work_or_unsupported_group_has_no_read_workspace(
    retained_rig, supported
):
    r = retained_rig
    authored_source(r)
    if not supported:
        save_draft(r, "Saved owner words remain protected")
        r.app.store.groups.observe(
            r.key, supported=False, access="lost", reason="authored_forum"
        )
    r.now[0] += WINDOW + 1
    assert r.client.get(r.retained).json() == []
    for endpoint in (
        "",
        "/window?around=1",
        "/history",
        "/originals/1",
        "/originals/1/versions",
    ):
        result = r.client.get(r.base + endpoint)
        assert result.status_code == 400, (endpoint, result.text)
        assert "Authored cached original" not in result.text
    result = r.client.post(r.base + "/originals/current", json={"identities": [1]})
    assert result.status_code == 400 and "Authored cached original" not in result.text
    if not supported:
        assert r.app.store.draft(r.key)["text"] == "Saved owner words remain protected"
    assert not any(native_calls(r).values()) and not r.app.engine.calls


@pytest.mark.parametrize(
    "scope",
    ["account", "typed_peer", "unknown_peer", "invalid_peer", "revoked_account"],
)
def test_retained_workspace_does_not_cross_account_or_exact_typed_peer(
    retained_rig, scope
):
    r = retained_rig
    authored_source(r)
    save_draft(r)
    r.now[0] += WINDOW + 1
    base = r.base
    if scope == "account":
        base = base.replace("accounts/1", "accounts/2")
        assert (
            r.client.get(r.retained.replace("accounts/1", "accounts/2")).status_code
            == 400
        )
    elif scope == "typed_peer":
        base = base.replace(r.peer, "channel:42" if r.peer == "chat:42" else "chat:42")
    elif scope == "unknown_peer":
        base = base.replace(r.peer, "chat:84")
    elif scope == "invalid_peer":
        base = base.replace(r.peer, "42")
    else:
        r.server.state.accounts.registry.set_state(1, "requires_login")
        assert r.client.get(r.retained).status_code == 400
    for endpoint in ("", "/history", "/window?around=1", "/originals/1"):
        result = r.client.get(base + endpoint)
        assert result.status_code == 400, result.text
        assert "Authored cached original" not in result.text
        assert "Exact authored saved draft" not in result.text
    result = r.client.post(base + "/originals/current", json={"identities": [1]})
    assert result.status_code == 400 and "Authored cached original" not in result.text
    assert not any(native_calls(r).values()) and not r.app.engine.calls


def test_retained_group_rejects_new_native_send_catalog_and_composer_rewrite(
    retained_rig,
):
    r = retained_rig
    authored_source(r)
    before = save_draft(r)
    catalog = r.client.get(r.base + "/catalog")
    assert catalog.status_code == 200
    r.now[0] += WINDOW + 1
    r.server.state.accounts.set_processing(1, True)
    actions = (
        ("/rewrite", {"revision": before["revision"]}),
        ("/send", {"revision": before["revision"]}),
        ("/processing", {"enabled": True}),
        ("/catalog/refresh", {"revision": catalog.json()["revision"]}),
        ("/catalog/search/open", {}),
        (
            "/selected-media",
            {
                "revision": before["revision"],
                "token": "a" * 32,
                "catalog_revision": catalog.json()["revision"],
            },
        ),
    )
    for endpoint, body in actions:
        result = r.client.post(r.base + endpoint, json=body)
        assert result.status_code == 400, (endpoint, result.text)
    assert r.client.get(r.base + "/catalog").status_code == 400
    assert r.app.store.draft(r.key) == before
    assert not r.app.store.groups.get(r.key).processing_enabled
    assert not r.app.store.rows("SELECT * FROM operations")
    assert not r.app.store.rows("SELECT * FROM jobs")
    assert not r.app.store.rows("SELECT * FROM reservations")
    assert not any(native_calls(r).values()) and not r.app.engine.calls


def test_source_expiry_keeps_exact_draft_operations_and_spending(retained_rig):
    r = retained_rig
    authored_source(r)
    draft = save_draft(r, "Authored unresolved exact send")
    submitted = r.client.post(r.base + "/send", json={"revision": draft["revision"]})
    assert submitted.status_code == 200
    saved = save_draft(r, undo="Authored preserved Undo", reply_to=1)
    # Authored historical accounting evidence in both protected purpose ledgers.
    with r.app.store.transaction() as db:
        for identity, family in (
            ("authored-daily", "daily"),
            ("authored-group", "group_briefing"),
        ):
            db.execute(
                "INSERT INTO reservations (id,bucket,engine,reserved,actual,status,created,family) VALUES (?,'day:2033-05-18','mimo',100,17,'receipted',?,?)",
                (identity, r.now[0], family),
            )
            db.execute(
                "INSERT INTO shared.model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created,family) VALUES (1,?,?,?,?,'day:2033-05-18','mimo',100,17,'receipted',?,?)",
                (
                    identity,
                    r.key,
                    "group_briefing"
                    if family == "group_briefing"
                    else "outgoing_rewrite",
                    identity,
                    r.now[0],
                    family,
                ),
            )
            db.execute(
                "INSERT INTO receipts (id,role,model,provider,usage,error,family) VALUES (?,'authored','authored-model','authored-provider','{}',NULL,?)",
                (identity, family),
            )
            db.execute(
                "INSERT INTO shared.model_receipts (account_id,id,role,model,provider,usage,error,family) VALUES (1,?,'authored','authored-model','authored-provider','{}',NULL,?)",
                (identity, family),
            )
    ledgers = (
        "reservations",
        "receipts",
        "shared.model_reservations",
        "shared.model_receipts",
    )
    spending = {table: r.app.store.rows(f"SELECT * FROM {table}") for table in ledgers}
    operation = r.app.store.send_operations.get(submitted.json()["operation"]["id"])
    r.now[0] += RETENTION + 1
    r.app.store.expire()
    retained = r.client.get(r.retained)
    assert retained.status_code == 200 and retained.json()[0]["reasons"] == [
        "draft",
        "send",
    ]
    view = r.client.get(r.base)
    assert view.status_code == 200 and view.json()["messages"] == []
    assert view.json()["draft"]["text"] == saved["text"]
    assert view.json()["operations"][0]["text"] == operation["text"]
    assert r.client.get(r.base + "/history").json() == []
    assert r.client.get(r.base + "/window?around=1").json() == []
    assert r.client.get(r.base + "/originals/1").json() is None
    current = r.client.post(r.base + "/originals/current", json={"identities": [1]})
    assert current.json() == {"messages": [], "missing": [1]}
    assert r.app.store.draft(r.key) == saved
    assert r.app.store.send_operations.get(operation["id"]) == operation
    assert {
        table: r.app.store.rows(f"SELECT * FROM {table}") for table in ledgers
    } == spending
    assert not any(native_calls(r).values()) and not r.app.engine.calls
