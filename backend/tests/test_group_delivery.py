"""GB7 group queue invariants use authored peers, bytes and handoff evidence.

No native session, member-reader request, provider or service action is used.
"""

import asyncio
import base64
import json

import pytest
from enloq.application.delivery import operation_view
from enloq.domain.delivery import GroupSendBlocked, ReceiptBatch
from enloq.domain.models import (
    Chat,
    Conflict,
    DeliveryRefused,
    Message,
    Refused,
)
from enloq.storage.accounts import AccountRegistry

from backend.tests.test_avatars import picture
from backend.tests.test_catalog import load
from backend.tests.test_rebuild import edit, fixture
from backend.tests.test_selected_media import item, media_bytes


def group_app(tmp_path, peer="channel:42"):
    app = fixture(tmp_path)
    app.store.put_chat(
        Chat(account_id=1, peer=peer, title="Authored group", kind="group")
    )
    key = f"1/{peer}"
    app.store.groups.observe(key, access="available", reason="authored_native")
    return app, key


def write(app, key, text="Exact authored group text", **values):
    return app.store.mutate_draft(
        key,
        app.store.draft(key)["revision"],
        lambda draft: draft.update(text=text, **values),
    )


def source(app, key, identity=1, **values):
    value = Message(
        account_id=1,
        peer=app.store.chat(key).peer,
        message_id=identity,
        revision=values.pop("revision", 1),
        date=app.store.clock(),
        text="Authored native group original",
        author="Authored member",
        author_peer="user:7",
        **values,
    )
    app.store.apply_message(value)
    return value


def access(app, key, state="lost", **values):
    return app.store.groups.observe(
        key, access=state, reason="authored_native", **values
    )


def operation(app, identity):
    return app.store.send_operations.get(identity)


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_group_send_is_deliberate_and_independent_of_all_ai_gates(tmp_path, peer):
    app, key = group_app(tmp_path, peer)
    app.account_processing_enabled = lambda: False
    registry = AccountRegistry(tmp_path)
    registry.set_processing_threshold(10_000)
    registry.set_budget(0)
    registry.set_budget(0, family="group_briefing")
    source(app, key)
    draft = write(app, key, "Authored short reply", reply_to=1)
    sent = await app.delivery.send(key, draft["revision"])
    assert sent["state"] == "confirmed"
    assert sent["peer"] == peer and sent["reply_to"] == 1
    assert sent["group_access_revision"] == app.store.groups.get(key).access_revision
    description = app.transport.deliveries[sent["random_id"]]["description"]
    assert (description.account_id, description.peer, description.text) == (
        1,
        peer,
        "Authored short reply",
    )
    assert not app.store.groups.get(key).processing_enabled
    assert not app.engine.calls and not app.store.rows("SELECT * FROM jobs")
    assert not registry.rows("SELECT * FROM model_reservations")
    assert not registry.rows("SELECT * FROM model_receipts")


@pytest.mark.parametrize(
    "state,supported", [("unknown", True), ("lost", True), ("available", False)]
)
async def test_group_acceptance_refuses_unconfirmed_lost_or_unsupported_access(
    tmp_path, state, supported
):
    app, key = group_app(tmp_path)
    access(app, key, state, supported=supported)
    draft = write(app, key, undo="Exact kept Undo")
    with pytest.raises(Refused, match="Native group access"):
        await app.delivery.submit(key, draft["revision"])
    assert app.store.draft(key) == draft
    assert not app.store.rows("SELECT * FROM operations")
    assert not app.transport.deliveries


@pytest.mark.parametrize(
    "peer,kind",
    [("channel:43", "broadcast"), ("user:43", "bot"), ("user:43", "group")],
)
async def test_send_never_uses_an_unsupported_or_mistyped_native_peer(
    tmp_path, peer, kind
):
    app, _ = group_app(tmp_path)
    app.store.put_chat(
        Chat(account_id=1, peer=peer, title="Authored excluded", kind=kind)
    )
    key = f"1/{peer}"
    draft = write(app, key)
    with pytest.raises(Refused, match="supported"):
        await app.delivery.submit(key, draft["revision"])
    assert app.store.draft(key) == draft
    assert not app.store.rows("SELECT * FROM operations")


