"""GB3 accounting over authored stores and fake providers; no live requests."""

import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from enloq.config import Settings
from enloq.cutover import retire
from enloq.domain.group_filtering import FILTER_ROLES
from enloq.domain.models import Refused
from enloq.inference import work
from enloq.inference.base import EngineResponse, ProviderRefused
from enloq.inference.openrouter import MODEL
from enloq.inference.streaming import read_stream
from enloq.inference.work import FLAT_UNKNOWN, BudgetReserve, ModelWork
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import SCHEMA_VERSION, Store

from backend.tests.test_integration import legacy_store

DAILY, GROUP = "daily", "group_briefing"
OLD_RESERVATION = "id,bucket,engine,reserved,actual,status,created"
OLD_RECEIPT = "id,role,model,provider,usage,error"
OLD_SHARED_RESERVATION = "account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created"
OLD_SHARED_RECEIPT = "account_id," + OLD_RECEIPT


def accounting(tmp_path, *, shared=True, now=None):
    clock = [now or 2_000_000_000.0]
    registry = AccountRegistry(tmp_path)
    stores = [
        Store(
            tmp_path / str(account) / "rewriting.sqlite3",
            account_id=account,
            clock=lambda: clock[0],
            timezone="UTC",
            shared_path=registry.path if shared else None,
        )
        for account in (1, 2)
    ]
    return SimpleNamespace(
        registry=registry,
        stores=stores,
        owners=[ModelWork(store) for store in stores],
        clock=clock,
    )


