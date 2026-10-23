"""ADR 48 MB2: each member's budgets, pseudonym and turns.

A member's spending counts only against their own Daily and Group Briefing
Budgets, which start at $0.50 for a friend and which only the owner sets.
Each member sends their own provider pseudonym, and members take turns for
model work and transcription.
"""

import asyncio
from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.application.fair_lock import FairLock
from enloq.inference.work import (
    FLAT_UNKNOWN,
    MEMBER_BUDGET,
    BudgetReached,
    BudgetReserve,
    ModelWork,
    PaidSpeech,
)
from enloq.storage.accounts import AccountRegistry
from enloq.storage.members import OWNER
from enloq.storage.store import Store
from fastapi.testclient import TestClient

from backend.tests.test_device_trust import ORIGIN, bearer, remote
from backend.tests.test_members import add_member, owner_phone


def befriend(registry, member="friend", account=2):
    with registry.transaction() as db:
        db.execute(
            "INSERT INTO members (id,role,state,home_account,approved_at) VALUES (?,?,?,?,1)",
            (member, "member", "active", account),
        )
    registry.register(account, f"{member}.session", member=member)


@pytest.fixture
def rig(tmp_path):
    clock = [2_000_000_000.0]
    registry = AccountRegistry(tmp_path)
    befriend(registry)
    registry.register(1, "owner.session")
    stores = {
        account: Store(
            tmp_path / str(account) / "rewriting.sqlite3",
            account_id=account,
            clock=lambda: clock[0],
            timezone="UTC",
            shared_path=registry.path,
        )
        for account in (1, 2)
    }
    yield SimpleNamespace(registry=registry, stores=stores, clock=clock)
    for store in stores.values():
        store.close()
    registry.connections.close()


