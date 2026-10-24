"""Authored controls acceptance: account isolation, stage races and durable queues."""

import asyncio
import json
import time
from dataclasses import replace

import pytest
from enloq.api.server import create_app
from enloq.application.accounts import AccountManager
from enloq.config import Settings
from enloq.domain.models import Chat, Message, ProcessingPaused
from enloq.inference.fake import FakeEngine
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import WINDOW, Store
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser

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


def incoming(app, identity=1, **changes):
    values = dict(
        account_id=app.store.account_id,
        peer="user:42",
        message_id=identity,
        revision=1,
        date=app.store.clock(),
        text="Please confirm the authored time.",
    )
    return Message(**(values | changes))


async def test_account_pause_persists_without_stopping_sync_send_or_other_account(rig):
    manager, now = rig
    a, b = manager.get(1), manager.get(2)
    assert all(row["ai_enabled"] for row in manager.summaries())
    manager.set_processing(1, False)
    a.sync.ingest(incoming(a))
    b.sync.ingest(incoming(b))
    now[0] += 31
    await a.process_due()
    await b.process_due()
    assert not a.engine.calls and len(b.engine.calls) == 1
    with pytest.raises(ProcessingPaused):
        a.rewriting.retry("1/user:42/1")
    draft = a.store.mutate_draft(
        "1/user:42", 0, lambda d: d.update(text="Send my wording")
    )
    with pytest.raises(ProcessingPaused):
        a.suggestions.generate("1/user:42", draft["revision"])
    assert (await a.delivery.send("1/user:42", draft["revision"]))[
        "state"
    ] == "confirmed"
    await manager.reconnect(1)
    assert not manager.registry.processing(1)["ai_enabled"]
    await manager.close()
    restarted = AccountManager(manager.settings)
    assert not restarted.registry.processing(1)["ai_enabled"]
    assert restarted.registry.processing(2)["ai_enabled"]
    assert restarted.get(1).store.rows("SELECT id FROM jobs WHERE state='queued'")
    await restarted.close()


async def test_resume_keeps_quiet_period_coalesces_changes_and_never_resamples(rig):
    manager, now = rig
    a = manager.get(1)
    manager.set_processing(1, False)
    a.sync.ingest(incoming(a))
    now[0] += 10
    a.sync.ingest(incoming(a, revision=2, text="Revised request"))
    due = a.store.rows("SELECT due FROM jobs WHERE state='queued'")[0]["due"]
    now[0] += 29
    manager.set_processing(1, True)
    await a.process_due()
    assert not a.engine.calls
    now[0] += 1
    assert now[0] == due
    await a.process_due()
    assert len(a.engine.calls) == 1
    assert a.conversation("1/user:42")["blocks"][0]["rendered"] == "Revised request"
    for _ in range(2):
        manager.set_processing(1, False)
        manager.set_processing(1, True)
        await a.process_due()
    assert len(a.engine.calls) == 1
    # An expired target is retained as an original but cannot start work.
    manager.set_processing(1, False)
    a.sync.ingest(incoming(a, 2))
    now[0] += WINDOW + 1
    manager.set_processing(1, True)
    await a.process_due()
    assert len(a.engine.calls) == 1


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
@pytest.mark.parametrize("boundary", ["capacity", "prepare"])
async def test_pause_after_provider_wait_never_reserves_or_dispatches(rig, boundary):
    manager, now = rig
    a = manager.get(1)
    entered, release = asyncio.Event(), asyncio.Event()

    class Engine(FakeEngine):
        async def prepare(self, packet):
            entered.set()
            await release.wait()
            return 0

    a.engine = Engine()
    capacity = a.rewriting.work.capacity
    capacity.limit = lambda: 1
    held = capacity.slot("new")
    if boundary == "capacity":
        await held.__aenter__()  # Every model slot is busy.
    a.sync.ingest(incoming(a))
    now[0] += 31
    task = asyncio.create_task(a.process_due())
    if boundary == "prepare":
        await asyncio.wait_for(entered.wait(), 2)
    else:
        await asyncio.sleep(0.02)
    manager.set_processing(1, False)
    if boundary == "capacity":
        await held.__aexit__(None, None, None)
    release.set()
    await task
    assert not a.engine.calls and not manager.registry.usage()
    job = a.store.rows("SELECT * FROM jobs WHERE role='incoming_rewrite'")[0]
    assert job["state"] == "queued" and job["due"] < now[0]
    manager.set_processing(1, True)
    await a.process_due()
    assert len(a.engine.calls) == 1


