"""TL2: the owner's own voice is transcribed, and Show transcript starts a
missing transcript (ADR 51 Q8, Q9). Authored audio and fake transports only."""

import asyncio
import time

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import (
    Chat,
    MediaInfo,
    Message,
    ProcessingPaused,
    Refused,
)
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_media_cache import voice
from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"


class Speech:
    def __init__(self, *, fail=False):
        self.fail, self.calls = fail, []

    async def transcribe(self, path):
        self.calls.append(path.read_bytes())
        if self.fail:
            raise TimeoutError("Authored transcription failure")
        return {"text": "Authored spoken words.", "language": "en"}


def rig(tmp_path, **speech):
    app = fixture(tmp_path)
    app.media.transcriber = Speech(**speech)
    for identity in range(1, 6):
        app.transport.media["user:42", identity] = b"OggS" + bytes([identity]) * 64
    return app


async def settled(app):
    while app.tasks:
        await asyncio.gather(*app.tasks)


async def test_the_owners_own_voice_is_transcribed_raw_once_confirmed(tmp_path):
    app = rig(tmp_path)
    app.sync.ingest(voice(app, 1, outgoing=True))
    app.sync.ingest(
        message(
            app,
            2,
            text="",
            outgoing=True,
            media="video_note",
            media_info=MediaInfo(duration=4),
        )
    )
    # A music file is no voice note, and a history page is no arrival.
    app.sync.ingest(
        message(
            app,
            3,
            text="",
            outgoing=True,
            media="audio",
            media_info=MediaInfo(voice=False, title="Authored song"),
        )
    )
    app.sync.ingest(voice(app, 4, outgoing=True), target=False)
    await settled(app)
    assert app.media.transcriber.calls == [
        b"OggS" + bytes([1]) * 64,
        b"OggS" + bytes([2]) * 64,
    ]
    status = app.transcripts.status(app.store.message(KEY, 1))
    assert status == {
        "state": "done",
        "transcript": {
            "text": "Authored spoken words.",
            "language": "en",
            "duration_seconds": None,
        },
        "reason": None,
    }
    # Raw only: an outgoing note never becomes a block to rewrite.
    assert not app.store.rows("SELECT 1 FROM blocks")
    assert app.transcripts.status(app.store.message(KEY, 4))["state"] == "none"


async def test_with_ai_off_nothing_is_transcribed_and_a_tap_says_so(tmp_path):
    app = rig(tmp_path)

    def paused():
        raise ProcessingPaused("AI processing is Off for this account")

    app.admit_ai = app.media.admit = paused
    app.sync.ingest(voice(app, 1, outgoing=True))
    app.sync.ingest(voice(app, 2))
    await settled(app)
    assert app.media.transcriber.calls == []
    assert not app.store.rows("SELECT 1 FROM jobs WHERE role='transcript'")
    with pytest.raises(ProcessingPaused, match="AI processing is Off"):
        app.transcripts.request(app.store.message(KEY, 2))
    assert app.transcripts.status(app.store.message(KEY, 2))["state"] == "none"


async def test_show_transcript_starts_a_missing_one_and_retries_a_failure(tmp_path):
    app = rig(tmp_path, fail=True)
    # An older incoming note that no block prepared.
    app.sync.ingest(voice(app, 5), target=False)
    note = app.store.message(KEY, 5)
    assert app.transcripts.request(note)["state"] == "running"
    assert app.transcripts.status(note)["state"] == "running"
    # A second tap while it runs starts nothing more.
    assert app.transcripts.request(note)["state"] == "running"
    await settled(app)
    assert app.transcripts.status(note) == {
        "state": "failed",
        "transcript": None,
        "reason": "Transcription failed",
    }
    assert len(app.media.transcriber.calls) == 1
    # The next tap tries again.
    app.media.transcriber.fail = False
    assert app.transcripts.request(note)["state"] == "running"
    await settled(app)
    assert app.transcripts.status(note)["state"] == "done"
    assert app.transcripts.request(note)["state"] == "done"
    assert len(app.media.transcriber.calls) == 2


async def test_saved_messages_and_other_media_are_never_transcribed(tmp_path):
    app = rig(tmp_path)
    app.store.put_chat(
        Chat(account_id=1, peer="user:1", title="Saved Messages", kind="saved")
    )
    saved = Message(
        account_id=1,
        peer="user:1",
        message_id=1,
        revision=1,
        date=app.store.clock(),
        outgoing=True,
        media="audio",
        media_info=MediaInfo(voice=True),
    )
    app.sync.ingest(saved, target=False)
    with pytest.raises(Refused, match="Saved Messages has no AI"):
        app.transcripts.request(app.store.message("1/user:1", 1))
    app.sync.ingest(message(app, 2, media="image"), target=False)
    with pytest.raises(Refused, match="Voice source unavailable"):
        app.transcripts.request(app.store.message(KEY, 2))
    await settled(app)
    assert app.media.transcriber.calls == []


def test_the_status_and_start_routes(tmp_path):
    served = create_app(Settings(data=tmp_path / "app"))
    with TestClient(served, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        conversation = served.state.accounts.get(1)
        conversation.media.transcriber = Speech()
        conversation.transport.media["user:101", 7] = b"OggS" + bytes(64)
        conversation.sync.ingest(
            Message(
                account_id=1,
                peer="user:101",
                message_id=7,
                revision=1,
                date=conversation.store.clock(),
                outgoing=True,
                media="audio",
                media_info=MediaInfo(voice=True),
            ),
            target=False,
        )
        base = "/api/accounts/1/chats/user:101/originals/7/transcript"
        assert client.get(base).json() is None
        assert client.get(f"{base}/status").json() == {
            "state": "none",
            "transcript": None,
            "reason": None,
        }
        started = client.post(
            base, json={}, headers={"Origin": "http://127.0.0.1:8765"}
        )
        assert started.status_code == 200, started.text
        assert started.json()["state"] in {"running", "done"}
        for _ in range(100):
            status = client.get(f"{base}/status").json()
            if status["state"] == "done":
                break
            time.sleep(0.02)
        assert status["transcript"]["text"] == "Authored spoken words."
        assert client.get(base).json()["text"] == "Authored spoken words."
