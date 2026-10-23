"""Transactions reuse one connection per thread instead of reparsing the schema."""

import asyncio
import gc
import sqlite3
import threading

import pytest
from enloq.storage import store as store_module
from enloq.storage.accounts import AccountRegistry
from enloq.storage.events import commits
from enloq.storage.store import Store


@pytest.fixture
def opened(monkeypatch):
    calls = []
    connect = sqlite3.connect

    def counting(*args, **kwargs):
        calls.append(threading.get_ident())
        return connect(*args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", counting)
    return calls


def test_repeated_transactions_on_one_thread_open_one_connection(tmp_path, opened):
    registry = AccountRegistry(tmp_path)
    store = Store(
        tmp_path / "1/rewriting.sqlite3", account_id=1, shared_path=registry.path
    )
    opened.clear()
    for _ in range(50):
        store.rows("SELECT count(*) AS n FROM jobs")
        registry.rows("SELECT count(*) AS n FROM metadata")
        # The attached registry stays readable through the reused connection.
        store.rows("SELECT count(*) AS n FROM shared.metadata")
    assert opened == []
    with store.transaction() as db:
        db.execute("INSERT INTO metadata VALUES ('probe','1')")
    assert store.rows("SELECT value FROM metadata WHERE key='probe'") == [
        {"value": "1"}
    ]


def test_failed_transaction_rolls_back_and_the_connection_stays_usable(tmp_path):
    store = Store(tmp_path / "1/rewriting.sqlite3", account_id=1)
    with pytest.raises(RuntimeError), store.transaction() as db:
        db.execute("INSERT INTO metadata VALUES ('probe','1')")
        raise RuntimeError("abandoned")
    assert store.rows("SELECT value FROM metadata WHERE key='probe'") == []
    with store.transaction() as db:
        db.execute("INSERT INTO metadata VALUES ('probe','2')")
    assert store.rows("SELECT value FROM metadata WHERE key='probe'") == [
        {"value": "2"}
    ]


async def test_each_thread_has_its_own_connection_closed_when_it_ends(tmp_path, opened):
    store = Store(tmp_path / "1/rewriting.sqlite3", account_id=1)
    opened.clear()
    held = []

    def work():
        store.rows("SELECT count(*) AS n FROM jobs")
        held.append(store.connections.local.held.db)

    await asyncio.to_thread(work)
    await asyncio.to_thread(work)
    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    del thread
    gc.collect()
    assert 1 <= len(set(opened)) <= 3
    # The ended thread's connection is closed with it.
    with pytest.raises(sqlite3.ProgrammingError):
        held[-1].execute("SELECT 1")


def test_close_reconnects_later_transactions(tmp_path, opened):
    store = Store(tmp_path / "1/rewriting.sqlite3", account_id=1)
    first = store.connections.local.held.db
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")
    opened.clear()
    assert store.rows("SELECT count(*) AS n FROM jobs") == [{"n": 0}]
    assert len(opened) == 1


async def test_commits_wake_all_browsers_from_threads_but_not_reads_or_rollbacks(
    tmp_path,
):
    store = Store(tmp_path / "1/rewriting.sqlite3", account_id=1)
    with commits.subscribe() as first, commits.subscribe() as second:
        store.rows("SELECT count(*) AS n FROM jobs")
        with pytest.raises(RuntimeError), store.transaction() as db:
            db.execute("INSERT INTO metadata VALUES ('probe','discarded')")
            raise RuntimeError("rollback")
        await asyncio.sleep(0)
        assert not first.is_set() and not second.is_set()

        def commit():
            with store.transaction() as db:
                db.execute("INSERT INTO metadata VALUES ('probe','committed')")

        await asyncio.to_thread(commit)
        await asyncio.wait_for(asyncio.gather(first.wait(), second.wait()), 1)
        assert store.rows("SELECT value FROM metadata WHERE key='probe'") == [
            {"value": "committed"}
        ]
    first.clear()
    second.clear()
    with store.transaction() as db:
        db.execute("DELETE FROM metadata WHERE key='probe'")
    await asyncio.sleep(0)
    assert not first.is_set() and not second.is_set()


def folders(root):
    return {
        path: path.stat().st_mtime_ns
        for path in [root, *root.rglob("*")]
        if path.is_dir()
    }


def test_transactions_that_write_nothing_create_no_file_beside_the_stores(tmp_path):
    registry = AccountRegistry(tmp_path)
    store = Store(
        tmp_path / "1/rewriting.sqlite3", account_id=1, shared_path=registry.path
    )
    before, sequence = folders(tmp_path), commits.sequence
    for _ in range(50):
        store.rows("SELECT count(*) AS n FROM shared.metadata")
        registry.rows("SELECT count(*) AS n FROM metadata")
        with store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('account_id','other')")
            db.execute("UPDATE metadata SET value='x' WHERE key='absent'")
            db.execute("DELETE FROM shared.metadata WHERE key='absent'")
    # A journal created and removed for the two files would touch a folder.
    assert folders(tmp_path) == before
    assert commits.sequence == sequence
    assert store.rows("SELECT value FROM metadata WHERE key='account_id'") == [
        {"value": "1"}
    ]


def test_changes_no_row_count_shows_are_still_committed(tmp_path):
    registry = AccountRegistry(tmp_path)
    path = tmp_path / "1/rewriting.sqlite3"
    store = Store(path, account_id=1, shared_path=registry.path)
    with store.transaction() as db:
        db.execute("CREATE TABLE authored_probe (value TEXT)")
    with store.transaction() as db:
        db.execute("ALTER TABLE authored_probe ADD COLUMN extra TEXT")
    with store.transaction() as db:
        db.execute("SAVEPOINT authored")
        db.execute("INSERT INTO authored_probe VALUES ('kept','')")
        db.execute("RELEASE authored")
    with store.transaction() as db:
        db.execute("PRAGMA application_id=7")
    store.close()
    with sqlite3.connect(path) as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(authored_probe)")] == [
            "value",
            "extra",
        ]
        assert db.execute("SELECT value FROM authored_probe").fetchall() == [("kept",)]
        assert db.execute("PRAGMA application_id").fetchone() == (7,)


