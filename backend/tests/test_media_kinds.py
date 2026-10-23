"""E1: typed media kinds, safe placeholders and non-AI media tolerance."""

import json
from datetime import UTC, datetime

from enloq.application.blocks import requeue_unsupported
from enloq.domain.models import MediaInfo
from enloq.transport.telethon import classify
from telethon.tl import types

from backend.tests.test_rebuild import fixture, message


def native(media, text=""):
    return types.Message(
        id=1,
        peer_id=types.PeerUser(42),
        date=datetime.now(UTC),
        message=text,
        media=media,
    )


def document(mime, *attributes, size=1234):
    return types.MessageMediaDocument(
        document=types.Document(
            id=1,
            access_hash=1,
            file_reference=b"",
            date=datetime.now(UTC),
            mime_type=mime,
            size=size,
            dc_id=1,
            attributes=list(attributes),
        )
    )


def sticker_attribute():
    return types.DocumentAttributeSticker(
        alt="🙂", stickerset=types.InputStickerSetEmpty()
    )


def video(round_message=False):
    return types.DocumentAttributeVideo(
        duration=12.5, w=240, h=240, round_message=round_message
    )


def test_every_native_kind_normalizes_with_safe_metadata():
    point = types.GeoPoint(long=105.8, lat=21.0, access_hash=1)
    webpage = types.WebPage(
        id=1, url="https://e.test", display_url="e.test", hash=0, title="Example"
    )
    cases = {
        "static sticker": (document("image/webp", sticker_attribute()), "sticker"),
        "animated sticker": (
            document("application/x-tgsticker", sticker_attribute()),
            "sticker",
        ),
        "video sticker": (
            document("video/webm", sticker_attribute(), video()),
            "sticker",
        ),
        "gif": (
            document("video/mp4", types.DocumentAttributeAnimated(), video()),
            "gif",
        ),
        "circle": (document("video/mp4", video(round_message=True)), "video_note"),
        "voice": (
            document("audio/ogg", types.DocumentAttributeAudio(duration=3, voice=True)),
            "audio",
        ),
        "audio file": (
            document(
                "audio/mpeg",
                types.DocumentAttributeAudio(duration=200, title="Track"),
                types.DocumentAttributeFilename("track.mp3"),
            ),
            "audio",
        ),
        "video": (
            document("video/mp4", video(), types.DocumentAttributeFilename("clip.mp4")),
            "video",
        ),
        "document": (
            document("application/pdf", types.DocumentAttributeFilename("report.pdf")),
            "document",
        ),
        "poll": (
            types.MessageMediaPoll(
                poll=types.Poll(
                    id=1,
                    question=types.TextWithEntities("Which day?", []),
                    answers=[
                        types.PollAnswer(types.TextWithEntities("Monday", []), b"1"),
                        types.PollAnswer(types.TextWithEntities("Friday", []), b"2"),
                    ],
                    hash=0,
                ),
                results=types.PollResults(),
            ),
            "poll",
        ),
        "location": (types.MessageMediaGeo(geo=point), "location"),
        "live location": (types.MessageMediaGeoLive(geo=point, period=60), "location"),
        "venue": (
            types.MessageMediaVenue(
                geo=point,
                title="Authored cafe",
                address="1 Authored Street",
                provider="",
                venue_id="",
                venue_type="",
            ),
            "location",
        ),
        "contact": (
            types.MessageMediaContact(
                phone_number="+15550100",
                first_name="Authored",
                last_name="Contact",
                vcard="BEGIN:VCARD\nTEL:+15550100\nEND:VCARD",
                user_id=0,
            ),
            "contact",
        ),
        "dice": (types.MessageMediaDice(value=3, emoticon="🎲"), "other"),
        "link": (types.MessageMediaWebPage(webpage=webpage), "link"),
        "unknown": (types.MessageMediaUnsupported(), "other"),
    }
    seen = {}
    for name, (media, kind) in cases.items():
        found, info = classify(native(media))
        assert found == kind, name
        seen[name] = info
    assert seen["static sticker"].sticker == "static"
    assert seen["animated sticker"].sticker == "animated"
    assert seen["video sticker"].sticker == "video"
    assert seen["voice"].voice and not seen["audio file"].voice
    assert seen["audio file"].file_name == "track.mp3"
    assert seen["circle"].duration == 12.5
    assert seen["poll"].question == "Which day?"
    assert seen["poll"].options == ["Monday", "Friday"]
    assert seen["live location"].live and seen["venue"].title == "Authored cafe"
    assert seen["contact"].title == "Authored Contact"
    stored = json.dumps([i.model_dump() for i in seen.values() if i])
    for private in ("15550100", "VCARD", "105.8", "21.0", "Authored Street"):
        assert private not in stored
    assert classify(native(None)) == (None, None)
    assert classify(native(types.MessageMediaEmpty())) == (None, None)


