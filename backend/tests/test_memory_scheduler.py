"""Deterministic ADR 21 admission, consumption and cross-account idle checks."""

import asyncio
import json

import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings
from enloq.domain.models import Chat
from enloq.inference.base import ProviderRefused
from enloq.storage.accounts import AccountRegistry

from backend.tests.test_memory import DAY, KEY, conversation, fold, no_start, update
from backend.tests.test_rebuild import fixture, message

# Memory as before ADR 56's Off default: these chats learn without switching
# on; test_memory_default covers the default as shipped.
pytestmark = pytest.mark.usefixtures("remember_on")


def pending(app, now):
    conversation(app, now)
    key = f"{app.store.account_id}/user:42"
    app.memory.record(key, [f"{key}/1"])
    return key


def waiting_job(app, role="incoming_rewrite", state="queued", klass="new"):
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO jobs VALUES ('waiting',?,?,?, ?,?,?,NULL)",
            (
                KEY,
                role,
                "1",
                state,
                app.store.clock() + DAY,
                json.dumps({"class": klass}),
            ),
        )


def finish_waiting(app):
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET state='done' WHERE id='waiting'")


def test_exact_quiet_boundary_and_unchanged_sync_reads(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    pending(app, now)
    native = message(app, 200, outgoing=True)
    app.sync.ingest(native)
    now[0] += 1799
    assert not app.memory.schedule(KEY)
    app.sync.ingest(native)
    app.conversation(KEY)
    app.memory.view(KEY)
    app.store.put_chat(app.store.chat(KEY))
    now[0] += 1
    assert app.memory.schedule(KEY)


@pytest.mark.parametrize("outgoing", [True, False])
async def test_new_native_activity_defers_an_already_queued_update(tmp_path, outgoing):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    pending(app, now)
    app.memory.schedule(KEY)
    app.sync.ingest(message(app, 200, outgoing=outgoing), target=False)
    await fold(app)
    assert not app.engine.calls
    now[0] += 1800
    await fold(app)
    assert len(app.engine.calls) == 1


@pytest.mark.parametrize(
    "role,state,klass",
    [
        ("incoming_rewrite", "queued", "catchup"),
        ("incoming_rewrite", "queued", "older"),
        ("incoming_rewrite", "claimed", "new"),
        ("incoming_rewrite", "running", "new"),
        ("response_suggestions", "queued", "suggestions"),
        ("hostility_screen", "queued", "new"),
        ("transcript", "queued", "new"),
        ("transcript", "running", "new"),
    ],
)
async def test_every_waiting_or_preparing_communication_stage_blocks_memory(
    tmp_path, role, state, klass
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    pending(app, now)
    app.memory.schedule(KEY)
    waiting_job(app, role, state, klass)
    next_job = app.rewriting.next_job()
    assert next_job is None or next_job["role"] != "chat_memory"
    await fold(app)
    assert not app.engine.calls and app.rewriting.work.capacity.running == 0
    finish_waiting(app)
    now[0] += 60
    await fold(app)
    assert len(app.engine.calls) == 1


async def test_dispatch_rechecks_idle_after_route_preparation(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    pending(app, now)
    app.memory.schedule(KEY)

    async def prepare(packet):
        waiting_job(app, "response_suggestions")

    app.engine.prepare = prepare
    job = await fold(app)
    assert not app.engine.calls and not app.store.rows("SELECT * FROM reservations")
    assert (
        app.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))[0]["state"]
        == "queued"
    )


async def test_no_change_consumes_exact_versions_across_restart_and_old_reprocessing(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    pending(app, now)

    async def empty(role, packet):
        return update()

    app.engine.generate = empty
    app.memory.schedule(KEY)
    await fold(app)
    app.store.close()
    restarted = fixture(tmp_path, clock=lambda: now[0])
    restarted.store.recover()
    restarted.memory.record(KEY, [f"{KEY}/1"])
    assert not restarted.memory.candidates(KEY) and not restarted.memory.schedule(KEY)
    # Later disclosure of a previously omitted older source is independent of coverage.
    restarted.memory.record(KEY, [f"{KEY}/3"])
    restarted.memory.schedule(KEY)
    await fold(restarted)
    changed = restarted.store.message(KEY, 1).model_copy(
        update={"revision": 2, "text": "Corrected old fact."}
    )
    restarted.sync.ingest(changed, target=False)
    assert not restarted.memory.candidates(
        KEY
    )  # Merely cached/edited is not disclosed.
    restarted.memory.record(KEY, [f"{KEY}/1"])
    assert [r["message_id"] for r in restarted.memory.candidates(KEY)] == [1]


def test_every_disclosed_message_is_a_source_whatever_the_window(tmp_path):
    # ADR 55 Q19 B: no wait for the latest 20 or seven days.
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    no_start(app)
    for identity in range(1, 22):
        app.sync.ingest(message(app, identity, text="x" * 5000, outgoing=True))
    app.memory.record(KEY, [f"{KEY}/1", f"{KEY}/2", f"{KEY}/21"])
    assert [r["message_id"] for r in app.memory.candidates(KEY)] == [1, 2, 21]
    now[0] += 7 * DAY + 1
    assert [r["message_id"] for r in app.memory.candidates(KEY)] == [1, 2, 21]


async def test_budget_wait_retains_attempt_and_unknown_recovery_needs_retry(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    pending(app, now)
    AccountRegistry(tmp_path).set_budget(0)
    app.engine.name = "muse"
    identity = app.memory.schedule(KEY)
    await fold(app)
    now[0] += 60
    assert app.memory.job(KEY)["id"] == identity
    assert not app.memory.state(KEY)["error"] and not app.store.rows(
        "SELECT * FROM reservations"
    )
    AccountRegistry(tmp_path).set_budget(1)
    job = app.memory.job(KEY)
    app.rewriting.work.reserve(job, "muse")
    app.store.recover()
    assert app.memory.state(KEY)["error"] and not app.memory.schedule(KEY)
    assert AccountRegistry(tmp_path).budget(clock=app.store.clock)["spent"] > 0


async def test_refusal_backoff_also_waits_for_idle(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    pending(app, now)
    calls = []

    async def refuse(role, packet):
        calls.append(1)
        raise ProviderRefused("busy", retry_after=10)

    app.engine.generate = refuse
    app.memory.schedule(KEY)
    await fold(app)
    waiting_job(app)
    now[0] += 10000
    assert app.rewriting.next_job() is None
    await fold(app)
    assert len(calls) == 1


@pytest.mark.parametrize("limit", [1, 4])
async def test_one_memory_globally_fair_accounts_and_new_communication_mid_call(
    tmp_path, limit
):
    manager = AccountManager(Settings(data=tmp_path))
    manager._create(manager.registry.register(2, "authored.session"))
    manager.registry.set_concurrency(limit)
    now = [2_000_000_000.0]
    started, release = asyncio.Event(), asyncio.Event()
    seen = []
    for account in (1, 2):
        app = manager.get(account)
        app.store.clock = lambda: now[0]
        app.store.put_chat(
            Chat(account_id=account, peer="user:42", title="Authored", kind="private")
        )
        key = pending(app, now)
        app.memory.schedule(key)

        async def generate(role, packet, account=account):
            seen.append((account, role))
            if len(seen) == 1:
                started.set()
                await release.wait()
            return (
                update()
                if role == "chat_memory"
                else {
                    "options": [
                        {"purpose": "acknowledge", "text": "Authored continuation."}
                    ]
                }
            )

        app.engine.generate = generate
    manager.dispatch()
    await started.wait()
    assert len(manager.active) == 1 and seen == [(1, "chat_memory")]
    app = manager.get(2)
    draft = app.store.mutate_draft(
        "2/user:42", 0, lambda d: d.update(text="Owner words.")
    )
    app.suggestions.generate("2/user:42", draft["revision"])
    manager.dispatch()
    await asyncio.sleep(0.05)
    if limit == 1:
        assert len(seen) == 1
    else:
        assert seen[-1] == (2, "response_suggestions")
    release.set()
    if manager.active:
        await asyncio.gather(*manager.active)
    manager.dispatch()
    if manager.active:
        await asyncio.gather(*manager.active)
    # At limit one the explicit generation waits for the active memory call;
    # it is still ahead of the next account's idle memory fold.
    for _ in range(4):
        manager.dispatch()
        if manager.active:
            await asyncio.gather(*manager.active)
    assert seen == [(1, "chat_memory"), (2, "response_suggestions"), (2, "chat_memory")]
    assert not manager.get(1).memory.candidates(KEY)
    await manager.close()


async def test_an_account_with_ai_off_never_holds_memory_back(tmp_path):
    # ADR 55's launch found Maya's AI-off account holding a queued incoming
    # job since 2026-10-19, which kept every memory update waiting.
    manager = AccountManager(Settings(data=tmp_path))
    manager._create(manager.registry.register(2, "authored.session"))
    other = manager.get(2)
    with other.store.transaction() as db:
        db.execute(
            "INSERT INTO jobs VALUES ('stuck','2/user:42','incoming_rewrite','1','queued',?,'{}',NULL)",
            (other.store.clock(),),
        )
    assert not manager.memory_idle()
    manager.registry.set_processing(2, False)
    assert manager.memory_idle()
    # A memory call still running there keeps the one-at-a-time rule.
    with other.store.transaction() as db:
        db.execute(
            "INSERT INTO jobs VALUES ('folding','2/user:42','chat_memory','0','running',?,'{}',NULL)",
            (other.store.clock(),),
        )
    assert not manager.memory_idle()
    await manager.close()
