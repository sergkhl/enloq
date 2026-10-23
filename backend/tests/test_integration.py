import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings, private_write
from enloq.cutover import retire
from enloq.domain.models import Chat, Refused
from enloq.storage.accounts import AccountRegistry
from enloq.storage.lock import OwnerLock
from enloq.transport.telethon import PersonalTransport
from telethon import types

from backend.tests.test_rebuild import fixture, message

# These cases exercise the rewrite path with text (ADR 54 keeps it for
# voice notes, images and calming).
pytestmark = pytest.mark.usefixtures("text_rewriting")


def job(app, identity):
    value = dict(
        id=identity,
        scope="test",
        role="incoming_rewrite",
        input_revision=identity,
        state="queued",
        due=0,
        payload="{}",
        error=None,
    )
    with app.store.transaction() as db:
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", tuple(value.values()))
    return value


def test_shared_budget_concurrency_unknowns_and_clock_rollback(tmp_path):
    from enloq.inference.work import FLAT_UNKNOWN

    now = [2_000_000_000]
    apps = [fixture(tmp_path, account=i, clock=lambda: now[0]) for i in (1, 2)]
    registry = AccountRegistry(tmp_path)
    # ADR 15: one call's flat unknown estimate reaches a $0.01 budget.
    registry.set_budget(FLAT_UNKNOWN)
    jobs = [job(a, str(i)) for i, a in enumerate(apps)]
    barrier = Barrier(2)

    def reserve(i):
        barrier.wait()
        try:
            apps[i].rewriting.work.reserve(jobs[i], "muse")
            return True
        except Refused:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(reserve, (0, 1))) == [False, True]
    # An unknown outcome counts only toward its own budget day.
    now[0] += 2 * 86400
    apps[0].rewriting.work.reserve(job(apps[0], "later"), "muse")
    # A rolled-back wall clock stays on the later budget day.
    now[0] -= 4 * 86400
    with pytest.raises(Refused, match="budget"):
        apps[1].rewriting.work.reserve(job(apps[1], "rollback"), "muse")
    assert registry.usage()[0]["unknown"] == 2 * FLAT_UNKNOWN
    assert registry.budget(clock=lambda: now[0])["reached"]


async def test_expiry_keeps_drafts_and_unknown_sends_only(tmp_path):
    now = [2_000_000_000]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a))
    d = a.store.mutate_draft(
        "1/user:42", 0, lambda d: d.update(text="An uncertain authored send")
    )
    a.transport.lose_receipt = True
    op = await a.delivery.send("1/user:42", d["revision"])
    # The owner's next draft, written after the uncertain submission.
    a.store.mutate_draft(
        "1/user:42",
        d["revision"] + 1,
        lambda d: d.update(text="Retain this authored draft"),
    )
    with a.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES ('1/user:42/999',1,?)",
            (json.dumps({"transcript": {"text": "An older exact reply source"}}),),
        )
    now[0] += 31 * 86400
    a.store.expire()
    assert not a.store.messages("1/user:42")
    assert not a.store.rows("SELECT * FROM blocks")
    assert not a.store.rows("SELECT * FROM derivatives")
    assert a.store.send_operations.get(op["id"])["state"] == "unknown"
    assert a.store.send_operations.get(op["id"])["text"] == d["text"]
    assert a.store.draft("1/user:42")["text"] == "Retain this authored draft"
    a.store.send_operations.refuse(op["id"], "Synthetic proof of refusal")
    # Not sent waits for a deliberate Retry or Cancel, however old it is.
    a.store.expire()
    assert a.store.send_operations.get(op["id"])["state"] == "refused"
    a.delivery.cancel(op["id"])
    a.store.expire()
    assert not a.store.rows("SELECT * FROM operations")