def test_commit_sequence_advances_only_with_changing_commits(tmp_path):
    store = Store(tmp_path / "1/rewriting.sqlite3", account_id=1)
    sequence = commits.sequence
    store.rows("SELECT count(*) AS n FROM jobs")
    with pytest.raises(RuntimeError), store.transaction() as db:
        db.execute("INSERT INTO metadata VALUES ('probe','discarded')")
        raise RuntimeError("rollback")
    assert commits.sequence == sequence
    with store.transaction() as db:
        db.execute("INSERT INTO metadata VALUES ('probe','committed')")
    assert commits.sequence == sequence + 1


def test_group_rows_reused_within_a_transaction_never_outlive_a_write(tmp_path):
    from enloq.domain.models import Chat

    store = Store(tmp_path / "1/rewriting.sqlite3", account_id=1)
    key = "1/channel:42"
    store.put_chat(
        Chat(account_id=1, peer="channel:42", title="Authored group", kind="group")
    )
    with store.transaction() as db:
        first = store.groups.get(key, db=db)
        assert store.groups.get(key, db=db) is first
        changed = first.model_copy(update={"reason": "authored_change"})
        db.execute(
            "UPDATE group_state SET body=? WHERE chat_key=?",
            (changed.model_dump_json(), key),
        )
        assert store.groups.get(key, db=db).reason == "authored_change"
        db.execute("SAVEPOINT authored")
        db.execute(
            "UPDATE group_state SET body=? WHERE chat_key=?",
            (first.model_dump_json(), key),
        )
        assert store.groups.get(key, db=db).reason == first.reason
        db.execute("ROLLBACK TO authored")
        assert store.groups.get(key, db=db).reason == "authored_change"
    assert store.groups.get(key).reason == "authored_change"
    with store.transaction() as db:
        db.execute("DELETE FROM group_state WHERE chat_key=?", (key,))
        db.execute("DELETE FROM group_intake WHERE chat_key=?", (key,))
        db.execute("UPDATE chats SET body=json_set(body,'$.kind','private')")
        with pytest.raises(Exception, match="Group unavailable"):
            store.groups.get(key, db=db)


async def test_unchanged_chat_and_off_group_rows_keep_derived_answers(tmp_path):
    from enloq.domain.models import Chat

    store = Store(tmp_path / "1/rewriting.sqlite3", account_id=1)
    group = Chat(account_id=1, peer="channel:42", title="Authored group", kind="group")
    key = group.key
    store.put_chat(group, activity=10)
    store.groups.observe(key, access="available", reason="authored_native")
    epoch = store.groups.intake(key).epoch
    store.groups.checkpoint(key, 5, epoch=epoch)
    with commits.subscribe() as browser:
        sequence, before = commits.sequence, folders(tmp_path)
        # A sync cycle reports the same dialog and the same checkpoint again.
        store.put_chat(group, activity=10)
        store.put_chat(group, activity=3)
        store.groups.checkpoint(key, 5, epoch=epoch)
        await asyncio.sleep(0)
        assert commits.sequence == sequence and folders(tmp_path) == before
        assert not browser.is_set()
        # Intake of a group whose AI is Off changes rows no planning reads.
        store.groups.begin_replay(key, epoch=epoch)
        store.groups.checkpoint(key, 6, epoch=epoch)
        store.groups.observe(key, access="available", reason="authored_reread")
        await asyncio.sleep(0)
        assert commits.sequence == sequence and browser.is_set()
        assert store.groups.intake(key).pts == 6
        assert not store.groups.intake(key).replay_pending
        assert store.groups.get(key).reason == "authored_reread"
        # Opting in, and everything after it, is announced.
        store.groups.set_processing_enabled(key, True)
        assert commits.sequence == sequence + 1
        store.groups.begin_replay(key, epoch=epoch)
        assert commits.sequence == sequence + 2
        store.groups.set_processing_enabled(key, False)
        assert commits.sequence == sequence + 3
        store.put_chat(group.model_copy(update={"title": "Renamed"}), activity=10)
        store.put_chat(group.model_copy(update={"title": "Renamed"}), activity=11)
        assert commits.sequence == sequence + 5
    assert store.chat(key).title == "Renamed"