def queue(store, identity, role=GROUP):
    job = dict(
        id=identity,
        scope=f"{store.account_id}/channel:42",
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


def budget(rig, family=DAILY):
    return rig.registry.budget(lambda: rig.clock[0], "UTC", family=family)


@pytest.fixture
def authored_group_roles(monkeypatch):
    # GB3 does not activate future generation schemas. Exercise ModelWork's
    # accounting seam using the existing authored Rewrite output contract.
    validate = work.validate_output
    monkeypatch.setattr(work, "ROLES", work.ROLES | work.GROUP_ROLES)
    monkeypatch.setattr(work, "BACKGROUND", work.BACKGROUND | work.GROUP_ROLES)
    monkeypatch.setattr(
        work,
        "validate_output",
        lambda role, response, packet: validate(
            "outgoing_rewrite" if role in work.GROUP_ROLES else role, response, packet
        ),
    )


class HeldEngine:
    name, model, provider = "mimo", "authored-budget-model", "xiaomi"

    def __init__(self, *, prepare=False, outcome="valid", actual=123):
        self.prepared, self.release = asyncio.Event(), asyncio.Event()
        self.started, self.finish = asyncio.Event(), asyncio.Event()
        self.prepare_calls, self.calls = [], []
        self.outcome, self.actual = outcome, actual
        if not prepare:
            self.release.set()

    async def prepare(self, packet):
        self.prepare_calls.append(packet)
        self.prepared.set()
        await self.release.wait()

    async def generate(self, role, packet):
        self.calls.append(role)
        self.started.set()
        await self.finish.wait()
        if self.outcome == "refused":
            raise ProviderRefused("Authored no-work refusal")
        if self.outcome == "error":
            raise ConnectionError("Authored unknown outcome")
        if self.outcome in {"cancel", "cancel_unknown"}:
            error = asyncio.CancelledError()
            if self.outcome == "cancel":
                error.receipt = EngineResponse({}, actual_nanos=self.actual)
            raise error
        return EngineResponse(
            "{invalid" if self.outcome == "invalid" else {"text": "Authored result"},
            actual_nanos=self.actual,
        )


def test_old_shared_and_local_ledgers_gain_daily_family_without_rewriting_values(
    tmp_path,
):
    shared_path, local_path = (
        tmp_path / "accounts.sqlite3",
        tmp_path / "content.sqlite3",
    )
    with sqlite3.connect(shared_path) as db:
        db.executescript(
            "CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
            "CREATE TABLE model_reservations(account_id INTEGER,id TEXT,scope TEXT,role TEXT,input_revision TEXT,bucket TEXT,engine TEXT,reserved INTEGER,actual INTEGER,status TEXT,created REAL,PRIMARY KEY(account_id,id));"
            "CREATE TABLE model_receipts(account_id INTEGER,id TEXT,role TEXT,model TEXT,provider TEXT,usage TEXT,error TEXT,PRIMARY KEY(account_id,id));"
            "CREATE TABLE budget_changes(at REAL,old INTEGER,new INTEGER);"
        )
        db.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("daily_budget", "765432100"),
                ("budget_day", "2033-05-18"),
                ("processing_threshold", "345"),
                ("format_failures", '{"incoming_rewrite":2}'),
            ],
        )
        db.execute("INSERT INTO budget_changes VALUES (17,1000000000,765432100)")
        for identity, status, actual, engine in (
            ("receipt", "receipted", 20_000_003, "muse"),
            ("unknown", "unknown", None, "mimo"),
            ("refused", "refused", 0, "mimo"),
            ("offline", "unknown", None, "offline"),
        ):
            db.execute(
                "INSERT INTO model_reservations VALUES (1,?,'1/user:42','analysis',?,'day:2033-05-18',?,777,?,?,19)",
                (identity, identity, engine, actual, status),
            )
            db.execute(
                "INSERT INTO model_receipts VALUES (1,?,'analysis','authored','meta',?,?)",
                (identity, '{"cost":0}', "Preserved " + status),
            )
    # A genuine schema-30 shape, including ledger columns without family.
    schema = Path(work.__file__).parents[1] / "storage" / "schema.sql"
    with sqlite3.connect(local_path) as db:
        db.executescript(
            schema.read_text()
            .replace(
                f"PRAGMA user_version = {SCHEMA_VERSION}", "PRAGMA user_version = 30"
            )
            .replace(", family TEXT NOT NULL DEFAULT 'daily'", "")
            .replace(",family TEXT NOT NULL DEFAULT 'daily'", "")
        )
        db.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("account_id", "1"),
                ("mode", "offline"),
                ("timezone", "UTC"),
                ("daily_budget", "123456700"),
                ("budget_day", "2033-05-18"),
                ("reactions_introduced", "1700000000"),
            ],
        )
        db.execute(
            "INSERT INTO reservations VALUES ('local','day:2033-05-18','muse',789,321,'receipted',22)"
        )
        db.execute(
            "INSERT INTO receipts VALUES ('local','analysis','old-model','old-provider','{}','Kept error')"
        )
        db.execute("INSERT INTO budget_changes VALUES (23,1000000000,123456700)")

    def old_rows(path, columns):
        with sqlite3.connect(path) as db:
            return {
                table: db.execute(
                    f"SELECT {fields} FROM {table} ORDER BY rowid"
                ).fetchall()
                for table, fields in columns.items()
            }

    shared_columns = {
        "metadata": "key,value",
        "model_reservations": OLD_SHARED_RESERVATION,
        "model_receipts": OLD_SHARED_RECEIPT,
        "budget_changes": "at,old,new",
    }
    local_columns = {
        "metadata": "key,value",
        "reservations": OLD_RESERVATION,
        "receipts": OLD_RECEIPT,
        "budget_changes": "at,old,new",
    }
    shared_before, local_before = (
        old_rows(shared_path, shared_columns),
        old_rows(local_path, local_columns),
    )
    for _ in range(2):
        registry = AccountRegistry(tmp_path)
        store = Store(
            local_path, account_id=1, timezone="UTC", shared_path=registry.path
        )
        assert (
            store.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION == 38
        )
        assert old_rows(shared_path, shared_columns) == shared_before
        local_after = old_rows(local_path, local_columns)
        # ADR 55 Q20 arms the memory start, an additive local key.
        local_after["metadata"] = [
            row for row in local_after["metadata"] if row[0] != "memory_start"
        ]
        assert local_after == local_before
        for table in ("model_reservations", "model_receipts", "budget_changes"):
            assert all(
                row["family"] == DAILY
                for row in registry.rows(f"SELECT * FROM {table}")
            )
        for table in ("reservations", "receipts", "budget_changes"):
            assert all(
                row["family"] == DAILY for row in store.rows(f"SELECT * FROM {table}")
            )

        def clock():
            return datetime(2033, 5, 18, 12, tzinfo=timezone.utc).timestamp()

        assert registry.budget(clock, "UTC")["limit"] == 765_432_100
        assert registry.budget(clock, "UTC")["spent"] == 20_000_003 + FLAT_UNKNOWN
        assert registry.budget(clock, "UTC", family=GROUP)["limit"] == 2_000_000_000
        assert registry.budget(clock, "UTC", family=GROUP)["spent"] == 0
        assert registry.budget(clock, "UTC", family=GROUP)["changes"] == []
        with store.transaction() as db:
            assert work.daily_budget(db, "metadata") == 123_456_700
            assert work.counted(db, "reservations", "day:2033-05-18") == 321
        store.close()
        registry.connections.close()


