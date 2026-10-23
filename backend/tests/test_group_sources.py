"""Complete local grouped reading across the bounded native-message page."""

import json

import pytest
from enloq.api.server import create_app
from enloq.application import blocks
from enloq.application.blocks import (
    GROUPING_VERSION,
    SOURCE_REVISIONS_VERSION,
    hydrate_source_revisions,
    rebuild,
    regroup_cached,
)
from enloq.application.conversations import Conversations
from enloq.config import Settings
from enloq.domain.models import Chat, Conflict, MediaInfo, Message, Refused
from enloq.storage.store import RETENTION, SCHEMA_VERSION, WINDOW, Store
from enloq.transport.fake import FakeTransport
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"
BASE = "/api/accounts/1/chats/user:42"


def seed_group(app, count=205, *, date=None):
    attachments = {
        1: ("document", MediaInfo(file_name="earlier-file.pdf", size=120)),
        2: ("image", MediaInfo(file_name="earlier-image.jpg", width=20, height=10)),
        3: ("audio", MediaInfo(file_name="voice.ogg", voice=True)),
        4: ("video_note", MediaInfo(duration=7)),
        5: ("video", MediaInfo(file_name="clip.mp4")),
        6: ("location", MediaInfo()),
        7: ("sticker", MediaInfo(sticker="static", emoji="🙂")),
    }
    for identity in range(1, count + 1):
        media, info = attachments.get(identity, (None, None))
        app.store.apply_message(
            message(
                app,
                identity,
                date=app.store.clock() if date is None else date,
                text=f"Authored source {identity}" if not media else "",
                media=media,
                media_info=info,
                attachment_id=f"authored:{identity}" if media else None,
            ),
            intake="history",
        )
    rebuild(app.store, f"{app.store.account_id}/user:42", target=False)
    return app.store.rows("SELECT * FROM blocks")[0]


def unchanged_state(app):
    return {
        table: app.store.rows(f"SELECT * FROM {table} ORDER BY rowid")
        for table in (
            "messages",
            "blocks",
            "jobs",
            "source_views",
            "message_versions",
            "derivatives",
            "drafts",
            "media_files",
            "changes",
            "memory_entries",
            "memory_state",
            "analyzed",
            "reservations",
            "receipts",
            "operations",
            "cursors",
        )
    }


def test_complete_sources_cross_native_page_with_earlier_file_and_image(tmp_path):
    app = fixture(tmp_path)
    block = seed_group(app)
    initial = app.conversation(KEY)
    assert initial["messages"][0]["message_id"] == 106
    assert len(initial["blocks"][0]["sources"]) == 205
    assert initial["blocks"][0]["source_revisions"] == {
        str(i): 1 for i in range(1, 206)
    }
    before = unchanged_state(app)
    pages = []
    cursor = 0
    while cursor is not None:
        page = app.source_page(KEY, 1, block["revision"], cursor=cursor)
        pages.append(page)
        assert page["block_id"] == block["id"]
        assert page["revision"] == block["revision"]
        assert page["total"] == 205
        assert not page["missing"]
        assert len(page["sources"]) == len(page["messages"]) <= 100
        cursor = page["next_cursor"]
    sources = [source for page in pages for source in page["sources"]]
    originals = [original for page in pages for original in page["messages"]]
    assert sources == [m["message_id"] for m in originals] == list(range(1, 206))
    assert [page["complete"] for page in pages] == [False, False, True]
    assert [page["next_cursor"] for page in pages] == [100, 200, None]
    assert originals[0]["media"] == "document"
    assert originals[0]["media_info"]["file_name"] == "earlier-file.pdf"
    assert originals[1]["media"] == "image"
    assert originals[1]["media_info"]["file_name"] == "earlier-image.jpg"
    assert [m["media"] for m in originals[2:7]] == [
        "audio",
        "video_note",
        "video",
        "location",
        "sticker",
    ]
    assert len({m["message_id"] for m in originals if m["media"]}) == 7
    for original in originals:
        current = app.store.message(KEY, original["message_id"])
        assert (
            original["viewed_content"]
            == app.store.viewing.original(current).model_dump()
        )
    assert unchanged_state(app) == before
    assert not app.transport.reads
    assert not app.transport.exact_reads
    assert not app.transport.downloads
    assert not app.engine.calls


def test_each_source_read_materializes_at_most_one_hundred_bodies(
    tmp_path, monkeypatch
):
    app = fixture(tmp_path)
    block = seed_group(app)
    parse = Message.model_validate_json
    observed = []

    def counted(cls, value, **kwargs):
        observed.append(value)
        return parse(value, **kwargs)

    monkeypatch.setattr(Message, "model_validate_json", classmethod(counted))
    page = app.source_page(KEY, 1, block["revision"], cursor=100)
    assert len(observed) == len(page["messages"]) == 100
    observed.clear()
    page = app.source_page(KEY, 1, block["revision"], cursor=200)
    assert len(observed) == len(page["messages"]) == 5


