"""GB5 stage boundaries over authored native evidence and fake engines only."""

import asyncio
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from enloq.application.capacity import Capacity
from enloq.application.group_context import eligible
from enloq.domain.briefing_contract import GROUP_ROLES
from enloq.domain.models import Chat, Message, Refused
from enloq.inference.base import EngineResponse, ProviderRefused
from enloq.inference.guidance import briefing_fingerprint
from enloq.inference.work import (
    FLAT_UNKNOWN,
    ROUTES,
    GroupGuidanceChanged,
    ModelWork,
)
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import Store

from backend.tests.group_test_support import briefing_output
from backend.tests.test_group_briefings import DAY, KEY, NOW, freeze, output, source


def queue(store, identity, role="group_briefing"):
    job = dict(
        id=identity,
        scope=KEY,
        role=role,
        input_revision=identity,
        state="queued",
        due=store.clock(),
        payload="{}",
        error=None,
    )
    with store.transaction() as db:
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", tuple(job.values()))
    return job


@pytest.fixture
def rig(tmp_path):
    clock = [NOW]
    registry = AccountRegistry(tmp_path)
    registry.register(1, "authored.session", directory="account")
    store = Store(
        tmp_path / "account" / "content.sqlite3",
        account_id=1,
        clock=lambda: clock[0],
        timezone="UTC",
        shared_path=registry.path,
    )
    store.put_chat(
        Chat(account_id=1, peer="channel:42", title="Authored group", kind="group")
    )
    store.groups.observe(KEY, access="available", reason="authored_native")
    store.groups.set_processing_enabled(KEY, True)
    value = SimpleNamespace(
        store=store,
        registry=registry,
        clock=clock,
        briefings=store.group_briefings,
        work=ModelWork(store),
        accounting_calls=[],
    )
    source(value, 1, text="Authored substantive group discussion. " * 10)
    value.work.guidance = registry.guidance
    snapshot = freeze(
        value,
        [1],
        guidance=briefing_fingerprint(registry.guidance()),
        route=ROUTES["group_briefing"],
    )
    value.attempt = value.briefings.create_attempt(snapshot, automatic=True)
    yield value
    store.close()


def admission(rig, db, job):
    """Synthetic owner gate reads the same committed facts as the planner."""
    assert db.in_transaction and job["role"] in GROUP_ROLES
    state = rig.store.groups.get(KEY, db=db)
    if not state.processing_enabled:
        raise Refused("Group AI processing is paused")
    if not db.execute(
        "SELECT ai_enabled FROM shared.accounts WHERE account_id=1"
    ).fetchone()[0]:
        raise Refused("Account AI processing is paused")
    if not rig.briefings.current(rig.attempt, admission=True, db=db):
        raise Refused("Group source changed")
    if rig.briefings.run_state(KEY, db=db).active_batch != rig.attempt.batch_id:
        raise Refused("Group batch changed")
    if rig.briefings.batch(rig.attempt.batch_id, db=db).state != "active":
        raise Refused("Group batch finished")
    row = db.execute(
        "SELECT value FROM shared.metadata WHERE key='processing_threshold'"
    ).fetchone()
    threshold = int(row[0]) if row else 200
    snapshot = rig.attempt.snapshot
    text = "\n".join(
        message.text
        for row in db.execute(
            "SELECT body FROM messages WHERE chat_key=? AND date>=? AND date<?",
            (KEY, snapshot.start, snapshot.end),
        )
        if eligible(message := Message.model_validate_json(row[0]))
    ).strip()
    if len(text) < threshold:
        raise Refused("Group character admission is held")


def accounting(rig, db, job):
    assert db.in_transaction
    rig.briefings.record_work(
        rig.attempt.batch_id,
        requests=1,
        source_ids=[1],
        dispatch_token=job["id"],
        db=db,
    )
    rig.accounting_calls.append(job["id"])


def hooks(rig, role="group_briefing"):
    return dict(
        group_admission=lambda db, job: admission(rig, db, job),
        group_accounting=lambda db, job: accounting(rig, db, job),
        group_guidance=briefing_fingerprint(
            rig.registry.guidance(), role=role, route=ROUTES[role]
        ),
    )


