"""Authored calendar-day grouping and cache conversion; no live services."""

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from enloq.application.accounts import AccountManager
from enloq.application.blocks import enqueue, rebuild, regroup_cached
from enloq.application.conversations import Conversations
from enloq.config import Settings
from enloq.domain.models import Chat
from enloq.domain.source_revision import fingerprint
from enloq.inference.base import EngineResponse
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import WINDOW, Store
from enloq.transport.fake import FakeTransport

from backend.tests.test_rebuild import message

# These cases exercise the rewrite path with text (ADR 54 keeps it for
# voice notes, images and calming).
pytestmark = pytest.mark.usefixtures("text_rewriting")

KEY = "1/user:42"
ZONE = "Asia/Ho_Chi_Minh"


def local_time(day=6, hour=12, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo(ZONE)).timestamp()


def application(tmp_path, now, *, timezone=ZONE):
    registry = AccountRegistry(tmp_path)
    registry.set_processing_threshold(0)
    store = Store(
        tmp_path / "1/rewriting.sqlite3",
        account_id=1,
        clock=lambda: now[0],
        timezone=timezone,
        shared_path=registry.path,
    )
    store.put_chat(Chat(account_id=1, peer="user:42", title="Authored", kind="private"))
    return Conversations(store, FakeTransport())


def legacy_group(app, ids, *, state="ready", due=None):
    sources = [app.store.message(KEY, i) for i in ids]
    revision = fingerprint(sources)
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO blocks (id,chat_key,revision,sources,rendered,rendered_revision,state,date,guidance,unanalysed,error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                sources[0].key,
                KEY,
                revision,
                json.dumps(ids),
                "Authored prior rewrite" if state == "ready" else None,
                revision if state == "ready" else None,
                state,
                sources[-1].date,
                4 if state == "ready" else None,
                "[]" if state == "ready" else None,
                "Authored failure" if state in {"failed", "unknown"} else None,
            ),
        )
        if due is not None:
            enqueue(db, sources[0].key, revision, due)


def cached_sources(app, *sources, read_through=99):
    app.store.put_chat(
        app.store.chat(KEY).model_copy(update={"read_through": read_through}),
        activity=app.store.clock(),
    )
    for source in sources:
        app.store.apply_message(source, intake="initial")


def blocks(app):
    return app.store.rows(
        "SELECT * FROM blocks WHERE chat_key=? ORDER BY date,id", (KEY,)
    )


def pending(app):
    return app.store.rows(
        "SELECT * FROM jobs WHERE role='incoming_rewrite' AND state='queued' AND scope IN (SELECT id FROM blocks WHERE chat_key=?)",
        (KEY,),
    )


def test_four_minute_gap_and_utc_day_change_do_not_split_the_app_day(tmp_path):
    now = [local_time()]
    app = application(tmp_path, now)
    # UTC midnight occurs within one local calendar day.
    app.sync.ingest(message(app, 1, date=local_time(hour=6, minute=59)))
    app.sync.ingest(message(app, 2, date=local_time(hour=7, minute=3)))
    assert [json.loads(b["sources"]) for b in blocks(app)] == [[1, 2]]
    assert len(pending(app)) == 1


@pytest.mark.parametrize("timezone,expected", [(ZONE, [[1], [2]]), ("UTC", [[1, 2]])])
def test_configured_calendar_day_controls_membership(tmp_path, timezone, expected):
    now = [local_time(day=7)]
    app = application(tmp_path, now, timezone=timezone)
    app.sync.ingest(message(app, 1, date=local_time(hour=23, minute=59)))
    app.sync.ingest(message(app, 2, date=local_time(day=7, hour=0, minute=1)))
    assert [json.loads(b["sources"]) for b in blocks(app)] == expected


@pytest.mark.parametrize("separator", ["outgoing", "service"])
def test_outgoing_and_service_messages_split_even_after_long_pauses(
    tmp_path, separator
):
    now = [local_time()]
    app = application(tmp_path, now)
    app.sync.ingest(message(app, 1))
    app.sync.ingest(message(app, 2, **{separator: True}))
    now[0] += 240
    app.sync.ingest(message(app, 3))
    assert [json.loads(b["sources"]) for b in blocks(app)] == [[1], [3]]


