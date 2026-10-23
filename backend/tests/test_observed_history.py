import json

import pytest
from enloq.application import context
from enloq.domain.models import Refused
from enloq.storage.store import RETENTION

from backend.tests.test_media_cache import BASE
from backend.tests.test_media_cache import served as served
from backend.tests.test_rebuild import fixture, message


@pytest.mark.parametrize("outgoing", [False, True])
async def test_observed_versions_not_unseen_history_or_inference(tmp_path, outgoing):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    original = message(app, text="Before edit", outgoing=outgoing)
    app.sync.ingest(original, target=False)
    for revision, text in [(2, "Second version"), (4, "Fourth version")]:
        app.sync.ingest(
            original.model_copy(update={"revision": revision, "text": text}),
            target=False,
        )
    # A repeated update does not fabricate another observed version.
    app.sync.ingest(app.store.message("1/user:42", 1), target=False)
    rows = app.store.history.rows("1/user:42", 1)
    assert [json.loads(r["body"])["text"] for r in rows] == [
        "Second version",
        "Before edit",
    ]
    app.sync.ingest(message(app, 2, outgoing=True), target=False)
    app.sync.ingest(message(app, 3, text="Latest target"))
    packet = await context.incoming(
        app, app.store.rows("SELECT * FROM blocks WHERE id='1/user:42/3'")[0]
    )
    assert packet["context"][0]["text"] == "Fourth version"
    assert "Before edit" not in json.dumps(
        packet
    ) and "Second version" not in json.dumps(packet)
    app.sync.deleted("user:42", [1])
    rows = app.store.history.rows("1/user:42", 1)
    assert len(rows) == 3 and rows[0]["reason"] == "deleted"
    assert json.loads(rows[0]["body"])["text"] == "Fourth version"
    assert not app.transport.exact_reads
    now[0] += RETENTION + 1
    assert not app.store.history.rows("1/user:42", 1)
    with pytest.raises(Refused):
        app.store.history.version("1/user:42", 1, rows[0]["id"])
    app.store.expire()
    assert not app.store.rows("SELECT * FROM message_versions")


def test_transcript_and_cached_media_retained_without_archive_fetch(served):
    app, client, conversation, tone = served
    first = client.get(f"{BASE}/7/media")
    with conversation.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (
                "1/user:101/7",
                1,
                json.dumps(
                    {
                        "transcript": {"text": "Do not confirm", "language": "en"},
                        "image_context": "never archived",
                    }
                ),
            ),
        )
    conversation.sync.deleted("user:101", [7])
    # ADR 52 Q7: the deleted voice note keeps its captured content, which is
    # no earlier step of its own.
    shown = [
        m
        for m in client.get("/api/accounts/1/chats/user:101").json()["messages"]
        if m["message_id"] == 7
    ]
    assert [(m["deleted"], m["media"], m["history_count"]) for m in shown] == [
        (True, "audio", 0)
    ]
    capture = shown[0]["deleted_version"]
    assert shown[0]["media_available"] and shown[0]["revision"] == 1
    assert client.get(f"{BASE}/7/versions").json() == []
    assert client.get(f"{BASE}/7/transcript").json()["text"] == "Do not confirm"
    status = client.get(f"{BASE}/7/transcript/status").json()
    assert status["state"] == "done" and "image_context" not in str(status)
    refused = client.post(f"{BASE}/7/transcript", json={})
    assert refused.status_code == 400, refused.text
    # Its media is the capture's, from the cache only.
    assert client.get(f"{BASE}/7/media").content == tone
    assert client.get(f"{BASE}/7/media?version={capture}").content == tone
    assert len(conversation.transport.downloads) == 1
    assert client.get(f"{BASE}/8/media?version={capture}").status_code == 400
    conversation.media.cache.clear()
    assert (
        client.get(
            f"{BASE}/7/media", headers={"If-None-Match": first.headers["etag"]}
        ).status_code
        == 400
    )
    shown = [
        m
        for m in client.get("/api/accounts/1/chats/user:101").json()["messages"]
        if m["message_id"] == 7
    ]
    assert not shown[0]["media_available"]
    assert len(conversation.transport.downloads) == 1
    client.post(
        "/api/accounts/1/logout", json={}, headers={"Origin": "http://127.0.0.1:8765"}
    )
    assert client.get(f"{BASE}/7/versions").status_code == 400