async def test_accounts_rotate_while_another_worker_waits_and_cancel_on_logout(
    tmp_path,
):
    manager = AccountManager(Settings(data=tmp_path))
    manager.set_processing_threshold(0)
    entry = manager.registry.register(2, "two.session")
    manager._create(entry)
    started, other, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow(role, packet):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def fast(role, packet):
        other.set()
        return {"text": "Rewritten.", "image_context": []}

    for account, generate in ((1, slow), (2, fast)):
        app = manager.get(account)
        app.engine.generate = generate
        app.store.put_chat(
            Chat(account_id=account, peer="user:42", title="Authored", kind="private")
        )
        app.sync.ingest(message(app))
        with app.store.transaction() as db:
            db.execute("UPDATE jobs SET due=?", (app.store.clock() - 1,))
    await manager.start()
    try:
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(other.wait(), 2)
        await manager.logout(1)
        assert cancelled.is_set()
        assert manager.get(2).transport.connected
    finally:
        await manager.close()


async def test_gap_recovery_skips_bots_groups_but_preserves_human_dm(tmp_path):
    a = fixture(tmp_path)
    date = datetime.now(UTC)
    natives = [
        types.Message(id=i, peer_id=peer, date=date, message="Authored source")
        for i, peer in enumerate(
            [types.PeerChat(2), types.PeerUser(3), types.PeerUser(4)], 1
        )
    ]

    class Client:
        async def __call__(self, request):
            return types.updates.Difference(
                natives,
                [],
                [],
                [],
                [
                    types.User(id=3, bot=True),
                    types.User(id=4, first_name="Authored user"),
                ],
                types.updates.State(8, 0, date, 0, 0),
            )

    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id, transport.client, transport.sync = (
        a.store,
        1,
        Client(),
        a.sync,
    )
    with a.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES ('global_difference',?)",
            (json.dumps(dict(pts=4, qts=0, date=1000)),),
        )
    await transport.catch_up()
    assert [r["key"] for r in a.store.rows("SELECT key FROM messages")] == [
        "1/user:4/3"
    ]
    assert a.store.chat("1/user:4").ai_eligible
    assert a.store.chat("1/user:4").eligibility_source == "default"
    assert (
        json.loads(
            a.store.rows("SELECT value FROM cursors WHERE key='global_difference'")[0][
                "value"
            ]
        )["pts"]
        == 8
    )


def legacy_store(root):
    registry = AccountRegistry(root)
    registry.register(1, "personal.session", directory=".")
    private_write(root / "personal.session", "synthetic authorization")
    private_write(root / "browsers.sqlite3", "synthetic browser trust")
    with sqlite3.connect(root / "assistant.sqlite3") as db:
        db.executescript(
            """CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE chats(key TEXT,body TEXT); CREATE TABLE messages(body TEXT); CREATE TABLE composers(body TEXT); CREATE TABLE results(id TEXT,body TEXT); CREATE TABLE jobs(id TEXT,scope TEXT,role TEXT,input_revision TEXT); CREATE TABLE reservations(id TEXT,bucket TEXT,engine TEXT,reserved INTEGER,actual INTEGER,status TEXT,created REAL); CREATE TABLE operations(id TEXT,state TEXT,body TEXT); CREATE TABLE cursors(key TEXT,value TEXT);"""
        )
        db.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("account_id", "1"),
                ("mode", "personal"),
                ("timezone", "Asia/Ho_Chi_Minh"),
                ("budget_day", "2035-01-01"),
            ],
        )
        chat = Chat(account_id=1, peer="user:42", title="Authored peer", kind="private")
        db.execute("INSERT INTO chats VALUES (?,?)", (chat.key, chat.model_dump_json()))
        db.execute("INSERT INTO messages VALUES (?)", ("Retired correspondence",))
        db.execute("INSERT INTO composers VALUES (?)", ("Retired draft",))
        db.execute("INSERT INTO jobs VALUES ('cost','1/user:42','analysis','rev')")
        db.execute(
            "INSERT INTO reservations (id,bucket,engine,reserved,actual,status,created) VALUES ('cost','day:2034-12-31','muse',600000000,NULL,'unknown',1)"
        )
        op = dict(
            id="pending",
            account_id=1,
            chat_key=chat.key,
            peer=chat.peer,
            text="Possibly delivered",
            reply_to=None,
            random_id=77,
            created=1,
            composer_revision=1,
            state="dispatching",
        )
        db.execute(
            "INSERT INTO operations VALUES ('pending','dispatching',?)",
            (json.dumps(op),),
        )
        db.execute("INSERT INTO cursors VALUES ('receipt:77','99')")
    return registry


