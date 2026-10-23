"""Authored local viewing, with no native reads, receipts or model work."""

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Conflict, Refused
from enloq.storage.store import RETENTION, WINDOW, Store
from enloq.storage.viewing import ViewedSources
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, message


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    return fixture(tmp_path, clock=lambda: now[0]), now


def rows(app):
    return {r["message_id"]: r for r in app.store.rows("SELECT * FROM source_views")}


def see(app, *ids):
    key = f"{app.store.account_id}/user:42"
    return ViewedSources(
        content=[app.store.viewing.original(app.store.message(key, i)) for i in ids]
    )


def ack(app, body):
    return app.store.viewing.acknowledge(f"{app.store.account_id}/user:42", body)


def test_baseline_waits_for_native_evidence_and_freezes_paging_snapshot(rig):
    app, now = rig
    for i in (1, 3):
        app.sync.ingest(message(app, i), target=False, arrival=False, intake="initial")
    assert all(r["candidate"] is None for r in rows(app).values())
    app.store.viewing.baseline("1/user:42", 2, 5)
    assert rows(app)[1]["viewed"] and rows(app)[3]["candidate"] == 1
    now[0] += 60
    # Telegram reads change during initial paging, including across restart.
    app.store.put_chat(
        app.store.chat("1/user:42").model_copy(update={"read_through": 9})
    )
    app.store.viewing.baseline("1/user:42", 9, 9)
    app.store.close()
    app.store = Store(
        app.store.path,
        account_id=1,
        clock=lambda: now[0],
        shared_path=app.store.shared_path,
    )
    app.sync.store = app.store
    for i in (2, 4, 6):
        app.sync.ingest(message(app, i), target=False, arrival=False, intake="initial")
    assert rows(app)[2]["viewed"]
    assert all(
        rows(app)[i]["candidate"] == 1 and rows(app)[i]["viewed"] is None
        for i in (3, 4, 6)
    )
    assert app.store.chat("1/user:42").read_through == 9
    assert not app.engine.calls


def test_initial_old_and_manual_history_never_create_candidates(rig):
    app, now = rig
    app.store.viewing.baseline("1/user:42", 0, 10)
    app.sync.ingest(message(app, 1, date=now[0] - WINDOW - 1), intake="initial")
    app.sync.ingest(message(app, 2), target=False)
    app.sync.ingest(message(app, 3, outgoing=True))
    app.sync.ingest(message(app, 4, service=True))
    assert set(rows(app)) == {1, 2}
    assert all(r["candidate"] == 0 and r["viewed"] for r in rows(app).values())


def test_view_before_readiness_is_individual_durable_and_idempotent(rig):
    app, now = rig
    for i in (3, 1, 2):
        app.sync.ingest(message(app, i))
    before = app.store.changes()[1]
    assert ack(app, see(app, 1, 3)) == {"viewed": 2}
    assert ack(app, see(app, 1, 3)) == {"viewed": 0}  # second browser
    assert rows(app)[2]["viewed"] is None
    assert rows(app)[1]["ready"] is None
    assert app.store.changes(before)[0] == ["1/user:42"]
    source = app.store.message("1/user:42", 1)
    app.sync.ingest(source.model_copy(update={"text": "Edited source"}))
    app.sync.ingest(message(app, 4))
    assert rows(app)[1]["viewed"] and rows(app)[4]["viewed"] is None
    assert app.store.chat("1/user:42").read_through == 0
    assert not app.engine.calls and not app.transport.deliveries


def test_late_original_ack_after_edit_is_rejected_atomically(rig):
    app, _ = rig
    app.sync.ingest(message(app, 1))
    app.sync.ingest(message(app, 2))
    old = see(app, 1, 2)
    app.sync.ingest(message(app, 2, text="Current revision"))
    with pytest.raises(Conflict):
        ack(app, old)
    assert all(r["viewed"] is None for r in rows(app).values())


async def test_rewrite_ack_is_bound_to_final_rendering_and_exact_sources(rig):
    app, now = rig
    app.sync.ingest(message(app, 1))
    now[0] += 31
    await app.process_due()
    await app.process_due()  # The Emotion Classification that follows it.
    with app.store.transaction() as db:
        block = db.execute("SELECT * FROM blocks").fetchone()
        content = app.store.viewing.rewrite(db, block)
    assert content
    # Same source revision but a different retry rendering cannot use the old token.
    with app.store.transaction() as db:
        db.execute("UPDATE blocks SET rendered='Another valid wording'")
    with pytest.raises(Conflict):
        ack(app, ViewedSources(content=[content]))
    with app.store.transaction() as db:
        content = app.store.viewing.rewrite(
            db, db.execute("SELECT * FROM blocks").fetchone()
        )
    app.sync.ingest(message(app, 2))
    with pytest.raises(Conflict):
        ack(app, ViewedSources(content=[content]))
    assert all(r["viewed"] is None for r in rows(app).values())
    now[0] += 31
    await app.process_due()
    with app.store.transaction() as db:
        content = app.store.viewing.rewrite(
            db, db.execute("SELECT * FROM blocks").fetchone()
        )
    assert ack(app, ViewedSources(content=[content])) == {"viewed": 2}


def test_deletion_expiry_and_historical_versions_cannot_restore_viewing(rig):
    app, now = rig
    for i in (1, 2):
        app.sync.ingest(message(app, i))
    old = see(app, 1)
    app.sync.deleted("user:42", [1])
    assert set(rows(app)) == {2}
    with pytest.raises(Conflict):
        ack(app, old)
    now[0] += RETENTION + 1
    with pytest.raises(Conflict):
        ack(
            app,
            old.model_copy(
                update={
                    "content": [old.content[0].model_copy(update={"identity": "2"})]
                }
            ),
        )
    app.store.expire()
    assert rows(app) == {}


def test_equal_native_identities_are_account_isolated(tmp_path):
    a, b = fixture(tmp_path), fixture(tmp_path, account=2)
    for app in (a, b):
        app.sync.ingest(message(app))
    with pytest.raises(Conflict):
        ack(b, see(a, 1))
    with pytest.raises(Refused):
        b.store.viewing.acknowledge("1/user:42", see(a, 1))
    ack(a, see(a, 1))
    assert rows(a)[1]["viewed"] and rows(b)[1]["viewed"] is None


def test_http_viewing_contract_is_bounded_and_requires_browser_trust(tmp_path):
    server = create_app(Settings(data=tmp_path))
    app = server.state.accounts.get(1)
    # Seeded demo is available, but use an authored original with known identity.
    key = app.store.recent_chats()[0]["key"]
    source = next(
        m for m in app.store.messages(key) if not m.outgoing and not m.deleted
    )
    content = app.store.viewing.original(source).model_dump()
    path = f"/api/accounts/1/chats/{source.peer}/viewed"
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        client.headers["Origin"] = str(client.base_url).rstrip("/")
        assert client.post(path, json={"content": [content]}).status_code == 401
        trust_browser(client, tmp_path)
        assert client.post(path, json={"content": [content] * 101}).status_code == 422
        assert client.post(path, json={"content": [content]}).status_code == 200
        assert client.post(path, json={"content": [content]}).json() == {"viewed": 0}