def test_deleted_first_source_keeps_identity_on_later_rebuilds(tmp_path):
    now = [local_time()]
    app = application(tmp_path, now)
    app.sync.ingest(message(app, 1))
    app.sync.ingest(message(app, 2))
    app.sync.deleted("user:42", [1])
    app.sync.ingest(message(app, 3))
    assert blocks(app)[0]["id"] == KEY + "/1"
    assert json.loads(blocks(app)[0]["sources"]) == [2, 3]
    rebuild(app.store, KEY)
    assert len(pending(app)) == 1


def test_cached_merge_inherits_later_ai_work_and_resumes_conversion_once(tmp_path):
    now = [local_time()]
    app = application(tmp_path, now)
    cached_sources(
        app,
        message(app, 1, date=now[0] - 600),
        message(app, 2, date=now[0] - 240),
    )
    legacy_group(app, [1], state="original")
    legacy_group(app, [2])
    regroup_cached(app.store)
    group = blocks(app)[0]
    assert json.loads(group["sources"]) == [1, 2]
    assert group["state"] == "queued"
    assert group["rendered"] is group["rendered_revision"] is None
    assert group["guidance"] is None and group["unanalysed"] is None
    first_jobs = app.store.rows("SELECT * FROM jobs")
    assert len(first_jobs) == 1
    assert json.loads(first_jobs[0]["payload"]) == {"class": "catchup"}
    regroup_cached(app.store)
    # Simulate interruption after the per-chat commit but before the pass marker.
    with app.store.transaction() as db:
        db.execute("DELETE FROM metadata WHERE key='incoming_grouping'")
    regroup_cached(app.store)
    restarted = application(tmp_path, now)
    regroup_cached(restarted.store)
    assert restarted.store.rows("SELECT * FROM jobs") == first_jobs
    assert not restarted.engine.calls and not restarted.transport.exact_reads
    assert not app.notifications.rows()


def test_cached_cross_midnight_group_splits_and_regenerates_both_subsets(tmp_path):
    now = [local_time(day=7)]
    app = application(tmp_path, now)
    cached_sources(
        app,
        message(app, 1, date=local_time(hour=23, minute=59)),
        message(app, 2, date=local_time(day=7, hour=0, minute=1)),
    )
    legacy_group(app, [1, 2])
    regroup_cached(app.store)
    assert [json.loads(b["sources"]) for b in blocks(app)] == [[1], [2]]
    assert all(b["rendered"] is None and b["state"] == "queued" for b in blocks(app))
    assert len(pending(app)) == 2
    regroup_cached(app.store)
    assert len(pending(app)) == 2


@pytest.mark.parametrize("read_through,expected", [(0, "queued"), (99, "original")])
def test_cached_sources_without_prior_blocks_admit_only_unread(
    tmp_path, read_through, expected
):
    now = [local_time()]
    app = application(tmp_path, now)
    cached_sources(app, message(app, 1), read_through=read_through)
    regroup_cached(app.store)
    assert blocks(app)[0]["state"] == expected
    assert len(pending(app)) == (1 if expected == "queued" else 0)


@pytest.mark.parametrize("state", ["ready", "queued", "failed", "unknown", "original"])
def test_unchanged_groups_keep_result_failure_and_pending_identity(tmp_path, state):
    now = [local_time()]
    app = application(tmp_path, now)
    cached_sources(app, message(app, 1, date=now[0] - 600))
    legacy_group(app, [1], state=state, due=now[0] + 20 if state == "queued" else None)
    before = blocks(app), app.store.rows("SELECT * FROM jobs")
    # The source-read upgrade adds only the verified native-revision snapshot.
    before[0][0]["source_revisions"] = '{"1": 1}'
    regroup_cached(app.store)
    rebuild(app.store, KEY)
    assert (blocks(app), app.store.rows("SELECT * FROM jobs")) == before


