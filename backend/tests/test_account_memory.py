"""ADR 55: Person and Account Memory, the start from the cache, Remember this
chat, and what Response Suggestions may read."""

import json
import sqlite3

import pytest
from enloq.application.memory import ACCOUNT
from enloq.domain.models import Chat, Refused
from enloq.storage.store import SCHEMA_VERSION, Store

from backend.tests.test_memory import DAY, KEY, conversation, fold, update
from backend.tests.test_rebuild import fixture, message

# Memory as before ADR 56's Off default: these chats learn without switching
# on; test_memory_default covers the default as shipped.
pytestmark = pytest.mark.usefixtures("remember_on")

OTHER = "1/user:43"


def other_chat(app):
    app.store.put_chat(
        Chat(
            account_id=1,
            peer="user:43",
            title="Another authored conversation",
            kind="private",
            ai_eligible=True,
        )
    )


async def test_the_start_learns_the_cached_month_once(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now, start=True)
    assert not app.memory.candidates(KEY)
    # Cached before the store's start, never sent to a model: admitted once.
    assert app.memory.start(KEY) == 5
    assert {r["message_id"] for r in app.memory.candidates(KEY)} == {1, 2, 3, 4, 100}
    assert {r["origin"] for r in app.memory.candidates(KEY)} == {"start"}
    assert app.memory.start(KEY) == 0
    assert app.memory.schedule(KEY)
    await fold(app)
    assert not app.memory.candidates(KEY)
    # A message after the start enters only when sent to a model.
    now[0] += 60
    app.sync.ingest(message(app, 101, text="Arrived after the start."))
    now[0] += 1800
    assert not app.memory.schedule(KEY)
    app.memory.record(KEY, [f"{KEY}/101"])
    assert app.memory.schedule(KEY)


