"""ADR 27: Telegram's voice waveform and an audio performer are display-only
metadata. Capturing them is a metadata-only update that keeps derivatives,
blocks and digests, and they never reach model inputs."""

import json

from enloq.application.context import projection
from enloq.application.media import placeholder
from enloq.domain.models import MediaInfo, same_content
from enloq.domain.source_revision import dependency_fingerprint
from enloq.storage.store import METADATA
from enloq.transport.telethon import classify
from telethon import utils
from telethon.tl import types

from backend.tests.test_media_kinds import document, native
from backend.tests.test_rebuild import fixture, message

LEVELS = bytes((index * 7) % 32 for index in range(100))


def test_voice_waveform_and_audio_performer_are_captured_and_bounded():
    kind, info = classify(
        native(
            document(
                "audio/ogg",
                types.DocumentAttributeAudio(
                    duration=3, voice=True, waveform=utils.encode_waveform(LEVELS)
                ),
            )
        )
    )
    assert kind == "audio" and info.voice
    # Every level round-trips, including the final one Telethon misreads.
    assert info.waveform == list(LEVELS)
    assert all(0 <= level <= 31 for level in info.waveform)
    assert len(info.waveform) <= 256
    # Legacy and third-party notes without one stay without (flat bars).
    _, bare = classify(
        native(
            document("audio/ogg", types.DocumentAttributeAudio(duration=3, voice=True))
        )
    )
    assert bare.waveform is None
    _, song = classify(
        native(
            document(
                "audio/mpeg",
                types.DocumentAttributeAudio(
                    duration=200, title="Authored track", performer="Authored artist"
                ),
                types.DocumentAttributeFilename("track.mp3"),
            )
        )
    )
    assert song.performer == "Authored artist" and song.waveform is None


def test_capture_is_metadata_only_and_never_model_input(tmp_path):
    app = fixture(tmp_path)
    legacy = message(
        app,
        text="",
        media="audio",
        media_info=MediaInfo(voice=True, duration=3),
        attachment_id="authored-voice",
    )
    app.sync.ingest(legacy)
    key = legacy.key
    with app.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (
                key,
                1,
                json.dumps({"transcript": {"text": "Authored", "language": "en"}}),
            ),
        )
    before = {
        table: app.store.rows(f"SELECT * FROM {table}")
        for table in ("blocks", "jobs", "derivatives", "message_versions")
    }
    digest = dependency_fingerprint(app.store.message("1/user:42", 1))
    captured = legacy.model_copy(
        update={
            "media_info": MediaInfo(
                voice=True, duration=3, waveform=list(LEVELS), performer="Unused"
            )
        }
    )
    assert same_content(legacy, captured)
    assert app.store.apply_message(captured) == METADATA
    stored = app.store.message("1/user:42", 1)
    assert stored.media_info.waveform == list(LEVELS)
    assert stored.revision == legacy.revision
    assert dependency_fingerprint(stored) == digest
    assert {
        table: app.store.rows(f"SELECT * FROM {table}") for table in before
    } == before
    # Model-visible projections and placeholders carry neither field.
    for value in (projection(stored), placeholder(stored)):
        text = json.dumps(value)
        assert "waveform" not in text and "Unused" not in text