@pytest.mark.parametrize(
    "age,old_state,read_through,eligible,expected",
    [
        (0, "original", 99, True, "original"),
        (0, "original", 0, True, "queued"),
        (0, "ready", 99, True, "queued"),
        (0, "ready", 99, False, "original"),
        (8 * 86400, "ready", 0, True, "original"),
        (8 * 86400, "original", 0, True, "original"),
    ],
)
def test_conversion_respects_unread_prior_work_target_and_seven_day_gate(
    tmp_path, age, old_state, read_through, eligible, expected
):
    now = [local_time()]
    app = application(tmp_path, now)
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"ai_eligible": eligible}))
    cached_sources(
        app,
        message(app, 1, date=now[0] - age - 600),
        message(app, 2, date=now[0] - age - 240),
        read_through=read_through,
    )
    legacy_group(app, [1], state=old_state)
    legacy_group(app, [2], state=old_state)
    regroup_cached(app.store)
    group = blocks(app)[0]
    assert group["state"] == expected
    assert group["rendered"] is None
    assert len(pending(app)) == (1 if expected == "queued" else 0)
    if age:
        assert app.rewriting.eligible(group, explicit=True)
        assert not app.rewriting.eligible(group)


def test_one_expired_source_keeps_the_complete_changed_group_click_only(tmp_path):
    now = [local_time()]
    app = application(tmp_path, now)
    cached_sources(
        app,
        message(app, 1, date=now[0] - WINDOW - 60),
        message(app, 2, date=now[0] - WINDOW + 60),
        read_through=0,
    )
    legacy_group(app, [1])
    legacy_group(app, [2])
    regroup_cached(app.store)
    assert json.loads(blocks(app)[0]["sources"]) == [1, 2]
    assert blocks(app)[0]["state"] == "original" and not pending(app)


def test_conversion_preserves_quiet_deadlines_of_all_merged_jobs(tmp_path):
    now = [local_time()]
    app = application(tmp_path, now)
    cached_sources(app, message(app, 1, date=now[0] - 600), message(app, 2))
    legacy_group(app, [1], state="queued", due=now[0] + 23)
    legacy_group(app, [2], state="queued", due=now[0] + 44)
    regroup_cached(app.store)
    assert len(pending(app)) == 1 and pending(app)[0]["due"] == now[0] + 44
    assert len(app.store.rows("SELECT * FROM jobs WHERE state='superseded'")) == 2


def test_split_preserves_prior_wait_even_when_first_subset_has_nothing_to_rewrite(
    tmp_path,
):
    now = [local_time(day=7)]
    app = application(tmp_path, now)
    cached_sources(
        app,
        message(app, 1, date=local_time(hour=23, minute=59), text="", media="document"),
        message(app, 2, date=local_time(day=7, hour=0, minute=1)),
    )
    legacy_group(app, [1, 2], state="queued", due=now[0] + 90)
    regroup_cached(app.store)
    assert [b["state"] for b in blocks(app)] == ["nothing", "queued"]
    assert len(pending(app)) == 1 and pending(app)[0]["due"] == now[0] + 90


