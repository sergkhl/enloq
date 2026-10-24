"""GS2/GS3 authored selected bytes, kept drafts and immutable queue boundaries."""

import asyncio
import gzip
import io
import json
import sqlite3
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from enloq.application.delivery import operation_view
from enloq.domain.catalog import (
    CatalogLibrary,
    CatalogMetadata,
    CatalogOrigin,
    NativeCatalogSource,
)
from enloq.domain.delivery import DeliveryRefused, StickerReselectionRequired
from enloq.domain.models import Chat, Conflict, Refused, Unavailable
from enloq.storage.store import RETENTION
from fastapi import HTTPException
from PIL import Image

from backend.tests.test_catalog import load
from backend.tests.test_rebuild import attach, edit, fixture, message

KEY = "1/user:42"
BROWSER = "authored-browser"


def media_bytes(kind="gif"):
    output = io.BytesIO()
    if kind == "gif":
        first = Image.new("RGB", (32, 32), "red")
        second = Image.new("RGB", (32, 32), "blue")
        first.save(
            output,
            format="GIF",
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
    else:
        Image.new("RGBA", (512, 512), (25, 50, 100, 255)).save(
            output, format="WEBP", lossless=True
        )
    return output.getvalue()


def source(kind="gif", identity=73, *, data=None, account=1, premium=False):
    data = data or media_bytes(kind)
    return NativeCatalogSource(
        account_id=account,
        document_id=str(identity),
        access_hash="-17",
        file_reference="YQ==",
        dc_id=2,
        origins=(CatalogOrigin(kind="saved" if kind == "gif" else "recent"),),
        metadata=CatalogMetadata(
            kind=kind,
            format="gif" if kind == "gif" else "static",
            mime="image/gif" if kind == "gif" else "image/webp",
            size=len(data),
            emoji="🙂" if kind == "sticker" else None,
            requires_premium=premium,
        ),
    )


def selected_app(tmp_path, **kwargs):
    return fixture(tmp_path, **kwargs)


async def item(app, kind="gif", identity=73, *, data=None, premium=False):
    data = data or media_bytes(kind)
    value = source(
        kind, identity, data=data, account=app.store.account_id, premium=premium
    )
    app.transport.catalog_media[value.document_id, False] = data
    app.transport.catalog_data = CatalogLibrary(
        premium=premium,
        gifs=(value,) if kind == "gif" else (),
        recent=(value,) if kind == "sticker" else (),
    )
    view = await load(app, browser=BROWSER)
    return (view.gifs if kind == "gif" else view.recent)[0]


async def stage(app, kind="gif", identity=73, **kwargs):
    value = await item(app, kind, identity, **kwargs)
    return await app.selected_media.select(
        KEY, app.store.draft(KEY)["revision"], BROWSER, value.token, value.revision
    )


def kept(draft):
    return {
        key: value
        for key, value in draft.items()
        if key not in {"revision", "selected_media"}
    }


@pytest.mark.parametrize("kind", ["gif", "sticker"])
async def test_selection_pins_exact_private_bytes_and_safe_public_facts(tmp_path, kind):
    app = selected_app(tmp_path)
    original = edit(app, "Authored caption or kept draft")
    selected = await stage(app, kind)
    assert kept(selected) == kept(original)
    descriptor = selected["selected_media"]
    assert (descriptor["kind"], descriptor["status"], descriptor["format"]) == (
        kind,
        "ready",
        "gif" if kind == "gif" else "webp",
    )
    value, path = app.selected_media.selected_file(KEY, descriptor["id"])
    assert path.read_bytes() == media_bytes(kind)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not app.store.rows("SELECT * FROM media_files")
    assert not app.store.rows("SELECT * FROM jobs") and not app.engine.calls
    public = json.dumps(selected)
    for private in (
        "access_hash",
        "document_id",
        "file_reference",
        "origins",
        "sha256",
        str(path),
        "url",
        "source",
    ):
        if private == "url":
            assert '"url"' not in public
        else:
            assert private not in public
    assert value["source"]["document_id"] == "73"
    assert app.transport.catalog_downloads == [("73", False)]


@pytest.mark.parametrize(
    "state", ["idle", "inserted", "rewritten", "incomplete", "failed"]
)
async def test_sticker_removal_and_acceptance_keep_every_draft_field(tmp_path, state):
    app = selected_app(tmp_path)
    app.sync.ingest(message(app, 1))
    original = app.store.mutate_draft(
        KEY,
        0,
        lambda d: d.update(
            text="Kept exact — “text”\n",
            undo="Exact old\nwording",
            state=state,
            error="Authored status",
            attempt="authored-attempt",
            reply_to=1,
        ),
    )
    selected = await stage(app, "sticker")
    assert kept(selected) == kept(original)
    discarded = app.selected_media.discard(
        KEY, selected["revision"], selected["selected_media"]["id"]
    )
    assert kept(discarded) == kept(original) and discarded["selected_media"] is None
    selected = await stage(app, "sticker", 74)
    operation = await app.delivery.submit(KEY, selected["revision"])
    assert operation["text"] == "" and operation["reply_to"] == 1
    restored = app.store.draft(KEY)
    assert kept(restored) == kept(original) and restored["selected_media"] is None
    assert (
        app.store.selected_media.get(KEY, operation["media"]["id"])["operation"]
        == operation["id"]
    )
    again = await app.delivery.submit(KEY, selected["revision"])
    assert again["id"] == operation["id"]
    newer = edit(app, "New exact draft", reply_to=1)
    sent = await app.delivery.drain(operation["id"])
    assert sent["state"] == "confirmed" and app.store.draft(KEY) == newer
    description = next(iter(app.transport.deliveries.values()))["description"]
    assert description.text == "" and description.media.kind == "sticker"
    with pytest.raises(FrozenInstanceError):
        description.media.size = 1
    stored = app.store.message(KEY, sent["message_id"])
    assert stored.media == "sticker" and stored.media_info.sticker == "static"
    assert stored.text == "" and stored.media_info.mime == "image/webp"


@pytest.mark.parametrize(
    "text,retains_reply",
    [("", False), (" ", True), ("x" * 4097, True), ("\ud800", True)],
)
async def test_sticker_payload_ignores_kept_text_bounds_and_empty_draft_clears_reply(
    tmp_path, text, retains_reply
):
    app = selected_app(tmp_path)
    app.sync.ingest(message(app, 1))
    edit(app, text, reply_to=1)
    selected = await stage(app, "sticker")
    operation = await app.delivery.submit(KEY, selected["revision"])
    assert operation["text"] == "" and operation["reply_to"] == 1
    draft = app.store.draft(KEY)
    assert draft["text"] == text and draft["reply_to"] == (1 if retains_reply else None)


@pytest.mark.parametrize(
    "caption,allowed",
    [
        ("", True),
        ("x" * 1024, True),
        ("😀" * 512, True),
        ("😀" * 513, False),
        ("\ud800", False),
    ],
)
async def test_gif_caption_uses_utf16_bound_and_is_consumed_only_on_acceptance(
    tmp_path, caption, allowed
):
    app = selected_app(tmp_path)
    edit(app, caption)
    selected = await stage(app)
    if allowed:
        operation = await app.delivery.submit(KEY, selected["revision"])
        assert operation["text"] == caption and operation["media"]["kind"] == "gif"
        assert (
            app.store.draft(KEY)["text"],
            app.store.draft(KEY)["selected_media"],
        ) == ("", None)
    else:
        with pytest.raises(Refused):
            await app.delivery.submit(KEY, selected["revision"])
        assert app.store.draft(KEY) == selected and not app.store.rows(
            "SELECT * FROM operations"
        )


async def test_transaction_failure_keeps_selection_bytes_and_exact_sticker_draft(
    tmp_path,
):
    app = selected_app(tmp_path)
    edit(app, "Kept authored words")
    selected = await stage(app, "sticker")
    identity = selected["selected_media"]["id"]
    with app.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER refuse_accept BEFORE UPDATE ON drafts BEGIN SELECT RAISE(ABORT, 'authored disk failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="authored disk failure"):
        await app.delivery.submit(KEY, selected["revision"])
    assert app.store.draft(KEY) == selected
    assert app.store.selected_media.get(KEY, identity)["operation"] is None
    assert not app.store.rows("SELECT * FROM operations")
    assert app.store.selected_media.path(identity).read_bytes() == media_bytes(
        "sticker"
    )


async def test_photo_mixing_and_sticker_text_actions_refuse_without_mutation(tmp_path):
    app = selected_app(tmp_path)
    attach(app)
    draft = app.store.draft(KEY)
    with pytest.raises(Refused, match="Remove images"):
        await stage(app)
    assert app.store.draft(KEY) == draft
    app.attachments.remove(KEY, draft["revision"], draft["attachments"][0])
    edit(app, "Authored words")
    selected = await stage(app, "sticker")
    before_files = set(app.attachments.root.iterdir())
    with pytest.raises(Refused, match="Remove the selected"):
        attach(app)
    assert set(app.attachments.root.iterdir()) == before_files
    with pytest.raises(Conflict, match="composer is busy"):
        app.suggestions.generate(KEY, selected["revision"])
    with pytest.raises(Refused, match="Remove the sticker"):
        app.rewriting.undo(KEY, selected["revision"])
    assert app.store.draft(KEY) == selected and not app.engine.calls


@pytest.mark.parametrize("change", ["caption", "reply", "discard", "replace"])
async def test_late_preparation_only_publishes_for_its_owned_selection_and_preserves_new_edits(
    tmp_path, change
):
    app = selected_app(tmp_path)
    edit(app, "Original caption")
    value = await item(app)
    entered, release = asyncio.Event(), asyncio.Event()
    download = app.transport.catalog_download

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await download(*args, **kwargs)

    app.transport.catalog_download = delayed
    task = asyncio.create_task(
        app.selected_media.select(
            KEY, app.store.draft(KEY)["revision"], BROWSER, value.token, value.revision
        )
    )
    await entered.wait()
    pending = app.store.draft(KEY)
    old_id = pending["selected_media"]["id"]
    if change in {"caption", "reply"}:
        app.store.mutate_draft(
            KEY,
            pending["revision"],
            lambda d: d.update(
                text="New caption" if change == "caption" else d["text"],
                reply_to=91 if change == "reply" else d["reply_to"],
            ),
        )
    else:
        app.selected_media.discard(KEY, pending["revision"], old_id)
        if change == "replace":
            app.transport.catalog_download = download
            replacement = await stage(app, identity=74)
    release.set()
    if change in {"discard", "replace"}:
        with pytest.raises(Conflict):
            await task
        assert not app.store.selected_media.path(old_id).exists()
        assert app.store.draft(KEY).get("selected_media") == (
            replacement["selected_media"] if change == "replace" else None
        )
    else:
        final = await task
        assert final["selected_media"]["status"] == "ready"
        assert final["text"] == (
            "New caption" if change == "caption" else "Original caption"
        )
        assert final["reply_to"] == (91 if change == "reply" else None)


@pytest.mark.parametrize("boundary", ["download", "validation", "entitlement"])
@pytest.mark.parametrize("revocation", ["browser", "account"])
async def test_access_revocation_after_await_never_returns_private_draft_or_ready_result(
    tmp_path, monkeypatch, boundary, revocation
):
    from enloq.application import selected_media as module

    app = selected_app(tmp_path)
    edit(app, "Private authored kept draft")
    value = await item(app, "sticker" if boundary == "entitlement" else "gif")
    authorized = [True]

    def guard():
        if not authorized[0] and revocation == "browser":
            raise HTTPException(401, "Authored revoked browser")

    app.catalog.authorized = lambda: authorized[0] if revocation == "account" else True
    if boundary == "download":
        original = app.transport.catalog_download

        async def changed(*args, **kwargs):
            result = await original(*args, **kwargs)
            authorized[0] = False
            return result

        app.transport.catalog_download = changed
    elif boundary == "validation":
        original = module.validate_selection

        async def changed(*args, **kwargs):
            result = await original(*args, **kwargs)
            authorized[0] = False
            return result

        monkeypatch.setattr(module, "validate_selection", changed)
    else:
        original = app.catalog.entitlement
        calls = [0]

        async def changed():
            result = await original()
            calls[0] += 1
            if calls[0] == 2:
                authorized[0] = False
            return result

        app.catalog.entitlement = changed
    with pytest.raises(HTTPException if revocation == "browser" else Refused):
        await app.selected_media.select(
            KEY,
            app.store.draft(KEY)["revision"],
            BROWSER,
            value.token,
            value.revision,
            guard=guard,
        )
    current = app.store.draft(KEY)
    assert (
        current["text"] == "Private authored kept draft"
        and current["selected_media"]["status"] != "ready"
    )
    assert not app.store.rows("SELECT * FROM operations")


async def test_restart_recovers_interrupted_preparation_without_automatic_work(
    tmp_path,
):
    app = selected_app(tmp_path)
    edit(app, "Keep exact words")
    native = source()
    value, pending = app.store.selected_media.begin(
        KEY, app.store.draft(KEY)["revision"], native
    )
    path = app.store.selected_media.path(value["id"])
    path.write_bytes(b"partial authored bytes")
    path.chmod(0o600)
    app.store.close()
    restarted = selected_app(tmp_path)
    restarted.store.recover()
    failed = restarted.store.draft(KEY)
    assert kept(failed) == kept(pending)
    assert (
        failed["selected_media"]["status"] == "failed"
        and failed["selected_media"]["retryable"]
    )
    assert not path.exists() and not restarted.transport.catalog_downloads
    restarted.transport.catalog_media[native.document_id, False] = media_bytes()
    restored = await restarted.selected_media.retry(
        KEY, failed["revision"], value["id"]
    )
    assert (
        restored["selected_media"]["status"] == "ready"
        and restored["selected_media"]["id"] == value["id"]
    )
    assert restored[
        "text"
    ] == "Keep exact words" and restarted.transport.catalog_downloads == [("73", False)]


@pytest.mark.parametrize(
    "state", ["queued", "refused", "unknown", "confirmed", "cancelled"]
)
async def test_every_retained_operation_pins_media_independently_of_history_and_lru(
    tmp_path, state
):
    now = [2_000_000_000.0]
    app = selected_app(tmp_path, clock=lambda: now[0])
    selected = await stage(app, "sticker")
    operation = await app.delivery.submit(KEY, selected["revision"])
    identity = operation["media"]["id"]
    with app.store.transaction() as db:
        operation["state"] = state
        db.execute(
            "UPDATE operations SET state=?,body=? WHERE id=?",
            (state, json.dumps(operation), operation["id"]),
        )
    now[0] += RETENTION - 1
    app.store.expire()
    app.store.selected_media.collect()
    value, path = app.selected_media.operation_file(KEY, operation["id"], identity)
    assert (
        path.read_bytes() == media_bytes("sticker")
        and value["operation"] == operation["id"]
    )
    # Terminal operation expiry releases bytes only when its own row is removed.
    now[0] += 2
    app.store.expire()
    app.store.selected_media.collect()
    assert path.exists() is (state not in {"confirmed", "cancelled"})


async def test_unsent_selection_survives_more_than_thirty_days_without_catalog_access(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = selected_app(tmp_path, clock=lambda: now[0])
    selected = await stage(app)
    now[0] += RETENTION + 86400
    app.store.expire()
    app.store.selected_media.collect()
    app.catalog.clear_transient()
    app.transport.connected = False
    value, path = app.selected_media.selected_file(
        KEY, selected["selected_media"]["id"]
    )
    assert path.read_bytes() == media_bytes() and value["status"] == "ready"
    discarded = app.selected_media.discard(
        KEY, selected["revision"], selected["selected_media"]["id"]
    )
    assert discarded["selected_media"] is None and not path.exists()


@pytest.mark.parametrize("tamper", ["bytes", "symlink", "mode", "size", "kind"])
async def test_integrity_and_explicit_caps_refuse_before_acceptance(tmp_path, tamper):
    app = selected_app(tmp_path)
    selected = await stage(app, "sticker")
    identity = selected["selected_media"]["id"]
    path = app.store.selected_media.path(identity)
    if tamper == "bytes":
        path.write_bytes(b"different authored bytes")
    elif tamper == "symlink":
        original = path.with_suffix(".original")
        path.rename(original)
        path.symlink_to(original)
    elif tamper == "mode":
        path.chmod(0o644)
    else:
        value = app.store.selected_media.get(KEY, identity)
        value["facts"]["size" if tamper == "size" else "kind"] = (
            5 * 1024**2 + 1 if tamper == "size" else "gif"
        )
        with app.store.transaction() as db:
            app.store.selected_media._save(db, value)
    with pytest.raises(Refused):
        await app.delivery.submit(KEY, selected["revision"])
    assert app.store.draft(KEY) == selected and not app.store.rows(
        "SELECT * FROM operations"
    )


async def test_selection_owner_is_bound_to_account_chat_and_exact_operation(tmp_path):
    app = selected_app(tmp_path)
    app.store.put_chat(
        Chat(account_id=1, peer="user:43", title="Other authored chat", kind="private")
    )
    selected = await stage(app)
    identity = selected["selected_media"]["id"]
    with pytest.raises(Refused):
        app.store.selected_media.get("1/user:43", identity)
    operation = await app.delivery.submit(KEY, selected["revision"])
    with pytest.raises(Conflict):
        app.selected_media.selected_file(KEY, identity)
    with pytest.raises(Refused):
        app.selected_media.operation_file("1/user:43", operation["id"], identity)
    other = selected_app(tmp_path, account=2)
    with pytest.raises(Refused):
        other.store.selected_media.get("2/user:42", identity)
    forged = deepcopy(operation)
    forged["id"] = "a" * 32
    with pytest.raises(Refused):
        app.store.selected_media.operation_file(forged, identity)


async def test_actual_premium_loss_and_submit_revision_change_keep_selection(tmp_path):
    app = selected_app(tmp_path)
    selected = await stage(app, "sticker", premium=True)
    app.transport.catalog_data = app.transport.catalog_data.model_copy(
        update={"premium": False}
    )
    with pytest.raises(Refused, match="Premium"):
        await app.delivery.submit(KEY, selected["revision"])
    assert app.store.draft(KEY) == selected and not app.store.rows(
        "SELECT * FROM operations"
    )
    app.transport.catalog_data = app.transport.catalog_data.model_copy(
        update={"premium": True}
    )
    original = app.catalog.entitlement

    async def changed():
        result = await original()
        app.store.mutate_draft(
            KEY, app.store.draft(KEY)["revision"], lambda d: d.update(reply_to=99)
        )
        return result

    app.catalog.entitlement = changed
    with pytest.raises(Conflict):
        await app.delivery.submit(KEY, selected["revision"])
    assert app.store.draft(KEY)["reply_to"] == 99 and not app.store.rows(
        "SELECT * FROM operations"
    )


async def test_final_refusal_retry_clears_old_evidence_and_unknown_never_auto_resends(
    tmp_path,
):
    app = selected_app(tmp_path)
    selected = await stage(app)
    operation = await app.delivery.submit(KEY, selected["revision"])
    sends = []

    async def expired(description, evidence):
        sends.append(description)
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        evidence.refused("Authored final file-reference rejection")
        raise DeliveryRefused("Authored final file-reference rejection")

    app.transport.send = expired
    refused = await app.delivery.drain(operation["id"])
    assert refused["state"] == "refused"
    app.delivery.retry(operation["id"])
    assert not app.store.rows(
        "SELECT 1 FROM cursors WHERE key=?", ("refusal:op:" + operation["id"],)
    )

    async def exposed(description, evidence):
        sends.append(description)
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        raise ConnectionError("Authored loss after possible handoff")

    app.transport.send = exposed
    unknown = await app.delivery.drain(operation["id"])
    assert unknown["state"] == "unknown"
    assert [d.random_ids for d in sends] == [(operation["random_id"],)] * 2
    assert (await app.delivery.recheck(operation["id"]))["state"] == "unknown"
    assert (await app.delivery.drain(operation["id"]))["state"] == "unknown" and len(
        sends
    ) == 2
    assert app.selected_media.operation_file(
        KEY, operation["id"], operation["media"]["id"]
    )[1].exists()


async def test_unrecoverable_sticker_requires_reselection_and_retains_pending_bytes(
    tmp_path,
):
    app = selected_app(tmp_path)
    edit(app, "Exact kept draft")
    selected = await stage(app, "sticker")
    operation = await app.delivery.submit(KEY, selected["revision"])

    async def missing(description, evidence):
        evidence.guard()
        raise StickerReselectionRequired("Original sticker unavailable; select again")

    app.transport.send = missing
    refused = await app.delivery.drain(operation["id"])
    view = operation_view(refused, app.store.clock())
    assert (
        view["cause"] == "sticker_unavailable"
        and not view["retryable"]
        and view["cancellable"]
    )
    with pytest.raises(Refused, match="select a sticker again"):
        app.delivery.retry(operation["id"])
    assert app.store.draft(KEY)["text"] == "Exact kept draft"
    app.delivery.cancel(operation["id"])
    assert app.selected_media.operation_file(
        KEY, operation["id"], operation["media"]["id"]
    )[1].exists()


@pytest.mark.parametrize("retire", ["search", "library"])
async def test_durably_selected_source_outlives_its_picker_ticket(tmp_path, retire):
    app = selected_app(tmp_path)
    value = await item(app)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.transport.catalog_download

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    app.transport.catalog_download = delayed
    task = asyncio.create_task(
        app.selected_media.select(KEY, 0, BROWSER, value.token, value.revision)
    )
    await entered.wait()
    identity = app.store.draft(KEY)["selected_media"]["id"]
    if retire == "search":
        app.catalog.open_search(KEY, BROWSER)
    else:
        app.transport.catalog_data = CatalogLibrary(premium=False)
        await load(app, browser=BROWSER)
    with pytest.raises(Refused):
        app.catalog.resolve(KEY, BROWSER, value.token, value.revision)
    release.set()
    prepared = await task
    assert (
        prepared["selected_media"]["id"] == identity
        and prepared["selected_media"]["status"] == "ready"
    )
    assert app.store.selected_media.path(identity).read_bytes() == media_bytes()


async def test_retired_picker_result_cannot_start_new_durable_selection(tmp_path):
    app = selected_app(tmp_path)
    value = await item(app)
    app.catalog.open_search(KEY, BROWSER)
    with pytest.raises(Refused):
        await app.selected_media.select(KEY, 0, BROWSER, value.token, value.revision)
    assert not app.store.draft(KEY).get("selected_media")
    assert (
        not app.store.rows("SELECT * FROM selected_media")
        and not app.transport.catalog_downloads
    )


@pytest.mark.parametrize("signal", ["incoming", "reaction"])
@pytest.mark.parametrize("kind", ["gif", "sticker"])
async def test_media_uses_ordinary_suggestion_suspend_restore_answer_lifecycle_without_emotion_reset(
    tmp_path, signal, kind
):
    from backend.tests.test_reaction_assessment import conversation, settle
    from backend.tests.test_reactions import snapshot
    from backend.tests.test_suggestion_state import ready

    now = [2_000_000_000.0]
    app = selected_app(tmp_path, clock=lambda: now[0])
    if signal == "incoming":
        app.sync.ingest(message(app, 1, text="Authored worry"))
        record = await ready(app)
    else:
        app.decider.assessment = {"emotion": "worried", "suggestions_useful": True}
        await conversation(app, now)
        added = now[0] + 1
        app.sync.reactions("user:42", 2, snapshot(peer=[("👎", added)], observed=added))
        await settle(app, now, 35)
        record = app.suggestions.record(KEY)
    assert app.suggestions.view(KEY).state == "ready"
    emotion = app.emotion.view(KEY).model_dump()
    jobs = app.store.rows("SELECT * FROM jobs")
    selected = await stage(app, kind)
    assert (
        app.suggestions.view(KEY).state == "ready"
        and app.emotion.view(KEY).model_dump() == emotion
    )
    if kind == "sticker":
        with pytest.raises(Refused, match="Remove the sticker"):
            app.suggestions.insert(KEY, selected["revision"], record["attempt"], 0)
    held = await app.delivery.submit(KEY, selected["revision"])
    assert app.suggestions.view(KEY).reason == "suspended"
    app.store.send_operations.not_dispatched(held["id"], "Authored no handoff")
    assert app.suggestions.view(KEY).state == "ready"
    app.delivery.cancel(held["id"])
    assert (
        app.suggestions.view(KEY).state == "ready"
        and app.store.rows("SELECT * FROM jobs") == jobs
    )
    selected = await stage(app, kind, 74)
    app.transport.lose_receipt = True
    unknown = await app.delivery.send(KEY, selected["revision"])
    assert (
        unknown["state"] == "unknown"
        and app.suggestions.view(KEY).reason == "suspended"
    )
    confirmed = await app.delivery.recheck(unknown["id"])
    assert (
        confirmed["state"] == "confirmed"
        and app.suggestions.view(KEY).reason == "answered"
    )
    assert app.emotion.view(KEY).model_dump() == emotion


@pytest.mark.parametrize(
    "change", ["missing", "edited", "deleted", "expired", "revision"]
)
async def test_retained_operation_lifetime_does_not_grant_native_source_access(
    tmp_path, change
):
    app = selected_app(tmp_path)
    selected = await stage(app, "sticker")
    operation = await app.delivery.send(KEY, selected["revision"])
    original = app.store.message(KEY, operation["message_id"])
    assert app.selected_media.message_file(original).id == operation["media"]["id"]
    if change == "missing":
        with app.store.transaction() as db:
            db.execute("DELETE FROM messages WHERE key=?", (original.key,))
        tested = original
    else:
        tested = original.model_copy(
            update={"native_edited": True}
            if change == "edited"
            else {"deleted": True}
            if change == "deleted"
            else {"date": original.date - RETENTION - 1}
            if change == "expired"
            else {"revision": 1}
        )
    assert app.selected_media.message_file(tested) is None
    assert app.selected_media.operation_file(
        KEY, operation["id"], operation["media"]["id"]
    )[1].exists()


async def test_retry_keeps_the_durable_exact_source_when_the_picker_changes(tmp_path):
    app = selected_app(tmp_path)
    value = await item(app)
    original = app.transport.catalog_download

    async def failed(*args, **kwargs):
        raise Unavailable("Authored download interrupted")

    app.transport.catalog_download = failed
    failed_draft = await app.selected_media.select(
        KEY, 0, BROWSER, value.token, value.revision
    )
    descriptor = failed_draft["selected_media"]
    assert descriptor["status"] == "failed" and descriptor["retryable"]
    assert not app.store.selected_media.path(descriptor["id"]).exists()
    await item(app, identity=99)
    app.catalog.clear_transient()
    app.transport.catalog_download = original
    ready = await app.selected_media.retry(
        KEY, failed_draft["revision"], descriptor["id"]
    )
    assert (
        ready["selected_media"]["id"] == descriptor["id"]
        and ready["selected_media"]["status"] == "ready"
    )
    assert app.transport.catalog_downloads == [("73", False)]


@pytest.mark.parametrize("boundary", ["download", "validation"])
async def test_cancellation_marks_preparation_retryable_without_publishing_ready_or_losing_draft(
    tmp_path, monkeypatch, boundary
):
    from enloq.application import selected_media as module

    app = selected_app(tmp_path)
    original_draft = edit(app, "Exact kept draft")
    value = await item(app)
    entered = asyncio.Event()
    if boundary == "download":

        async def delayed(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        app.transport.catalog_download = delayed
    else:

        async def delayed(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(module, "validate_selection", delayed)
    task = asyncio.create_task(
        app.selected_media.select(
            KEY, original_draft["revision"], BROWSER, value.token, value.revision
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    failed = app.store.draft(KEY)
    assert kept(failed) == kept(original_draft)
    assert (
        failed["selected_media"]["status"] == "failed"
        and failed["selected_media"]["retryable"]
    )
    assert not app.store.selected_media.path(failed["selected_media"]["id"]).exists()
    assert (
        not app.store.rows("SELECT * FROM operations") and not app.selected_media.tasks
    )


@pytest.mark.parametrize("mode,supported", [("i", True), ("d", False)])
async def test_tgs_rendering_fallback_is_distinct_from_exact_native_sendability(
    tmp_path, mode, supported
):
    from backend.tests.test_selection_formats import animation, mask

    app = selected_app(tmp_path)
    original = animation()
    original["layers"][0]["masksProperties"] = [mask(mode)]
    data = gzip.compress(json.dumps(original).encode())
    selected = await stage(app, "sticker", data=data)
    descriptor = selected["selected_media"]
    assert (descriptor["status"], descriptor["format"], descriptor["sticker"]) == (
        "ready",
        "tgs",
        "animated",
    )
    assert descriptor["renderer_supported"] is supported
    assert (descriptor["animation_url"] is not None) is supported
    assert ("unsupported_mask_mode" not in descriptor["features"]) is supported
    operation = await app.delivery.send(KEY, selected["revision"])
    public = operation_view(operation, app.store.clock())["media"]
    assert (
        operation["state"] == "confirmed" and public["renderer_supported"] is supported
    )
    assert public["features"] == descriptor["features"]
    assert "animation_json" not in operation["media"]
    stored = app.store.message(KEY, operation["message_id"])
    assert (
        stored.media_info.sticker == "animated"
        and stored.media_info.mime == "application/x-tgsticker"
    )
    assert (
        app.selected_media.operation_file(
            KEY, operation["id"], operation["media"]["id"]
        )[1].read_bytes()
        == data
    )