async def test_link_preview_rewrites_as_text(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(
        message(
            a,
            1,
            text="See https://e.test first.",
            media="link",
            media_info=MediaInfo(title="Example"),
        )
    )
    now[0] += 30
    await a.process_due()
    packet = a.engine.calls[0][1]
    assert packet["messages"][0]["media"] is None
    assert "placeholder" not in packet["messages"][0]
    block = a.conversation("1/user:42")["blocks"][0]
    assert block["state"] == "ready" and block["unanalysed"] == []


async def test_mixed_block_marks_what_was_not_analysed(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a, 1, text="Here is the contract draft."))
    a.sync.ingest(
        message(
            a,
            2,
            text="",
            media="document",
            media_info=MediaInfo(file_name="draft.pdf", size=2048),
        )
    )
    a.sync.ingest(
        message(
            a,
            3,
            text="",
            media="sticker",
            media_info=MediaInfo(sticker="animated", emoji="🙂"),
        )
    )
    now[0] += 30
    await a.process_due()
    assert len(a.engine.calls) == 1
    placeholders = [m.get("placeholder") for m in a.engine.calls[0][1]["messages"]]
    assert placeholders[0] is None
    assert placeholders[1] == {
        "kind": "document",
        "not_analysed": True,
        "file_name": "draft.pdf",
        "size": 2048,
    }
    assert placeholders[2]["kind"] == "sticker" and placeholders[2]["emoji"] == "🙂"
    assert not a.transport.downloads  # File contents never become model input.
    block = a.conversation("1/user:42")["blocks"][0]
    assert block["state"] == "ready"
    assert [(i["id"], i["kind"]) for i in block["unanalysed"]] == [
        (2, "document"),
        (3, "sticker"),
    ]


async def test_sticker_only_block_makes_no_call_and_is_not_queued(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(
        message(
            a,
            1,
            text="",
            media="sticker",
            media_info=MediaInfo(sticker="static", emoji="👍"),
        )
    )
    block = a.conversation("1/user:42")["blocks"][0]
    assert block["state"] == "nothing"
    assert not a.store.rows("SELECT * FROM jobs")
    now[0] += 30
    await a.process_due()
    assert not a.engine.calls
    import pytest
    from enloq.domain.models import Refused

    with pytest.raises(Refused, match="Nothing"):
        a.rewriting.retry(block["id"])
    # A source change re-evaluates the block.
    a.sync.ingest(message(a, 2, text="Thanks, see you then."))
    block = a.conversation("1/user:42")["blocks"][0]
    assert block["sources"] == [1, 2] and block["state"] == "queued"


async def test_unsupported_and_unpreparable_context_never_fails_a_block(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a, 1, text="", media="unsupported"), target=False)
    a.sync.ingest(message(a, 2, text="", media="image"), target=False)  # no bytes
    a.sync.ingest(message(a, 3, outgoing=True, text="Noted."), target=False)
    a.sync.ingest(message(a, 4, text="Please confirm the time."))
    now[0] += 30
    await a.process_due()
    assert len(a.engine.calls) == 1
    context = {m["id"]: m for m in a.engine.calls[0][1]["context"]}
    assert context[1]["placeholder"]["kind"] == "other"
    assert context[2]["placeholder"]["reason"] == "unavailable"
    block = a.conversation("1/user:42")["blocks"][-1]
    assert block["state"] == "ready" and block["unanalysed"] == []


async def test_unavailable_target_image_keeps_retry_but_oversized_is_placeholder(
    tmp_path,
):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a, 1, text="Look at this.", media="image"))
    now[0] += 30
    await a.process_due()
    block = a.conversation("1/user:42")["blocks"][0]
    assert block["state"] == "failed" and not a.engine.calls
    a.transport.media["user:42", 1] = b"x" * (6 * 1024 * 1024)
    a.rewriting.retry(block["id"])
    await a.process_due()
    block = a.conversation("1/user:42")["blocks"][0]
    assert block["state"] == "ready"
    assert block["unanalysed"][0]["reason"].startswith("exceeds")


