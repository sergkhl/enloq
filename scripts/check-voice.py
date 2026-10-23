"""Finite authored local voice check. No Telegram or remote model adapter is constructed."""

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

from enloq.application.conversations import Conversations
from enloq.config import ROOT, private_directory, private_write
from enloq.domain.models import Chat, Message
from enloq.inference.audio import WHISPER
from enloq.media_tools import FFMPEG
from enloq.storage.store import Store
from enloq.transport.fake import FakeTransport


async def main():
    os.umask(0o077)
    private_directory(ROOT / ".local")
    with tempfile.TemporaryDirectory(
        prefix="authored-voice-", dir=ROOT / ".local"
    ) as folder:
        root = Path(folder)
        source = root / "authored.aiff"
        wav = root / "authored.wav"
        subprocess.run(
            [
                "/usr/bin/say",
                "-v",
                "Samantha",
                "-o",
                str(source),
                "I may agree only if the total is below twenty. Please do not confirm yet.",
            ],
            check=True,
            timeout=30,
            capture_output=True,
        )
        subprocess.run(
            [
                FFMPEG,
                "-v",
                "error",
                "-i",
                str(source),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(wav),
            ],
            check=True,
            timeout=30,
            capture_output=True,
        )
        store = Store(root / "rewriting.sqlite3", account_id=1)
        store.put_chat(
            Chat(
                account_id=1,
                peer="user:42",
                title="Authored voice",
                kind="private",
                ai_eligible=True,
            )
        )
        transport = FakeTransport()
        transport.media["user:42", 1] = wav.read_bytes()
        app = Conversations(store, transport)
        app.sync.ingest(
            Message(
                account_id=1,
                peer="user:42",
                message_id=1,
                revision=1,
                date=store.clock(),
                media="audio",
            )
        )
        with store.transaction() as db:
            db.execute("UPDATE jobs SET due=0")
        await app.process_due()
        result = app.media.cached(store.message("1/user:42", 1)).get("transcript")
        if not result:
            raise RuntimeError(
                "Local voice preparation failed; inspect the content-free job status"
            )
        restarted = Conversations(Store(store.path, account_id=1), transport)
        cached = await restarted.media.transcript(store.message("1/user:42", 1))
        evidence = {
            "model": WHISPER,
            "transcript": result["text"],
            "language": result["language"],
            "duration_seconds": result["duration_seconds"],
            "cache_reused": cached == result,
            "temporary_media_removed": not list(app.media.workspace.path.iterdir()),
            "rewritten_block_ready": app.conversation("1/user:42")["blocks"][0]["state"]
            == "ready",
            "provider_calls": 0,
            "telegram_calls": 0,
        }
        private_write(
            ROOT / ".local/rebuild-voice-check.json",
            json.dumps(evidence, ensure_ascii=False, indent=2),
        )
        print(json.dumps(evidence, ensure_ascii=False))


asyncio.run(main())
