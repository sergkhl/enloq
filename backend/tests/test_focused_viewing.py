"""Bounded ready-source snapshots for focused local viewing, without native reads."""

from contextlib import contextmanager

import pytest
from enloq.api.server import create_app
from enloq.application.blocks import rebuild
from enloq.config import Settings
from enloq.domain.models import (
    Chat,
    Conflict,
    Message,
    ProcessingPaused,
    Refused,
)
from enloq.storage.viewing import READY_SOURCES_SQL, ViewedSources
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_group_sources import unchanged_state
from backend.tests.test_local_viewing import ack, rows, see
from backend.tests.test_notifications import complete
from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"
BASE = "/api/accounts/1/chats/user:42"


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    return fixture(tmp_path, clock=lambda: now[0]), now


def captured(app, *, key=KEY, after=0, limit=100):
    return app.store.viewing.ready_sources(key, after=after, limit=limit)


def acknowledge(app, page, *, key=KEY):
    return app.store.viewing.acknowledge(
        key, ViewedSources.model_validate({"content": page["content"]})
    )


def identities(page):
    return [int(content["identity"]) for content in page["content"]]


def ready_group(app, now, count):
    for identity in range(count, 0, -1):
        app.store.apply_message(message(app, identity), intake="arrival")
    rebuild(app.store, KEY, target=False)
    now[0] += 31
    app.unseen.advance()  # Existing original fallback, no generation.


def test_focus_projection_reaches_offscreen_sources_in_bounded_indexed_pages(
    rig, monkeypatch
):
    app, now = rig
    ready_group(app, now, 205)
    ack(app, see(app, 201, 202, 203, 204, 205))
    initial = app.conversation(KEY)
    assert initial["messages"][0]["message_id"] == 106
    assert initial["opening_source"] == 1
    before = unchanged_state(app)
    parse = Message.model_validate_json
    materialized = []

    def counted(cls, body, **kwargs):
        materialized.append(body)
        return parse(body, **kwargs)

    monkeypatch.setattr(Message, "model_validate_json", classmethod(counted))
    pages = []
    after = 0
    while after is not None:
        materialized.clear()
        page = captured(app, after=after)
        assert len(materialized) == len(page["content"]) <= 100
        pages.append(page)
        after = page["next_cursor"]
    assert [page["next_cursor"] for page in pages] == [100, 200, None]
    assert [source for page in pages for source in identities(page)] == list(
        range(1, 201)
    )
    assert all(
        content["kind"] == "original" for page in pages for content in page["content"]
    )
    assert unchanged_state(app) == before  # GET cannot acknowledge or modify readiness.
    plan = app.store.rows(
        "EXPLAIN QUERY PLAN " + READY_SOURCES_SQL,
        (KEY, KEY, 0, now[0] - 30 * 86400, 100),
    )
    assert any("unseen_sources" in row["detail"] for row in plan)
    for page in pages:
        if page["content"]:
            acknowledge(app, page)
    assert app.previews.opening(KEY) is None
    assert captured(app) == {"content": [], "next_cursor": None}
    assert not app.transport.reads and not app.transport.exact_reads
    assert not app.engine.calls and not app.transport.deliveries


def test_projection_filters_readiness_candidates_retention_and_native_kind(rig):
    app, now = rig
    sources = (
        (1, {}, "arrival"),
        (2, {}, "arrival"),
        (3, {}, "initial"),
        (4, {}, "history"),
        (5, {}, "arrival"),
        (6, {"date": now[0] - 30 * 86400 - 1}, "arrival"),
        (7, {"outgoing": True}, "arrival"),
        (8, {"service": True}, "arrival"),
        (9, {"deleted": True}, "arrival"),
        (10, {"date": now[0] - 8 * 86400}, "arrival"),
    )
    for identity, fields, intake in sources:
        app.store.apply_message(message(app, identity, **fields), intake=intake)
    with app.store.transaction() as db:
        db.execute("UPDATE source_views SET ready=? WHERE message_id!=5", (now[0],))
        db.execute("UPDATE source_views SET viewed=? WHERE message_id=2", (now[0],))
        # Authored malformed old state exercises guards independently of intake.
        for identity in (7, 8, 9):
            db.execute(
                "INSERT INTO source_views VALUES (?,?,?,'arrival',1,?,NULL,?)",
                (f"{KEY}/{identity}", KEY, identity, now[0], now[0]),
            )
        db.execute(
            "UPDATE source_views SET candidate=1,viewed=NULL WHERE message_id IN (6,10)"
        )
    before = unchanged_state(app)
    assert identities(captured(app)) == [1, 10]
    assert identities(captured(app, after=1, limit=1)) == [10]
    assert captured(app, after=10) == {"content": [], "next_cursor": None}
    assert unchanged_state(app) == before
    assert not app.engine.calls and not app.transport.reads