def packet(rig, role="group_briefing"):
    value = copy.deepcopy(rig.attempt.snapshot.packets[0])
    value["contract_version"] = 2
    if role == "group_briefing_image":
        value["images"] = [
            dict(message_id=1, data_url="data:image/jpeg;base64,YXV0aG9yZWQ=")
        ]
    return value


class ControlledEngine:
    name, model, provider = "mimo", "authored-stage-model", "xiaomi"

    def __init__(self, *, hold_prepare=False, prepare_refused=False, outcome="valid"):
        self.prepared, self.started = asyncio.Event(), asyncio.Event()
        self.release_prepare, self.finish = asyncio.Event(), asyncio.Event()
        self.prepare_refused, self.outcome = prepare_refused, outcome
        self.prepare_packets, self.calls = [], []
        if not hold_prepare:
            self.release_prepare.set()
        self.finish.set()

    async def prepare(self, value):
        self.prepare_packets.append(copy.deepcopy(value))
        self.prepared.set()
        await self.release_prepare.wait()
        if self.prepare_refused:
            raise ProviderRefused("Authored exact route unavailable")

    async def generate(self, role, value):
        self.calls.append((role, copy.deepcopy(value)))
        self.started.set()
        await self.finish.wait()
        if self.outcome == "refused":
            raise ProviderRefused("Authored zero-cost provider refusal")
        if self.outcome == "unknown":
            raise ConnectionError("Authored uncertain outcome")
        if self.outcome == "interrupted":
            raise asyncio.CancelledError()
        content = (
            {"image_context": [{"message_id": 1, "text": "Authored chart meaning."}]}
            if role == "group_briefing_image"
            else briefing_output([1])
        )
        if self.outcome == "invalid":
            content = {"unrecognized": "shape"}
        return EngineResponse(content, actual_nanos=123)

    async def generate_stream(self, role, value, on_text):
        on_text("Authored provisional final")
        return await self.generate(role, value)


async def run(rig, engine, job=None, **changes):
    job = job or queue(rig.store, "stage")
    return await rig.work.run(
        engine,
        job,
        packet(rig, job["role"]),
        lambda: rig.briefings.current(rig.attempt),
        **(hooks(rig, job["role"]) | changes),
    )


def requests(rig):
    return rig.briefings.batch(rig.attempt.batch_id).requests


def no_spending(rig):
    assert not rig.store.rows("SELECT * FROM reservations")
    assert not rig.store.rows("SELECT * FROM receipts")
    assert not rig.registry.rows("SELECT * FROM model_reservations")
    assert not rig.registry.rows("SELECT * FROM model_receipts")
    assert (
        rig.registry.budget(rig.store.clock, "UTC", family="group_briefing")["spent"]
        == 0
    )


@pytest.mark.parametrize("role", sorted(GROUP_ROLES))
@pytest.mark.parametrize(
    "outcome,status,spent",
    [
        ("valid", "receipted", 123),
        ("invalid", "receipted", 123),
        ("unknown", "unknown", FLAT_UNKNOWN),
        ("interrupted", "unknown", FLAT_UNKNOWN),
        ("refused", "refused", 0),
    ],
)
async def test_each_dispatched_stage_outcome_consumes_one_request_once(
    rig, role, outcome, status, spent
):
    job = queue(rig.store, "stage", role)
    engine = ControlledEngine(outcome=outcome)
    result = (await asyncio.gather(run(rig, engine, job), return_exceptions=True))[0]
    assert isinstance(result, BaseException) == (outcome != "valid")
    assert requests(rig) == 1 and rig.accounting_calls == [job["id"]]
    assert rig.briefings.batch(rig.attempt.batch_id).source_ids == [1]
    assert len(engine.calls) == 1
    assert rig.store.rows("SELECT family,status FROM reservations") == [
        dict(family="group_briefing", status=status)
    ]
    assert (
        rig.registry.budget(rig.store.clock, "UTC", family="group_briefing")["spent"]
        == spent
    )
    assert len(rig.store.rows("SELECT * FROM receipts")) == 1
    assert rig.registry.budget(rig.store.clock, "UTC")["spent"] == 0
    with pytest.raises(Refused, match="already dispatched"):
        await run(rig, engine, job)
    assert requests(rig) == 1 and len(engine.calls) == 1