async def test_group_acceptance_keeps_exact_image_caption_reply_and_newer_draft(
    tmp_path,
):
    app, key = group_app(tmp_path)
    source(app, key, 5)
    attached = app.attachments.add(
        key, 0, "authored.png", base64.b64encode(picture()).decode()
    )
    draft = write(app, key, "Exact caption\nsecond line", reply_to=5)
    saved = await app.delivery.submit(key, draft["revision"])
    assert saved["attachments"][0]["id"] == attached["attachments"][0]
    assert app.store.draft(key)["attachments"] == []
    newer = write(app, key, "Newer independent draft", undo="Prior authored draft")
    sent = await app.delivery.drain(saved["id"])
    assert sent["state"] == "confirmed"
    description = next(iter(app.transport.deliveries.values()))["description"]
    assert (description.text, description.reply_to) == ("Exact caption\nsecond line", 5)
    assert description.images[0].sha256 == saved["attachments"][0]["sha256"]
    assert (
        app.transport.media["channel:42", next(iter(sent["receipts"].values()))]
        == picture()
    )
    assert app.store.draft(key) == newer


@pytest.mark.parametrize("kind", ["gif", "sticker"])
async def test_group_selected_media_preserves_exact_bytes_and_kept_sticker_draft(
    tmp_path, kind
):
    app, key = group_app(tmp_path)
    source(app, key, 7)
    original = write(
        app, key, "Exact caption or kept text", reply_to=7, undo="Exact Undo"
    )
    await item(app, kind)
    catalog = await load(app, key, browser="authored-browser")
    value = (catalog.gifs if kind == "gif" else catalog.recent)[0]
    selected = await app.selected_media.select(
        key, original["revision"], "authored-browser", value.token, value.revision
    )
    saved = await app.delivery.submit(key, selected["revision"])
    native_text = "" if kind == "sticker" else original["text"]
    assert (saved["text"], saved["reply_to"], saved["media"]["kind"]) == (
        native_text,
        7,
        kind,
    )
    after = app.store.draft(key)
    assert after["selected_media"] is None
    if kind == "sticker":
        assert (after["text"], after["undo"], after["reply_to"]) == (
            original["text"],
            original["undo"],
            original["reply_to"],
        )
    else:
        assert after["text"] == ""
    sent = await app.delivery.drain(saved["id"])
    assert sent["state"] == "confirmed"
    assert app.transport.media["channel:42", sent["message_id"]] == media_bytes(kind)
    assert not app.engine.calls


async def test_group_access_change_during_exact_reply_read_keeps_the_entire_draft(
    tmp_path,
):
    app, key = group_app(tmp_path)
    draft = write(app, key, reply_to=91, undo="Exact Undo")

    async def exact(peer, identity):
        access(app, key)
        access(app, key, "available")
        return Message(
            account_id=1,
            peer=peer,
            message_id=identity,
            revision=1,
            date=app.store.clock(),
            text="Authored exact reply source",
        )

    app.transport.exact = exact
    with pytest.raises(GroupSendBlocked, match="access changed"):
        await app.delivery.submit(key, draft["revision"])
    assert app.store.draft(key) == draft
    assert not app.store.rows("SELECT * FROM operations")


async def test_draft_or_reply_changed_during_exact_native_read_cannot_be_accepted(
    tmp_path,
):
    app, key = group_app(tmp_path)
    draft = write(app, key, reply_to=91)
    newer = None

    async def exact(peer, identity):
        nonlocal newer
        newer = write(app, key, "New exact text", reply_to=92)
        return Message(
            account_id=1,
            peer=peer,
            message_id=identity,
            revision=1,
            date=app.store.clock(),
            text="Authored reply source",
        )

    app.transport.exact = exact
    with pytest.raises(Conflict, match="Draft changed"):
        await app.delivery.submit(key, draft["revision"])
    assert app.store.draft(key) == newer
    assert not app.store.rows("SELECT * FROM operations")


