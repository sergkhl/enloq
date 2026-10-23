"""E5: atomic claims, one global limit, priorities, fairness and refusals."""

import asyncio
import json
import time

import pytest
from enloq.application.accounts import AccountManager
from enloq.application.blocks import enqueue
from enloq.application.capacity import CLASSES, RANK, Capacity
from enloq.config import Settings
from enloq.domain.models import Chat, MediaInfo, Message, ProcessingPaused
from enloq.inference.base import ProviderRefused
from enloq.storage.store import Store

# These cases exercise the rewrite path with text (ADR 54 keeps it for
# voice notes, images and calming).
pytestmark = pytest.mark.usefixtures("text_rewriting")


@pytest.fixture
def rig(tmp_path):
    manager = AccountManager(Settings(data=tmp_path))
    manager.set_processing_threshold(0)
    manager._create(manager.registry.register(2, "second.session"))
    now = [time.time()]
    for account in (1, 2):
        app = manager.get(account)
        app.store.clock = lambda: now[0]
        app.store.put_chat(
            Chat(account_id=account, peer="user:42", title="Authored", kind="private")
        )
    return manager, now


def blocks(app, count, *, klass=None, **changes):
    """Separate incoming blocks, each due now."""
    for index in range(count):
        values = dict(
            account_id=app.store.account_id,
            peer="user:42",
            message_id=2 * index + 1,
            revision=1,
            date=app.store.clock(),
            text=f"Separate authored request {index}.",
        )
        app.sync.ingest(Message(**(values | changes)))
        app.sync.ingest(
            Message(**values | {"message_id": 2 * index + 2, "outgoing": True})
        )
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET due=?", (app.store.clock() - 1,))
        if klass:
            db.execute("UPDATE jobs SET payload=?", (json.dumps({"class": klass}),))


class Gate:
    """A fake engine call that holds until released, counting concurrency."""

    def __init__(self, manager):
        self.running = self.peak = 0
        self.release = asyncio.Event()
        self.seen = []
        for app in manager.runtimes.values():
            app.assistant.engine.generate = self.generate

    async def generate(self, role, packet):
        self.running += 1
        self.peak = max(self.peak, self.running)
        self.seen.append((role, packet.get("fingerprint") or packet["draft"]["text"]))
        try:
            await self.release.wait()
        finally:
            self.running -= 1
        if role == "outgoing_rewrite":
            return {"text": packet["draft"]["text"]}
        return {"text": "Rewritten.", "image_context": []}


async def settle(manager, rounds=200):
    for _ in range(rounds):
        manager.dispatch()
        await asyncio.sleep(0.01)
        if not manager.active and not any(
            r.assistant.rewriting.next_job() for r in manager.runtimes.values()
        ):
            return


async def test_calls_never_exceed_the_limit_and_no_job_runs_twice(rig):
    manager, now = rig
    manager.registry.set_concurrency(3)
    for account in (1, 2):
        blocks(manager.get(account), 5)
    gate = Gate(manager)
    manager.dispatch()
    await asyncio.sleep(0.05)
    assert gate.running == 3 and len(manager.active) == 4  # limit + 1 preparing
    gate.release.set()
    await settle(manager)
    assert gate.peak == 3
    assert len(gate.seen) == 10 and len(set(gate.seen)) == 10
    for account in (1, 2):
        app = manager.get(account)
        assert (
            len(app.store.rows("SELECT * FROM receipts WHERE role='incoming_rewrite'"))
            == 5
        )
        states = {
            b["state"] for b in app.conversation("%d/user:42" % account)["blocks"]
        }
        assert states == {"ready"}


async def test_one_jobs_error_leaves_siblings_intact(rig):
    manager, now = rig
    app = manager.get(1)
    blocks(app, 2)
    gate = Gate(manager)
    original = app.rewriting.incoming

    async def incoming(job):
        if job["scope"].endswith("/3"):
            await asyncio.sleep(0.02)
            raise RuntimeError("unexpected preparation fault")
        return await original(job)

    app.rewriting.incoming = incoming
    manager.dispatch()
    await asyncio.sleep(0.1)
    assert gate.running == 1
    gate.release.set()
    await settle(manager)
    first, second = app.conversation("1/user:42")["blocks"]
    assert first["state"] == "ready"
    assert second["state"] == "failed" and "Retry" in second["error"]
    assert not app.store.rows("SELECT * FROM jobs WHERE state='unknown'")