@pytest.mark.parametrize("role", sorted(GROUP_ROLES))
async def test_prepare_route_refusal_consumes_cap_at_zero_cost_without_reservation(
    rig, role
):
    job = queue(rig.store, "no-route", role)
    engine = ControlledEngine(prepare_refused=True)
    with pytest.raises(ProviderRefused):
        await run(rig, engine, job)
    assert requests(rig) == 1 and rig.accounting_calls == [job["id"]]
    assert not engine.calls
    assert rig.store.rows("SELECT state FROM jobs") == [dict(state="refused")]
    no_spending(rig)
    with pytest.raises(Refused, match="stopped"):
        await run(rig, engine, job)
    assert requests(rig) == 1 and len(engine.prepare_packets) == 1


def hold(rig, kind, job=None):
    if kind == "budget":
        rig.registry.set_budget(0, family="group_briefing")
    elif kind == "group_off":
        rig.store.groups.set_processing_enabled(KEY, False)
    elif kind == "master_off":
        rig.registry.set_processing(1, False)
    elif kind == "threshold":
        rig.registry.set_processing_threshold(10000)
    elif kind == "access":
        rig.store.groups.observe(KEY, access="lost", reason="authored_loss")
    elif kind == "source":
        source(rig, 1, revision=1, text="Authored changed native source")
    elif kind == "guidance":
        rig.registry.set_guidance({"group_briefing": "Updated group instructions."})
    elif kind == "stopped":
        with rig.store.transaction() as db:
            db.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job["id"],))


@pytest.mark.parametrize(
    "kind", ["budget", "group_off", "master_off", "threshold", "access", "source"]
)
async def test_initial_admission_holds_consume_no_request_or_preparation(rig, kind):
    job, engine = queue(rig.store, "held"), ControlledEngine()
    hold(rig, kind, job)
    with pytest.raises(Refused):
        await run(rig, engine, job)
    assert requests(rig) == 0 and not engine.prepare_packets and not engine.calls
    no_spending(rig)


@pytest.mark.parametrize(
    "kind",
    ["budget", "group_off", "master_off", "threshold", "access", "source", "stopped"],
)
@pytest.mark.parametrize("prepare_refused", [False, True])
async def test_hold_while_prepare_waits_consumes_no_request_or_reservation(
    rig, kind, prepare_refused
):
    job = queue(rig.store, "held-prepare")
    engine = ControlledEngine(hold_prepare=True, prepare_refused=prepare_refused)
    task = asyncio.create_task(run(rig, engine, job))
    await engine.prepared.wait()
    assert requests(rig) == 0
    hold(rig, kind, job)
    engine.release_prepare.set()
    with pytest.raises(Refused):
        await task
    assert requests(rig) == 0 and not engine.calls
    no_spending(rig)


@pytest.mark.parametrize("prepare_refused", [False, True])
async def test_save_while_prepare_waits_holds_then_dispatches_exact_latest_guidance(
    rig, prepare_refused
):
    job, engine = (
        queue(rig.store, "guided"),
        ControlledEngine(hold_prepare=True, prepare_refused=prepare_refused),
    )
    stage_hooks = hooks(rig)
    task = asyncio.create_task(run(rig, engine, job, **stage_hooks))
    await engine.prepared.wait()
    original = rig.attempt.snapshot
    hold(rig, "guidance")
    engine.release_prepare.set()
    with pytest.raises(GroupGuidanceChanged):
        await task
    assert requests(rig) == 0 and not engine.calls
    assert rig.store.rows("SELECT state FROM jobs") == [dict(state="queued")]
    no_spending(rig)
    latest = rig.registry.guidance()
    rig.attempt = rig.briefings.refresh_guidance(
        rig.attempt.id,
        guidance_hash=briefing_fingerprint(latest),
        route=ROUTES["group_briefing"],
    )
    assert rig.attempt.snapshot.model_dump(exclude={"guidance_hash", "route"}) == (
        original.model_dump(exclude={"guidance_hash", "route"})
    )
    next_engine = ControlledEngine()
    await run(rig, next_engine, job)
    assert requests(rig) == 1
    assert next_engine.calls[0][1]["guidance"] == latest
    assert (
        json.loads(rig.store.rows("SELECT usage FROM receipts")[0]["usage"])[
            "guidance_version"
        ]
        == latest["version"]
    )