@pytest.mark.parametrize("wrong", ["account", "peer", "id", "deleted"])
async def test_exact_reply_identity_is_bound_to_the_same_group_and_account(
    tmp_path, wrong
):
    app, key = group_app(tmp_path)
    draft = write(app, key, reply_to=91)

    async def exact(peer, identity):
        return Message(
            account_id=2 if wrong == "account" else 1,
            peer="channel:43" if wrong == "peer" else peer,
            message_id=92 if wrong == "id" else identity,
            deleted=wrong == "deleted",
            revision=1,
            date=app.store.clock(),
        )

    app.transport.exact = exact
    with pytest.raises(Refused, match="Native reply target"):
        await app.delivery.submit(key, draft["revision"])
    assert app.store.draft(key) == draft and not app.transport.deliveries


async def test_deleted_group_reply_holds_accepted_content_without_retargeting(tmp_path):
    app, key = group_app(tmp_path)
    target = source(app, key, 3)
    saved = await app.delivery.submit(key, write(app, key, reply_to=3)["revision"])
    app.store.apply_message(target.model_copy(update={"deleted": True, "revision": 2}))
    refused = await app.delivery.drain(saved["id"])
    assert (refused["state"], refused["cause"], refused["reply_to"]) == (
        "refused",
        "reply_deleted",
        3,
    )
    assert refused["text"] == saved["text"] and not app.transport.deliveries
    assert not operation_view(refused, app.store.clock())["retryable"]


@pytest.mark.parametrize("restore", [False, True])
async def test_loss_before_dispatch_holds_every_item_and_never_uses_ordinary_retry(
    tmp_path, restore
):
    app, key = group_app(tmp_path)
    first = await app.delivery.submit(key, write(app, key, "Exact first")["revision"])
    second = await app.delivery.submit(key, write(app, key, "Exact second")["revision"])
    newer = write(app, key, "Exact unsent editable draft")
    access(app, key)
    app.delivery.revalidate_group(key)
    if restore:
        access(app, key, "available")
        app.delivery.revalidate_group(key)
    assert app.store.send_operations.runnable(key) is None
    for saved in (first, second):
        held = operation(app, saved["id"])
        assert (held["state"], held["cause"], held["text"]) == (
            "refused",
            "group_access_loss",
            saved["text"],
        )
        view = operation_view(held, app.store.clock())
        assert view["cancellable"] and not view["retryable"] and not view["checkable"]
        with pytest.raises(Refused, match="Review.*Resume"):
            app.delivery.retry(saved["id"])
        assert (await app.delivery.dispatch(saved["id"]))["state"] == "refused"
        cancelled = app.delivery.cancel(saved["id"])
        assert cancelled["state"] == "cancelled" and cancelled["text"] == saved["text"]
    assert app.store.draft(key) == newer and not app.transport.deliveries


async def test_loss_and_restore_without_notification_is_detected_at_atomic_claim(
    tmp_path,
):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])
    app.store.groups.on_access_change = lambda state: None
    access(app, key)
    access(app, key, "available")
    assert not app.store.send_operations.claim(saved["id"])
    held = operation(app, saved["id"])
    assert (held["state"], held["cause"]) == ("refused", "group_access_loss")
    assert not app.transport.deliveries


async def test_low_level_acceptance_rechecks_the_captured_access_in_its_transaction(
    tmp_path,
):
    app, key = group_app(tmp_path)
    draft = write(app, key)
    captured = app.store.groups.get(key).access_revision
    access(app, key)
    access(app, key, "available")
    with pytest.raises(GroupSendBlocked, match="access changed"):
        app.store.send_operations.prepare(
            key,
            draft["revision"],
            snapshot=app.attachments.snapshot,
            expected_group_revision=captured,
        )
    assert app.store.draft(key) == draft
    assert not app.store.rows("SELECT * FROM operations")


