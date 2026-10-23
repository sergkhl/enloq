"""E6: explicit processing of existing messages within the 30-day cache."""

import json
import time

import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings
from enloq.domain.models import Chat, Message, Refused
from enloq.inference.base import EngineResponse
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.store import WINDOW

from backend.tests.test_parallel import settle
from backend.tests.test_rebuild import fixture, image_bytes, message

# These cases exercise the rewrite path with text (ADR 54 keeps it for
# voice notes, images and calming).
pytestmark = pytest.mark.usefixtures("text_rewriting")

DAY = 86400


@pytest.fixture
def rig(tmp_path):
    manager = AccountManager(Settings(data=tmp_path))
    manager.set_processing_threshold(0)
    now = [time.time()]
    app = manager.get(1)
    app.store.clock = lambda: now[0]
    app.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    )
    return manager, app, now


def history(app, now, ages, *, start=1):
    """Read, never-processed blocks at the given ages in days, oldest first."""
    identity = start
    for age in ages:
        for outgoing in (False, True):
            app.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:42",
                    message_id=identity,
                    revision=1,
                    date=now[0] - age * DAY + (identity % 2),
                    text="Authored reply." if outgoing else f"Request from {age} days.",
                    outgoing=outgoing,
                ),
                target=False,
            )
            identity += 1
    # A recent message keeps the chat in the recent list.
    app.sync.ingest(
        Message(
            account_id=1,
            peer="user:42",
            message_id=identity,
            revision=1,
            date=now[0],
            text="Latest own message.",
            outgoing=True,
        ),
        target=False,
    )


async def test_action_queues_only_never_processed_blocks_newest_first(rig):
    manager, app, now = rig
    history(app, now, [25, 12, 9, 2])
    blocks = app.conversation("1/user:42")["blocks"]
    with app.store.transaction() as db:
        db.execute("UPDATE blocks SET state='failed' WHERE id=?", (blocks[1]["id"],))
        db.execute("UPDATE blocks SET state='ready' WHERE id=?", (blocks[3]["id"],))
    assert app.conversation("1/user:42")["older"] == {"available": 2, "notice": None}
    order = []
    original = app.engine.generate

    async def generate(role, packet):
        if role == "incoming_rewrite":
            order.append(packet["messages"][0]["text"])
        return await original(role, packet)

    app.engine.generate = generate
    manager.registry.set_concurrency(1)
    assert app.rewriting.process_older("1/user:42") == 2
    assert all(
        json.loads(j["payload"])["class"] == "older"
        for j in app.store.rows("SELECT payload FROM jobs WHERE state='queued'")
    )
    # Its progress is the explicit run's (ADR 28: Rewriting earlier messages).
    assert app.conversation("1/user:42")["progress"] == {
        "done": 0,
        "total": 2,
        "explicit": True,
    }
    await settle(manager)
    assert app.conversation("1/user:42")["progress"] is None
    assert order == ["Request from 9 days.", "Request from 25 days."]
    states = [b["state"] for b in app.conversation("1/user:42")["blocks"]]
    assert states == ["ready", "failed", "ready", "ready"]
    assert app.conversation("1/user:42")["older"]["available"] == 0


async def test_new_incoming_work_preempts_the_older_run(rig):
    manager, app, now = rig
    history(app, now, [20, 15, 10])
    manager.registry.set_concurrency(1)
    order = []
    original = app.engine.generate

    async def generate(role, packet):
        if role == "incoming_rewrite":
            order.append(packet["messages"][0]["text"])
        return await original(role, packet)

    app.engine.generate = generate
    app.rewriting.process_older("1/user:42")
    app.sync.ingest(message(app, 50, text="A brand new request."))
    with app.store.transaction() as db:
        db.execute(
            "UPDATE jobs SET due=? WHERE json_extract(payload,'$.class')='new'",
            (now[0] - 1,),
        )
    await settle(manager)
    assert order[0] == "A brand new request." and len(order) == 4


async def test_budget_reserve_stops_the_run_with_its_count(rig):
    manager, app, now = rig
    history(app, now, [20, 15, 10, 9, 8])
    manager.registry.set_budget(4 * FLAT_UNKNOWN)  # $0.04: reserve below $0.03.
    manager.registry.set_memory_enabled(False)  # Only rewrites spend here.
    manager.registry.set_concurrency(1)

    class Muse:
        name, model, provider = "muse", "exact-test-route", "meta"

        async def generate(self, role, packet):
            return EngineResponse(
                {"text": "Rewritten.", "image_context": []}, actual_nanos=FLAT_UNKNOWN
            )

    app.engine = Muse()
    app.rewriting.process_older("1/user:42")
    await settle(manager)
    data = app.conversation("1/user:42")
    assert [b["state"] for b in data["blocks"]].count("ready") == 3
    assert data["older"] == {
        "available": 2,
        "notice": {"remaining": 2, "reason": "budget"},
    }
    await settle(manager)
    assert [b["state"] for b in data["blocks"]].count("ready") == 3  # No resume.