def queue(store, identity, role="incoming_rewrite"):
    job = dict(
        id=identity,
        scope=f"{store.account_id}/user:42",
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


def reserve(rig, account, identity, **options):
    store = rig.stores[account]
    return ModelWork(store).reserve(queue(store, identity), "mimo", **options)


def test_a_members_spending_counts_only_against_their_own_budget(rig):
    for index in range(MEMBER_BUDGET // FLAT_UNKNOWN):
        reserve(rig, 2, f"friend-{index}")
    with pytest.raises(BudgetReached):
        reserve(rig, 2, "friend-over")
    # The owner's own budget is untouched by the friend's spending.
    reserve(rig, 1, "owner-1")
    friend = rig.registry.budget(lambda: rig.clock[0], "UTC", member="friend")
    owner = rig.registry.budget(lambda: rig.clock[0], "UTC")
    assert (friend["spent"], friend["limit"], friend["reached"]) == (
        MEMBER_BUDGET,
        MEMBER_BUDGET,
        True,
    )
    assert (owner["spent"], owner["reached"]) == (FLAT_UNKNOWN, False)
    members = rig.registry.rows(
        "SELECT DISTINCT member FROM model_reservations ORDER BY member"
    )
    assert [row["member"] for row in members] == ["friend", OWNER]


def test_the_quarter_rule_reads_the_members_own_budget(rig):
    for index in range(38):  # $0.38 of $0.50: less than a quarter remains.
        reserve(rig, 2, f"friend-{index}")
    with pytest.raises(BudgetReserve):
        reserve(rig, 2, "friend-older", reserve=0.25)
    reserve(rig, 1, "owner-older", reserve=0.25)


def test_a_group_stage_checks_the_members_group_briefing_budget(rig):
    rig.registry.set_budget(0, family="group_briefing", member="friend")
    with pytest.raises(BudgetReached):
        ModelWork(rig.stores[2]).check_budget("group_briefing")
    ModelWork(rig.stores[1]).check_budget("group_briefing")


def test_paid_speech_counts_against_its_members_budget(rig):
    rig.registry.set_budget(0, member="friend")
    with pytest.raises(BudgetReached):
        PaidSpeech(rig.stores[2], "speech-1", "2/user:42", "rev").reserve()
    PaidSpeech(rig.stores[1], "speech-2", "1/user:42", "rev").reserve()
    rows = rig.registry.rows("SELECT id,member,engine FROM model_reservations")
    assert rows == [{"id": "speech-2", "member": OWNER, "engine": "speech"}]


def test_budgets_start_at_fifty_cents_for_friends_and_keep_the_owners(tmp_path):
    registry = AccountRegistry(tmp_path)
    with registry.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('daily_budget','3000000000')"
        )
        db.execute("DELETE FROM member_settings")
    registry.connections.close()
    registry = AccountRegistry(tmp_path)
    try:
        befriend(registry)
        assert registry.budget(member="friend")["limit"] == MEMBER_BUDGET
        assert (
            registry.budget(member="friend", family="group_briefing")["limit"]
            == MEMBER_BUDGET
        )
        assert registry.budget()["limit"] == 3_000_000_000
        assert registry.budget(family="group_briefing")["limit"] == 2_000_000_000
        registry.set_budget(250_000_000, member="friend")
        assert registry.budget(member="friend")["changes"][0] == {
            "at": registry.budget(member="friend")["changes"][0]["at"],
            "old": MEMBER_BUDGET,
            "new": 250_000_000,
        }
        assert registry.budget()["changes"] == []
    finally:
        registry.connections.close()


def test_only_the_owner_sets_a_friends_budget(tmp_path):
    app = create_app(remote(tmp_path))
    friend = add_member(app)
    owner = owner_phone(app)
    with TestClient(app, base_url=ORIGIN) as client:
        body = {"family": "daily", "limit_usd": "2.00"}
        refused = client.post(
            "/api/people/friend/budget", json=body, headers=bearer(friend)
        )
        assert refused.status_code == 403
        changed = client.post(
            "/api/people/friend/budget", json=body, headers=bearer(owner)
        )
        assert changed.status_code == 200
        assert changed.json()["budget"]["limit"] == 2_000_000_000
        assert changed.json()["group_budget"]["limit"] == MEMBER_BUDGET
        # The owner's own budgets stay in Settings → Budget.
        unknown = client.post(
            "/api/people/owner/budget", json=body, headers=bearer(owner)
        )
        assert unknown.status_code == 400
        settings = client.get("/api/settings", headers=bearer(friend)).json()
        assert settings["budget"]["limit"] == 2_000_000_000
        assert (
            client.get("/api/settings", headers=bearer(owner)).json()["budget"]["limit"]
            == 1_000_000_000
        )


def test_each_member_has_their_own_provider_pseudonym(tmp_path):
    registry = AccountRegistry(tmp_path)
    with registry.transaction() as db:
        db.execute("INSERT INTO metadata VALUES ('provider_user','authored-pseudonym')")
        db.execute("DELETE FROM member_settings")
    registry.connections.close()
    registry = AccountRegistry(tmp_path)
    try:
        assert registry.provider_user() == "authored-pseudonym"
        friend = registry.provider_user("friend")
        assert friend != "authored-pseudonym" and len(friend) == 32
        assert registry.provider_user("friend") == friend
    finally:
        registry.connections.close()


async def test_transcription_turns_rotate_between_members():
    lock = FairLock()
    order = []
    owner, friend = lock.handle(OWNER), lock.handle("friend")
    await owner.__aenter__()
    assert owner.locked() and friend.locked()

    async def note(handle, name):
        async with handle:
            order.append(name)

    tasks = [
        asyncio.create_task(note(owner, "owner-2")),
        asyncio.create_task(note(owner, "owner-3")),
    ]
    await asyncio.sleep(0)
    tasks.append(asyncio.create_task(note(friend, "friend-1")))
    await asyncio.sleep(0)
    await owner.__aexit__(None, None, None)
    await asyncio.gather(*tasks)
    # The friend's one note waits behind one of the owner's, not all of them.
    assert order == ["owner-2", "friend-1", "owner-3"]
    assert not lock.locked()


async def test_a_cancelled_waiter_leaves_its_turn(tmp_path):
    lock = FairLock()
    owner, friend = lock.handle(OWNER), lock.handle("friend")
    await owner.__aenter__()
    waiting = asyncio.create_task(friend.__aenter__())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await owner.__aexit__(None, None, None)
    assert not lock.locked()
    async with friend:
        assert lock.locked()


async def test_dispatch_rotates_between_members_before_accounts(tmp_path, monkeypatch):
    import enloq.application.accounts as accounts

    app = create_app(remote(tmp_path))
    manager = app.state.accounts
    claimed = []

    def runtime(identity, member):
        pending = [
            {"id": f"job-{identity}", "role": "incoming_rewrite", "payload": "{}"}
        ]
        work = SimpleNamespace(
            next_job=lambda **_: pending[0] if pending else None,
            claim=lambda job: claimed.append(job["id"]) or pending.pop() or True,
        )
        assistant = SimpleNamespace(
            admit_ai=lambda: None,
            rewriting=work,
            briefings=SimpleNamespace(next_job=lambda **_: None),
            memory=SimpleNamespace(local_idle=lambda: True),
            store=SimpleNamespace(rows=lambda *_: []),
        )
        return SimpleNamespace(
            accepting=True, assistant=assistant, served=0, member=member, jobs=set()
        )

    async def run(runtime, job):
        manager.active.pop(asyncio.current_task(), None)

    monkeypatch.setattr(accounts, "reconcile_admission", lambda store: None)
    monkeypatch.setattr(manager, "_run", run)
    manager.group_rollout = SimpleNamespace(tick=lambda: None)
    manager.runtimes = {
        1: runtime(1, OWNER),
        3: runtime(3, OWNER),
        2: runtime(2, "friend"),
    }
    manager.dispatch()
    await asyncio.sleep(0)
    # One owner account, then the friend, then the owner's other account.
    assert claimed == ["job-1", "job-2", "job-3"]