def test_upgrade_cache_key_keeps_existing_bytes_once(served):
    from enloq.application.media_cache import MediaCache

    app, client, conversation, tone = served
    client.get(f"{BASE}/7/media")
    cache = conversation.media.cache
    message = conversation.store.message("1/user:101", 7)
    current, legacy = cache.entry(message), message.key + "#file"
    cache.path(current).rename(cache.path(legacy))
    with conversation.store.transaction() as db:
        db.execute("UPDATE media_files SET key=? WHERE key=?", (legacy, current))
    reopened = MediaCache(conversation.store, conversation.transport)
    assert reopened.get(message)["path"].read_bytes() == tone
    assert reopened.usage()["files"] == 1
    assert not reopened.path(legacy).exists()


def test_timestamp_only_edits_collapse_but_keep_native_edit_evidence(tmp_path):
    app = fixture(tmp_path)
    first = message(app, text="Same visible words")
    app.sync.ingest(first)
    initial = app.store.rows("SELECT revision FROM blocks")[0]["revision"]
    app.sync.ingest(first.model_copy(update={"revision": 2, "native_edited": True}))
    current = app.store.message("1/user:42", 1)
    assert current.native_edited and current.native_revision == 2
    assert current.revision == first.revision
    assert not app.store.history.rows("1/user:42", 1)
    assert app.store.rows("SELECT revision FROM blocks")[0]["revision"] == initial


@pytest.mark.parametrize("outgoing", [False, True])
def test_attachment_add_remove_replace_and_same_timestamp_guards(tmp_path, outgoing):
    from enloq.application.media import Media
    from enloq.domain.models import MediaInfo

    app = fixture(tmp_path)
    first = message(app, text="Unchanged caption", outgoing=outgoing)
    app.sync.ingest(first)
    versions = [first]
    for asset in ("photo:1", "photo:2", None):
        update = first.model_copy(
            update={
                "media": "image" if asset else None,
                "media_info": MediaInfo(size=123) if asset else None,
                "attachment_id": asset,
            }
        )
        app.sync.ingest(update)
        versions.append(app.store.message("1/user:42", 1))
    assert len({m.revision for m in versions}) == 4
    assert len({Media.etag(m) for m in versions}) == 4
    assert [
        json.loads(r["body"])["attachment_id"]
        for r in app.store.history.rows("1/user:42", 1)
    ] == ["photo:2", "photo:1", None]
    assert versions[-1].native_edited
    # Duplicate delivery uses the native timestamp, not the bumped local clock.
    app.sync.ingest(first)
    assert len(app.store.history.rows("1/user:42", 1)) == 3


async def test_same_timestamp_context_change_rejects_late_output(tmp_path):
    app = fixture(tmp_path)
    source = message(app, text="Before")
    app.sync.ingest(source)
    packet = await context.incoming(app, app.store.rows("SELECT * FROM blocks")[0])
    app.sync.ingest(source.model_copy(update={"text": "After"}))
    assert not context.current(app.store, packet)
    assert (
        app.store.rows("SELECT revision FROM blocks")[0]["revision"]
        != packet["fingerprint"]
    )