async def test_restart_keeps_the_run_and_off_ends_it(rig, tmp_path):
    manager, app, now = rig
    history(app, now, [20, 15, 10])
    app.rewriting.process_older("1/user:42")
    restarted = AccountManager(Settings(data=tmp_path))
    again = restarted.get(1)
    again.store.clock = lambda: now[0]
    again.store.recover()
    assert len(again.store.rows("SELECT * FROM jobs WHERE state='queued'")) == 3
    restarted.set_processing(1, False)
    data = again.conversation("1/user:42")
    assert data["older"]["notice"] == {"remaining": 3, "reason": "off"}
    assert {b["state"] for b in data["blocks"]} == {"original"}
    restarted.set_processing(1, True)
    await settle(restarted)
    assert not again.engine.calls  # Another click is needed.


async def test_older_targets_use_target_relative_local_context(rig):
    manager, app, now = rig
    for identity, age in ((1, 29), (2, 22), (3, 21), (4, 20)):
        app.sync.ingest(
            Message(
                account_id=1,
                peer="user:42",
                message_id=identity,
                revision=1,
                date=now[0] - age * DAY,
                text=f"Context {age} days ago.",
                outgoing=identity != 4,
            ),
            target=False,
        )
    app.sync.ingest(message(app, 9, outgoing=True, text="Recent own message."))
    reads = []
    app.transport.history = lambda *a: reads.append(a)
    app.transport.exact = lambda *a: reads.append(a)
    assert app.rewriting.process_older("1/user:42") == 1
    await settle(manager)
    context = [m["text"] for m in app.engine.calls[0][1]["context"]]
    assert context == ["Context 22 days ago.", "Context 21 days ago."]
    assert not reads
    assert app.conversation("1/user:42")["blocks"][0]["state"] == "ready"


async def test_editing_an_older_processed_source_withdraws_its_rendering(rig):
    manager, app, now = rig
    history(app, now, [12])
    assert app.rewriting.process_older("1/user:42") == 1
    await settle(manager)
    assert app.conversation("1/user:42")["blocks"][0]["rendered"]
    edited = app.store.message("1/user:42", 1).model_copy(
        update={"revision": 2, "text": "Edited later."}
    )
    app.sync.ingest(edited)
    after = app.conversation("1/user:42")["blocks"][0]
    assert after["rendered"] is None and after["state"] == "original"
    assert not app.store.rows("SELECT * FROM jobs WHERE state='queued'")


async def test_cached_history_pages_need_no_telegram_read(tmp_path):
    a = fixture(tmp_path)
    a.store.put_chat(a.store.chat("1/user:42"), activity=a.store.clock())
    a.transport.messages = [
        message(a, i, date=a.store.clock() - i, outgoing=True) for i in range(1, 121)
    ]
    await a.sync.scan("1/user:42")
    await a.sync.scan("1/user:42")
    reads = []
    original = a.transport.history

    async def counted(*args):
        reads.append(args)
        return await original(*args)

    a.transport.history = counted
    page = await a.history("1/user:42", before=100)
    assert [m["message_id"] for m in page] == list(range(99, 49, -1))
    assert not reads
    older = await a.history("1/user:42", before=40)
    assert len(reads) == 1 and older[0]["message_id"] == 39


async def test_load_originals_does_not_restore_a_floor_cleared_during_its_read(
    tmp_path,
):
    from enloq.application.sync import coverage

    a = fixture(tmp_path)
    a.store.put_chat(a.store.chat("1/user:42"), activity=a.store.clock())
    a.transport.messages = [
        message(a, i, date=a.store.clock() - i, outgoing=True) for i in range(1, 121)
    ]
    await a.sync.scan("1/user:42")
    assert coverage(a.store, "1/user:42") == 21
    original = a.transport.history

    async def restarted(*args):
        # A gap restart clears every floor while this page is being read.
        with a.store.transaction() as db:
            db.execute("DELETE FROM cursors WHERE key LIKE 'coverage:%'")
        return await original(*args)

    a.transport.history = restarted
    page = await a.history("1/user:42", before=30)
    assert page[0]["message_id"] == 29
    assert coverage(a.store, "1/user:42") is None


async def test_context_images_are_described_once_and_not_resent(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.transport.media["user:42", 1] = image_bytes()
    a.sync.ingest(message(a, 1, outgoing=True, text="", media="image"))
    a.sync.ingest(message(a, 2, text="Is this the right one?"))
    now[0] += 30
    await a.process_due()
    assert [i["message_id"] for i in a.engine.calls[0][1]["images"]] == [1]
    await a.process_due()  # The Emotion Classification that follows it.
    a.sync.ingest(message(a, 3, outgoing=True, text="Let me check."))
    a.sync.ingest(message(a, 4, text="And the size?"))
    now[0] += 30
    await a.process_due()
    second = a.engine.calls[1][1]
    assert not second["images"]
    assert [m for m in second["context"] if m["id"] == 1][0]["image_context"]


def test_explicit_range_is_thirty_days_and_background_stays_seven(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a, 1, date=now[0] - 12 * DAY), target=False)
    a.sync.ingest(message(a, 2, outgoing=True))
    block = a.rewriting.block(a.conversation("1/user:42")["blocks"][0]["id"])
    assert not a.rewriting.eligible(block)
    assert a.rewriting.eligible(block, explicit=True)
    assert a.rewriting.process_older("1/user:42") == 1
    a.rewriting.stop("1/user:42")
    now[0] += WINDOW + 1  # The chat is no longer a recent chat.
    assert not a.rewriting.eligible(block, explicit=True)
    assert a.rewriting.process_older("1/user:42") == 0
    with pytest.raises(Refused):
        a.rewriting.retry(block["id"])