@pytest.mark.parametrize("change", ["append", "edit", "delete", "remove"])
def test_next_page_rejects_changed_or_removed_membership(tmp_path, change):
    app = fixture(tmp_path)
    block = seed_group(app, 110)
    first = app.source_page(KEY, 1, block["revision"])
    assert first["next_cursor"] == 100
    if change == "append":
        app.sync.ingest(message(app, 111), target=False, arrival=False)
    elif change == "edit":
        app.sync.ingest(
            message(app, 99, revision=2, text="Changed source"),
            target=False,
            arrival=False,
        )
    elif change == "delete":
        app.sync.deleted("user:42", [1])
    else:
        app.sync.deleted("user:42", list(range(1, 111)))
    with pytest.raises(Conflict, match="Message group changed"):
        app.source_page(KEY, 1, block["revision"], cursor=100)
    current = app.store.rows("SELECT * FROM blocks WHERE id=?", (block["id"],))
    if change != "remove":
        fresh = app.source_page(KEY, 1, current[0]["revision"])
        assert fresh["sources"][0] == (2 if change == "delete" else 1)
        assert fresh["total"] == (
            111 if change == "append" else 109 if change == "delete" else 110
        )
        assert fresh["revision"] != first["revision"]
    else:
        assert not current


def test_missing_or_expired_sources_keep_slots_without_fetching(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    block = seed_group(app, 3)
    with app.store.transaction() as db:
        db.execute("DELETE FROM messages WHERE chat_key=? AND message_id=2", (KEY,))
    page = app.source_page(KEY, 1, block["revision"], limit=2)
    assert page["sources"] == [1, 2]
    assert [m["message_id"] for m in page["messages"]] == [1]
    assert page["missing"] == [2]
    assert page["total"] == 3
    assert page["next_cursor"] == 2 and not page["complete"]
    now[0] += RETENTION + 1
    expired = app.source_page(KEY, 1, block["revision"])
    assert expired["sources"] == expired["missing"] == [1, 2, 3]
    assert not expired["messages"]
    assert expired["complete"] and expired["next_cursor"] is None
    assert not app.transport.exact_reads
    assert not app.transport.downloads
    assert not app.engine.calls


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_native_mutation_before_group_rebuild_cannot_validate_old_pages(
    tmp_path, change
):
    app = fixture(tmp_path)
    block = seed_group(app, 110)
    app.source_page(KEY, 1, block["revision"])
    source = app.store.message(KEY, 1)
    # Intake has committed a changed first source, while its synchronous
    # projection callback has not yet rebuilt the group. The next page does
    # not itself contain that source and must still reject the old snapshot.
    app.store.apply_message(
        source.model_copy(
            update={
                "revision": 2,
                "text": "Changed native content",
                "deleted": change == "delete",
            }
        )
    )
    assert (
        app.store.rows("SELECT revision FROM blocks")[0]["revision"]
        == block["revision"]
    )
    with pytest.raises(Conflict, match="Message group changed"):
        app.source_page(KEY, 1, block["revision"], cursor=100)
    assert not app.transport.exact_reads and not app.engine.calls


@pytest.mark.parametrize("policy", ["read", "excluded", "inactive"])
def test_schema_22_hydrates_all_cached_sources_without_regeneration(tmp_path, policy):
    app = fixture(tmp_path)
    chat = app.store.chat(KEY)
    app.store.put_chat(
        chat.model_copy(
            update={
                "read_through": 205 if policy == "read" else 0,
                "ai_eligible": policy != "excluded",
            }
        )
    )
    block = seed_group(
        app, date=app.store.clock() - WINDOW - 1 if policy == "inactive" else None
    )
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="Keep authored draft"))
    app.memory.add(KEY, 0, "Keep owner memory")
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='ready',rendered='Authored ready group',rendered_revision=revision"
        )
        db.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('incoming_grouping',?)",
            (GROUPING_VERSION,),
        )
    before = unchanged_state(app)
    with app.store.transaction() as db:
        db.execute("ALTER TABLE blocks DROP COLUMN source_revisions")
        db.execute("PRAGMA user_version=22")
    app.store.close()
    reopened = Store(
        app.store.path,
        account_id=1,
        clock=app.store.clock,
        shared_path=app.store.shared_path,
    )
    upgraded = Conversations(reopened, FakeTransport())
    assert reopened.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    assert (
        reopened.rows("SELECT source_revisions FROM blocks")[0]["source_revisions"]
        is None
    )
    regroup_cached(reopened)
    hydrated = unchanged_state(upgraded)
    # Hydrating the block's metadata publishes a local projection change, and
    # schema 37 clears Chat Memory's takeaways for the start (ADR 55 Q20).
    changes = hydrated.pop("changes")
    assert [c["scope"] for c in changes] == [KEY]
    assert changes[0]["seq"] > before["changes"][0]["seq"]
    before.pop("changes")
    for table in ("memory_entries", "memory_state"):
        assert hydrated.pop(table) == []
        before.pop(table)
    assert [r["consumed"] for r in hydrated.pop("analyzed")] == [
        None for _ in before.pop("analyzed")
    ]
    assert hydrated == before
    assert json.loads(
        reopened.rows("SELECT source_revisions FROM blocks")[0]["source_revisions"]
    ) == {str(i): 1 for i in range(1, 206)}
    assert (
        reopened.rows("SELECT value FROM metadata WHERE key='incoming_grouping'")[0][
            "value"
        ]
        == GROUPING_VERSION
    )
    assert (
        reopened.rows(
            "SELECT value FROM metadata WHERE key='incoming_source_revisions'"
        )[0]["value"]
        == SOURCE_REVISIONS_VERSION
    )
    after = unchanged_state(upgraded)
    regroup_cached(reopened)
    assert unchanged_state(upgraded) == after
    assert upgraded.source_page(KEY, 1, block["revision"])["total"] == 205
    assert not upgraded.transport.exact_reads and not upgraded.engine.calls