async def test_started_provider_result_and_receipt_survive_off(rig):
    manager, now = rig
    a = manager.get(1)
    entered, release = asyncio.Event(), asyncio.Event()

    class Engine(FakeEngine):
        async def generate(self, role, packet):
            entered.set()
            await release.wait()
            return await super().generate(role, packet)

    a.engine = Engine()
    a.sync.ingest(incoming(a))
    now[0] += 31
    task = asyncio.create_task(a.process_due())
    await asyncio.wait_for(entered.wait(), 2)
    manager.set_processing(1, False)
    release.set()
    await task
    assert len(a.engine.calls) == 1
    assert a.store.rows("SELECT status FROM reservations")[0]["status"] == "receipted"
    assert a.conversation("1/user:42")["blocks"][0]["state"] == "ready"


@pytest.mark.parametrize("boundary", ["capacity", "download", "started"])
async def test_voice_pause_boundaries_and_resume_cached_transcript(rig, boundary):
    manager, now = rig
    a = manager.get(1)
    entered, release = asyncio.Event(), asyncio.Event()
    transcriptions = []

    class Speech:
        async def transcribe(self, path):
            transcriptions.append(1)
            if boundary == "started":
                entered.set()
                await release.wait()
            return {"text": "Authored transcript"}

    async def acquire(message, path, limit):
        if boundary == "download":
            entered.set()
            await release.wait()
        path.write_bytes(b"authored-placeholder")

    a.media.transcriber, a.media.acquire = Speech(), acquire
    a.sync.ingest(incoming(a, text="", media="audio"))
    now[0] += 31
    if boundary == "capacity":
        await a.media.transcription_lock.acquire()
    task = asyncio.create_task(a.process_due())
    if boundary == "capacity":
        await asyncio.sleep(0.02)
    else:
        await asyncio.wait_for(entered.wait(), 2)
    manager.set_processing(1, False)
    release.set()
    if boundary == "capacity":
        a.media.transcription_lock.release()
    await task
    assert not a.engine.calls
    assert len(transcriptions) == (1 if boundary == "started" else 0)
    assert not a.store.rows("SELECT id FROM jobs WHERE state IN ('failed','unknown')")
    manager.set_processing(1, True)
    await a.process_due()
    assert len(transcriptions) == 1 and len(a.engine.calls) == 1
    assert a.conversation("1/user:42")["blocks"][0]["rendered"] == "Authored transcript"


async def test_activation_reconciles_cached_unread_without_resetting_cursor(rig):
    manager, now = rig
    a = manager.get(1)
    a.store.put_chat(a.store.chat("1/user:42").model_copy(update={"read_through": 1}))
    a.sync.ingest(incoming(a, 1, date=now[0] - 500), target=False)
    a.sync.ingest(incoming(a, 2, date=now[0] - 100), target=False)
    with a.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES ('recent:1/user:42',?)",
            (json.dumps({"complete": True, "before": 1}),),
        )
    manager.set_processing(1, True)
    await a.process_due()
    assert len(a.engine.calls) == 1
    assert [m["id"] for m in a.engine.calls[0][1]["messages"]] == [1, 2]
    assert json.loads(a.store.rows("SELECT value FROM cursors")[0]["value"])["complete"]


async def test_failed_and_unknown_attempts_are_not_retried_by_toggle(rig):
    manager, now = rig
    a = manager.get(1)

    class Failure(FakeEngine):
        async def generate(self, role, packet):
            self.calls.append((role, packet))
            raise TimeoutError("Authored unavailable response")

    a.engine = Failure()
    a.sync.ingest(incoming(a))
    now[0] += 31
    await a.process_due()
    assert len(a.engine.calls) == 1
    for state in ("failed", "unknown"):
        with a.store.transaction() as db:
            db.execute("UPDATE blocks SET state=?", (state,))
            db.execute("UPDATE jobs SET state=?", (state,))
        manager.set_processing(1, False)
        manager.set_processing(1, True)
        await a.process_due()
    assert len(a.engine.calls) == 1


