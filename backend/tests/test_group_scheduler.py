"""Authored group/private work shares the real global scheduler without Telegram."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from enloq.application.accounts import AccountManager
from enloq.application.group_rollout import GroupBriefingRollout
from enloq.config import Settings
from enloq.domain.briefing_contract import GROUP_ROLES
from enloq.domain.models import Chat, Message

# These cases exercise the rewrite path with text (ADR 54 keeps it for
# voice notes, images and calming).
pytestmark = pytest.mark.usefixtures("text_rewriting")

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC).timestamp()


@pytest.fixture
def rig(tmp_path):
    manager = AccountManager(Settings(data=tmp_path))
    manager._create(manager.registry.register(2, "authored-second.session"))
    now = [NOW]
    for runtime in manager.runtimes.values():
        runtime.assistant.store.clock = lambda: now[0]
    manager.set_processing_threshold(0)
    # These cases qualify ordinary automatic scheduling, including its clock.
    # Freeze the rollout while no group is enabled so it cannot replace the
    # automatic first run; separate rollout tests cover its admission/caps.
    manager.group_rollout = GroupBriefingRollout(manager, "authored-scheduler")
    assert not manager.group_rollout.start()["targets"]
    return manager, now


def group(app, peer="channel:42", *, count=1, text="Authored group update."):
    key = f"{app.store.account_id}/{peer}"
    app.store.put_chat(
        Chat(
            account_id=app.store.account_id,
            peer=peer,
            title="Authored participants",
            kind="group",
        ),
        activity=app.store.clock(),
    )
    app.store.groups.observe(key, access="available", reason="authored_native")
    app.set_group_processing(key, True)
    for identity in range(1, count + 1):
        app.sync.ingest(
            Message(
                account_id=app.store.account_id,
                peer=peer,
                message_id=identity,
                revision=1,
                date=app.store.clock(),
                text=text,
                author="Authored participant",
                author_peer="user:7",
            ),
            group_epoch=app.store.groups.intake(key).epoch,
        )
    return key


def private(app, *, identity=1):
    peer = "user:42"
    key = f"{app.store.account_id}/{peer}"
    app.store.put_chat(
        Chat(
            account_id=app.store.account_id,
            peer=peer,
            title="Authored DM",
            kind="private",
        )
    )
    app.sync.ingest(
        Message(
            account_id=app.store.account_id,
            peer=peer,
            message_id=identity,
            revision=1,
            date=app.store.clock(),
            text="A private request needs a reply.",
        )
    )
    return key


class EngineGate:
    def __init__(self, manager):
        self.seen = []
        self.running = self.peak = 0
        self.release = asyncio.Event()
        for identity, runtime in manager.runtimes.items():
            engine = runtime.assistant.engine
            original = engine.generate

            async def generate(role, packet, original=original, identity=identity):
                self.seen.append((identity, role, packet.get("chat_key")))
                self.running += 1
                self.peak = max(self.peak, self.running)
                try:
                    await self.release.wait()
                    return await original(role, packet)
                finally:
                    self.running -= 1

            engine.generate = generate


async def advance(manager, *, rounds=400):
    for _ in range(rounds):
        manager.dispatch()
        await asyncio.sleep(0.005)
        if not manager.active and not any(
            runtime.assistant.rewriting.next_job()
            or runtime.assistant.briefings.next_job()
            for runtime in manager.runtimes.values()
        ):
            return
    pytest.fail("Authored scheduler work did not settle within the bounded check")


async def started(manager, gate, count=1):
    for _ in range(100):
        if len(gate.seen) >= count:
            return
        manager.dispatch()
        await asyncio.sleep(0.005)
    pytest.fail("An admitted authored model stage did not start")


async def test_private_work_precedes_group_at_one_slot(rig):
    manager, now = rig
    manager.registry.set_concurrency(1)
    app = manager.get(1)
    key = group(app)
    private(manager.get(2))
    now[0] += 31
    gate = EngineGate(manager)
    manager.dispatch()
    await started(manager, gate)
    assert gate.seen[0][:2] == (2, "incoming_rewrite")
    assert app.store.group_briefings.run_state(key).last_auto_started is None
    gate.release.set()
    await advance(manager)
    assert any(role in GROUP_ROLES for _, role, _ in gate.seen)
    assert gate.peak == 1
    await manager.close()


async def test_group_preserves_last_slot_for_new_private_work(rig):
    manager, now = rig
    manager.registry.set_concurrency(2)
    first = group(manager.get(1))
    second = group(manager.get(2))
    now[0] += 31
    gate = EngineGate(manager)
    manager.dispatch()
    await started(manager, gate)
    await asyncio.sleep(0.025)
    assert gate.running == 1 and len(manager.active) == 1
    waiting = manager.get(2 if gate.seen[0][0] == 1 else 1)
    private(waiting)
    now[0] += 31
    manager.dispatch()
    await started(manager, gate, 2)
    assert gate.seen[1][1] == "incoming_rewrite"
    assert gate.running == 2
    gate.release.set()
    await advance(manager)
    assert gate.peak == 2
    for identity, key in ((1, first), (2, second)):
        assert (
            manager.get(identity).store.group_briefings.state(key, "2026-10-11").final
        )
    await manager.close()


async def test_accounts_each_receive_a_group_stage_before_repeated_service(rig):
    manager, now = rig
    manager.registry.set_concurrency(1)
    for identity in (1, 2):
        group(manager.get(identity), count=6, text="Authored longer update. " * 250)
    now[0] += 31
    gate = EngineGate(manager)
    gate.release.set()
    await advance(manager)
    sequence = [identity for identity, role, _ in gate.seen if role in GROUP_ROLES]
    assert len(sequence) >= 4
    assert set(sequence[:2]) == {1, 2}
    assert all(sequence[index] != sequence[index + 1] for index in range(3))
    assert gate.peak == 1
    for identity in (1, 2):
        rows = manager.get(identity).store.rows(
            "SELECT body FROM group_briefing_batches"
        )
        assert rows and all(json.loads(row["body"])["requests"] <= 5 for row in rows)
    await manager.close()


async def test_group_stage_yields_to_a_new_private_request(rig):
    manager, now = rig
    manager.registry.set_concurrency(1)
    group(manager.get(1), count=6, text="Authored longer update. " * 250)
    now[0] += 31
    gate = EngineGate(manager)
    manager.dispatch()
    await started(manager, gate)
    assert gate.seen[0][1] in GROUP_ROLES
    private(manager.get(2))
    now[0] += 31
    gate.release.set()
    await advance(manager)
    assert gate.seen[1][:2] == (2, "incoming_rewrite")
    assert any(role in GROUP_ROLES for _, role, _ in gate.seen[2:])
    await manager.close()


async def test_two_groups_on_one_account_each_receive_a_stage(rig):
    manager, now = rig
    manager.registry.set_concurrency(1)
    app = manager.get(1)
    first = group(app, count=6, text="Authored longer update. " * 250)
    second = group(app, peer="chat:42")
    now[0] += 31
    gate = EngineGate(manager)
    gate.release.set()
    await advance(manager)
    sequence = [key for _, role, key in gate.seen if role in GROUP_ROLES]
    assert set(sequence[:2]) == {first, second}
    assert first in sequence[2:]
    await manager.close()


def test_pending_group_admission_inspection_never_starts_cadence(rig):
    manager, now = rig
    app = manager.get(1)
    key = group(app)
    now[0] += 31
    for _ in range(3):
        assert app.briefings.next_job()
        app.conversation(key)
    assert app.store.group_briefings.run_state(key).last_auto_started is None
    assert not app.store.rows("SELECT id FROM group_briefing_batches")


def test_threshold_deferred_groups_leave_private_memory_idle(rig):
    manager, now = rig
    app = manager.get(1)
    key = group(app, text="Short.")
    manager.set_processing_threshold(200)
    now[0] += 31
    assert not app.briefings.next_job()
    assert manager.memory_idle()
    assert app.store.group_briefings.run_state(key).last_auto_started is None
    assert not app.store.rows(
        "SELECT id FROM jobs WHERE role LIKE 'group_briefing%' "
        "AND state IN ('queued','claimed','running')"
    )
    manager.set_processing_threshold(0)
    assert app.briefings.next_job()
    assert not manager.memory_idle()


def test_processing_saves_preserve_group_clock_and_current_sources(rig):
    manager, now = rig
    app = manager.get(1)
    key = group(app)
    now[0] += 31
    job = app.briefings.next_job()
    assert app.briefings.claim(job)
    began = app.store.group_briefings.run_state(key).last_auto_started
    assert began == now[0]
    original = app.store.message(key, 1).model_dump()
    app.set_group_processing(key, False)
    manager.set_processing(1, False)
    manager.set_processing_threshold(10000)
    manager.set_processing_threshold(0)
    manager.set_processing(1, True)
    app.set_group_processing(key, True)
    assert app.store.group_briefings.run_state(key).last_auto_started == began
    assert app.store.message(key, 1).model_dump() == original


def test_material_access_change_reconciles_group_work_without_native_fetch(rig):
    manager, now = rig
    app = manager.get(1)
    key = group(app)
    now[0] += 31
    assert app.briefings.claim(app.briefings.next_job())
    attempts = app.store.rows("SELECT id FROM group_briefing_attempts")
    assert len(attempts) == 1
    app.store.groups.observe(key, access="lost", reason="authored_revocation")
    attempt = app.store.group_briefings.attempt(attempts[0]["id"])
    assert attempt.state == "superseded"
    assert not app.briefings.next_job()
    assert not app.engine.calls
    assert not app.transport.exact_reads


def test_changed_sources_close_stale_batch_without_reopening_cadence(rig):
    manager, now = rig
    app = manager.get(1)
    key = group(app)
    now[0] += 31
    assert app.briefings.claim(app.briefings.next_job())
    initial = app.store.group_briefings.run_state(key)
    original = app.store.message(key, 1)
    app.sync.ingest(
        original.model_copy(
            update={
                "revision": original.revision + 1,
                "text": "A changed authored update.",
            }
        ),
        group_epoch=app.store.groups.intake(key).epoch,
    )
    run = app.store.group_briefings.run_state(key)
    assert run.active_batch is None
    assert run.last_auto_started == initial.last_auto_started
    assert app.store.group_briefings.batch(initial.active_batch).state != "active"
    assert not app.briefings.next_job()
    now[0] += 4 * 3600 + 31
    assert app.briefings.next_job()
    assert not app.engine.calls and not app.transport.exact_reads


async def test_threshold_save_between_stages_retains_caps_and_releases_memory(rig):
    manager, now = rig
    manager.registry.set_concurrency(1)
    app = manager.get(1)
    key = group(app, count=6, text="😀" * 1200)
    now[0] += 31
    gate = EngineGate(manager)
    manager.dispatch()
    await started(manager, gate)
    assert gate.seen[0][1] == "group_briefing_chunk"
    began = app.store.group_briefings.run_state(key).last_auto_started
    manager.set_processing_threshold(10000)
    gate.release.set()
    await asyncio.gather(*list(manager.active))
    manager.dispatch()
    assert len(gate.seen) == 1
    assert not manager.active and manager.memory_idle()
    chunks = app.store.rows("SELECT id FROM group_briefing_chunks WHERE valid=1")
    assert chunks
    run = app.store.group_briefings.run_state(key)
    assert run.last_auto_started == began and run.active_batch
    batch = app.store.group_briefings.batch(run.active_batch)
    assert batch.requests == 1
    manager.set_processing_threshold(0)
    await advance(manager)
    final = app.store.group_briefings.state(key, "2026-10-11").final
    assert final and set(final.covered_ids) == set(range(1, 7))
    assert app.store.group_briefings.run_state(key).last_auto_started == began
    assert app.store.group_briefings.batch(batch.id).requests <= 5
    await manager.close()


def later(app, key, identity):
    app.sync.ingest(
        Message(
            account_id=app.store.account_id,
            peer=key.split("/", 1)[1],
            message_id=identity,
            revision=1,
            date=app.store.clock(),
            text="A later authored group update.",
            author="Authored participant",
            author_peer="user:7",
        ),
        group_epoch=app.store.groups.intake(key).epoch,
    )


async def waiting(rig):
    """A briefed group with a newer original, held by its four-hour clock."""
    manager, now = rig
    app = manager.get(1)
    key = group(app)
    now[0] += 31
    await advance(manager)
    assert app.store.group_briefings.state(key, "2026-10-11").final
    later(app, key, 2)
    now[0] += 31
    return manager, now, app, key


def derivations(app, monkeypatch):
    calls = []
    pending = app.briefings.planner.pending

    def counted(*args, **kwargs):
        calls.append(app.store.clock())
        return pending(*args, **kwargs)

    monkeypatch.setattr(app.briefings.planner, "pending", counted)
    return calls


def folders(root):
    return {
        path: path.stat().st_mtime_ns
        for path in [root, *root.rglob("*")]
        if path.is_dir()
    }


async def test_group_waiting_for_its_next_update_is_planned_once(
    rig, monkeypatch, tmp_path
):
    manager, now, app, key = await waiting(rig)
    calls = derivations(app, monkeypatch)
    manager.dispatch()
    planned, touched, requested = len(calls), folders(tmp_path), len(app.engine.calls)
    assert planned and not manager.active
    for _ in range(20):
        manager.dispatch()
        assert not app.briefings.next_job() and not manager.memory_idle()
    assert len(calls) == planned and len(app.engine.calls) == requested
    # Waiting writes nothing: no journal appears beside either database.
    assert folders(tmp_path) == touched
    later(app, key, 3)
    manager.dispatch()
    assert len(calls) > planned
    await manager.close()


async def test_waiting_group_starts_exactly_when_its_holds_lift(rig):
    manager, now, app, key = await waiting(rig)
    began = app.store.group_briefings.run_state(key).last_auto_started
    now[0] = began + 4 * 3600 - 5
    assert not app.briefings.next_job()
    now[0] += 4.999
    assert not app.briefings.next_job()
    now[0] = began + 4 * 3600
    job = app.briefings.next_job()
    assert job and app.briefings.claim(job)
    # A held stage is offered again at its due time, not a recheck later.
    app.briefings._hold(job, delay=5)
    assert not app.briefings.next_job()
    now[0] += 5
    assert app.briefings.next_job()["id"] == job["id"]
    await manager.close()


async def test_quiet_and_refusal_holds_end_on_time_for_a_waiting_group(rig):
    manager, now, app, key = await waiting(rig)
    now[0] = app.store.group_briefings.run_state(key).last_auto_started + 4 * 3600
    later(app, key, 3)
    now[0] += 20
    assert not app.briefings.next_job()
    now[0] += 10
    assert app.briefings.next_job()
    app.briefings.planner.refuse(key, delay=5)
    assert not app.briefings.next_job()
    now[0] += 4
    assert not app.briefings.next_job()
    now[0] += 1
    assert app.briefings.next_job()
    await manager.close()


async def test_unannounced_changes_reach_a_waiting_group_within_the_recheck(
    rig, monkeypatch
):
    from enloq.application.group_briefings import RECHECK

    manager, now, app, key = await waiting(rig)
    calls = derivations(app, monkeypatch)
    manager.dispatch()
    planned = len(calls)
    now[0] += RECHECK - 1
    manager.dispatch()
    assert len(calls) == planned
    now[0] += 1
    manager.dispatch()
    assert len(calls) > planned
    await manager.close()


async def test_exhausted_group_budget_is_not_replanned_until_it_changes(
    rig, monkeypatch
):
    manager, now, app, key = await waiting(rig)
    now[0] = app.store.group_briefings.run_state(key).last_auto_started + 4 * 3600
    manager.registry.set_budget(0, family="group_briefing")
    calls = derivations(app, monkeypatch)
    manager.dispatch()
    planned = len(calls)
    for _ in range(10):
        manager.dispatch()
    assert len(calls) == planned and not manager.active
    manager.registry.set_budget(2_000_000_000, family="group_briefing")
    assert app.briefings.next_job()
    await manager.close()


async def test_hold_ending_while_a_waiting_group_is_planned_is_not_missed(
    rig, monkeypatch
):
    manager, now, app, key = await waiting(rig)
    ends = app.store.group_briefings.run_state(key).last_auto_started + 4 * 3600
    now[0] = ends - 0.05
    pending = app.briefings.planner.pending

    def slow(*args, **kwargs):
        result = pending(*args, **kwargs)
        now[0] += 0.1
        return result

    monkeypatch.setattr(app.briefings.planner, "pending", slow)
    assert not app.briefings.next_job()
    monkeypatch.setattr(app.briefings.planner, "pending", pending)
    assert now[0] > ends and app.briefings.next_job()
    await manager.close()


async def test_account_becoming_available_again_replans_its_waiting_group(rig):
    manager, now, app, key = await waiting(rig)
    runtime = manager.runtimes[1]
    assert not app.briefings.local_idle()
    runtime.accepting = False
    assert app.briefings.local_idle()
    # No commit announces the account's in-memory state.
    runtime.accepting = True
    assert not app.briefings.local_idle() and not manager.memory_idle()
    await manager.close()


async def test_reached_group_budget_releases_work_at_the_next_budget_day(rig):
    manager, now, app, key = await waiting(rig)
    now[0] = app.store.group_briefings.run_state(key).last_auto_started + 4 * 3600
    with manager.registry.transaction() as db:
        db.execute(
            "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created,family) VALUES (1,'authored','authored','group_briefing','1',?,'authored',0,?,'receipted',?,'group_briefing')",
            ("day:2026-10-11", 2_000_000_000, now[0]),
        )
    assert not app.briefings.next_job()
    midnight = datetime(2026, 10, 11, 17, tzinfo=UTC).timestamp()
    now[0] = midnight - 1
    assert not app.briefings.next_job()
    now[0] = midnight
    assert app.briefings.next_job()
    await manager.close()


async def test_other_groups_intake_does_not_replan_a_waiting_group(rig, monkeypatch):
    manager, now, app, key = await waiting(rig)
    other = "1/channel:43"
    app.store.put_chat(
        Chat(
            account_id=1, peer="channel:43", title="Authored quiet group", kind="group"
        ),
        activity=app.store.clock(),
    )
    app.store.groups.observe(other, access="available", reason="authored_native")
    calls = derivations(app, monkeypatch)
    manager.dispatch()
    planned = len(calls)
    for pts in range(1, 6):
        # A native intake poll of a group whose AI is Off.
        epoch = app.store.groups.intake(other).epoch
        app.store.groups.begin_replay(other, epoch=epoch)
        app.store.groups.checkpoint(other, pts, epoch=epoch)
        manager.dispatch()
    assert len(calls) == planned
    # The waiting group's own poll holds its admission until it finishes.
    epoch = app.store.groups.intake(key).epoch
    app.store.groups.begin_replay(key, epoch=epoch)
    manager.dispatch()
    assert len(calls) > planned and manager.memory_idle()
    app.store.groups.checkpoint(key, 1, epoch=epoch)
    assert not manager.memory_idle()
    await manager.close()