def test_default_interfaces_keep_daily_shape_and_purpose_mapping(tmp_path):
    rig = accounting(tmp_path)
    assert budget(rig)["limit"] == 1_000_000_000
    assert budget(rig, GROUP)["limit"] == 2_000_000_000
    assert set(budget(rig)) == {
        "day",
        "limit",
        "spent",
        "calls",
        "unknown",
        "flat_unknown",
        "reached",
        "resets_at",
        "changes",
    }
    assert work.GROUP_ROLES <= work.ROLES & work.BACKGROUND
    assert {work.budget_family(role) for role in work.GROUP_ROLES} == {GROUP}
    assert {work.budget_family(role) for role in FILTER_ROLES} == {GROUP}
    assert {
        work.budget_family(role)
        for role in work.ROLES - work.GROUP_ROLES - FILTER_ROLES
    } == {DAILY}
    assert work.budget_family("future_private_role") == DAILY
    rig.owners[0].reserve(queue(rig.stores[0], "composer", "outgoing_rewrite"), "mimo")
    rig.owners[1].reserve(queue(rig.stores[1], "briefing"), "mimo")
    assert rig.registry.usage()[0]["calls"] == 1
    assert rig.registry.usage(family=GROUP)[0]["calls"] == 1
    assert "family" not in rig.registry.usage()[0]


@pytest.mark.parametrize("family", ["groups", "", None, True, [GROUP]])
def test_invalid_family_refuses_instead_of_selecting_daily(tmp_path, family):
    rig = accounting(tmp_path)
    for read in (
        lambda: rig.registry.budget(family=family),
        lambda: rig.registry.usage(family=family),
        lambda: rig.registry.set_budget(0, family=family),
    ):
        with pytest.raises(Refused, match="budget family"):
            read()
    with rig.stores[0].transaction() as db:
        for read in (
            lambda: work.counted(db, "reservations", "day:x", family=family),
            lambda: work.daily_budget(db, "metadata", family=family),
        ):
            with pytest.raises(Refused, match="budget family"):
                read()
    assert budget(rig)["limit"] == 1_000_000_000 and not budget(rig)["changes"]


@pytest.mark.parametrize("family", [DAILY, GROUP])
@pytest.mark.parametrize("value", [True, False, -1, 10_000_000_001, 1.5, "100"])
def test_budget_values_require_integer_nanos_in_range(tmp_path, family, value):
    registry = AccountRegistry(tmp_path)
    with pytest.raises(Refused, match="between"):
        registry.set_budget(value, family=family)
    assert not registry.rows("SELECT * FROM budget_changes")