async def test_save_in_capacity_queue_holds_before_prepare_without_permanent_failure(
    rig,
):
    rig.work.capacity = Capacity(lambda: 1)
    job, engine, original_hooks = (
        queue(rig.store, "capacity"),
        ControlledEngine(),
        hooks(rig),
    )
    async with rig.work.capacity.slot("new"):
        task = asyncio.create_task(run(rig, engine, job, **original_hooks))
        await asyncio.sleep(0)
        hold(rig, "guidance")
    with pytest.raises(GroupGuidanceChanged):
        await task
    assert requests(rig) == 0 and not engine.prepare_packets
    assert rig.store.rows("SELECT state FROM jobs") == [dict(state="queued")]
    no_spending(rig)


async def test_private_guidance_save_does_not_hold_group_stage_and_latest_version_is_recorded(
    rig,
):
    job, engine = queue(rig.store, "private-save"), ControlledEngine(hold_prepare=True)
    task = asyncio.create_task(run(rig, engine, job))
    await engine.prepared.wait()
    rig.registry.set_guidance({"incoming": "Unrelated private wording."})
    latest = rig.registry.guidance()
    engine.release_prepare.set()
    await task
    assert requests(rig) == 1 and engine.calls[0][1]["guidance"] == latest
    assert rig.attempt.snapshot.guidance_hash == briefing_fingerprint(latest)


async def test_valid_started_stream_keeps_dispatched_guidance_and_finishes_after_off_save(
    rig,
):
    job, engine, prefixes = queue(rig.store, "started"), ControlledEngine(), []
    original = rig.registry.guidance()
    engine.finish.clear()
    task = asyncio.create_task(run(rig, engine, job, on_text=prefixes.append))
    await engine.started.wait()
    assert requests(rig) == 1 and prefixes == ["Authored provisional final"]
    hold(rig, "group_off")
    hold(rig, "master_off")
    hold(rig, "budget")
    hold(rig, "guidance")
    engine.finish.set()
    result = await task
    assert result.text and requests(rig) == 1
    assert engine.calls[0][1]["guidance"] == original
    final = rig.briefings.publish(rig.attempt.id, result, covered_ids=[1])
    assert final.snapshot.guidance_hash == briefing_fingerprint(original)
    assert rig.briefings.current(final)
    assert (
        json.loads(rig.store.rows("SELECT usage FROM receipts")[0]["usage"])[
            "guidance_version"
        ]
        == original["version"]
    )


@pytest.mark.parametrize("boundary", ["dispatch", "prepare_refusal"])
async def test_full_request_cap_rolls_back_job_reservation_and_refusal_boundary(
    rig, boundary
):
    rig.briefings.record_work(rig.attempt.batch_id, requests=5)
    job = queue(rig.store, "sixth")
    engine = ControlledEngine(prepare_refused=boundary == "prepare_refusal")
    before = rig.briefings.batch(rig.attempt.batch_id)
    with pytest.raises(Refused, match="work limit"):
        await run(rig, engine, job)
    assert rig.briefings.batch(rig.attempt.batch_id) == before
    assert rig.store.rows("SELECT state FROM jobs") == [dict(state="queued")]
    assert not engine.calls
    no_spending(rig)