async def test_repeated_group_submission_reuses_its_immutable_id_before_newer_draft(
    tmp_path,
):
    app, key = group_app(tmp_path)
    draft = write(app, key)
    saved = await app.delivery.submit(key, draft["revision"])
    newer = write(app, key, "New exact draft")
    again = await app.delivery.submit(key, draft["revision"])
    assert again["id"] == saved["id"]
    assert app.store.draft(key) == newer
    assert len(app.store.rows("SELECT * FROM operations")) == 1


async def test_changed_native_group_kind_cannot_redirect_an_accepted_send(tmp_path):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])
    app.store.put_chat(app.store.chat(key).model_copy(update={"kind": "broadcast"}))
    assert not app.store.send_operations.claim(saved["id"])
    held = operation(app, saved["id"])
    assert held["state"] == "refused" and held["peer"] == saved["peer"]
    assert held["cause"] == "target_changed"
    assert not operation_view(held, app.store.clock())["retryable"]
    assert not app.transport.deliveries


@pytest.mark.parametrize("checkpoint", ["guard", "handoff"])
async def test_loss_during_preparation_is_stopped_before_the_native_handoff(
    tmp_path, checkpoint
):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])

    async def send(description, evidence):
        evidence.calling()
        evidence.watching()
        access(app, key)
        access(app, key, "available")
        if checkpoint == "guard":
            evidence.guard()
        else:
            evidence.handing_off()
        raise AssertionError("Native handoff must be refused")

    app.transport.send = send
    held = await app.delivery.dispatch(saved["id"])
    assert (held["state"], held["cause"]) == ("refused", "group_access_loss")
    assert [value["outcome"] for value in held["wire"]] == ["watched"]
    assert held["text"] == saved["text"]


async def test_lost_group_unknown_handoff_never_becomes_retryable_or_cancellable(
    tmp_path,
):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])

    async def send(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        access(app, key)
        access(app, key, "available")
        evidence.guard()

    app.transport.send = send
    unknown = await app.delivery.dispatch(saved["id"])
    assert unknown["state"] == "unknown" and unknown["text"] == saved["text"]
    assert [value["outcome"] for value in unknown["wire"]] == ["watched", "exposed"]
    assert operation_view(unknown, app.store.clock())["checkable"]
    for resolve in (app.delivery.retry, app.delivery.cancel):
        with pytest.raises(Refused):
            resolve(saved["id"])
    app.delivery.revalidate_group(key)
    assert operation(app, saved["id"])["state"] == "unknown"
    app.store.send_operations.record_receipts(
        ReceiptBatch(((saved["random_id"], 700),))
    )
    confirmed = await app.delivery.recheck(saved["id"])
    assert (confirmed["state"], confirmed["message_id"]) == ("confirmed", 700)
    assert not app.transport.deliveries


async def test_group_disconnection_retains_existing_deliberate_retry_and_random_id(
    tmp_path,
):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])
    before = app.store.groups.get(key)
    app.transport.connected = False
    refused = await app.delivery.dispatch(saved["id"])
    assert (refused["state"], refused["cause"]) == ("refused", "not_sent")
    assert app.store.groups.get(key) == before
    assert operation_view(refused, app.store.clock())["retryable"]
    app.transport.connected = True
    retried = app.delivery.retry(saved["id"])
    assert retried["random_id"] == saved["random_id"]
    confirmed = await app.delivery.drain(saved["id"])
    assert confirmed["state"] == "confirmed" and len(app.transport.deliveries) == 1


