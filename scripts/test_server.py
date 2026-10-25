"""Disposable authored browser fixture. Never resolves populated configuration."""

import asyncio
import json
import os
import re
import shutil
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path

import uvicorn
from fastapi import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from enloq.api.server import create_app
from enloq.config import (
    ROOT,
    Settings,
    private_directory,
    private_read,
    private_write,
)
from enloq.logs import configure_logging

from backend.tests.login_fixture import SyntheticLoginClient
from scripts.album_fixture import register as album_fixture
from scripts.calming_fixture import register as calming_fixture
from scripts.deletion_fixture import register as deletion_fixture
from scripts.details_fixture import register as details_fixture
from scripts.emotion_fixture import register as emotion_fixture
from scripts.gif_sticker_fixture import register as gif_sticker_fixture
from scripts.group_budget_fixture import register as group_budget_fixture
from scripts.group_chat_fixture import register as group_chat_fixture
from scripts.group_fixture import register as group_fixture
from scripts.group_recovery_fixture import register as group_recovery_fixture
from scripts.members_fixture import register as members_fixture
from scripts.pairing_fixture import register as pairing_fixture
from scripts.people_fixture import register as people_fixture
from scripts.reminder_fixture import register as reminder_fixture
from scripts.saved_fixture import register as saved_fixture
from scripts.suggestion_fixture import register as suggestion_fixture
from scripts.threshold_fixture import register as threshold_fixture
from scripts.unseen_fixture import register as unseen_fixture
from scripts.video_fixture import register as video_fixture

# The app's journeys and the desktop specs use 8776. The web's phone-size
# journeys (ADR 44 Q8) run their own fixture on another port, so the two can
# run at once; each port records its own data folder and serves its own build.
PORT = int(os.environ.get("FIXTURE_PORT", "8776"))
LOCATION = ROOT / (
    ".local/browser-test-location"
    if PORT == 8776
    else f".local/browser-test-location-{PORT}"
)
WEB = ROOT / os.environ.get("FIXTURE_WEB", ".local/web-test-dist")


def remove_abandoned_fixture():
    """A server killed outright skips cleanup. Remove only its recorded
    directory, and only once no fixture server still listens."""
    if not LOCATION.exists():
        return
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            return
    previous = Path(private_read(LOCATION))
    if (
        previous.parent == ROOT / ".local"
        and re.fullmatch(r"browser-[a-z0-9_]{8}", previous.name)
        and not previous.is_symlink()
        and previous.is_dir()
    ):
        shutil.rmtree(previous)


