"""P2: immutable submissions, ordered per-chat queues and deliberate recovery
(ADR 26). Authored transport only; nothing reaches Telegram."""

import asyncio
import json

import pytest
from enloq.api.server import create_app
from enloq.application.delivery import operation_view
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import attach, edit, fixture, message

KEY = "1/user:42"
OTHER = "1/user:43"


def other_chat(app):
    app.store.put_chat(
        Chat(account_id=1, peer="user:43", title="Another chat", kind="private")
    )


def write(app, key, text):
    draft = app.store.draft(key)
    return app.store.mutate_draft(
        key, draft["revision"], lambda d: d.update(text=text, undo=None)
    )


async def test_acceptance_clears_the_composer_and_owns_the_exact_content(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1))
    attach(app)
    draft = edit(app, "Authored caption", reply_to=1)
    operation = await app.delivery.submit(KEY, draft["revision"])
    assert operation["state"] == "queued" and operation["submitted"]
    assert operation["text"] == "Authored caption" and operation["reply_to"] == 1
    assert [i["id"] for i in operation["attachments"]] == draft["attachments"]
    cleared = app.store.draft(KEY)
    assert cleared["revision"] == draft["revision"] + 1
    assert (cleared["text"], cleared["attachments"], cleared["reply_to"]) == (
        "",
        [],
        None,
    )
    # A repeated request for the submitted revision never queues a second one.
    again = await app.delivery.submit(KEY, draft["revision"])
    assert again["id"] == operation["id"]
    assert len(app.store.rows("SELECT 1 FROM operations")) == 1
    # Later composer edits cannot change the submission.
    write(app, KEY, "A new draft")
    sent = await app.delivery.drain(operation["id"])
    assert sent["state"] == "confirmed" and app.transport.deliveries
    delivered = next(iter(app.transport.deliveries.values()))["description"]
    assert delivered.text == "Authored caption" and delivered.reply_to == 1
    assert app.store.draft(KEY)["text"] == "A new draft"


async def test_unsaved_submissions_keep_the_draft(tmp_path):
    app = fixture(tmp_path)
    draft = edit(app, "Authored reply")
    app.transport.connected = False
    with pytest.raises(Refused, match="disconnected"):
        await app.delivery.submit(KEY, draft["revision"])
    app.transport.connected = True
    too_long = edit(app, "x" * 4097)
    with pytest.raises(Refused):
        await app.delivery.submit(KEY, too_long["revision"])
    stale = edit(app, "Authored reply")
    with pytest.raises(Exception, match="Draft changed"):
        await app.delivery.submit(KEY, stale["revision"] - 1)
    assert app.store.draft(KEY)["text"] == "Authored reply"
    assert not app.store.rows("SELECT 1 FROM operations")
    assert not app.transport.deliveries


async def test_one_native_send_at_a_time_per_chat_in_order(tmp_path):
    app = fixture(tmp_path)
    other_chat(app)
    attach(app)
    head = await app.delivery.submit(KEY, edit(app, "First, with an image")["revision"])
    second = await app.delivery.submit(KEY, write(app, KEY, "Second")["revision"])
    third = await app.delivery.submit(KEY, write(app, KEY, "Third")["revision"])
    elsewhere = await app.delivery.submit(
        OTHER, write(app, OTHER, "Elsewhere")["revision"]
    )
    # The head fails before reaching Telegram: its chat pauses, others do not.
    app.transport.upload_failure = True
    assert (await app.delivery.drain(head["id"]))["state"] == "refused"
    assert (await app.delivery.drain(third["id"]))["state"] == "queued"
    assert app.store.send_operations.runnable(KEY) is None
    assert (await app.delivery.drain(elsewhere["id"]))["state"] == "confirmed"
    assert app.delivery.paused(KEY) and not app.delivery.paused(OTHER)
    # A deliberate Retry continues the paused chat in submission order.
    app.transport.upload_failure = False
    app.delivery.retry(head["id"])
    for operation in (head, second, third):
        assert (await app.delivery.drain(operation["id"]))["state"] == "confirmed"
    texts = [m.text for m in app.store.messages(KEY) if m.outgoing and m.text]
    assert texts == ["First, with an image", "Second", "Third"]
    first_id = app.store.send_operations.get(head["id"])["receipts"]
    assert first_id and len(app.transport.deliveries) == 4


async def test_the_service_worker_sends_without_the_submitting_request(tmp_path):
    app = fixture(tmp_path)
    other_chat(app)
    worker = asyncio.create_task(app.delivery.run())
    try:
        one = await app.delivery.submit(KEY, edit(app, "Queued here")["revision"])
        two = await app.delivery.submit(
            OTHER, write(app, OTHER, "And there")["revision"]
        )
        async with asyncio.timeout(10):
            while {
                app.store.send_operations.get(o["id"])["state"] for o in (one, two)
            } != {"confirmed"}:
                await asyncio.sleep(0.05)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
    assert len(app.transport.deliveries) == 2 and not app.delivery.chats