def test_reservation_and_batch_charge_share_one_rollback_boundary(rig):
    job = queue(rig.store, "atomic")
    before = rig.briefings.batch(rig.attempt.batch_id)

    def reject_after_charge(db, current_job):
        assert (
            db.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
            == "running"
        )
        assert (
            db.execute(
                "SELECT family FROM reservations WHERE id=?", (job["id"],)
            ).fetchone()[0]
            == "group_briefing"
        )
        accounting(rig, db, current_job)
        raise Refused("Authored post-charge rollback")

    with pytest.raises(Refused, match="rollback"):
        rig.work.reserve(
            job,
            "mimo",
            **(hooks(rig) | dict(group_accounting=reject_after_charge)),
        )
    assert rig.briefings.batch(rig.attempt.batch_id) == before
    assert rig.store.rows("SELECT state FROM jobs") == [dict(state="queued")]
    no_spending(rig)
    rig.work.reserve(job, "mimo", **hooks(rig))
    assert requests(rig) == 1
    with pytest.raises(Refused, match="already dispatched"):
        rig.work.reserve(job, "mimo", **hooks(rig))
    assert requests(rig) == 1


def test_two_atomic_reservations_compete_for_final_batch_request(rig):
    rig.briefings.record_work(rig.attempt.batch_id, requests=4)
    jobs = [queue(rig.store, "raced-" + str(i)) for i in (1, 2)]
    callbacks = hooks(rig)
    barrier = threading.Barrier(2)

    def reserve_job(job):
        barrier.wait()
        try:
            rig.work.reserve(job, "mimo", **callbacks)
            return "admitted"
        except Refused:
            return "held"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve_job, jobs)) == ["admitted", "held"]
    assert requests(rig) == 5
    assert len(rig.store.rows("SELECT * FROM reservations")) == 1
    assert len(rig.registry.rows("SELECT * FROM model_reservations")) == 1
    assert sorted(row["state"] for row in rig.store.rows("SELECT state FROM jobs")) == [
        "queued",
        "running",
    ]


def test_held_db_group_helpers_never_open_nested_begin(rig, monkeypatch):
    with rig.store.transaction() as db:

        def nested_transaction():
            raise AssertionError("Nested transaction attempted")

        monkeypatch.setattr(rig.store, "transaction", nested_transaction)
        assert rig.briefings.current(rig.attempt, admission=True, db=db)
        assert rig.briefings.attempt(rig.attempt.id, db=db) == rig.attempt
        assert rig.briefings.run_state(KEY, db=db).active_batch == rig.attempt.batch_id
        assert rig.briefings.batch(rig.attempt.batch_id, db=db).requests == 0
        rig.briefings.record_work(rig.attempt.batch_id, source_ids=[1], db=db)
        rig.briefings.record_speech(
            rig.attempt.batch_id,
            45,
            source_ids=[1],
            token="native1",
            admission=lambda held: admission(rig, held, dict(role="group_briefing")),
            db=db,
        )
        assert rig.briefings.batch(rig.attempt.batch_id, db=db).speech_seconds == 45


def test_speech_charge_is_guarded_atomic_and_deduplicates_exact_local_stage(rig):
    def gate(db):
        admission(rig, db, dict(role="group_briefing"))

    first = rig.briefings.record_speech(
        rig.attempt.batch_id, 900, source_ids=[1], token="source1:rev0", admission=gate
    )
    assert first.requests == 0 and first.speech_seconds == 900
    assert (
        rig.briefings.record_speech(
            rig.attempt.batch_id,
            900,
            source_ids=[1],
            token="source1:rev0",
            admission=gate,
        )
        == first
    )
    for seconds, token in ((901, "source1:rev0"), (301, "source2:rev0")):
        with pytest.raises(Refused):
            rig.briefings.record_speech(
                rig.attempt.batch_id,
                seconds,
                source_ids=[2],
                token=token,
                admission=gate,
            )
        assert rig.briefings.batch(rig.attempt.batch_id) == first
    hold(rig, "group_off")
    with pytest.raises(Refused):
        rig.briefings.record_speech(
            rig.attempt.batch_id,
            1,
            source_ids=[2],
            token="source2:rev0",
            admission=gate,
        )
    assert rig.briefings.batch(rig.attempt.batch_id) == first
    no_spending(rig)