async def test_outgoing_starts_beside_background_and_goes_first_when_full(rig):
    manager, now = rig
    manager.registry.set_concurrency(3)
    app = manager.get(1)
    blocks(app, 2)
    gate = Gate(manager)
    manager.dispatch()
    await asyncio.sleep(0.05)
    draft = app.store.mutate_draft("1/user:42", 0, lambda d: d.update(text="Mine."))
    outgoing = asyncio.create_task(
        app.rewriting.outgoing("1/user:42", draft["revision"])
    )
    await asyncio.sleep(0.05)
    assert gate.running == 3 and gate.seen[-1] == ("outgoing_rewrite", "Mine.")
    gate.release.set()
    await outgoing
    await settle(manager)
    # With every slot busy, a waiting outgoing Rewrite precedes queued work.
    manager.registry.set_concurrency(1)
    gate.release.clear()
    blocks(manager.get(2), 2)
    manager.dispatch()
    await asyncio.sleep(0.05)
    draft = app.store.draft("1/user:42")
    outgoing = asyncio.create_task(
        app.rewriting.outgoing("1/user:42", draft["revision"])
    )
    await asyncio.sleep(0.05)
    gate.release.set()
    await outgoing
    await settle(manager)
    order = [role for role, _ in gate.seen[3:]]
    assert order[:2] == ["incoming_rewrite", "outgoing_rewrite"]


async def test_a_new_limit_applies_without_restart(rig):
    manager, now = rig
    manager.registry.set_concurrency(1)
    blocks(manager.get(1), 3)
    gate = Gate(manager)
    manager.dispatch()
    await asyncio.sleep(0.05)
    assert gate.running == 1
    manager.registry.set_concurrency(3)
    manager.dispatch()
    await asyncio.sleep(0.05)
    assert gate.running == 3
    gate.release.set()
    await settle(manager)


async def test_low_classes_never_take_the_last_free_slot(rig):
    manager, now = rig
    manager.registry.set_concurrency(2)
    app = manager.get(1)
    blocks(app, 3, klass="older")
    gate = Gate(manager)
    manager.dispatch()
    await asyncio.sleep(0.05)
    assert gate.running == 1 and len(manager.active) == 1
    blocks(manager.get(2), 1)
    manager.dispatch()
    await asyncio.sleep(0.05)
    assert gate.running == 2  # New work takes the slot older work may not.
    gate.release.set()
    await settle(manager)
    assert gate.peak == 2


@pytest.mark.parametrize("limit", [1, 2, 4, 8])
def test_group_capacity_follows_every_private_class_and_preserves_the_last_slot(limit):
    private = ("outgoing", "new", "catchup", "reaction", "older", "suggestions")
    assert CLASSES == (*private, "group", "memory")
    assert all(RANK[klass] < RANK["group"] < RANK["memory"] for klass in private)
    capacity = Capacity(lambda: limit)
    assert capacity.admits("group")
    assert capacity.admits("group", running=limit - 1) is (limit == 1)
    assert capacity.admits("new", running=limit - 1)


async def test_waiting_private_stages_precede_group_and_group_keeps_memory_idle():
    capacity = Capacity(lambda: 1)
    held = capacity.slot("outgoing")
    await held.__aenter__()
    order = []

    async def stage(klass):
        async with capacity.slot(klass):
            order.append(klass)
            await asyncio.sleep(0)

    group = asyncio.create_task(stage("group"))
    suggestion = asyncio.create_task(stage("suggestions"))
    incoming = asyncio.create_task(stage("new"))
    await asyncio.sleep(0)
    with pytest.raises(ProcessingPaused):
        async with capacity.slot("memory"):
            pass
    await held.__aexit__(None, None, None)
    await asyncio.gather(group, suggestion, incoming)
    assert order == ["new", "suggestions", "group"]
    assert capacity.running == 0


async def test_group_last_slot_hold_releases_when_capacity_changes_or_private_finishes():
    limit = [2]
    capacity = Capacity(lambda: limit[0])
    held = capacity.slot("new")
    await held.__aenter__()
    started = asyncio.Event()
    release = asyncio.Event()

    async def stage():
        async with capacity.slot("group"):
            started.set()
            await release.wait()

    pending = asyncio.create_task(stage())
    await asyncio.sleep(0)
    assert not started.is_set() and capacity.running == 1
    limit[0] = 3
    capacity.wake()
    await asyncio.wait_for(started.wait(), 1)
    assert capacity.running == 2
    release.set()
    await pending
    await held.__aexit__(None, None, None)
    assert capacity.running == 0


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_refusals_requeue_three_times_at_zero_cost_then_fail(rig):
    manager, now = rig
    app = manager.get(1)
    blocks(app, 1)
    attempts = []

    async def refuse(role, packet):
        attempts.append(1)
        raise ProviderRefused("Muse HTTP 429", retry_after=5)

    app.engine.generate = refuse
    for round in range(4):
        await settle(manager)
        block = app.conversation("1/user:42")["blocks"][0]
        if round < 3:
            assert block["state"] == "queued" and block["stage"] == "retrying"
            now[0] += 6
    assert len(attempts) == 4
    assert block["state"] == "failed" and "Retry" in block["error"]
    ledger = manager.registry.rows("SELECT status,actual FROM model_reservations")
    assert [r["status"] for r in ledger] == ["refused"] * 4
    assert manager.registry.budget()["spent"] == 0
    assert len({j["id"] for j in app.store.rows("SELECT id FROM jobs")}) == 4