def test_accounts_share_each_pool_atomically_while_families_are_independent(tmp_path):
    rig = accounting(tmp_path)
    for family in (DAILY, GROUP):
        rig.registry.set_budget(FLAT_UNKNOWN, family=family)
    attempts = [
        (0, queue(rig.stores[0], "group-one")),
        (1, queue(rig.stores[1], "group-two")),
        (0, queue(rig.stores[0], "daily", "outgoing_rewrite")),
    ]
    barrier = threading.Barrier(3)

    def reserve(attempt):
        owner, job = attempt
        barrier.wait(timeout=5)
        try:
            rig.owners[owner].reserve(job, "mimo")
            return job["role"]
        except Refused:
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(reserve, attempts))
    assert outcomes.count(GROUP) == 1 and outcomes.count("outgoing_rewrite") == 1
    assert budget(rig)["spent"] == budget(rig, GROUP)["spent"] == FLAT_UNKNOWN
    assert budget(rig)["calls"] == budget(rig, GROUP)["calls"] == 1
    assert len(rig.registry.rows("SELECT * FROM model_reservations")) == 2


@pytest.mark.parametrize("exhausted", [DAILY, GROUP])
def test_zero_or_exhausted_family_never_borrows_the_other_allowance(
    tmp_path, exhausted
):
    rig = accounting(tmp_path)
    other = GROUP if exhausted == DAILY else DAILY
    rig.registry.set_budget(0, family=exhausted)
    roles = {DAILY: "outgoing_rewrite", GROUP: "group_briefing_chunk"}
    for index, owner in enumerate(rig.owners):
        owner.reserve(queue(rig.stores[index], "allowed", roles[other]), "mimo")
        with pytest.raises(Refused, match="budget reached"):
            owner.reserve(queue(rig.stores[index], "blocked", roles[exhausted]), "mimo")
    assert budget(rig, exhausted)["spent"] == budget(rig, exhausted)["calls"] == 0
    assert budget(rig, other)["spent"] == 2 * FLAT_UNKNOWN
    rig.registry.set_budget(2 * FLAT_UNKNOWN, family=other)
    with pytest.raises(Refused, match="budget reached"):
        rig.owners[0].reserve(queue(rig.stores[0], "full", roles[other]), "mimo")


def test_group_full_allowance_preserves_the_private_older_quarter_reserve(tmp_path):
    rig = accounting(tmp_path)
    for family in (DAILY, GROUP):
        rig.registry.set_budget(4 * FLAT_UNKNOWN, family=family)
    for index in range(3):
        rig.owners[0].reserve(
            queue(rig.stores[0], f"group-{index}"), "mimo", reserve=0.25
        )
        rig.owners[1].reserve(
            queue(rig.stores[1], f"daily-{index}", "incoming_rewrite"),
            "mimo",
            reserve=0.25,
        )
    rig.owners[0].reserve(queue(rig.stores[0], "group-four"), "mimo", reserve=0.25)
    with pytest.raises(BudgetReserve):
        rig.owners[1].reserve(
            queue(rig.stores[1], "older-four", "incoming_rewrite"), "mimo", reserve=0.25
        )
    assert budget(rig, GROUP)["spent"] == 4 * FLAT_UNKNOWN
    assert budget(rig)["spent"] == 3 * FLAT_UNKNOWN


def test_runtime_saves_preserve_spending_and_filter_changes_by_family(tmp_path):
    rig = accounting(tmp_path)
    rig.owners[0].reserve(queue(rig.stores[0], "daily", "outgoing_rewrite"), "mimo")
    rig.owners[1].reserve(queue(rig.stores[1], "group"), "mimo")
    before = rig.registry.rows("SELECT * FROM model_reservations")
    for family, default in ((DAILY, 1_000_000_000), (GROUP, 2_000_000_000)):
        rig.registry.set_budget(0, lambda: 10, family=family)
        assert budget(rig, family)["reached"]
        rig.registry.set_budget(10_000_000_000, lambda: 11, family=family)
        rig.registry.set_budget(10_000_000_000, lambda: 12, family=family)
        assert budget(rig, family)["spent"] == FLAT_UNKNOWN
        assert budget(rig, family)["changes"] == [
            {"at": 11, "old": 0, "new": 10_000_000_000},
            {"at": 10, "old": default, "new": 0},
        ]
    assert rig.registry.rows("SELECT * FROM model_reservations") == before