def test_hydration_refuses_stale_fingerprint_without_rewriting(tmp_path):
    app = fixture(tmp_path)
    block = seed_group(app, 110)
    with app.store.transaction() as db:
        db.execute("UPDATE blocks SET source_revisions=NULL")
    app.store.apply_message(message(app, 100, revision=2, text="Committed edit"))
    before = unchanged_state(app)
    hydrate_source_revisions(app.store)
    assert unchanged_state(app) == before
    assert (
        app.store.rows("SELECT source_revisions FROM blocks")[0]["source_revisions"]
        is None
    )
    assert app.stages(KEY)[0]["source_revisions"] is None
    with pytest.raises(Conflict):
        app.source_page(KEY, 1, block["revision"])
    assert not app.engine.calls and not app.transport.exact_reads


def test_interrupted_snapshot_pass_resumes_without_touching_completed_group(
    tmp_path, monkeypatch
):
    app = fixture(tmp_path)
    seed_group(app, 110)
    app.store.apply_message(message(app, 111, outgoing=True), intake="history")
    app.store.apply_message(message(app, 112), intake="history")
    rebuild(app.store, KEY, target=False)
    with app.store.transaction() as db:
        db.execute("UPDATE blocks SET source_revisions=NULL")
    fingerprint = blocks.fingerprint
    calls = []

    def fail_second(sources):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("Authored interruption")
        return fingerprint(sources)

    monkeypatch.setattr(blocks, "fingerprint", fail_second)
    with pytest.raises(RuntimeError, match="Authored interruption"):
        hydrate_source_revisions(app.store)
    partial = app.store.rows("SELECT * FROM blocks ORDER BY id")
    assert partial[0]["source_revisions"] is not None
    assert partial[1]["source_revisions"] is None
    assert not app.store.rows(
        "SELECT value FROM metadata WHERE key='incoming_source_revisions'"
    )
    monkeypatch.setattr(blocks, "fingerprint", fingerprint)
    hydrate_source_revisions(app.store)
    complete = app.store.rows("SELECT * FROM blocks ORDER BY id")
    assert complete[0] == partial[0]
    assert json.loads(complete[1]["source_revisions"]) == {"112": 1}
    assert not app.store.rows("SELECT * FROM jobs")
    after = unchanged_state(app)
    hydrate_source_revisions(app.store)
    assert unchanged_state(app) == after
    assert not app.engine.calls and not app.transport.exact_reads


def test_native_versions_and_attribution_remain_bound_to_the_source(tmp_path):
    app = fixture(tmp_path)
    block = seed_group(app, 3)
    original = app.store.message(KEY, 2)
    app.sync.ingest(
        original.model_copy(
            update={
                "revision": 2,
                "attachment_id": "authored:replacement",
                "media_info": MediaInfo(file_name="replacement.jpg"),
                "forwarded": True,
                "forwarded_from": "Authored forward source",
            }
        ),
        target=False,
        arrival=False,
    )
    current = app.store.rows("SELECT * FROM blocks")[0]
    page = app.source_page(KEY, 1, current["revision"])
    native = page["messages"][1]
    assert native["revision"] == 2 and native["native_edited"]
    assert native["history_count"] == 1
    assert native["attachment_id"] == "authored:replacement"
    assert native["media_info"]["file_name"] == "replacement.jpg"
    assert native["forwarded"] and native["forwarded_from"] == "Authored forward source"
    version = app.store.history.rows(KEY, 2)[0]
    historical = app.store.history.version(KEY, 2, version["id"])
    assert historical.attachment_id == original.attachment_id
    assert historical.media_info.file_name == "earlier-image.jpg"
    with pytest.raises(Refused, match="Observed version unavailable"):
        app.store.history.version(KEY, 1, version["id"])
    assert not app.media.cache.get(historical)
    with pytest.raises(Conflict):
        app.source_page(KEY, 1, block["revision"])
    # Safe native metadata repairs do not replace the group's content revision.
    unforwarded = app.store.message(KEY, 1)
    app.store.learn_forwarding(
        unforwarded.model_copy(
            update={"forwarded": True, "forwarded_from": "File source"}
        )
    )
    repaired = app.source_page(KEY, 1, current["revision"])
    assert repaired["messages"][0]["forwarded_from"] == "File source"
    assert repaired["messages"][0]["history_count"] == 0
    assert not app.transport.exact_reads and not app.engine.calls