def test_eligibility_upgrade_preserves_explicit_exclusions_and_spending(rig):
    manager, now = rig
    a = manager.get(1)
    old = a.store.chat("1/user:42").model_dump()
    old.pop("eligibility_source")
    old["ai_eligible"] = False
    with a.store.transaction() as db:
        db.execute("UPDATE chats SET body=? WHERE key='1/user:42'", (json.dumps(old),))
    a.store.put_chat(
        Chat(
            account_id=1,
            peer="user:43",
            title="Excluded",
            kind="private",
            ai_eligible=False,
        )
    )
    reopened = Store(a.store.path, account_id=1, shared_path=manager.registry.path)
    assert reopened.chat("1/user:42").ai_eligible
    excluded = reopened.chat("1/user:43")
    assert not excluded.ai_eligible and excluded.eligibility_source == "explicit"
    reopened.put_chat(
        Chat(account_id=1, peer="user:43", title="Rediscovered", kind="private")
    )
    assert not reopened.chat("1/user:43").ai_eligible


def test_processing_http_is_account_bound_authenticated_and_shared(tmp_path):
    app = create_app(Settings(data=tmp_path))
    app.state.accounts._create(app.state.accounts.registry.register(2, "two.session"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as one:
        assert one.get("/api/accounts/1/processing").status_code == 401
        trust_browser(one, tmp_path)
        assert one.post(
            "/api/accounts/1/processing", json={"ai_enabled": False}
        ).json() == {"account_id": 1, "ai_enabled": False}
        assert one.get("/api/accounts/2/processing").json()["ai_enabled"]
        assert (
            one.post(
                "/api/accounts/99/processing", json={"ai_enabled": False}
            ).status_code
            == 400
        )
        # A second independently trusted browser sees the same durable setting.
        one.cookies.clear()
        trust_browser(one, tmp_path)
        assert not one.get("/api/accounts/1/processing").json()["ai_enabled"]


async def test_reading_view_is_durable_independent_and_does_not_invalidate_manual_suggestions(
    rig,
):
    manager, now = rig
    registry = manager.registry
    assert registry.reading_view() == {"reading_view": "ai"}
    a = manager.get(1)
    entered, release = asyncio.Event(), asyncio.Event()

    class Engine(FakeEngine):
        async def generate(self, role, packet):
            entered.set()
            await release.wait()
            return await super().generate(role, packet)

    a.engine = Engine()
    draft = a.store.mutate_draft(
        "1/user:42", 0, lambda d: d.update(text="Authored outgoing")
    )
    a.suggestions.generate("1/user:42", draft["revision"])
    task = asyncio.create_task(a.process_due())
    await asyncio.wait_for(entered.wait(), 2)
    style = a.suggestions.settings("1/user:42")
    registry.set_reading_view("originals")
    assert a.suggestions.settings("1/user:42") == style
    release.set()
    await task
    assert a.suggestions.view("1/user:42").state == "ready"
    manager.set_processing(1, False)
    restored = AccountRegistry(manager.settings.data)
    assert restored.reading_view() == {"reading_view": "originals"}
    assert a.suggestions.settings("1/user:42")["style"] == "friendly"
    assert not restored.processing(1)["ai_enabled"]
    assert len(a.engine.calls) == 1


async def test_authorization_loss_differs_from_off_and_keeps_receipt_without_publication(
    rig,
):
    manager, now = rig
    a = manager.get(1)
    entered, release = asyncio.Event(), asyncio.Event()

    class Engine(FakeEngine):
        async def generate(self, role, packet):
            entered.set()
            await release.wait()
            return await super().generate(role, packet)

    a.engine = Engine()
    draft = a.store.mutate_draft(
        "1/user:42", 0, lambda d: d.update(text="Authored text")
    )
    a.suggestions.generate("1/user:42", draft["revision"])
    task = asyncio.create_task(a.process_due())
    await asyncio.wait_for(entered.wait(), 2)
    manager.registry.set_state(1, "requires_login")
    release.set()
    await task
    recovered = a.store.draft("1/user:42")
    assert recovered == draft
    assert a.suggestions.view("1/user:42").state == "failed"
    assert a.store.rows("SELECT status FROM reservations")[0]["status"] == "receipted"


async def test_startup_unread_waits_for_the_existing_transport_to_connect(rig):
    manager, now = rig
    a = manager.get(1)
    manager.settings = replace(manager.settings, mode="personal")
    a.transport.connected = False
    a.sync.ingest(incoming(a))
    now[0] += 31
    await a.process_due()
    assert not a.engine.calls
    assert a.store.rows("SELECT state FROM jobs")[0]["state"] == "queued"
    a.transport.connected = True
    await a.process_due()
    assert len(a.engine.calls) == 1