async def test_group_zero_budget_holds_before_preparation_or_dispatch_callback(
    tmp_path, authored_group_roles
):
    rig = accounting(tmp_path)
    rig.registry.set_budget(0, family=GROUP)
    engine, dispatched = HeldEngine(), []
    with pytest.raises(Refused, match="Group Briefing budget"):
        await rig.owners[0].run(
            engine,
            queue(rig.stores[0], "held"),
            {},
            lambda: True,
            on_dispatch=lambda: dispatched.append(True),
        )
    assert not engine.prepare_calls and not engine.calls and not dispatched
    assert not rig.registry.rows("SELECT * FROM model_reservations")


async def test_limit_saved_during_group_preparation_is_rechecked_at_dispatch(
    tmp_path, authored_group_roles
):
    rig = accounting(tmp_path)
    engine = HeldEngine(prepare=True)
    task = asyncio.create_task(
        rig.owners[0].run(engine, queue(rig.stores[0], "preparing"), {}, lambda: True)
    )
    await engine.prepared.wait()
    rig.registry.set_budget(0, family=GROUP)
    engine.release.set()
    with pytest.raises(Refused, match="Group Briefing budget"):
        await task
    assert not engine.calls and not rig.stores[0].rows("SELECT * FROM reservations")
    assert not rig.registry.rows("SELECT * FROM model_receipts")


@pytest.mark.parametrize("family", [DAILY, GROUP])
async def test_exact_route_refusal_precedes_dispatch_callbacks_and_spending(
    tmp_path, authored_group_roles, family
):
    rig = accounting(tmp_path)
    role = "incoming_rewrite" if family == DAILY else "group_briefing_image"
    callbacks = []

    class UnavailableRoute(HeldEngine):
        async def prepare(self, packet):
            raise ProviderRefused("Exact authored MiMo / Xiaomi route unavailable")

    engine = UnavailableRoute()
    job = queue(rig.stores[0], "no-route", role)
    with pytest.raises(ProviderRefused):
        await rig.owners[0].run(
            engine, job, {}, lambda: True, on_dispatch=lambda: callbacks.append(True)
        )
    assert not callbacks and not engine.calls
    assert not rig.stores[0].rows("SELECT * FROM reservations")
    assert not rig.registry.rows("SELECT * FROM model_receipts")
    assert rig.stores[0].rows("SELECT state FROM jobs") == [{"state": "refused"}]
    assert budget(rig, family)["spent"] == 0


@pytest.mark.parametrize("family", [DAILY, GROUP])
@pytest.mark.parametrize("superseded", [False, True])
async def test_late_receipt_settles_original_dispatch_day_and_family(
    tmp_path, authored_group_roles, family, superseded
):
    start = datetime(2033, 5, 18, 23, 59, tzinfo=timezone.utc).timestamp()
    rig = accounting(tmp_path, now=start)
    role = "outgoing_rewrite" if family == DAILY else GROUP
    engine, store = HeldEngine(actual=4321), rig.stores[0]
    job = queue(store, "midnight", role)
    task = asyncio.create_task(rig.owners[0].run(engine, job, {}, lambda: True))
    await engine.started.wait()
    assert budget(rig, family)["spent"] == FLAT_UNKNOWN
    rig.clock[0] += 120
    rig.registry.set_budget(0, family=family)
    if superseded:
        with store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='superseded',role='changed-source' WHERE id=?",
                (job["id"],),
            )
    engine.finish.set()
    if superseded:
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await task).text == "Authored result"
    assert budget(rig, family)["day"] == "2033-05-19"
    assert budget(rig, family)["spent"] == 0
    assert store.rows("SELECT bucket,family,status,actual FROM reservations") == [
        {
            "bucket": "day:2033-05-18",
            "family": family,
            "status": "receipted",
            "actual": 4321,
        }
    ]
    assert rig.registry.rows("SELECT bucket,family,actual FROM model_reservations") == [
        {"bucket": "day:2033-05-18", "family": family, "actual": 4321}
    ]
    for rows in (
        store.rows("SELECT family FROM receipts"),
        rig.registry.rows("SELECT family FROM model_receipts"),
    ):
        assert rows == [{"family": family}]
    with store.transaction() as db:
        assert (
            work.counted(
                db, "shared.model_reservations", "day:2033-05-18", family=family
            )
            == 4321
        )
        assert (
            work.counted(
                db,
                "shared.model_reservations",
                "day:2033-05-18",
                family=GROUP if family == DAILY else DAILY,
            )
            == 0
        )


