"""GB9 exact reviewed Resume uses authored stores, bytes and native RPC objects.

Reviews send nothing, known non-delivery and account/peer/native rights are
rechecked, and a fresh uncertain attempt never inherits the earlier refusal.
"""

import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest
from enloq.api.server import create_app
from enloq.application.delivery import operation_view
from enloq.application.group_send_resume import REVIEW_SECONDS
from enloq.config import Settings
from enloq.domain.delivery import ReceiptBatch
from enloq.domain.models import Chat, Conflict, DeliveryRefused, Refused
from enloq.storage.store import RETENTION, WINDOW
from fastapi.testclient import TestClient
from telethon import errors, functions, types

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_avatars import picture
from backend.tests.test_catalog import load
from backend.tests.test_group_delivery import (
    access,
    group_app,
    operation,
    write,
)
from backend.tests.test_group_send_transport import SENDS, setup
from backend.tests.test_rebuild import fixture
from backend.tests.test_selected_media import item, media_bytes

BROWSER = "authored-browser"


async def pause(app, key, text="Exact retained wording", *, revision=None):
    if revision is None:
        revision = write(app, key, text)["revision"]
    saved = await app.delivery.submit(key, revision)
    access(app, key)
    access(app, key, "available")
    paused = operation(app, saved["id"])
    assert paused["cause"] == "group_access_loss" and paused["state"] == "refused"
    return paused


def mutate_operation(app, identity, **changes):
    value = operation(app, identity) | changes
    with app.store.transaction() as db:
        db.execute(
            "UPDATE operations SET state=?,body=? WHERE id=?",
            (value["state"], json.dumps(value), identity),
        )
    return value


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_review_leaves_exact_item_paused_then_resume_preserves_ids_and_newer_draft(
    tmp_path, peer
):
    app, key = group_app(tmp_path, peer)
    saved = await pause(app, key, "Exact first line\nsecond line")
    newer = write(app, key, "New independent draft", undo="Original newer wording")
    review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    assert operation(app, saved["id"]) == saved
    assert not app.transport.deliveries
    assert (review["account_id"], review["peer"], review["title"]) == (
        1,
        peer,
        "Authored group",
    )
    assert review["operation"]["text"] == saved["text"]
    assert review["operation"]["resumable"] and not review["operation"]["retryable"]
    assert review["expires"] == app.store.clock() + REVIEW_SECONDS
    assert "path" not in json.dumps(review) and "random_id" not in json.dumps(review)
    queued = await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
    assert queued["state"] == "queued" and queued["random_id"] == saved["random_id"]
    assert queued["group_access_revision"] == app.store.groups.get(key).access_revision
    assert (
        queued["attempts"][-1]["group_access_revision"]
        == saved["group_access_revision"]
    )
    assert queued["attempts"][-1]["cause"] == "group_access_loss"
    assert app.store.draft(key) == newer
    with pytest.raises(Refused, match="Review this exact"):
        await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
    sent = await app.delivery.drain(saved["id"])
    assert sent["state"] == "confirmed" and len(app.transport.deliveries) == 1
    assert (
        app.transport.deliveries[saved["random_id"]]["description"].text
        == saved["text"]
    )
    assert app.store.draft(key) == newer and not app.engine.calls


async def test_existing_inactive_work_can_resume_without_new_send_or_history_authority(
    tmp_path,
):
    app, key = group_app(tmp_path)
    saved = await pause(app, key)
    assert not any(c["key"] == key for c in app.store.recent_chats("group"))
    app.account_processing_enabled = lambda: False
    review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
    assert (await app.delivery.drain(saved["id"]))["state"] == "confirmed"
    assert not app.store.groups.get(key).processing_enabled
    assert not app.engine.calls and not app.transport.exact_reads
    assert not app.store.rows("SELECT * FROM jobs")


@pytest.mark.parametrize("cause", ["not_sent", "reply_deleted", "sticker_unavailable"])
async def test_review_refuses_ordinary_failures_that_keep_existing_retry_or_cancel(
    tmp_path, cause
):
    app, key = group_app(tmp_path)
    saved = await pause(app, key)
    mutate_operation(app, saved["id"], cause=cause)
    with pytest.raises(Refused, match="paused after access loss"):
        await app.delivery.review_resume(saved["id"], browser=BROWSER)
    assert not app.transport.group_send_reviews and not app.transport.deliveries


@pytest.mark.parametrize("state", ["queued", "unknown", "confirmed", "cancelled"])
async def test_review_never_replays_an_ineligible_or_uncertain_operation(
    tmp_path, state
):
    app, key = group_app(tmp_path)
    saved = await pause(app, key)
    mutate_operation(app, saved["id"], state=state)
    with pytest.raises(Refused):
        await app.delivery.review_resume(saved["id"], browser=BROWSER)
    assert not operation_view(operation(app, saved["id"]), app.store.clock())[
        "resumable"
    ]
    assert not app.transport.group_send_reviews and not app.transport.deliveries