async def test_group_native_refusal_without_access_loss_retains_retry_then_order(
    tmp_path,
):
    app, key = group_app(tmp_path)
    first = await app.delivery.submit(key, write(app, key, "Exact first")["revision"])
    second = await app.delivery.submit(key, write(app, key, "Exact second")["revision"])
    original = app.transport.send

    async def refuse(description, evidence):
        raise DeliveryRefused("Authored native slow-mode or permission refusal")

    app.transport.send = refuse
    failed = await app.delivery.drain(first["id"])
    assert (
        failed["state"] == "refused"
        and operation_view(failed, app.store.clock())["retryable"]
    )
    assert (await app.delivery.drain(second["id"]))["state"] == "queued"
    app.transport.send = original
    app.delivery.retry(first["id"])
    for saved in (first, second):
        assert (await app.delivery.drain(saved["id"]))["state"] == "confirmed"
    descriptions = [value["description"] for value in app.transport.deliveries.values()]
    assert [value.text for value in descriptions] == ["Exact first", "Exact second"]


async def test_native_definite_refusal_after_confirmed_loss_requires_resume(tmp_path):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])

    async def refuse(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        access(app, key)
        raise DeliveryRefused("Authored native membership rejection")

    app.transport.send = refuse
    held = await app.delivery.dispatch(saved["id"])
    assert (held["state"], held["cause"]) == ("refused", "group_access_loss")
    access(app, key, "available")
    with pytest.raises(Refused, match="Review.*Resume"):
        app.delivery.retry(saved["id"])


@pytest.mark.parametrize("restore", [False, True])
async def test_later_access_loss_holds_a_definitively_refused_exposed_send(
    tmp_path, restore
):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])

    async def refuse(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        raise DeliveryRefused("Authored definitive native permission refusal")

    app.transport.send = refuse
    refused = await app.delivery.dispatch(saved["id"])
    assert refused["state"] == "refused" and refused["native_refused"] is True
    assert refused["wire"][-1]["outcome"] == "exposed"
    assert operation_view(refused, app.store.clock())["retryable"]
    access(app, key)
    if restore:
        access(app, key, "available")
    held = operation(app, saved["id"])
    assert (held["state"], held["cause"], held["text"]) == (
        "refused",
        "group_access_loss",
        saved["text"],
    )
    view = operation_view(held, app.store.clock())
    assert not view["retryable"] and view["cancellable"]
    with pytest.raises(Refused, match="Review.*Resume"):
        app.delivery.retry(saved["id"])
    assert app.delivery.cancel(saved["id"])["text"] == saved["text"]
    assert not app.transport.deliveries


async def test_a_new_unknown_attempt_never_inherits_prior_native_refusal_proof(
    tmp_path,
):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])

    async def refuse(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        raise DeliveryRefused("Authored definitive native refusal")

    app.transport.send = refuse
    refused = await app.delivery.dispatch(saved["id"])
    assert refused["native_refused"] is True
    queued = app.delivery.retry(saved["id"])
    assert "native_refused" not in queued
    assert queued["attempts"][-1]["native_refused"] is True

    async def uncertain(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        raise ConnectionError("Authored possible execution without receipt")

    app.transport.send = uncertain
    unknown = await app.delivery.dispatch(saved["id"])
    assert unknown["state"] == "unknown" and "native_refused" not in unknown
    access(app, key)
    access(app, key, "available")
    unchanged = operation(app, saved["id"])
    assert unchanged["state"] == "unknown" and "cause" not in unchanged
    view = operation_view(unchanged, app.store.clock())
    assert view["checkable"] and not view["cancellable"] and not view["retryable"]


async def test_group_hold_does_not_block_private_or_another_group_queue(tmp_path):
    app, key = group_app(tmp_path)
    held = await app.delivery.submit(key, write(app, key)["revision"])
    private = await app.delivery.submit(
        "1/user:42", edit(app, "Private authored text")["revision"]
    )
    other = "1/chat:42"
    app.store.put_chat(
        Chat(account_id=1, peer="chat:42", title="Other authored group", kind="group")
    )
    access(app, other, "available")
    elsewhere = await app.delivery.submit(other, write(app, other)["revision"])
    access(app, key)
    app.delivery.revalidate_group(key)
    assert (await app.delivery.drain(held["id"]))["cause"] == "group_access_loss"
    for saved in (private, elsewhere):
        assert (await app.delivery.drain(saved["id"]))["state"] == "confirmed"
    assert len(app.transport.deliveries) == 2


@pytest.mark.parametrize("checkpoint", ["queued", "unhanded", "exposed"])
async def test_group_recovery_preserves_lost_access_hold_or_uncertainty(
    tmp_path, checkpoint
):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])
    if checkpoint != "queued":
        assert app.store.send_operations.claim(saved["id"])
        evidence = app.store.send_operations.evidence(saved["id"])
        evidence.calling()
        evidence.watching()
        if checkpoint == "exposed":
            evidence.handing_off()
    access(app, key)
    access(app, key, "available")
    with app.store.transaction() as db:
        app.store.send_operations.recover(db)
    recovered = operation(app, saved["id"])
    expected = "unknown" if checkpoint == "exposed" else "refused"
    assert recovered["state"] == expected and recovered["text"] == saved["text"]
    if expected == "refused":
        assert recovered["cause"] == "group_access_loss"
    assert not app.transport.deliveries