def test_captured_sources_cannot_swallow_later_readiness_or_arrival(rig):
    app, now = rig
    ready_group(app, now, 1)
    first = captured(app)
    app.sync.ingest(message(app, 2))
    assert identities(captured(app)) == [1]
    now[0] += 31
    complete(app)
    app.unseen.advance()
    app.sync.ingest(message(app, 3))
    assert identities(captured(app)) == [1, 2]
    assert acknowledge(app, first) == {"viewed": 1}
    assert rows(app)[2]["viewed"] is None and rows(app)[3]["viewed"] is None
    assert app.previews.opening(KEY) == 2
    second = captured(app)
    now[0] += 31
    complete(app)
    app.unseen.advance()
    assert identities(captured(app)) == [2, 3]
    assert acknowledge(app, second) == {"viewed": 1}
    assert identities(captured(app)) == [3]
    assert app.previews.opening(KEY) == 3
    assert not app.engine.calls and not app.transport.reads


def test_stale_source_batch_is_atomic_and_fresh_capture_can_retry(rig):
    app, now = rig
    ready_group(app, now, 2)
    old = captured(app)
    app.store.apply_message(message(app, 2, text="Edited after focus snapshot"))
    before = unchanged_state(app)
    with pytest.raises(Conflict):
        acknowledge(app, old)
    assert unchanged_state(app) == before
    assert all(source["viewed"] is None for source in rows(app).values())
    current = captured(app)
    assert current["content"][0] == old["content"][0]
    assert current["content"][1] != old["content"][1]
    assert acknowledge(app, current) == {"viewed": 2}
    assert acknowledge(app, current) == {"viewed": 0}
    assert app.store.chat(KEY).read_through == 0
    assert not app.transport.reads and not app.transport.exact_reads