@pytest.mark.parametrize("proof", ["exposed", "missing", "receipt", "recorded_receipt"])
async def test_review_requires_positive_non_delivery_and_receipts_always_win(
    tmp_path, proof
):
    app, key = group_app(tmp_path)
    saved = await pause(app, key)
    if proof == "exposed":
        mutate_operation(app, saved["id"], wire=[{"outcome": "exposed"}])
    elif proof == "missing":
        mutate_operation(app, saved["id"], wire=None)
    elif proof == "receipt":
        mutate_operation(app, saved["id"], message_id=700)
    else:
        app.store.send_operations.record_receipts(
            ReceiptBatch(((saved["random_id"], 700),))
        )
    with pytest.raises(Refused):
        await app.delivery.review_resume(saved["id"], browser=BROWSER)
    assert not app.transport.group_send_reviews and not app.transport.deliveries


@pytest.mark.parametrize(
    "change", ["browser", "account", "identity", "missing_token", "restart"]
)
async def test_token_is_bound_to_its_browser_operation_and_current_account(
    tmp_path, change
):
    app, key = group_app(tmp_path)
    saved = await pause(app, key)
    review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    identity, token, browser = saved["id"], review["token"], BROWSER
    if change == "browser":
        browser = "another-authored-browser"
    elif change == "account":
        app.transport.account_id = 2
    elif change == "identity":
        identity = "b" * 32
    elif change == "missing_token":
        token = "0" * 64
    else:
        app.delivery.group_resume.reviews.clear()
    with pytest.raises(Refused):
        await app.delivery.resume(identity, token, browser=browser)
    assert operation(app, saved["id"]) == saved and not app.transport.deliveries


@pytest.mark.parametrize("direction", ["expired", "backwards"])
async def test_expired_or_backwards_clock_invalidates_review(tmp_path, direction):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    key = "1/chat:42"
    app.store.put_chat(
        Chat(account_id=1, peer="chat:42", title="Authored group", kind="group")
    )
    access(app, key, "available")
    saved = await pause(app, key)
    review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    now[0] += REVIEW_SECONDS if direction == "expired" else -1
    with pytest.raises(Refused, match="expired"):
        await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
    assert operation(app, saved["id"]) == saved and not app.transport.deliveries


@pytest.mark.parametrize(
    "change",
    [
        "text",
        "random_id",
        "reply",
        "media",
        "title",
        "access",
        "migration",
        "rights",
        "receipt",
    ],
)
async def test_changed_frozen_payload_or_native_evidence_demands_new_review(
    tmp_path, change
):
    app, key = group_app(tmp_path)
    saved = await pause(app, key)
    review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    if change in {"text", "random_id", "reply", "media"}:
        values = {
            "text": {"text": "Changed retained text"},
            "random_id": {"random_id": saved["random_id"] + 1},
            "reply": {"reply_to": 91},
            "media": {"attachments": [{"id": "changed"}]},
        }[change]
        mutate_operation(app, saved["id"], **values)
    elif change == "title":
        app.store.put_chat(
            app.store.chat(key).model_copy(update={"title": "Changed group title"})
        )
    elif change == "access":
        access(app, key)
        access(app, key, "available")
    elif change == "migration":
        access(app, key, "available", migrated_to="channel:43")
    elif change == "rights":
        app.transport.group_send_rights_version += 1
    else:
        app.store.send_operations.record_receipts(
            ReceiptBatch(((saved["random_id"], 700),))
        )
    with pytest.raises((Refused, Conflict)):
        await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
    assert operation(app, saved["id"])["state"] == "refused"
    assert not app.transport.deliveries


@pytest.mark.parametrize("change", ["auth", "browser", "access"])
async def test_review_rechecks_permission_after_native_await_without_queuing(
    tmp_path, change
):
    app, key = group_app(tmp_path)
    saved = await pause(app, key)
    trusted = [True]

    def guard():
        if not trusted[0]:
            raise Refused("Authored browser was revoked")

    def hook():
        if change == "auth":
            app.delivery.authorized = lambda: False
        elif change == "browser":
            trusted[0] = False
        else:
            access(app, key)

    app.transport.group_send_review_hook = hook
    with pytest.raises((Refused, Conflict)):
        await app.delivery.review_resume(saved["id"], browser=BROWSER, guard=guard)
    assert not app.delivery.group_resume.reviews and not app.transport.deliveries
    assert operation(app, saved["id"])["state"] == "refused"