def test_legacy_media_equality_unknown_links_and_version_pagination(tmp_path):
    from enloq.domain.models import TextLink

    app = fixture(tmp_path)
    first = message(app, text="Caption", media="image")
    app.sync.ingest(first, target=False)
    app.sync.ingest(first.model_copy(update={"revision": 2}), target=False)
    assert len(app.store.history.rows("1/user:42", 1)) == 1
    for number in range(3, 58):
        app.sync.ingest(
            first.model_copy(
                update={
                    "revision": number,
                    "links": [
                        TextLink(
                            offset=0, length=7, url=f"https://example.test/{number}"
                        )
                    ],
                }
            ),
            target=False,
        )
    page = app.store.history.rows("1/user:42", 1)
    tail = app.store.history.rows("1/user:42", 1, before=page[-1]["id"])
    assert len(page) == 50 and len(tail) == 6
    assert len({r["id"] for r in page + tail}) == 56


def test_native_attachment_identity_excludes_capabilities():
    from enloq.transport.telethon import attachment_identity

    from backend.tests.test_media_kinds import document, native

    first = native(document("application/pdf"))
    second = native(document("application/pdf"))
    second.media.document.id = 2
    second.media.document.access_hash = 12345
    second.media.document.file_reference = b"private"
    assert attachment_identity(first) == "document:1"
    assert attachment_identity(second) == "document:2"


def test_upgrade_compacts_proven_duplicates_preserves_unknown_images(tmp_path):
    from enloq.storage.history import compact

    app = fixture(tmp_path)
    for identity, media in [(1, None), (2, "image")]:
        first = message(app, identity, text="Same", media=media)
        app.sync.ingest(first, target=False)
        with app.store.transaction() as db:
            for revision in (1, 2):
                db.execute(
                    "INSERT INTO message_versions(source,date,body,observed,reason) VALUES (?,?,?,?,'edited')",
                    (
                        first.key,
                        first.date,
                        first.model_copy(
                            update={"revision": revision}
                        ).model_dump_json(),
                        app.store.clock(),
                    ),
                )
    with app.store.transaction() as db:
        compact(db)
    assert not app.store.history.rows("1/user:42", 1)
    assert len(app.store.history.rows("1/user:42", 2)) == 2
    assert app.store.message("1/user:42", 1).native_edited


def test_same_timestamp_media_retains_separate_cached_versions(served):
    app, client, conversation, tone = served
    assert client.get(f"{BASE}/7/media").content == tone
    source = conversation.store.message("1/user:101", 7)
    conversation.sync.ingest(
        source.model_copy(update={"attachment_id": "document:2"}), target=False
    )
    old = client.get(f"{BASE}/7/versions").json()[0]
    assert old["media_available"]
    assert client.get(f"{BASE}/7/media?version={old['id']}").content == tone
    assert conversation.store.message("1/user:101", 7).revision != source.revision


async def sent(app, kind):
    """One send through the service: the receipt's provisional record and the
    copy Telegram later supplies, with its own date and attachment identity."""
    from enloq.domain.models import MediaInfo

    from backend.tests.test_rebuild import attach, edit
    from backend.tests.test_selected_media import stage

    if kind == "text":
        draft = edit(app, "Sent words")
    elif kind == "image":
        draft = attach(app)
    else:
        draft = await stage(app, kind)
    operation = await app.delivery.send("1/user:42", draft["revision"])
    identity = operation["message_id"] or next(iter(operation["receipts"].values()))
    receipt = app.store.message("1/user:42", identity)
    assert receipt.revision == 0 and receipt.date == operation["created"]
    date = int(operation["created"]) + 3
    media = operation.get("media") or {}
    copy = receipt.model_copy(
        update={
            "revision": date * 1_000_000,
            "date": date,
            "attachment_id": None if kind == "text" else f"document:{identity}",
            "media_info": MediaInfo(
                mime=media["mime"],
                size=media["size"],
                width=media["width"],
                height=media["height"],
                # Telegram's rounding of the length the service measured.
                duration=round(media["duration"]) if media["duration"] else None,
                sticker=media["sticker"],
                file_name="animation.mp4",
            )
            if media
            else receipt.media_info,
        }
    )
    return operation, receipt, copy