def test_account_chat_and_source_body_isolation(tmp_path):
    app, other = fixture(tmp_path), fixture(tmp_path, account=2)
    block, other_block = seed_group(app, 2), seed_group(other, 2)
    other.store.apply_message(message(other, 1, revision=2, text="Other account"))
    with pytest.raises(Refused, match="Conversation unavailable"):
        other.source_page(KEY, 1, block["revision"])
    with pytest.raises(Conflict):
        app.source_page(KEY, 1, other_block["revision"])
    app.store.put_chat(
        Chat(account_id=1, peer="user:43", title="Other chat", kind="private")
    )
    app.store.apply_message(
        message(app, 1, text="Other chat").model_copy(update={"peer": "user:43"})
    )
    with pytest.raises(Conflict):
        app.source_page("1/user:43", 1, block["revision"])
    page = app.source_page(KEY, 1, block["revision"])
    assert all(
        m["account_id"] == 1 and m["peer"] == "user:42" for m in page["messages"]
    )
    # Refuse corrupted metadata rather than returning another account's body.
    with app.store.transaction() as db:
        db.execute(
            "UPDATE messages SET body=? WHERE chat_key=? AND message_id=1",
            (message(other, 1).model_dump_json(), KEY),
        )
    with pytest.raises(Refused, match="Group source identity mismatch"):
        app.source_page(KEY, 1, block["revision"])


def test_http_source_page_is_authenticated_bounded_and_side_effect_free(
    tmp_path, monkeypatch
):
    app = fixture(tmp_path)
    block = seed_group(app, 110)
    server = create_app(Settings(data=tmp_path), store=app.store)
    conversation = server.state.accounts.get(1)
    server.state.accounts.set_processing(1, False)

    def unexpected(*args, **kwargs):
        raise AssertionError("A source page must only read the local cache")

    monkeypatch.setattr(conversation, "schedule_refresh", unexpected)
    monkeypatch.setattr(conversation.transport, "history", unexpected)
    monkeypatch.setattr(conversation.transport, "exact", unexpected)
    monkeypatch.setattr(conversation.media, "prefetch", unexpected)
    monkeypatch.setattr(conversation.engine, "generate", unexpected)
    client = TestClient(server, base_url="http://127.0.0.1:8765")
    path = f"{BASE}/blocks/1/sources"
    query = {"revision": block["revision"]}
    assert client.get(path, params=query).status_code == 401
    trust_browser(client, tmp_path)
    before = unchanged_state(conversation)
    response = client.get(path, params=query)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(response.json()["messages"]) == 100
    second = client.get(path, params={**query, "cursor": 100}).json()
    assert len(second["messages"]) == 10 and second["complete"]
    assert second["missing"] == [] and second["next_cursor"] is None
    assert client.get(path, params={**query, "limit": 101}).status_code == 422
    assert client.get(path, params={**query, "limit": 0}).status_code == 422
    assert client.get(path, params={**query, "cursor": -1}).status_code == 422
    assert client.get(path, params={**query, "cursor": 2**63}).status_code == 422
    assert client.get(path, params={**query, "cursor": 111}).status_code == 400
    assert client.get(path, params={"revision": "unbounded"}).status_code == 422
    assert client.get(path).status_code == 422
    assert client.get(f"{BASE}/blocks/0/sources", params=query).status_code == 422
    assert client.get(f"{BASE}/blocks/{2**63}/sources", params=query).status_code == 422
    assert client.get(path, params={"revision": "0" * 64}).status_code == 409
    assert (
        client.get(path.replace("accounts/1", "accounts/2"), params=query).status_code
        == 400
    )
    assert unchanged_state(conversation) == before
    assert not conversation.transport.reads
    assert not conversation.transport.exact_reads
    assert not conversation.transport.downloads
    assert not conversation.engine.calls