async def test_definitive_exposed_refusal_is_archived_and_cleared_before_new_uncertainty(
    tmp_path,
):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])

    async def reject(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        evidence.refused("Authored definite native rejection")
        raise DeliveryRefused("Authored definite native rejection")

    app.transport.send = reject
    await app.delivery.dispatch(saved["id"])
    access(app, key)
    access(app, key, "available")
    held = operation(app, saved["id"])
    assert held["native_refused"] is True and held["wire"][-1]["outcome"] == "exposed"
    review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    queued = await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
    assert (
        "native_refused" not in queued
        and queued["attempts"][-1]["native_refused"] is True
    )
    assert not app.store.rows("SELECT 1 FROM cursors WHERE key LIKE 'refusal:op:%'")

    async def uncertain(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        raise ConnectionError("Authored uncertain execution")

    app.transport.send = uncertain
    unknown = await app.delivery.dispatch(saved["id"])
    assert unknown["state"] == "unknown" and "native_refused" not in unknown
    access(app, key)
    access(app, key, "available")
    with pytest.raises(Refused):
        await app.delivery.review_resume(saved["id"], browser=BROWSER)
    assert operation(app, saved["id"])["state"] == "unknown"


@pytest.mark.parametrize("stage", ["before_review", "after_review"])
async def test_review_proves_exact_retained_images_before_every_new_attempt(
    tmp_path, stage
):
    app, key = group_app(tmp_path)
    attached = app.attachments.add(
        key, 0, "authored.png", base64.b64encode(picture()).decode()
    )
    saved = await pause(
        app, key, "Exact caption", revision=write(app, key, "Exact caption")["revision"]
    )
    review = None
    if stage == "after_review":
        review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    Path(saved["attachments"][0]["path"]).write_bytes(b"authored changed bytes")
    with pytest.raises(Refused):
        if review:
            await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
        else:
            await app.delivery.review_resume(saved["id"], browser=BROWSER)
    assert operation(app, saved["id"]) == saved and not app.transport.deliveries
    assert saved["attachments"][0]["id"] == attached["attachments"][0]


@pytest.mark.parametrize("kind", ["gif", "sticker"])
async def test_selected_media_resume_uses_saved_bytes_and_keeps_newer_draft(
    tmp_path, kind
):
    app, key = group_app(tmp_path)
    app.store.put_chat(app.store.chat(key), activity=app.store.clock())
    draft = write(app, key, "Exact caption or kept sticker draft", undo="Kept Undo")
    await item(app, kind)
    catalog = await load(app, key, browser=BROWSER)
    choice = (catalog.gifs if kind == "gif" else catalog.recent)[0]
    selected = await app.selected_media.select(
        key, draft["revision"], BROWSER, choice.token, choice.revision
    )
    saved = await pause(app, key, revision=selected["revision"])
    with app.store.transaction() as db:
        db.execute(
            "UPDATE chats SET activity=? WHERE key=?",
            (app.store.clock() - WINDOW - 1, key),
        )
    newer = write(app, key, "Newer independent draft", undo="Newer Undo")
    app.store.selected_media.collect()
    review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    assert review["operation"]["media"]["kind"] == kind
    assert "source" not in json.dumps(review["operation"]["media"])
    queued = await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
    sent = await app.delivery.drain(queued["id"])
    assert app.transport.media["channel:42", sent["message_id"]] == media_bytes(kind)
    assert app.store.draft(key) == newer


async def test_cancelling_after_review_keeps_content_and_invalidates_token(tmp_path):
    app, key = group_app(tmp_path)
    saved = await pause(app, key)
    review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    cancelled = app.delivery.cancel(saved["id"])
    assert (
        cancelled["text"] == saved["text"]
        and cancelled["random_id"] == saved["random_id"]
    )
    with pytest.raises((Refused, Conflict)):
        await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
    assert (
        operation(app, saved["id"])["state"] == "cancelled"
        and not app.transport.deliveries
    )


async def test_concurrent_resume_claims_a_token_once_and_keeps_the_original_queue_order(
    tmp_path,
):
    app, key = group_app(tmp_path)
    first = await pause(app, key, "First exact item")
    second = await app.delivery.submit(
        key, write(app, key, "Second exact item")["revision"]
    )
    review = await app.delivery.review_resume(first["id"], browser=BROWSER)
    original = app.transport.review_group_send
    started, released = asyncio.Event(), asyncio.Event()

    async def held(description, guard):
        started.set()
        await released.wait()
        return await original(description, guard)

    app.transport.review_group_send = held
    task = asyncio.create_task(
        app.delivery.resume(first["id"], review["token"], browser=BROWSER)
    )
    await started.wait()
    with pytest.raises(Refused):
        await app.delivery.resume(first["id"], review["token"], browser=BROWSER)
    released.set()
    await task
    await app.delivery.drain(second["id"])
    assert [d["description"].text for d in app.transport.deliveries.values()] == [
        "First exact item",
        "Second exact item",
    ]


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_native_review_is_read_only_and_restores_only_the_exact_cached_reply_proof(
    tmp_path, peer
):
    app, _, key, rpc, description, receipts = setup(tmp_path, peer)
    before = app.store.message(key, 1)
    access(app, key)
    access(app, key, "available")
    assert not app.store.groups.current(before, admission=False)
    proof = await app.transport.review_group_send(description, lambda: None)
    proof.guard(fresh=True)
    assert app.store.groups.current(before, admission=False)
    assert app.store.message(key, 1) == before
    assert not receipts and not rpc.final and not rpc.uploads
    assert not any(isinstance(request, SENDS) for request in rpc.calls)
    assert (
        sum(
            isinstance(
                request,
                (
                    functions.channels.GetMessagesRequest,
                    functions.messages.GetMessagesRequest,
                ),
            )
            for request in rpc.calls
        )
        == 1
    )
    assert not any(
        isinstance(request, functions.messages.GetHistoryRequest)
        for request in rpc.calls
    )


@pytest.mark.parametrize(
    "change", ["rights", "identity", "source", "expired_source", "unknown_source"]
)
async def test_native_review_rejects_wrong_identity_rights_and_reply_without_send(
    tmp_path, change
):
    app, now, key, rpc, description, _ = setup(tmp_path)
    access(app, key)
    access(app, key, "available")
    if change == "rights":
        rpc.metadata.default_banned_rights = types.ChatBannedRights(
            now[0] + 600, send_messages=True
        )
    elif change == "identity":
        rpc.user = types.User(id=2)
    elif change == "source":
        rpc.native.message = "Changed authored reply wording"
    elif change == "expired_source":
        now[0] += RETENTION + 1
    else:
        description = replace(description, reply_to=999)
    with pytest.raises(Refused):
        await app.transport.review_group_send(description, lambda: None)
    assert not rpc.final and not rpc.uploads
    assert not any(isinstance(request, SENDS) for request in rpc.calls)


async def test_native_resume_rejects_a_rights_change_first_observed_during_recheck(
    tmp_path,
):
    app, _, key, rpc, _, _ = setup(tmp_path)
    saved = await pause(app, key)
    review = await app.delivery.review_resume(saved["id"], browser=BROWSER)
    rpc.metadata.default_banned_rights = types.ChatBannedRights(0, send_photos=True)
    with pytest.raises(Refused, match="rights changed"):
        await app.delivery.resume(saved["id"], review["token"], browser=BROWSER)
    assert operation(app, saved["id"]) == saved and not rpc.final


async def test_native_access_loss_during_review_never_queues_or_retargets(tmp_path):
    app, _, key, rpc, _, _ = setup(tmp_path)
    saved = await pause(app, key)
    rpc.metadata = errors.ChannelPrivateError(None)
    with pytest.raises((Refused, Conflict)):
        await app.delivery.review_resume(saved["id"], browser=BROWSER)
    assert app.store.groups.get(key).access == "lost"
    assert operation(app, saved["id"])["state"] == "refused"
    assert not rpc.final and not app.delivery.group_resume.reviews


def test_resume_http_requires_browser_origin_exact_account_and_review_then_queues(
    tmp_path,
):
    directory = tmp_path / "served"
    server = create_app(Settings(data=directory))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        app = server.state.accounts.get(1)
        app.store.clock = lambda: 2_000_000_000.0
        key, peer = "1/chat:42", "chat:42"
        app.store.put_chat(
            Chat(account_id=1, peer=peer, title="Authored retained group", kind="group")
        )
        access(app, key, "available")
        saved = asyncio.run(pause(app, key))
        base = f"/api/accounts/1/operations/{saved['id']}"
        assert client.post(base + "/review-resume", json={}).status_code == 403
        client.headers["Origin"] = str(client.base_url).rstrip("/")
        assert client.post(base + "/review-resume", json={}).status_code == 401
        trust_browser(client, directory)
        missing = client.post(base + "/resume", json={"token": "0" * 64})
        assert missing.status_code == 400
        invalid = client.post(
            base + "/review-resume", json={"text": "Unreviewed replacement"}
        )
        assert invalid.status_code == 422
        response = client.post(base + "/review-resume", json={})
        assert response.status_code == 200, response.text
        review = response.json()
        assert (
            review["operation"]["state"] == "refused" and not app.transport.deliveries
        )
        assert review["peer"] == peer and "path" not in response.text
        assert (
            client.post(
                base.replace("accounts/1", "accounts/2") + "/resume",
                json={"token": review["token"]},
            ).status_code
            == 400
        )
        response = client.post(base + "/resume", json={"token": review["token"]})
        assert response.status_code == 200, response.text
        assert response.json()["state"] in {"queued", "dispatching", "confirmed"}
        assert (
            client.post(base + "/resume", json={"token": review["token"]}).status_code
            == 400
        )
        assert not app.engine.calls