def test_cutover_locks_preserves_accounting_sessions_and_pending_send(tmp_path):
    registry = legacy_store(tmp_path)
    settings = Settings(mode="personal", data=tmp_path)
    lock = tmp_path / "service.lock"
    with OwnerLock(lock), pytest.raises(Refused):
        retire(settings, lock)
    with OwnerLock(tmp_path / "personal.session.owner.lock"), pytest.raises(Refused):
        retire(settings, lock)
    assert (tmp_path / "assistant.sqlite3").exists()
    first = retire(settings, lock)
    assert (
        first["unresolved_sends_preserved"] == 1
        and first["after"]["legacy_stores"] == 0
    )
    assert (tmp_path / "personal.session").read_text() == "synthetic authorization"
    assert (tmp_path / "browsers.sqlite3").read_text() == "synthetic browser trust"
    assert registry.usage()[0]["unknown"] == 600_000_000
    assert (
        registry.rows("SELECT value FROM metadata WHERE key='budget_day'")[0]["value"]
        == "2035-01-01"
    )
    with sqlite3.connect(tmp_path / "rewriting.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM drafts").fetchone()[0] == 0
        pending = json.loads(db.execute("SELECT body FROM operations").fetchone()[0])
        assert (
            pending["composer_revision"] == -1
            and pending["random_id"] == 77
            and pending["state"] == "unknown"
        )
    retire(settings, lock)
    assert registry.usage()[0]["calls"] == 1


async def test_history_peer_guard_and_direct_reply_deleted_before_send(tmp_path):
    a = fixture(tmp_path)
    a.sync.ingest(message(a))
    a.transport.messages = [message(a).model_copy(update={"peer": "user:900"})]

    async def wrong(*args):
        return a.transport.messages

    a.transport.history = wrong
    with pytest.raises(Refused, match="identity"):
        await a.history("1/user:42")
    d = a.store.mutate_draft(
        "1/user:42", 0, lambda d: d.update(text="Authored reply", reply_to=1)
    )

    with a.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES (?,?)",
            ("send_due:1/user:42", str(a.store.clock() + 0.2)),
        )
    sending = asyncio.create_task(a.delivery.send("1/user:42", d["revision"]))
    async with asyncio.timeout(2):
        while not a.store.send_operations.recent("1/user:42"):
            await asyncio.sleep(0.01)
    a.sync.deleted("user:42", [1])
    op = await sending
    assert op["state"] == "refused" and not a.transport.deliveries


def test_unregistered_offline_fixture_can_cut_over_without_authorization(tmp_path):
    legacy_store(tmp_path)
    (tmp_path / "accounts.sqlite3").unlink()
    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        db.execute("UPDATE metadata SET value='offline' WHERE key='mode'")
    result = retire(Settings(data=tmp_path), tmp_path / "service.lock")
    assert result["after"]["legacy_stores"] == 0
    assert AccountRegistry(tmp_path).get(1)["directory"] == "."


async def test_reply_to_older_original_fetches_only_exact_id_without_caching_history(
    tmp_path,
):
    a = fixture(tmp_path)
    source = message(a, 99, date=a.store.clock() - 40 * 86400, reply_to=98)
    reads = []

    async def exact(peer, identity):
        reads.append((peer, identity))
        return source

    a.transport.exact = exact
    draft = a.store.mutate_draft(
        "1/user:42",
        0,
        lambda d: d.update(text="Authored reply to an older original", reply_to=99),
    )
    op = await a.delivery.send("1/user:42", draft["revision"])
    assert op["state"] == "confirmed" and op["reply_to"] == 99
    assert reads == [("user:42", 99)]
    assert a.store.message("1/user:42", 99) is None
    assert not a.engine.calls
    draft = a.store.mutate_draft(
        "1/user:42",
        a.store.draft("1/user:42")["revision"],
        lambda d: d.update(text="Wrong parent must refuse", reply_to=97),
    )
    with pytest.raises(Refused, match="reply target"):
        await a.delivery.send("1/user:42", draft["revision"])
    assert len(a.transport.deliveries) == 1