def test_pending_guidance_refresh_keeps_frozen_sources_and_old_chunk_identity(rig):
    original = rig.attempt.snapshot
    chunk = rig.briefings.store_chunk(rig.attempt.id, [1], output(1))
    rig.briefings.record_work(rig.attempt.batch_id, requests=1, source_ids=[1])
    hold(rig, "guidance")
    latest = rig.registry.guidance()
    current = rig.briefings.refresh_guidance(
        rig.attempt.id,
        guidance_hash=briefing_fingerprint(latest),
        route=ROUTES["group_briefing"],
    )
    assert current.snapshot.model_dump(
        exclude={"guidance_hash", "route"}
    ) == original.model_dump(exclude={"guidance_hash", "route"})
    assert rig.briefings.current(chunk)
    assert chunk.snapshot.guidance_hash == original.guidance_hash
    assert rig.briefings.chunk(current.snapshot, [1]) is None
    assert requests(rig) == 1
    with pytest.raises(Refused, match="unavailable chunk"):
        rig.briefings.publish(
            current.id, output(1), covered_ids=[1], chunk_ids=[chunk.id]
        )


def test_deliberate_retry_can_use_latest_guidance_without_native_expansion_or_cap_reuse(
    rig,
):
    original = rig.attempt
    rig.briefings.record_work(original.batch_id, requests=2, source_ids=[1])
    rig.briefings.fail(original.id, "Authored counted failure")
    source(rig, 2)
    hold(rig, "guidance")
    latest = rig.registry.guidance()
    retry = rig.briefings.retry(
        original.id,
        guidance_hash=briefing_fingerprint(latest),
        route=ROUTES["group_briefing"],
    )
    assert retry.retry_of == original.id and retry.snapshot.intended_ids == [1]
    assert retry.snapshot.model_dump(
        exclude={"guidance_hash", "route"}
    ) == original.snapshot.model_dump(exclude={"guidance_hash", "route"})
    assert retry.snapshot.guidance_hash == briefing_fingerprint(latest)
    assert rig.briefings.batch(retry.batch_id).requests == 0
    assert rig.briefings.batch(original.batch_id).requests == 2


def test_older_failed_retry_cannot_regenerate_sources_now_covered_by_newer_final(rig):
    failed = rig.attempt
    rig.briefings.fail(failed.id, "Authored old failure")
    source(rig, 2)
    newer = rig.briefings.create_attempt(freeze(rig, [1, 2]))
    final = rig.briefings.publish(newer.id, output(1, 2), covered_ids=[1, 2])
    with pytest.raises(Refused, match="Completed briefing sources"):
        rig.briefings.retry(failed.id, guidance_hash="latest-group-guidance")
    assert rig.briefings.state(KEY, DAY).final == final


async def test_private_path_preserves_on_dispatch_guidance_and_never_charges_group_batch(
    rig,
):
    job, calls = queue(rig.store, "private", "outgoing_rewrite"), []

    class PrivateEngine(ControlledEngine):
        async def generate(self, role, value):
            self.calls.append((role, copy.deepcopy(value)))
            return EngineResponse(
                {"text": "Authored private rewrite."}, actual_nanos=37
            )

    result = await rig.work.run(
        PrivateEngine(),
        job,
        {},
        lambda: True,
        on_dispatch=lambda: calls.append("private-dispatch"),
    )
    assert result.text == "Authored private rewrite." and calls == ["private-dispatch"]
    assert requests(rig) == 0 and not rig.accounting_calls
    assert rig.store.rows("SELECT family FROM reservations") == [dict(family="daily")]
    assert rig.registry.budget(rig.store.clock, "UTC")["spent"] == 37
    assert (
        rig.registry.budget(rig.store.clock, "UTC", family="group_briefing")["spent"]
        == 0
    )
    with pytest.raises(Refused, match="group briefing role"):
        rig.work.reserve(
            queue(rig.store, "wrong-hook", "outgoing_rewrite"), "mimo", **hooks(rig)
        )