def test_either_family_future_dispatch_prevents_rollback_from_reopening_both(tmp_path):
    rig = accounting(tmp_path)
    for family in (DAILY, GROUP):
        rig.registry.set_budget(FLAT_UNKNOWN, family=family)
    rig.clock[0] += 86400
    future = budget(rig, GROUP)["day"]
    rig.owners[0].reserve(queue(rig.stores[0], "future-group"), "mimo")
    rig.owners[1].reserve(
        queue(rig.stores[1], "future-daily", "outgoing_rewrite"), "mimo"
    )
    rig.clock[0] -= 2 * 86400
    for index, family in enumerate((GROUP, DAILY)):
        assert budget(rig, family)["day"] == future and budget(rig, family)["reached"]
        role = GROUP if family == GROUP else "outgoing_rewrite"
        with pytest.raises(Refused, match="budget reached"):
            rig.owners[index].reserve(
                queue(rig.stores[index], "rolled-back", role), "mimo"
            )


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize(
    "outcome,actual,status,spent",
    [
        ("valid", 123, "receipted", 123),
        ("invalid", 19, "receipted", 19),
        ("valid", None, "unknown", FLAT_UNKNOWN),
        ("error", None, "unknown", FLAT_UNKNOWN),
        ("cancel", 23, "receipted", 23),
        ("cancel_unknown", None, "unknown", FLAT_UNKNOWN),
        ("refused", None, "refused", 0),
    ],
)
async def test_group_outcomes_settle_once_and_keep_invalid_interrupted_unknown_costs(
    tmp_path, authored_group_roles, shared, outcome, actual, status, spent
):
    rig = accounting(tmp_path, shared=shared)
    store, owner = rig.stores[0], rig.owners[0]
    job = queue(store, "first", "group_briefing_chunk")
    engine = HeldEngine(outcome=outcome, actual=actual)
    engine.finish.set()
    task = asyncio.create_task(owner.run(engine, job, {}, lambda: True))
    result = (await asyncio.gather(task, return_exceptions=True))[0]
    assert isinstance(result, BaseException) == (outcome != "valid")
    assert store.rows("SELECT family,status,actual FROM reservations") == [
        {
            "family": GROUP,
            "status": status,
            "actual": 0 if status == "refused" else actual,
        }
    ]
    bucket = "day:" + budget(rig)["day"]
    with store.transaction() as db:
        assert work.counted(db, "reservations", bucket, family=GROUP) == spent
        assert work.counted(db, "reservations", bucket) == 0
    if shared:
        assert budget(rig, GROUP)["spent"] == spent and budget(rig)["spent"] == 0
    before = store.rows("SELECT * FROM receipts")
    assert len(before) == 1 and before[0]["family"] == GROUP
    with pytest.raises(Refused, match="already dispatched"):
        await owner.run(engine, job, {}, lambda: True)
    with pytest.raises(Refused, match="already dispatched"):
        owner.reserve(job | {"role": "outgoing_rewrite"}, "mimo")
    assert (
        engine.calls == [job["role"]] and store.rows("SELECT * FROM receipts") == before
    )