async def test_group_migration_never_retargets_an_accepted_basic_group_send(tmp_path):
    app, key = group_app(tmp_path, "chat:42")
    saved = await app.delivery.submit(key, write(app, key)["revision"])
    access(app, key, "available", migrated_to="channel:42")
    app.store.put_chat(
        Chat(
            account_id=1,
            peer="channel:42",
            title="Migrated authored group",
            kind="group",
        )
    )
    access(app, "1/channel:42", "available")
    held = await app.delivery.drain(saved["id"])
    assert (held["state"], held["cause"], held["peer"]) == (
        "refused",
        "group_access_loss",
        "chat:42",
    )
    assert not app.store.groups.get("1/channel:42").processing_enabled
    assert not app.transport.deliveries


async def test_group_retry_and_cancel_cannot_use_another_connected_account(tmp_path):
    app, key = group_app(tmp_path)
    saved = await app.delivery.submit(key, write(app, key)["revision"])
    app.store.send_operations.not_dispatched(saved["id"], "Authored pause.")
    app.transport.account_id = 2
    for resolve in (app.delivery.retry, app.delivery.cancel):
        with pytest.raises(Refused, match="another account"):
            resolve(saved["id"])
    assert operation(app, saved["id"])["state"] == "refused"


async def test_group_media_cancel_after_access_loss_retains_exact_accepted_bytes(
    tmp_path,
):
    app, key = group_app(tmp_path)
    attached = app.attachments.add(
        key, 0, "authored.png", base64.b64encode(picture()).decode()
    )
    saved = await app.delivery.submit(
        key, write(app, key, "Exact kept caption")["revision"]
    )
    access(app, key)
    app.delivery.revalidate_group(key)
    cancelled = app.delivery.cancel(saved["id"])
    app.attachments.collect()
    identity = attached["attachments"][0]
    value, path = app.attachments.get(key, identity)
    assert path.read_bytes() == picture()
    assert value["sha256"] == cancelled["attachments"][0]["sha256"]
    public = json.dumps(operation_view(cancelled, app.store.clock()))
    assert "path" not in public and "sha256" not in public
    assert cancelled["text"] == "Exact kept caption"
    assert not app.transport.deliveries


async def test_group_access_loss_during_service_drain_preserves_unknown_handoff(
    tmp_path,
):
    app, key = group_app(tmp_path)
    handed = asyncio.Event()

    async def silent(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        handed.set()
        await asyncio.Event().wait()

    app.transport.send = silent
    worker = asyncio.create_task(app.delivery.run())
    saved = await app.delivery.submit(key, write(app, key)["revision"])
    await asyncio.wait_for(handed.wait(), 10)
    access(app, key)
    app.delivery.revalidate_group(key)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert operation(app, saved["id"])["state"] == "unknown"
    assert not app.delivery.chats and not app.transport.deliveries