async def test_metadata_refresh_keeps_block_revision_and_derivatives(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    legacy = message(a, 1, text="See attached.", media="unsupported")
    a.sync.ingest(legacy)
    block = a.conversation("1/user:42")["blocks"][0]
    with a.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES (?,?,?)",
            ("1/user:42/1", 1, json.dumps({"image_context": "kept"})),
        )
    a.transport.messages = [
        legacy.model_copy(
            update={
                "media": "document",
                "media_info": MediaInfo(file_name="plan.pdf", size=10),
            }
        )
    ]
    now[0] += 30
    await a.process_due()  # Processing refreshes the legacy row by exact ID.
    assert a.transport.exact_reads == [("user:42", (1,))]
    placeholder = a.engine.calls[0][1]["messages"][0]["placeholder"]
    assert placeholder["kind"] == "document" and placeholder["file_name"] == "plan.pdf"
    assert a.store.message("1/user:42", 1).media_info.file_name == "plan.pdf"
    after = a.conversation("1/user:42")["blocks"][0]
    assert after["revision"] == block["revision"] and after["state"] == "ready"
    assert a.store.rows("SELECT * FROM derivatives")
    assert await a.refresh_legacy("1/user:42") == 0  # Nothing legacy remains.
    a.sync.ingest(message(a, 2, text="And one more thing."))
    assert a.conversation("1/user:42")["blocks"][0]["revision"] != block["revision"]


async def test_refresh_reveals_nothing_to_rewrite_without_queueing(tmp_path):
    a = fixture(tmp_path)
    legacy = message(a, 1, text="", media="unsupported")
    a.sync.ingest(legacy, target=False)
    assert a.conversation("1/user:42")["blocks"][0]["state"] == "nothing"
    with a.store.transaction() as db:
        db.execute("UPDATE blocks SET state='original'")
    a.transport.messages = [
        legacy.model_copy(
            update={"media": "sticker", "media_info": MediaInfo(sticker="static")}
        )
    ]
    await a.refresh_legacy("1/user:42")
    assert a.conversation("1/user:42")["blocks"][0]["state"] == "nothing"
    assert not a.store.rows("SELECT * FROM jobs")


async def test_only_predispatch_unsupported_failures_requeue_once(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a, 1, text="Pre-dispatch failure"))
    a.sync.ingest(message(a, 2, outgoing=True))
    a.sync.ingest(message(a, 3, text="Dispatched failure"))
    blocks = a.conversation("1/user:42")["blocks"]
    with a.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='failed',error='This media needs supported local preparation'"
        )
        db.execute("UPDATE jobs SET state='failed'")
        dispatched = db.execute(
            "SELECT id FROM jobs WHERE scope=?", (blocks[1]["id"],)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO reservations (id,bucket,engine,reserved,actual,status,created) VALUES (?,?,?,?,NULL,'unknown',?)",
            (dispatched, "day:x", "muse", 1, now[0]),
        )
    requeue_unsupported(a.store, a.rewriting.eligible)
    states = [b["state"] for b in a.conversation("1/user:42")["blocks"]]
    assert states == ["queued", "failed"]
    await a.process_due()
    assert a.conversation("1/user:42")["blocks"][0]["state"] == "ready"
    with a.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='failed',error='This media needs supported local preparation'"
        )
    requeue_unsupported(a.store, a.rewriting.eligible)
    assert [b["state"] for b in a.conversation("1/user:42")["blocks"]] == [
        "failed",
        "failed",
    ]
    now[0] += 8 * 86400
    with a.store.transaction() as db:
        db.execute("DELETE FROM jobs")
    requeue_unsupported(a.store, a.rewriting.eligible)
    assert not a.store.rows("SELECT * FROM jobs")  # Outside seven days.