async def test_draining_the_worker_settles_an_interrupted_send_by_its_evidence(
    tmp_path,
):
    app = fixture(tmp_path)
    handed = asyncio.Event()
    original = app.transport.send

    async def silent(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        handed.set()
        await asyncio.Event().wait()

    app.transport.send = silent
    worker = asyncio.create_task(app.delivery.run())
    operation = await app.delivery.submit(KEY, edit(app, "Handed off")["revision"])
    await asyncio.wait_for(handed.wait(), 10)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    # It may have reached Telegram: unknown, never cancellable or resent.
    assert app.store.send_operations.get(operation["id"])["state"] == "unknown"
    with pytest.raises(Refused):
        app.delivery.cancel(operation["id"])
    app.transport.send = original


async def test_cancel_needs_proof_that_nothing_reached_telegram(tmp_path):
    app = fixture(tmp_path)
    queued = await app.delivery.submit(KEY, edit(app, "Never sent")["revision"])
    later = await app.delivery.submit(KEY, write(app, KEY, "Sent after")["revision"])
    cancelled = app.delivery.cancel(queued["id"])
    assert cancelled["state"] == "cancelled" and cancelled["text"] == "Never sent"
    assert cancelled["reason"] == "Cancelled. Nothing was sent."
    # Cancelled work leaves the queue; later accepted work continues in order.
    assert (await app.delivery.drain(later["id"]))["state"] == "confirmed"
    assert len(app.transport.deliveries) == 1
    with pytest.raises(Refused):
        app.delivery.cancel(later["id"])
    with pytest.raises(Refused):
        app.delivery.retry(cancelled["id"])
    # Cancelled content stays visible, with Copy, until its retention ends.
    shown = operation_view(app.store.send_operations.get(queued["id"]), 0)
    assert shown["state"] == "cancelled" and shown["text"] == "Never sent"
    assert not shown["cancellable"] and not shown["retryable"]


def legacy(app, identity, state, **extra):
    body = dict(
        id=identity,
        account_id=1,
        peer="user:42",
        chat_key=KEY,
        text="Pre-queue send",
        attachments=[],
        reply_to=None,
        composer_revision=0,
        random_id=7,
        state=state,
        created=app.store.clock(),
        message_id=None,
        **extra,
    )
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)",
            (identity, KEY, state, json.dumps(body)),
        )


async def test_pre_queue_refusals_never_block_but_uncertain_sends_do(tmp_path):
    app = fixture(tmp_path)
    # Before the queue, a refused send kept its draft: it is history only.
    legacy(app, "refused", "refused", cause="not_sent", wire=[])
    operation = await app.delivery.submit(KEY, edit(app, "After upgrade")["revision"])
    assert app.store.send_operations.runnable(KEY)["id"] == operation["id"]
    assert all(o["id"] != "refused" for o in app.store.send_operations.recent(KEY))
    assert (await app.delivery.drain(operation["id"]))["state"] == "confirmed"
    # An uncertain hand-off from before the queue still holds the chat.
    legacy(app, "uncertain", "unknown", wire=[{"outcome": "exposed"}])
    waiting = await app.delivery.submit(KEY, write(app, KEY, "Waits")["revision"])
    assert (await app.delivery.drain(waiting["id"]))["state"] == "queued"
    assert [o["id"] for o in app.store.send_operations.recent(KEY)][:2] == [
        waiting["id"],
        "uncertain",
    ]


async def test_browser_views_expose_no_local_paths(tmp_path):
    app = fixture(tmp_path)
    attach(app)
    app.transport.lose_receipt = True
    sent = await app.delivery.send(KEY, edit(app, "Caption")["revision"])
    view = operation_view(sent, app.store.clock())
    assert "path" not in json.dumps(view) and "attachments" not in view
    assert view["images"] == [i["id"] for i in sent["attachments"]]
    assert view["state"] == "unknown" and view["checkable"]
    assert not view["cancellable"] and not view["retryable"]
    assert view["native_ids"] == list(sent["receipts"].values())
    assert app.conversation(KEY)["operations"] == [view]