os.umask(0o077)
configure_logging()
private_directory(ROOT / ".local")
remove_abandoned_fixture()
# Playwright stops the server with SIGTERM, which uvicorn re-raises after its
# shutdown. As KeyboardInterrupt, uvicorn.run returns and the with block exits.
signal.signal(signal.SIGTERM, signal.default_int_handler)
with tempfile.TemporaryDirectory(prefix="browser-", dir=ROOT / ".local") as directory:
    path = Path(directory)
    private_write(LOCATION, directory)
    app = create_app(
        # The project is only a name here: phone pushes go to a fake transport.
        # Account 1 is the synthetic Owner Account, whose Saved Messages
        # posts its fake transport records (ADR 43 Q8).
        Settings(data=path, port=PORT, fcm_project="enloq-fixture", owner_account=1),
        login_factory=SyntheticLoginClient,
        web=WEB,
    )
    # Existing authored browser journeys exercise rewriting on short sources.
    # Threshold journeys explicitly adopt 200 and restore this fixture baseline.
    # The production/offline application default remains 200.
    app.state.accounts.registry.set_processing_threshold(0)
    # The journeys exercise the rewrite path with text, which the shipped app
    # keeps for voice notes, images and calming (ADR 54); the combined-text
    # journey turns it off for itself.
    app.state.accounts.registry.set_text_rewriting(True)
    # Fixture messages keep one native date across repeated setup calls.
    STARTED = time.time()
    stream_fixture = {}
    emotion_fixture(app)
    deletion_fixture(app)
    details_fixture(app)
    group_fixture(app)
    group_chat_fixture(app)
    group_budget_fixture(app)
    group_recovery_fixture(app)
    unseen_fixture(app)
    suggestion_fixture(app)
    reminder_fixture(app)
    threshold_fixture(app)
    video_fixture(app)
    gif_sticker_fixture(app)
    pairing_fixture(app)
    members_fixture(app)
    saved_fixture(app)
    album_fixture(app)
    people_fixture(app)
    calming_fixture(app)

    @app.post("/api/test/notification-target")
    async def notification_target(request: Request):
        import uuid

        body = await request.json()
        delivery = app.state.notifications
        event = {
            "id": uuid.uuid4().hex,
            "account_id": body["account_id"],
            "peer": body["peer"],
            "expires": time.time() + 300,
        }
        delivery.claim(event, request.state.local_session, "push")
        return {"id": event["id"]}

    @app.get("/api/test/notification-state")
    def notification_state():
        sent = app.state.notifications.transport.sent
        return {"sent": len(sent), "silent": sent[-1][1]["silent"] if sent else None}

    entry = app.state.accounts.registry.register(
        2, "synthetic2.session", name="Second demo account"
    )
    app.state.accounts._create(entry)

    @app.post("/api/test/format-failures")
    async def format_failures(request: Request):
        """Authored consecutive format failures that pause a role's automatic
        dispatch, as three real ones would."""
        body = await request.json()
        manager = app.state.accounts
        # A running success would restart the count: wait for none, after
        # which a paused role dispatches nothing automatically.
        for _ in range(100):
            if not any(
                runtime.assistant.store.rows(
                    "SELECT 1 FROM jobs WHERE role=? AND state IN ('claimed','running')",
                    (body["role"],),
                )
                for runtime in manager.runtimes.values()
            ):
                break
            await asyncio.sleep(0.1)
        for _ in range(body["failures"]):
            manager.registry.record_format(body["role"], False)
        return {"paused": sorted(manager.registry.paused_roles())}

    @app.post("/api/test/scan")
    async def scan(request: Request):
        body = await request.json()
        flow = app.state.logins.flow
        assert flow and flow.client and flow.client.listening
        flow.client.user_id = body["account_id"]
        flow.client.password_required = body.get("password", False)
        flow.client.scan.set()
        return {"ok": True}

    @app.post("/api/test/versions")
    async def versions_fixture(request: Request):
        from enloq.domain.models import Chat, Message

        body = await request.json()
        conversation = app.state.accounts.get(1)
        if body.get("delete"):
            conversation.sync.deleted("user:120", [1])
        else:
            conversation.store.put_chat(
                Chat(
                    account_id=1,
                    peer="user:120",
                    title="Observed history",
                    kind="private",
                )
            )
            for identity, outgoing in [(1, False), (2, True)]:
                for revision, text in [
                    (1, "First observed wording"),
                    (3, "Latest native wording"),
                ]:
                    conversation.sync.ingest(
                        Message(
                            account_id=1,
                            peer="user:120",
                            message_id=identity,
                            revision=revision,
                            date=STARTED - 200,
                            text=text,
                            outgoing=outgoing,
                        ),
                        target=False,
                    )
        return {"ok": True}

    @app.post("/api/test/stream")
    async def stream_test(request: Request):
        from enloq.domain.models import Chat, Message
        from enloq.inference.base import EngineResponse

        body = await request.json()
        if body.get("action") == "finish":
            stream_fixture["finish"].set()
            return {"ok": True}
        if body.get("action") == "status":
            return {"calls": stream_fixture.get("calls", 0)}
        conversation = app.state.accounts.get(1)
        if body.get("action") == "start":
            with conversation.store.transaction() as db:
                db.execute("UPDATE jobs SET due=0 WHERE scope='1/user:121/1'")
            conversation.run(conversation.process_due())
            return {"ok": True}
        if body.get("action") == "restore":
            # The journeys share one server: a journey's streaming engine
            # leaves with it, so later rewrites use the offline engine again.
            original = stream_fixture.pop("original", None)
            if original is not None:
                conversation.engine = original
            return {"ok": True}
        previous = conversation.engine
        stream_fixture.setdefault("original", previous)
        finish, started = asyncio.Event(), asyncio.Event()
        stream_fixture.update(finish=finish, calls=0)

        class StreamingEngine:
            name, model, provider = "offline", "synthetic-stream", "local"

            async def generate(self, role, packet):
                return await previous.generate(role, packet)

            async def generate_stream(self, role, packet, on_text):
                if role == "incoming_rewrite" and not any(
                    m["text"].startswith("Streaming fixture")
                    for m in packet["messages"]
                ):
                    return await previous.generate(role, packet)
                stream_fixture["calls"] += 1
                if body.get("fast"):
                    for index in range(1, 9):
                        on_text("Fast rewrite " + "word " * index)
                        await asyncio.sleep(0.04)
                    content = {"text": "Fast rewrite completed"}
                    if role == "incoming_rewrite":
                        content["image_context"] = []
                    return EngineResponse(content, actual_nanos=0)
                if not body.get("empty"):
                    on_text("Partial rewrite 😀")
                started.set()
                try:
                    await finish.wait()
                except asyncio.CancelledError:
                    if not body.get("late"):
                        raise
                    await finish.wait()
                on_text("Late completed rewrite")
                content = {"text": "Complete validated rewrite"}
                if role == "incoming_rewrite":
                    content["image_context"] = []
                    if body.get("omit"):
                        content = {"parts": [], "image_context": []}
                return EngineResponse(
                    {"wrong": "format"} if body.get("fail") else content, actual_nanos=0
                )

        conversation.engine = StreamingEngine()
        if not body.get("outgoing"):
            conversation.store.put_chat(
                Chat(account_id=1, peer="user:121", title="Streaming", kind="private")
            )
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:121",
                    message_id=1,
                    revision=int(time.time() * 1e6),
                    date=STARTED,
                    text="Streaming fixture fast source"
                    if body.get("fast")
                    else "Streaming fixture empty-final original source"
                    if body.get("omit")
                    else "Streaming fixture original source",
                )
            )
            if body.get("fast"):
                with conversation.store.transaction() as db:
                    db.execute(
                        "UPDATE jobs SET due=? WHERE scope='1/user:121/1'",
                        (time.time() + 3600,),
                    )
            else:
                with conversation.store.transaction() as db:
                    db.execute("UPDATE jobs SET due=0 WHERE scope='1/user:121/1'")
                conversation.run(conversation.process_due())
                await asyncio.wait_for(started.wait(), 5)
        return {"ok": True}

    @app.post("/api/test/incoming")
    async def incoming(request: Request):
        from enloq.domain.models import Message

        body = await request.json()
        conversation = app.state.accounts.get(body.get("account", 1))
        for identity, text in [
            (1, "Please send the revised layout by Friday."),
            (2, "Please also confirm the illustration price."),
        ]:
            conversation.sync.ingest(
                Message(
                    account_id=conversation.store.account_id,
                    peer="user:101",
                    message_id=identity,
                    revision=10,
                    date=conversation.store.clock() - 60 + identity,
                    text=text,
                )
            )
        with conversation.store.transaction() as db:
            db.execute("UPDATE jobs SET due=0 WHERE state='queued'")
        await conversation.process_due()
        return {"ok": True}

    @app.post("/api/test/image")
    async def image_fixture():
        from io import BytesIO

        from enloq.domain.models import Message
        from PIL import Image

        conversation = app.state.accounts.get(1)
        output = BytesIO()
        Image.new("RGB", (180, 120), (60, 130, 90)).save(output, format="PNG")
        conversation.transport.media["user:101", 3] = output.getvalue()
        conversation.sync.ingest(
            Message(
                account_id=1,
                peer="user:101",
                message_id=3,
                revision=10,
                date=conversation.store.clock(),
                text="Please check the green sample.",
                media="image",
            )
        )
        with conversation.store.transaction() as db:
            db.execute("UPDATE jobs SET due=0 WHERE state='queued'")
        await conversation.process_due()
        return {"ok": True}

    @app.post("/api/test/voice")
    async def voice_fixture():
        import wave
        from io import BytesIO

        from enloq.domain.models import Message

        class Speech:
            async def transcribe(self, path):
                return {
                    "text": "I may agree only below twenty. Please do not confirm yet.",
                    "language": "en",
                }

        conversation = app.state.accounts.get(1)
        conversation.media.transcriber = Speech()
        output = BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(bytes(32000))
        conversation.transport.media["user:101", 4] = output.getvalue()
        conversation.sync.ingest(
            Message(
                account_id=1,
                peer="user:101",
                message_id=4,
                revision=10,
                date=conversation.store.clock(),
                media="audio",
            )
        )
        with conversation.store.transaction() as db:
            db.execute("UPDATE jobs SET due=0 WHERE state='queued'")
        await conversation.process_due()
        return {"ok": True}

    @app.post("/api/test/speech")
    async def speech_fixture_route(request: Request):
        """A voice original with a cached raw transcript; nothing transcribes."""
        from enloq.domain.models import Chat, Message

        conversation = app.state.accounts.get(1)
        conversation.store.put_chat(
            Chat(
                account_id=1,
                peer="user:113",
                title="Speech controls",
                kind="private",
                ai_eligible=True,
            )
        )
        native = Message(
            account_id=1,
            peer="user:113",
            message_id=1,
            revision=1,
            date=STARTED,
            media="audio",
        )
        conversation.transport.media["user:113", 1] = b"OggS" + bytes(100)
        conversation.sync.ingest(native, target=False)
        with conversation.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
                (
                    native.key,
                    native.revision,
                    json.dumps(
                        {
                            "transcript": {
                                "text": "Old uncertain transcript",
                                "language": "en",
                                "machine_generated": True,
                            }
                        }
                    ),
                ),
            )
        return {"ok": True}

    @app.post("/api/test/media-kinds")
    async def media_kinds():
        from enloq.domain.models import Chat, MediaInfo, Message

        conversation = app.state.accounts.get(1)
        for peer, title in (("user:104", "Media kinds"), ("user:105", "Mixed block")):
            conversation.store.put_chat(
                Chat(account_id=1, peer=peer, title=title, kind="private")
            )
        authored = await app.state.authored_gif_sticker_assets()
        items = [
            ("document", MediaInfo(file_name="report.pdf", size=48213), ""),
            (
                "poll",
                MediaInfo(question="Which day works?", options=["Mon", "Fri"]),
                "",
            ),
            ("location", MediaInfo(title="Authored cafe"), ""),
            ("contact", MediaInfo(title="Authored Contact"), ""),
            (
                "sticker",
                MediaInfo(
                    sticker="static",
                    emoji="🙂",
                    mime="image/webp",
                    size=authored.media["static"].facts.size,
                    width=512,
                    height=512,
                ),
                "",
            ),
            (
                "sticker",
                MediaInfo(
                    sticker="animated",
                    emoji="🎉",
                    mime="application/x-tgsticker",
                    size=authored.media["animated"].facts.size,
                    width=512,
                    height=512,
                ),
                "",
            ),
            ("video_note", MediaInfo(duration=14, size=520000), ""),
            ("gif", MediaInfo(duration=3, size=90000), ""),
            ("video", MediaInfo(file_name="clip.mp4", duration=65, size=3100000), ""),
            ("audio", MediaInfo(file_name="song.mp3", title="Song", size=4000000), ""),
            (
                "link",
                MediaInfo(site="Example", title="Authored page"),
                "https://e.test",
            ),
            ("other", None, ""),
            (
                "sticker",
                MediaInfo(
                    sticker="video",
                    emoji="😺",
                    mime="video/webm",
                    size=authored.media["video"].facts.size,
                    width=512,
                    height=512,
                ),
                "",
            ),
        ]
        conversation.transport.media["user:104", 15] = authored.media[
            "static"
        ].path.read_bytes()
        conversation.transport.media["user:104", 16] = authored.media[
            "animated"
        ].path.read_bytes()
        conversation.transport.media["user:104", 18] = authored.media[
            "gif_mp4"
        ].path.read_bytes()
        conversation.transport.media["user:104", 11] = b"%PDF-authored"
        # The video sticker (CU1): its VP9 bytes and a still for the phones.
        conversation.transport.media["user:104", 23] = authored.media[
            "video"
        ].path.read_bytes()
        conversation.transport.media["user:104", 23, "poster"] = authored.media[
            "static"
        ].path.read_bytes()
        for offset, (kind, info, text) in enumerate(items):
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:104",
                    message_id=11 + offset,
                    revision=1,
                    date=STARTED - 3600 + offset * 200,
                    text=text,
                    media=kind,
                    media_info=info,
                ),
                target=False,
            )
        for identity, kind, info, text in (
            (5, None, None, "Here is the signed contract."),
            (6, "document", MediaInfo(file_name="contract.pdf", size=2048), ""),
        ):
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:105",
                    message_id=identity,
                    revision=1,
                    date=conversation.store.clock(),
                    text=text,
                    media=kind,
                    media_info=info,
                )
            )
        with conversation.store.transaction() as db:
            db.execute("UPDATE jobs SET due=0 WHERE state='queued'")
        await conversation.process_due()
        return {"ok": True}

    @app.post("/api/test/playback")
    async def playback_fixture():
        from io import BytesIO

        from enloq.domain.models import Chat, MediaInfo, Message
        from PIL import Image

        conversation = app.state.accounts.get(1)
        conversation.store.put_chat(
            Chat(account_id=1, peer="user:106", title="Playback", kind="private")
        )
        fixtures = Path(__file__).resolve().parents[1] / "backend/tests/fixtures"
        for identity, kind, info, name in (
            (1, "audio", MediaInfo(voice=True, duration=2), "tone.ogg"),
            (
                2,
                "video_note",
                MediaInfo(duration=2, width=240, height=240),
                "circle.mp4",
            ),
        ):
            conversation.transport.media["user:106", identity] = (
                fixtures / name
            ).read_bytes()
            if kind == "video_note":  # Telegram stores a thumbnail with circles.
                poster = BytesIO()
                Image.new("RGB", (240, 240), (90, 90, 160)).save(poster, format="JPEG")
                conversation.transport.media["user:106", identity, "poster"] = (
                    poster.getvalue()
                )
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:106",
                    message_id=identity,
                    revision=1,
                    date=STARTED - 600 + identity,
                    media=kind,
                    media_info=info,
                ),
                target=False,
            )
        return {"ok": True}

    long_tone = {}

    async def long_voice():
        """A three-minute authored tone: the phone's journey checks a voice
        note while it plays, and Maestro over the tunnel takes a minute."""
        if "bytes" not in long_tone:
            from enloq.inference.runner import bounded_process
            from enloq.media_tools import FFMPEG

            output, _ = await bounded_process(
                [
                    FFMPEG,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=330:duration=180",
                    "-ac",
                    "1",
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "16k",
                    "-f",
                    "ogg",
                    "pipe:1",
                ],
                stdin=b"",
                cwd=tempfile.gettempdir(),
                env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "C"},
                timeout=30,
                stdout_limit=1024 * 1024,
            )
            long_tone["bytes"] = output
        return long_tone["bytes"]

    @app.post("/api/test/voice-player")
    async def voice_player_fixture(request: Request):
        """Its own chat: two adjacent voice notes (with and without Telegram's
        waveform), text, a third note, an audio file and a circle. With
        `long`, the first note lasts three minutes."""
        from io import BytesIO

        from enloq.domain.models import Chat, MediaInfo, Message
        from PIL import Image

        conversation = app.state.accounts.get(1)
        peer = "user:160"
        key = f"1/{peer}"
        # Every case owns a fresh authored chat. Other fixtures may have run
        # its old queued jobs, producing transcripts that this case did not ask
        # for. Reset only this fixture's local sources and generated state.
        with conversation.store.transaction() as db:
            db.execute("DELETE FROM jobs WHERE scope LIKE ?", (key + "%",))
            for table in ("blocks", "messages"):
                db.execute(f"DELETE FROM {table} WHERE chat_key=?", (key,))
            db.execute("DELETE FROM derivatives WHERE source LIKE ?", (key + "/%",))
            db.execute(
                "DELETE FROM message_versions WHERE source LIKE ?", (key + "/%",)
            )
        # A note's bytes change with `long`: cached copies of either go.
        for row in conversation.store.rows(
            "SELECT key FROM media_files WHERE key LIKE ?", (key + "/%",)
        ):
            conversation.media.cache.remove(row["key"])
        conversation.store.put_chat(
            Chat(account_id=1, peer=peer, title="Voice player", kind="private")
        )
        fixtures = Path(__file__).resolve().parents[1] / "backend/tests/fixtures"
        tone = (fixtures / "tone.ogg").read_bytes()
        long = bool((await request.body()) and (await request.json()).get("long"))
        first = await long_voice() if long else tone
        poster = BytesIO()
        Image.new("RGB", (240, 240), (60, 130, 90)).save(poster, format="JPEG")
        wave = [(index * 7) % 32 for index in range(100)]
        sources = [
            (
                1,
                "audio",
                MediaInfo(voice=True, duration=180 if long else 2, waveform=wave),
                "",
            ),
            (2, "audio", MediaInfo(voice=True, duration=2), ""),
            (3, None, None, "Authored text between the notes."),
            (4, "audio", MediaInfo(voice=True, duration=2, waveform=wave), ""),
            (
                5,
                "audio",
                MediaInfo(
                    file_name="tone.ogg",
                    title="Authored tone",
                    performer="Authored performer",
                    duration=2,
                    mime="audio/ogg",
                ),
                "",
            ),
            (6, "video_note", MediaInfo(duration=2, width=240, height=240), ""),
        ]
        for identity, kind, info, text in sources:
            if kind == "audio":
                conversation.transport.media[peer, identity] = (
                    first if identity == 1 else tone
                )
            if kind == "video_note":
                conversation.transport.media[peer, identity] = (
                    fixtures / "circle.mp4"
                ).read_bytes()
                conversation.transport.media[peer, identity, "poster"] = (
                    poster.getvalue()
                )
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer=peer,
                    message_id=identity,
                    revision=1,
                    date=STARTED - 900 + identity,
                    text=text,
                    media=kind,
                    media_info=info,
                    attachment_id=f"authored-voice-player-{identity}" if kind else None,
                ),
                target=False,
            )
        with conversation.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
                (
                    f"1/{peer}/1",
                    1,
                    json.dumps(
                        {
                            "transcript": {
                                "text": "Authored first note transcript.",
                                "language": "en",
                            }
                        }
                    ),
                ),
            )
        return {"ok": True}

    @app.post("/api/test/circles")
    async def circles_fixture():
        from io import BytesIO

        from enloq.domain.models import Chat, MediaInfo, Message
        from PIL import Image

        class Speech:
            async def transcribe(self, path):
                return {"text": "Authored circle transcript.", "language": "en"}

        conversation = app.state.accounts.get(1)
        conversation.media.transcriber = Speech()
        conversation.store.put_chat(
            Chat(account_id=1, peer="user:107", title="Circles", kind="private")
        )
        fixtures = Path(__file__).resolve().parents[1] / "backend/tests/fixtures"
        poster = BytesIO()
        Image.new("RGB", (240, 240), (80, 120, 200)).save(poster, format="JPEG")
        media = conversation.transport.media
        media["user:107", 1] = (fixtures / "circle.mp4").read_bytes()
        media["user:107", 1, "poster"] = poster.getvalue()
        media["user:107", 2] = (fixtures / "loop.mp4").read_bytes()
        for identity, kind, info in (
            (1, "video_note", MediaInfo(duration=2, width=240, height=240)),
            (2, "gif", MediaInfo(duration=1, mime="video/mp4")),
        ):
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:107",
                    message_id=identity,
                    revision=1,
                    date=STARTED - 200 * (3 - identity),
                    media=kind,
                    media_info=info,
                )
            )
        with conversation.store.transaction() as db:
            db.execute("UPDATE jobs SET due=0 WHERE state='queued'")
        await conversation.process_due()
        return {"ok": True}

    @app.post("/api/test/progress")
    async def progress_fixture(request: Request):
        from enloq.domain.models import Chat, Message

        body = await request.json()
        conversation = app.state.accounts.get(1)
        conversation.store.put_chat(
            Chat(account_id=1, peer="user:108", title="Progress", kind="private")
        )
        if body.get("append"):
            identity = 100 + int(body["append"])
            items = [(identity, body["text"], False)]
            target = False
        else:
            items = [
                (1, "First separate request.", False),
                (2, "Noted.", True),
                (3, "Second separate request.", False),
                (4, "Noted.", True),
                (5, "Third separate request.", False),
            ]
            target = True
        for identity, text, outgoing in items:
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:108",
                    message_id=identity,
                    revision=1,
                    date=STARTED + identity,
                    text=text,
                    outgoing=outgoing,
                ),
                target=target,
            )
        return {"ok": True}

    @app.post("/api/test/older")
    async def older_fixture():
        from enloq.domain.models import Chat, Message

        conversation = app.state.accounts.get(1)
        conversation.store.put_chat(
            Chat(account_id=1, peer="user:109", title="Earlier", kind="private")
        )
        for identity, age, text, outgoing in (
            (1, 12, "Could you resend the old invoice?", False),
            (2, 12, "Noted.", True),
            (3, 9, "Also the delivery address, please.", False),
            (4, 9, "Noted.", True),
            (5, 0, "Here is my latest message.", True),
        ):
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:109",
                    message_id=identity,
                    revision=1,
                    date=STARTED - age * 86400 + identity,
                    text=text,
                    outgoing=outgoing,
                ),
                target=False,
            )
        # A journey posts this seed again to make open browsers read the
        # conversation anew; an unchanged seed alone announces nothing.
        with conversation.store.transaction() as db:
            db.execute("UPDATE chats SET body=body WHERE key='1/user:109'")
        return {"ok": True}

    @app.get("/api/test/downloads")
    async def downloads():
        return {"count": len(app.state.accounts.get(1).transport.downloads)}

    @app.post("/api/test/delivery")
    async def delivery_fault(request: Request):
        from enloq.domain.models import Unavailable

        body = await request.json()
        transport = app.state.accounts.get(1).transport
        transport.lose_receipt = body.get("unknown", False)
        # Seconds a synthetic flood hold keeps sends from being handed off.
        transport.held = body.get("held", 0)
        transport.check_failure = (
            Unavailable("Synthetic check failure") if body.get("uncheckable") else None
        )
        return {"ok": True}

    @app.post("/api/test/menu")
    async def menu_fixture(request: Request):
        """Its own chat: a processed two-source group, then an owner message.

        Processing skips the quiet seconds but readiness does not: repeated
        setup leaves unchanged sources' arrival alone, so readiness latches 30
        seconds after the chat's first setup, possibly during a later journey.
        ``settled`` passes the quiet seconds and latches readiness now."""
        from enloq.domain.models import Chat, Message

        conversation = app.state.accounts.get(1)
        conversation.store.put_chat(
            Chat(
                account_id=1,
                peer="user:150",
                title="Menu fixture",
                kind="private",
                ai_eligible=True,
            )
        )
        for identity, text, outgoing in [
            (1, "Please send the revised layout by Friday.", False),
            (2, "Please also confirm the illustration price.", False),
            (3, "I will check and answer today.", True),
        ]:
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:150",
                    message_id=identity,
                    revision=1,
                    date=conversation.store.clock() - 60 + identity,
                    text=text,
                    outgoing=outgoing,
                )
            )
        with conversation.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET due=0 WHERE state='queued' AND scope LIKE '1/user:150/%'"
            )
        await conversation.process_due()
        if (await request.json()).get("settled"):
            with conversation.store.transaction() as db:
                db.execute(
                    "UPDATE source_views SET arrived=min(arrived,?) "
                    "WHERE chat_key='1/user:150' AND ready IS NULL",
                    (conversation.store.clock() - 31,),
                )
            conversation.unseen.advance()
        return {"ok": True}

    @app.post("/api/test/reaction")
    async def reaction_fixture(request: Request):
        """A correspondent's dated reaction, past its quiet seconds and settled."""
        from enloq.domain.models import (
            Reaction,
            ReactionActor,
            ReactionChip,
            ReactionSnapshot,
        )

        body = await request.json()
        conversation = app.state.accounts.get(1)
        now = conversation.store.clock()
        present = not body.get("remove")
        reaction = Reaction(emoji=body["emoji"])
        conversation.sync.reactions(
            body["peer"],
            body["message_id"],
            ReactionSnapshot(
                chips=[ReactionChip(reaction=reaction, count=1, mine=False)]
                if present
                else [],
                actors=[ReactionActor(actor="peer", reaction=reaction, date=now)]
                if present
                else [],
                complete=True,
                observed=now,
            ),
        )
        with conversation.store.transaction() as db:
            db.execute(
                "UPDATE reaction_events SET observed=observed-31 WHERE chat_key=?",
                (f"1/{body['peer']}",),
            )
            db.execute(
                "UPDATE jobs SET due=0 WHERE role='reaction_assessment' AND state='queued'"
            )
        await conversation.process_due()
        conversation.unseen.advance()
        return {"ok": True}

    @app.post("/api/test/read-outbox")
    async def read_outbox_fixture(request: Request):
        body = await request.json()
        conversation = app.state.accounts.get(1)
        return {"changed": conversation.sync.read_outbox(body["peer"], body["through"])}

    @app.post("/api/test/handoff")
    async def handoff_fixture():
        from enloq.domain.models import Chat, Message

        conversation = app.state.accounts.get(1)
        conversation.store.put_chat(
            Chat(account_id=1, peer="user:110", title="Hand-off", kind="private")
        )
        conversation.sync.ingest(
            Message(
                account_id=1,
                peer="user:110",
                message_id=1,
                revision=1,
                date=STARTED,
                text="Could you confirm the time?",
            ),
            target=False,
        )
        return {"ok": True}

    @app.post("/api/test/avatars")
    async def avatar_fixture(request: Request):
        from io import BytesIO
        from types import SimpleNamespace

        from PIL import Image

        action = (await request.json())["action"]
        assert action in {"initial", "change", "remove", "reset"}
        for identity in (1, 2):
            conversation = app.state.accounts.get(identity)
            if action in {"initial", "reset"}:
                entry = app.state.accounts.registry.get(identity)
                own_id = str(9000 + identity) if action == "initial" else None
                app.state.accounts.registry.identity(
                    identity,
                    SimpleNamespace(
                        first_name=entry["name"],
                        photo=SimpleNamespace(photo_id=int(own_id)) if own_id else None,
                    ),
                )
                if own_id:
                    output = BytesIO()
                    Image.new("RGB", (64, 64), (40, 100, 130)).save(
                        output, format="PNG"
                    )
                    conversation.transport.avatars[f"user:{identity}", own_id] = (
                        output.getvalue()
                    )
            if identity == 1:
                chat = conversation.store.chat("1/user:101")
                photo_id = {
                    "initial": "9223372036854775805",
                    "change": "9223372036854775806",
                }.get(action)
                conversation.store.put_chat(
                    chat.model_copy(update={"photo_id": photo_id})
                )
                if photo_id:
                    output = BytesIO()
                    Image.new(
                        "RGB",
                        (64, 64),
                        (45, 145, 95) if action == "initial" else (130, 80, 155),
                    ).save(output, format="PNG")
                    conversation.transport.avatars[chat.peer, photo_id] = (
                        output.getvalue()
                    )
                if action in {"initial", "reset"}:
                    failed = conversation.store.chat("1/user:102")
                    conversation.store.put_chat(
                        failed.model_copy(
                            update={"photo_id": "8000" if action == "initial" else None}
                        )
                    )
            conversation.avatars.collect()
        return {"ok": True}

    # An app left open on a phone keeps a stream open through adb's port
    # reversal, so shutdown stops waiting for open connections after a moment.
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=2,
    )