def test_failed_commit_rolls_back_and_same_snapshot_can_retry(rig, monkeypatch):
    app, now = rig
    ready_group(app, now, 2)
    page = captured(app)
    before = unchanged_state(app)
    transaction = app.store.transaction

    @contextmanager
    def fail_after_write():
        with transaction() as db:
            changes = db.total_changes
            yield db
            if db.total_changes != changes:
                raise RuntimeError("Authored commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(app.store, "transaction", fail_after_write)
        with pytest.raises(RuntimeError, match="Authored commit failure"):
            acknowledge(app, page)
    assert unchanged_state(app) == before
    assert acknowledge(app, page) == {"viewed": 2}
    assert captured(app)["content"] == []


def test_navigation_and_equal_native_ids_keep_account_chat_isolation(tmp_path):
    now = [2_000_000_000.0]
    first, other = (
        fixture(tmp_path, clock=lambda: now[0]),
        fixture(tmp_path, account=2, clock=lambda: now[0]),
    )
    ready_group(first, now, 1)
    other.store.apply_message(message(other), intake="arrival")
    with other.store.transaction() as db:
        db.execute("UPDATE source_views SET ready=?", (now[0],))
    first.store.put_chat(
        Chat(account_id=1, peer="user:43", title="Other authored chat", kind="private")
    )
    first.store.apply_message(
        message(first).model_copy(update={"peer": "user:43"}), intake="arrival"
    )
    with first.store.transaction() as db:
        db.execute("UPDATE source_views SET ready=?", (now[0],))
    page = captured(first)
    assert page["content"] != captured(first, key="1/user:43")["content"]
    assert page["content"] != captured(other, key="2/user:42")["content"]
    with pytest.raises(Conflict):
        acknowledge(first, page, key="1/user:43")
    with pytest.raises(Conflict):
        acknowledge(other, page, key="2/user:42")
    with pytest.raises(Refused):
        captured(other)
    assert acknowledge(first, page) == {"viewed": 1}
    assert identities(captured(first, key="1/user:43")) == [1]
    assert identities(captured(other, key="2/user:42")) == [1]
    assert not first.engine.calls and not other.engine.calls


@pytest.mark.parametrize("fallback", ["off", "failed", "format", "budget", "original"])
def test_original_fallback_and_pre_readiness_view_survive_focus_projection(
    rig, fallback
):
    app, now = rig
    app.sync.ingest(message(app, 1))
    app.sync.ingest(message(app, 2))
    assert captured(app)["content"] == []
    ack(app, see(app, 1))  # Existing deliberate original viewing before readiness.
    if fallback == "off":

        def paused():
            raise ProcessingPaused("AI is Off")

        app.admit_ai = paused
    elif fallback == "budget":
        app.notifications.unavailable = lambda: True
    elif fallback == "format":
        app.rewriting.paused_roles = lambda: ["incoming_rewrite"]
    else:
        complete(app, state=fallback)
    now[0] += 31
    app.unseen.advance()
    assert identities(captured(app)) == [2]
    assert acknowledge(app, captured(app)) == {"viewed": 1}
    assert captured(app)["content"] == []
    assert not app.engine.calls and not app.transport.reads


def test_multiple_readers_and_restart_share_durable_viewing_without_receipts(
    rig, tmp_path
):
    app, now = rig
    ready_group(app, now, 3)
    first, second = captured(app), captured(app)
    assert acknowledge(app, first) == {"viewed": 3}
    assert acknowledge(app, second) == {"viewed": 0}
    app.store.close()
    restarted = fixture(tmp_path, clock=lambda: now[0])
    assert captured(restarted) == {"content": [], "next_cursor": None}
    assert all(source["viewed"] for source in rows(restarted).values())
    assert restarted.store.chat(KEY).read_through == 0
    assert not restarted.engine.calls and not restarted.transport.reads


def test_authenticated_http_readiness_is_independent_of_muted_disabled_alerts(
    rig, tmp_path, monkeypatch
):
    app, now = rig
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"muted": True}))
    ready_group(app, now, 2)
    server = create_app(Settings(data=tmp_path), store=app.store)
    conversation = server.state.accounts.get(1)
    server.state.accounts.set_processing(1, False)

    def unexpected(*args, **kwargs):
        raise AssertionError("Ready sources must only read latched local state")

    monkeypatch.setattr(conversation, "schedule_refresh", unexpected)
    monkeypatch.setattr(conversation.transport, "history", unexpected)
    monkeypatch.setattr(conversation.transport, "exact", unexpected)
    monkeypatch.setattr(conversation.engine, "generate", unexpected)
    monkeypatch.setattr(conversation.media, "prefetch", unexpected)
    monkeypatch.setattr(conversation.unseen, "advance", unexpected)
    client = TestClient(server, base_url="http://127.0.0.1:8765")
    path = BASE + "/ready-sources"
    assert client.get(path).status_code == 401
    trust_browser(client, tmp_path)
    second_reader = TestClient(server, base_url="http://127.0.0.1:8765")
    trust_browser(second_reader, tmp_path)
    assert (
        client.post(
            "/api/notifications/preferences", json={"sound": False, "push": False}
        ).status_code
        == 200
    )
    before = unchanged_state(conversation)
    response = client.get(path, params={"limit": 1})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert identities(response.json()) == [1] and response.json()["next_cursor"] == 1
    assert identities(client.get(path, params={"after": 1}).json()) == [2]
    shared_snapshot = second_reader.get(path).json()
    assert identities(shared_snapshot) == [1, 2]
    assert unchanged_state(conversation) == before
    for query in ({"after": -1}, {"after": 2**63}, {"limit": 0}, {"limit": 101}):
        assert client.get(path, params=query).status_code == 422
    assert client.get(path.replace("accounts/1", "accounts/2")).status_code == 400
    assert client.get(path.replace("user:42", "invalid")).status_code == 400
    assert client.post(
        BASE + "/viewed", json={"content": response.json()["content"]}
    ).json() == {"viewed": 1}
    assert identities(client.get(path).json()) == [2]
    assert identities(second_reader.get(path).json()) == [2]
    assert second_reader.post(
        BASE + "/viewed", json={"content": shared_snapshot["content"]}
    ).json() == {"viewed": 1}
    assert (
        client.get(path).json()
        == second_reader.get(path).json()
        == {
            "content": [],
            "next_cursor": None,
        }
    )
    assert not conversation.engine.calls and not conversation.transport.reads
    assert (
        not conversation.transport.exact_reads and not conversation.transport.deliveries
    )