def test_conversion_preserves_native_state_memory_derivatives_draft_and_spending(
    tmp_path,
):
    now = [local_time()]
    app = application(tmp_path, now)
    cached_sources(app, message(app, 1, date=now[0] - 600), message(app, 2))
    legacy_group(app, [1])
    legacy_group(app, [2])
    app.store.viewing.baseline(KEY, 0, 2)
    app.memory.add(KEY, 0, "Authored owner memory")
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="Authored unsent draft"))
    with app.store.transaction() as db:
        db.execute(
            "UPDATE source_views SET ready=?,viewed=? WHERE message_id=1",
            (now[0], now[0]),
        )
        db.execute("UPDATE source_views SET ready=? WHERE message_id=2", (now[0],))
        db.execute(
            "INSERT INTO derivatives VALUES (?,1,?)",
            (KEY + "/1", '{"image_context":"Authored cached context"}'),
        )
        db.execute(
            "INSERT INTO analyzed (key,chat_key,message_id,date) VALUES (?,?,1,?)",
            (KEY + "/1", KEY, now[0] - 600),
        )
        db.execute(
            "INSERT INTO shared.model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created) VALUES (1,'prior',?,'incoming_rewrite','prior','day:2026-10-06','muse',100,73,'receipted',?)",
            (KEY + "/1", now[0]),
        )
        db.execute(
            "INSERT INTO shared.model_receipts (account_id,id,role,model,provider,usage,error) VALUES (1,'prior','incoming_rewrite','synthetic','meta','{}',NULL)"
        )
    tables = [
        "messages",
        "source_views",
        "viewing_baselines",
        "memory_entries",
        "memory_state",
        "analyzed",
        "derivatives",
        "drafts",
        "reservations",
        "receipts",
        "message_versions",
        "incoming_notices",
        "shared.model_reservations",
        "shared.model_receipts",
    ]
    before = {
        table: app.store.rows(f"SELECT * FROM {table} ORDER BY rowid")
        for table in tables
    }
    regroup_cached(app.store)
    assert {
        table: app.store.rows(f"SELECT * FROM {table} ORDER BY rowid")
        for table in tables
    } == before
    assert (
        not app.transport.reads
        and not app.transport.deliveries
        and not app.engine.calls
    )


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_changed_membership_hides_partial_and_rejects_late_output_with_receipt(
    tmp_path,
):
    now = [local_time()]
    app = application(tmp_path, now)
    entered, release = asyncio.Event(), asyncio.Event()

    class Engine:
        name, model, provider = "muse", "synthetic", "meta"
        calls = 0

        async def generate_stream(self, role, packet, on_text):
            self.calls += 1
            on_text("Authored incomplete rewrite")
            if len(packet["messages"]) == 1:
                entered.set()
                await release.wait()
            on_text("Authored late rewrite")
            return EngineResponse(
                {"text": "Complete authored group", "image_context": []}, actual_nanos=7
            )

    app.engine = Engine()
    app.sync.ingest(message(app, 1))
    now[0] += 30
    running = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 2)
    assert app.conversation(KEY)["blocks"][0]["stream"]
    app.sync.ingest(message(app, 2))
    assert app.conversation(KEY)["blocks"][0]["stream"] is None
    assert blocks(app)[0]["rendered"] is None
    release.set()
    await running
    assert blocks(app)[0]["rendered"] is None
    assert app.store.rows("SELECT status FROM reservations") == [
        {"status": "receipted"}
    ]
    now[0] += 29
    await app.process_due()
    assert app.engine.calls == 1
    now[0] += 1
    await app.process_due()
    assert app.engine.calls == 2
    assert blocks(app)[0]["state"] == "ready"
    assert json.loads(blocks(app)[0]["sources"]) == [1, 2]


async def test_oversized_coherent_group_fails_without_splitting_or_calling(tmp_path):
    now = [local_time()]
    app = application(tmp_path, now)
    for identity in range(1, 6):
        app.sync.ingest(message(app, identity, text="x" * 5000))
        now[0] += 240
    await app.process_due()
    assert len(blocks(app)) == 1
    assert json.loads(blocks(app)[0]["sources"]) == list(range(1, 6))
    assert blocks(app)[0]["state"] == "failed"
    assert "exceeds the text bound" in blocks(app)[0]["error"]
    assert blocks(app)[0]["rendered"] is None
    assert all(len(m.text) == 5000 for m in app.store.messages(KEY))
    assert not app.engine.calls and not app.store.rows("SELECT * FROM reservations")


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_startup_conversion_stays_behind_account_role_and_budget_gates(tmp_path):
    manager = AccountManager(Settings(data=tmp_path))
    manager.set_processing_threshold(0)
    app = manager.get(1)
    now = [local_time()]
    app.store.clock = lambda: now[0]
    app.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    )
    cached_sources(
        app, message(app, 1, date=now[0] - 600), message(app, 2, date=now[0] - 240)
    )
    legacy_group(app, [1])
    legacy_group(app, [2])
    manager.set_processing(1, False)
    manager.registry.set_budget(0)
    for _ in range(3):
        manager.registry.record_format("incoming_rewrite", False)

    class Engine:
        name, model, provider = "muse", "synthetic", "meta"

        async def generate(self, role, packet):
            raise AssertionError("No dispatch beyond a held gate")

    app.engine = Engine()
    try:
        await manager._start(1)
        assert len(pending(app)) == 1
        await app.process_due()
        assert app.conversation(KEY)["blocks"][0]["stage"] == "paused"
        manager.set_processing(1, True)
        await app.process_due()
        assert app.conversation(KEY)["blocks"][0]["stage"] == "format_paused"
        manager.registry.keep_guidance("incoming_rewrite")
        await app.process_due()
        assert app.conversation(KEY)["blocks"][0]["stage"] == "budget"
        assert not app.store.rows("SELECT * FROM reservations")
        assert not manager.registry.usage()
    finally:
        await manager.close()