async def test_downloads_are_bounded_typed_and_never_inline_documents(tmp_path):
    from io import BytesIO

    from enloq.api.server import create_app
    from enloq.config import Settings
    from enloq.domain.models import Message
    from fastapi.testclient import TestClient
    from PIL import Image

    from backend.tests.auth_helpers import trust_browser

    app = create_app(Settings(data=tmp_path / "app"))
    sticker_bytes = BytesIO()
    Image.new("RGBA", (512, 128), (30, 100, 170, 255)).save(
        sticker_bytes, format="WEBP"
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        conversation = app.state.accounts.get(1)
        items = {
            5: ("document", MediaInfo(file_name="Отчёт <1>.html", size=12), b"<html>"),
            6: (
                "sticker",
                MediaInfo(sticker="static", emoji="🙂"),
                sticker_bytes.getvalue(),
            ),
            7: ("audio", MediaInfo(voice=True), b"OggS" + bytes(40)),
            8: ("document", MediaInfo(file_name="big.bin"), b"x" * (51 * 1024 * 1024)),
            9: ("poll", MediaInfo(question="Which?"), None),
        }
        for identity, (kind, info, data) in items.items():
            if data is not None:
                conversation.transport.media["user:101", identity] = data
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:101",
                    message_id=identity,
                    revision=1,
                    date=conversation.store.clock(),
                    media=kind,
                    media_info=info,
                ),
                target=False,
            )
        base = "/api/accounts/1/chats/user:101/originals"
        document = client.get(f"{base}/5/media")
        assert document.status_code == 200
        assert document.headers["content-type"] == "application/octet-stream"
        assert document.headers["content-disposition"].startswith("attachment")
        assert document.content == b"<html>"
        sticker = client.get(f"{base}/6/media")
        assert sticker.headers["content-type"] == "image/webp"
        voice = client.get(f"{base}/7/media")
        assert voice.headers["content-type"] == "audio/ogg"
        assert client.get(f"{base}/8/media").status_code == 400
        assert client.get(f"{base}/9/media").status_code == 400
        assert not list(conversation.media.workspace.path.glob("*.bin"))


async def test_circle_audio_is_transcribed_locally_and_frames_never_leave(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    calls = []

    class Speech:
        fail = True

        async def transcribe(self, path):
            calls.append(path.read_bytes())
            if self.fail:
                raise TimeoutError()
            return {"text": "Meet at noon, not before.", "language": "en"}

    speech = Speech()
    a.media.transcriber = speech
    a.transport.media["user:42", 1] = b"\x00\x00\x00\x18ftypmp42" + bytes(64)
    circle = message(
        a, 1, text="", media="video_note", media_info=MediaInfo(duration=12)
    )
    a.sync.ingest(circle)
    now[0] += 30
    await a.process_due()
    block = a.conversation("1/user:42")["blocks"][0]
    # The speech is the substance: a failed transcript keeps Retry.
    assert block["state"] == "failed" and not a.engine.calls
    speech.fail = False
    a.rewriting.retry(block["id"])
    await a.process_due()
    packet = a.engine.calls[0][1]
    assert packet["messages"][0]["transcript"]["text"] == "Meet at noon, not before."
    assert packet["images"] == [] and "placeholder" not in packet["messages"][0]
    assert not any("data:" in str(v) for v in packet["messages"][0].values())
    assert a.conversation("1/user:42")["blocks"][0]["state"] == "ready"
    assert len(a.transport.downloads) == 1  # Prefetched once, then shared.
    restarted = fixture(tmp_path, clock=lambda: now[0])
    restarted.media.transcriber = speech
    cached = await restarted.media.transcript(restarted.store.message("1/user:42", 1))
    assert cached["text"] == "Meet at noon, not before." and len(calls) == 2
    edited = circle.model_copy(update={"revision": 2})
    a.sync.ingest(edited)
    assert not a.media.cached(a.store.message("1/user:42", 1))


def test_circle_poster_and_transcript_routes(tmp_path):
    from io import BytesIO

    from enloq.api.server import create_app
    from enloq.config import Settings
    from enloq.domain.models import Message
    from fastapi.testclient import TestClient
    from PIL import Image

    from backend.tests.auth_helpers import trust_browser

    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        conversation = app.state.accounts.get(1)
        poster = BytesIO()
        Image.new("RGB", (90, 90), (1, 2, 3)).save(poster, format="JPEG")
        conversation.transport.media["user:101", 9, "poster"] = poster.getvalue()
        conversation.transport.media["user:101", 9] = b"\x00\x00\x00\x18ftypmp42"
        conversation.sync.ingest(
            Message(
                account_id=1,
                peer="user:101",
                message_id=9,
                revision=1,
                date=conversation.store.clock(),
                media="video_note",
                media_info=MediaInfo(duration=3),
            ),
            target=False,
        )
        base = "/api/accounts/1/chats/user:101/originals/9"
        frame = client.get(f"{base}/media?variant=poster")
        assert frame.headers["content-type"] == "image/jpeg"
        video = client.get(f"{base}/media")
        assert video.headers["content-type"] == "video/mp4"
        assert frame.headers["etag"] != video.headers["etag"]
        assert client.get(f"{base}/transcript").json() is None