async def test_accounts_share_capacity_fairly_across_restart(rig, tmp_path):
    manager, now = rig
    manager.registry.set_concurrency(1)
    for account in (1, 2):
        blocks(manager.get(account), 4)
    served = []

    for runtime in manager.runtimes.values():
        account = runtime.assistant.store.account_id

        async def generate(role, packet, account=account):
            served.append(account)
            return {"text": "Rewritten.", "image_context": []}

        runtime.assistant.engine.generate = generate
    for _ in range(2):  # Limit 1 claims two: one calling, one preparing.
        manager.dispatch()
        await asyncio.gather(*manager.active)
    assert served == [1, 2, 1, 2]
    await manager.close()
    restarted = AccountManager(Settings(data=tmp_path))
    restarted._create(restarted.registry.get(2))
    for runtime in restarted.runtimes.values():
        account = runtime.assistant.store.account_id
        runtime.assistant.store.clock = lambda: now[0]

        async def generate(role, packet, account=account):
            served.append(account)
            return {"text": "Rewritten.", "image_context": []}

        runtime.assistant.engine.generate = generate
    await settle(restarted)
    assert sorted(served[4:]) == [1, 1, 2, 2] and served[4] != served[5]


def test_claimed_work_recovers_to_queued_and_dispatched_to_unknown(rig):
    manager, now = rig
    app = manager.get(1)
    blocks(app, 2)
    first, second = app.store.rows("SELECT * FROM jobs ORDER BY scope")
    assert app.rewriting.claim(first) and not app.rewriting.claim(first)
    app.rewriting.stage(first["scope"], "preparing", state="working")
    app.rewriting.claim(second)
    app.rewriting.stage(second["scope"], "rewriting", state="working")
    app.rewriting.work.reserve(second, "offline")
    reopened = Store(
        app.store.path,
        account_id=1,
        clock=app.store.clock,
        shared_path=app.store.shared_path,
    )
    reopened.recover()
    states = {
        r["scope"]: r["state"] for r in reopened.rows("SELECT scope,state FROM jobs")
    }
    assert states == {first["scope"]: "queued", second["scope"]: "unknown"}
    assert [b["state"] for b in app.conversation("1/user:42")["blocks"]] == [
        "queued",
        "unknown",
    ]


async def test_stop_also_cancels_claimed_undispatched_work(rig):
    manager, now = rig
    app = manager.get(1)
    blocks(app, 1)
    job = app.store.rows("SELECT * FROM jobs")[0]
    app.rewriting.claim(job)
    assert app.rewriting.stop("1/user:42") == 1
    await app.rewriting.incoming(job)
    assert not app.engine.calls
    assert app.conversation("1/user:42")["blocks"][0]["state"] == "original"
    assert not manager.registry.usage()


async def test_one_transcription_serves_concurrent_jobs(rig):
    manager, now = rig
    app = manager.get(1)
    calls = []

    class Speech:
        async def transcribe(self, path):
            calls.append(path)
            await asyncio.sleep(0.05)
            return {"text": "Shared authored speech.", "language": "en"}

    app.media.transcriber = Speech()
    app.transport.media["user:42", 1] = b"OggS" + bytes(32)
    app.sync.ingest(
        Message(
            account_id=1,
            peer="user:42",
            message_id=1,
            revision=1,
            date=now[0],
            media="audio",
            media_info=MediaInfo(voice=True),
        )
    )
    for identity, outgoing in ((2, True), (3, False)):
        app.sync.ingest(
            Message(
                account_id=1,
                peer="user:42",
                message_id=identity,
                revision=1,
                date=now[0],
                text="About that voice note." if not outgoing else "Noted.",
                outgoing=outgoing,
            )
        )
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET due=?", (now[0] - 1,))
    await settle(manager)
    assert len(calls) == 1
    assert {b["state"] for b in app.conversation("1/user:42")["blocks"]} == {"ready"}


def test_enqueue_records_the_priority_class(rig):
    manager, now = rig
    app = manager.get(1)
    with app.store.transaction() as db:
        enqueue(db, "1/user:42/9", "rev", now[0], klass="older")
    assert json.loads(app.store.rows("SELECT payload FROM jobs")[0]["payload"]) == {
        "class": "older"
    }