@pytest.mark.parametrize("kind", ["text", "image", "gif", "sticker"])
async def test_telegrams_copy_of_a_sent_message_is_not_an_edit(tmp_path, kind):
    app = fixture(tmp_path)
    operation, receipt, copy = await sent(app, kind)
    app.sync.ingest(copy, target=False)
    current = app.store.message("1/user:42", copy.message_id)
    assert not current.native_edited
    assert not app.store.history.rows("1/user:42", copy.message_id)
    # Telegram's date, not the moment Send was pressed (ADR 19), in the record and in the ordering column.
    assert current.date == copy.date
    assert app.store.rows("SELECT date FROM messages WHERE key=?", (copy.key,)) == [
        {"date": copy.date}
    ]
    if kind == "text":
        assert current.revision == 0 and current.native_revision == copy.revision
    else:
        assert current.attachment_id == copy.attachment_id
    if kind in ("gif", "sticker"):
        # The sent bytes still serve the preview of Telegram's copy.
        assert app.selected_media.message_file(current).id == operation["media"]["id"]
    # A repeated read changes nothing; a later edit is an observed version of
    # Telegram's copy, not of the receipt's record.
    app.sync.ingest(copy, target=False)
    assert app.store.message("1/user:42", copy.message_id) == current
    app.sync.ingest(
        copy.model_copy(
            update={
                "revision": copy.revision + 60_000_000,
                "text": "Corrected words",
                "native_edited": True,
            }
        ),
        target=False,
    )
    edited = app.store.message("1/user:42", copy.message_id)
    assert edited.native_edited and edited.date == copy.date
    rows = app.store.history.rows("1/user:42", copy.message_id)
    assert [json.loads(r["body"])["attachment_id"] for r in rows] == [
        copy.attachment_id
    ]


async def test_receipt_record_never_overrides_telegrams_copy_or_its_edit(tmp_path):
    app = fixture(tmp_path)
    operation, receipt, copy = await sent(app, "text")
    # Telegram's copy was already edited when the service first read it.
    app.sync.ingest(copy.model_copy(update={"native_edited": True}), target=False)
    assert app.store.message("1/user:42", copy.message_id).native_edited
    # The receipt's record arriving again never replaces the native copy.
    app.store.apply_message(receipt)
    current = app.store.message("1/user:42", copy.message_id)
    assert current.date == copy.date and current.native_revision == copy.revision
    assert not app.store.history.rows("1/user:42", copy.message_id)


async def test_hidden_native_edit_dates_are_not_edits(tmp_path):
    from datetime import UTC, datetime

    from enloq.transport.telethon import PersonalTransport
    from telethon import types

    app = fixture(tmp_path)
    transport = object.__new__(PersonalTransport)
    transport.account_id, transport.store = 1, app.store
    date = datetime.fromtimestamp(app.store.clock(), UTC)
    native = types.Message(
        id=1, peer_id=types.PeerUser(42), date=date, message="Same visible words"
    )
    app.sync.ingest(await transport.normalize(native))
    native.edit_date = datetime.fromtimestamp(app.store.clock() + 5, UTC)
    native.edit_hide = True
    hidden = await transport.normalize(native)
    assert hidden.edit_hidden and not hidden.native_edited
    assert "edit_hidden" not in hidden.model_dump()
    app.sync.ingest(hidden)
    current = app.store.message("1/user:42", 1)
    assert not current.native_edited and current.native_revision == hidden.revision
    # A hidden change of content is still an observed version, without the
    # edited mark.
    native.edit_date = datetime.fromtimestamp(app.store.clock() + 9, UTC)
    native.message = "Changed under a hidden edit date"
    app.sync.ingest(await transport.normalize(native))
    assert not app.store.message("1/user:42", 1).native_edited
    assert len(app.store.history.rows("1/user:42", 1)) == 1
    native.edit_date = datetime.fromtimestamp(app.store.clock() + 20, UTC)
    native.edit_hide = None
    shown = await transport.normalize(native)
    assert shown.native_edited and not shown.edit_hidden
    app.sync.ingest(shown)
    assert app.store.message("1/user:42", 1).native_edited