async def test_remember_this_chat_off_takes_the_chat_out_both_ways(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    app.memory.record(KEY, [f"{KEY}/1", f"{KEY}/2"])
    await fold_now(app, now)
    app.memory.record(KEY, [f"{KEY}/3"])
    now[0] += 1800
    assert app.memory.schedule(KEY)
    assert app.memory.remember_view(KEY) == {"enabled": True}
    assert app.memory.remember(KEY, False) == {"enabled": False}
    # The waiting update stops, and what was admitted but not learned leaves.
    assert app.memory.job(KEY) is None
    assert not app.memory.candidates(KEY)
    app.memory.record(KEY, [f"{KEY}/4"])
    assert not app.memory.candidates(KEY) and not app.memory.schedule(KEY)
    # Nothing in it reads either memory; what memory holds stays.
    assert app.memory.for_suggestions(KEY) is None
    assert app.memory.for_target(KEY, 500, now[0]) is None
    assert app.memory.view(KEY)["entries"]
    # Switched on again, its cached past never starts again.
    app.memory.remember(KEY, True)
    with app.store.transaction() as db:
        db.execute("UPDATE metadata SET value=? WHERE key='memory_start'", (now[0],))
    assert not app.memory.start(KEY)
    with pytest.raises(Refused):
        app.memory.remember("1/user:999", False)


async def fold_now(app, now):
    now[0] += 1800
    assert app.memory.schedule(KEY)
    return await fold(app)


async def test_account_notes_cite_the_owner_and_keep_their_chats(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)

    async def generate(role, packet):
        app.engine.calls.append((role, packet))
        if role != "chat_memory":
            return {"add": [], "remove": []}
        return update(
            {"add": [{"text": "Training for a marathon.", "citations": [1]}]},
            {
                "add": [
                    {"text": "Moving to Vietnam until January.", "citations": [2]},
                    # Cites only the person's message: it describes someone else.
                    {"text": "Likes oysters.", "citations": [1]},
                ]
            },
        )

    app.engine.generate = generate
    app.memory.record(KEY, [f"{KEY}/1", f"{KEY}/2"])
    await fold_now(app, now)
    packet = next(p for r, p in app.engine.calls if r == "chat_memory")
    assert packet["person"]["memory"] == [] and packet["account"]["memory"] == []
    assert [e["text"] for e in app.memory.view(KEY)["entries"]] == [
        "Training for a marathon."
    ]
    account = app.memory.account_view()
    assert [e["text"] for e in account["entries"]] == [
        "Moving to Vietnam until January."
    ]
    assert account["entries"][0]["sources"] == [
        {"id": 2, "availability": "cached", "chat": "user:42"}
    ]
    assert account["revision"] == 1
    # Another chat's fold sees the account note, not that chat's originals.
    other_chat(app)
    app.sync.ingest(
        message(app, 7, text="Own words here.", outgoing=True).model_copy(
            update={"peer": "user:43"}
        ),
        target=False,
    )
    app.engine.calls.clear()
    app.memory.record(OTHER, [f"{OTHER}/7"])
    now[0] += 1800
    assert app.memory.schedule(OTHER)
    job = app.store.rows(
        "SELECT * FROM jobs WHERE role='chat_memory' AND scope=? AND state='queued'",
        (OTHER,),
    )[0]
    assert app.rewriting.claim(job)
    await app.rewriting.run(job)
    seen = next(p for r, p in app.engine.calls if r == "chat_memory")["account"]
    assert seen["memory"][0]["citations"] == []
    assert seen["memory"][0]["cited_elsewhere"] == 1
    assert {m["id"] for m in next(p for r, p in app.engine.calls)["messages"]} == {7}


async def test_a_deleted_message_removes_account_notes_citing_it(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    app.memory.record(KEY, [f"{KEY}/1", f"{KEY}/2"])
    await fold_now(app, now)
    assert app.memory.account_view()["entries"]
    revision = app.memory.account_view()["revision"]
    app.sync.deleted("user:42", [2])
    view = app.memory.account_view()
    assert not view["entries"] and view["revision"] == revision + 1


def test_account_memory_is_the_owners_to_edit(tmp_path):
    app = fixture(tmp_path)
    view = app.memory.add(ACCOUNT, 0, "I don't drink.")
    entry = view["entries"][0]
    assert entry["source"] == "owner" and entry["citations"] == []
    view = app.memory.edit(ACCOUNT, view["revision"], entry["id"], "No alcohol.")
    assert [e["text"] for e in view["entries"]] == ["No alcohol."]
    view = app.memory.remove(ACCOUNT, view["revision"], entry["id"])
    assert not view["entries"]
    app.memory.add(ACCOUNT, view["revision"], "Building Enloq.")
    assert not app.memory.clear(ACCOUNT, view["revision"] + 1)["entries"]
    # A person's memory and the account's never mix.
    assert not app.memory.view(KEY)["entries"]


async def test_suggestions_read_both_memories_said_only_where_said(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    app.memory.record(KEY, [f"{KEY}/1", f"{KEY}/2"])
    await fold_now(app, now)
    app.memory.add(ACCOUNT, app.memory.account_view()["revision"], "Written by me.")
    here = app.memory.for_suggestions(KEY)
    assert here["today"] == "2033-05-18"
    assert [p["text"] for p in here["person"]] == ["Fact number 1."]
    assert sorted((o["text"], o["may_say"]) for o in here["owner"]) == [
        ("Fact number 2.", True),
        ("Written by me.", False),
    ]
    other_chat(app)
    app.sync.ingest(
        message(app, 7, text="Hello there.").model_copy(update={"peer": "user:43"}),
        target=False,
    )
    elsewhere = app.memory.for_suggestions(OTHER)
    assert elsewhere["person"] == []
    assert [o["may_say"] for o in elsewhere["owner"]] == [False, False]
    app.memory.enabled = lambda: False
    assert app.memory.for_suggestions(KEY) is None


def test_schema_37_clears_takeaways_and_arms_the_start(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.memory.add(KEY, 0, "An old takeaway.")
    app.memory.record(KEY, [])
    path = app.store.path
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO analyzed (key,chat_key,message_id,date,consumed) VALUES ('1/user:42/9',?,9,1,'x')",
            (KEY,),
        )
        db.execute("DELETE FROM metadata WHERE key='memory_start'")
    app.store.close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version=36")
    raw.commit()
    raw.close()
    now[0] += DAY
    store = Store(
        path,
        account_id=1,
        clock=lambda: now[0],
        shared_path=tmp_path / "accounts.sqlite3",
    )
    rows = store.rows("SELECT * FROM memory_entries")
    assert not rows and not store.rows("SELECT * FROM memory_state")
    assert store.rows("SELECT consumed FROM analyzed") == [{"consumed": None}]
    assert store.rows("SELECT value FROM metadata WHERE key='memory_start'") == [
        {"value": str(now[0])}
    ]
    assert store.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION == 38
    assert not store.rows(
        "SELECT name FROM sqlite_master WHERE name='memory_consolidation'"
    )
    assert json.loads(json.dumps(store.rows("SELECT * FROM remembering"))) == []