async def test_suggestions_suspend_on_acceptance_and_return_after_a_definite_non_send(
    tmp_path,
):
    from backend.tests.test_suggestion_state import ready

    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, text="Worried authored message"))
    record = await ready(app)
    assert app.suggestions.view(KEY).state == "ready"
    jobs = app.store.rows("SELECT * FROM jobs")
    held = await app.delivery.submit(KEY, edit(app, "My answer")["revision"])
    assert app.suggestions.view(KEY).reason == "suspended"
    draft = app.store.draft(KEY)
    with pytest.raises(Exception, match="Suggestions changed"):
        app.suggestions.insert(KEY, draft["revision"], record["attempt"], 0)
    # Proven unsent: the cached options return without another call.
    app.store.send_operations.not_dispatched(held["id"], "Authored hold.")
    assert app.suggestions.view(KEY).state == "ready"
    app.delivery.cancel(held["id"])
    assert app.suggestions.view(KEY).state == "ready"
    assert app.store.rows("SELECT * FROM jobs") == jobs
    # Uncertain delivery keeps them suspended; native confirmation answers.
    app.transport.lose_receipt = True
    unknown = await app.delivery.send(KEY, edit(app, "Second answer")["revision"])
    assert unknown["state"] == "unknown"
    assert app.suggestions.view(KEY).reason == "suspended"
    confirmed = await app.delivery.recheck(unknown["id"])
    assert confirmed["state"] == "confirmed"
    assert app.suggestions.view(KEY).reason == "answered"


@pytest.fixture
def served(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        yield app, client


def test_http_send_returns_the_cleared_composer_and_the_service_sends(served):
    app, client = served
    conversation = app.state.accounts.get(1)
    base = "/api/accounts/1/chats/user:101"
    draft = client.post(base + "/draft", json={"revision": 0, "text": "Hello"})
    accepted = client.post(base + "/send", json={"revision": draft.json()["revision"]})
    body = accepted.json()
    assert accepted.status_code == 200
    assert body["draft"]["text"] == "" and body["operation"]["text"] == "Hello"
    assert body["operation"]["state"] in ("queued", "dispatching", "confirmed")
    for _ in range(200):
        state = conversation.store.send_operations.get(body["operation"]["id"])
        if state["state"] == "confirmed":
            break
        client.get(base)
        import time

        time.sleep(0.02)
    assert state["state"] == "confirmed"
    # Another account cannot resolve or cancel this account's submission.
    other = f"/api/accounts/2/operations/{state['id']}/cancel"
    assert client.post(other, json={}).status_code in (400, 403)
    own = f"/api/accounts/1/operations/{state['id']}/cancel"
    assert client.post(own, json={}).status_code == 400
    assert client.post(own.replace("cancel", "retry"), json={}).status_code == 400


async def test_a_paused_chat_does_not_spin_the_worker(tmp_path):
    """Review finding: a chat paused behind a Not sent item with later queued
    work must wait for a deliberate action, not restart its task in a loop."""
    app = fixture(tmp_path)
    app.transport.upload_failure = True
    attach(app)
    failed = await app.delivery.send(KEY, edit(app, "Fails")["revision"])
    assert failed["state"] == "refused"
    await app.delivery.submit(KEY, write(app, KEY, "Waits behind it")["revision"])
    checks = []
    original = app.store.send_operations.runnable

    def counted(key):
        checks.append(key)
        return original(key)

    app.store.send_operations.runnable = counted
    worker = asyncio.create_task(app.delivery.run())
    await asyncio.sleep(0.6)
    assert len(checks) <= 3 and not app.delivery.chats
    # A deliberate Cancel wakes it and the later message goes in order.
    app.delivery.cancel(failed["id"])
    async with asyncio.timeout(10):
        while not app.transport.deliveries:
            await asyncio.sleep(0.05)
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert len(app.transport.deliveries) == 1


async def test_not_sent_work_never_expires_out_of_its_chat_queue(tmp_path):
    from enloq.storage.store import RETENTION

    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.transport.upload_failure = True
    attach(app)
    failed = await app.delivery.send(KEY, edit(app, "Fails with an image")["revision"])
    later = await app.delivery.submit(KEY, write(app, KEY, "Still waits")["revision"])
    cancelled = await app.delivery.submit(KEY, write(app, KEY, "Withdrawn")["revision"])
    app.delivery.cancel(cancelled["id"])
    now[0] += RETENTION + 86400
    app.store.expire()
    app.attachments.collect()
    kept = app.store.send_operations.get(failed["id"])
    assert kept["state"] == "refused" and kept["text"] == "Fails with an image"
    assert app.attachments.get(KEY, kept["attachments"][0]["id"])
    assert app.store.send_operations.get(later["id"])["state"] == "queued"
    assert app.store.send_operations.runnable(KEY) is None
    # A cancelled item expires with ordinary retention.
    with pytest.raises(Refused):
        app.store.send_operations.get(cancelled["id"])


async def test_the_worker_survives_a_transient_store_error(tmp_path):
    app = fixture(tmp_path)
    original = app.store.send_operations.waiting
    failures = [RuntimeError("authored database is locked")]

    def flaky():
        if failures:
            raise failures.pop()
        return original()

    app.store.send_operations.waiting = flaky
    worker = asyncio.create_task(app.delivery.run())
    await asyncio.sleep(0.05)
    operation = await app.delivery.submit(KEY, edit(app, "Still sent")["revision"])
    async with asyncio.timeout(10):
        while app.store.send_operations.get(operation["id"])["state"] != "confirmed":
            await asyncio.sleep(0.05)
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