@pytest.mark.parametrize(
    "charged,upstream,expected", [(0, 0.00008036, 80360), (0.0003, 0.0001, 300000)]
)
async def test_group_receipt_counts_larger_provider_cost_without_changing_engine_rules(
    tmp_path, authored_group_roles, charged, upstream, expected
):
    rig = accounting(tmp_path)
    event = {
        "model": MODEL,
        "provider": "Xiaomi",
        "choices": [
            {
                "delta": {"content": '{"text":"Authored output"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "cost": charged,
            "cost_details": {"upstream_inference_cost": upstream},
        },
    }

    class StreamReceipt(HeldEngine):
        async def generate(self, role, packet):
            response = httpx.Response(
                200, content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
            )
            return await read_stream(response, MODEL, lambda text: None)

    await rig.owners[0].run(
        StreamReceipt(), queue(rig.stores[0], "receipt"), {}, lambda: True
    )
    assert budget(rig, GROUP)["spent"] == expected and budget(rig)["spent"] == 0
    assert (
        json.loads(rig.stores[0].rows("SELECT usage FROM receipts")[0]["usage"])[
            "cost_details"
        ]["upstream_inference_cost"]
        == upstream
    )


def test_historical_cutover_import_retains_original_fields_in_daily_family(tmp_path):
    registry = legacy_store(tmp_path)
    registry.set_budget(765_432_100)
    with sqlite3.connect(tmp_path / "assistant.sqlite3") as db:
        db.execute(
            "INSERT INTO results VALUES (?,?)",
            (
                "cost",
                json.dumps(
                    {
                        "role": "analysis",
                        "model": "historical-model",
                        "provider": "historical-provider",
                        "usage": {"cost": 0.00001},
                        "engine": "muse",
                    }
                ),
            ),
        )
    retire(Settings(mode="personal", data=tmp_path), tmp_path / "service.lock")
    assert registry.rows(
        "SELECT family,reserved,actual,status FROM model_reservations"
    ) == [
        {"family": DAILY, "reserved": 600_000_000, "actual": None, "status": "unknown"}
    ]
    assert registry.rows("SELECT family,role,model,provider FROM model_receipts") == [
        {
            "family": DAILY,
            "role": "analysis",
            "model": "historical-model",
            "provider": "historical-provider",
        }
    ]
    assert registry.budget()["limit"] == 765_432_100
    assert registry.budget(family=GROUP)["limit"] == 2_000_000_000
    assert not registry.usage(family=GROUP)
    retire(Settings(mode="personal", data=tmp_path), tmp_path / "service.lock")
    assert registry.usage()[0]["calls"] == 1


@pytest.mark.parametrize("conflict", ["reservation", "receipt"])
def test_cutover_refuses_conflicting_budget_family_without_retiring_evidence(
    tmp_path, conflict
):
    registry = legacy_store(tmp_path)
    legacy = tmp_path / "assistant.sqlite3"
    with sqlite3.connect(legacy) as db:
        db.execute(
            "INSERT INTO results VALUES (?,?)",
            (
                "cost",
                json.dumps(
                    {
                        "role": "analysis",
                        "model": "historical-model",
                        "provider": "historical-provider",
                        "usage": {"cost": 0.00001},
                        "engine": "muse",
                    }
                ),
            ),
        )
    with registry.transaction() as db:
        if conflict == "reservation":
            db.execute(
                "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created,family) VALUES (1,'cost','authored-group','group_briefing','cost','day:2034-12-31','muse',600000000,NULL,'unknown',1,?)",
                (GROUP,),
            )
        else:
            db.execute(
                "INSERT INTO model_receipts (account_id,id,role,model,provider,usage,error,family) VALUES (1,'cost','group_briefing','authored-group-model','xiaomi','{}',NULL,?)",
                (GROUP,),
            )
    before = {
        table: registry.rows(f"SELECT * FROM {table}")
        for table in ("model_reservations", "model_receipts")
    }
    legacy_bytes = legacy.read_bytes()
    with pytest.raises(Refused, match="Accounting differs; legacy content retained"):
        retire(Settings(mode="personal", data=tmp_path), tmp_path / "service.lock")
    assert legacy.exists() and legacy.read_bytes() == legacy_bytes
    assert {
        table: registry.rows(f"SELECT * FROM {table}") for table in before
    } == before
