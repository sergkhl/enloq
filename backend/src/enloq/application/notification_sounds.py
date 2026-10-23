"""One private custom chime and a durable sound choice per trusted browser."""

import base64
import binascii
import unicodedata
import uuid
from typing import Literal, get_args

from ..domain.models import Refused

SoundPreset = Literal["harp", "bell", "chime", "pop", "digital", "custom"]
MAX_AUDIO_BYTES = 2 * 1024 * 1024
MAX_AUDIO_BASE64 = 4 * ((MAX_AUDIO_BYTES + 2) // 3)
MAX_SOUND_NAME = 128


def audio_mime(body: bytes) -> str:
    """Recognize common audio containers; browser decoding checks playability."""
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WAVE":
        return "audio/wav"
    if len(body) >= 27 and body[:5] == b"OggS\x00":
        return "audio/ogg"
    if len(body) >= 8 and body[:4] == b"fLaC":
        return "audio/flac"
    if (
        len(body) >= 10
        and body[:3] == b"ID3"
        and body[3] in (2, 3, 4)
        and all(value < 128 for value in body[6:10])
    ):
        return "audio/mpeg"
    if len(body) >= 7 and body[0] == 0xFF and body[1] & 0xF6 == 0xF0:
        # ADTS AAC has the MPEG sync word with reserved layer bits clear.
        if (body[2] >> 2) & 0xF < 13:
            return "audio/aac"
    if (
        len(body) >= 4
        and body[0] == 0xFF
        and body[1] & 0xE0 == 0xE0
        and (body[1] >> 3) & 3 != 1
        and (body[1] >> 1) & 3 != 0
        and (body[2] >> 4) != 15
        and (body[2] >> 2) & 3 != 3
    ):
        return "audio/mpeg"
    if (
        len(body) >= 16
        and body[4:8] == b"ftyp"
        and body[8:12]
        in {b"M4A ", b"M4B ", b"isom", b"iso2", b"mp41", b"mp42", b"dash"}
    ):
        return "audio/mp4"
    if len(body) >= 12 and body[:4] == b"FORM" and body[8:12] in {b"AIFF", b"AIFC"}:
        return "audio/aiff"
    if body[:4] == b"\x1a\x45\xdf\xa3" and b"webm" in body[4:4096]:
        return "audio/webm"
    raise Refused("Choose a supported audio file")


def sound_name(value: str) -> str:
    if not isinstance(value, str) or len(value) > 1024:
        raise Refused("Choose a file with a shorter name")
    # File names are display metadata, never paths or response header values.
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = "".join(
        char for char in value if not unicodedata.category(char).startswith("C")
    )
    value = value.strip(" .")[:MAX_SOUND_NAME].rstrip(" .")
    if not value:
        raise Refused("Choose a file with a name")
    return value


class NotificationSounds:
    def __init__(self, delivery):
        self.delivery, self.registry = delivery, delivery.registry
        with self.registry.transaction() as db:
            columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(notification_browsers)")
            }
            if "sound_preset" not in columns:
                db.execute(
                    "ALTER TABLE notification_browsers ADD COLUMN sound_preset TEXT NOT NULL DEFAULT 'harp'"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS notification_sounds (browser TEXT PRIMARY KEY, revision TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL, body BLOB NOT NULL)"
            )

    @staticmethod
    def metadata(current):
        # The browser row joins only file metadata, so all sound fields describe
        # one registry snapshot even if another tab uploads before projection.
        return {
            "sound_preset": current["sound_preset"],
            "sound_file_name": current["sound_file_name"],
            "sound_revision": current["sound_revision"],
        }

    def select(self, db, identity, preset):
        if preset not in get_args(SoundPreset):
            raise Refused("Choose a supported notification sound")
        if (
            preset == "custom"
            and not db.execute(
                "SELECT 1 FROM notification_sounds WHERE browser=?", (identity,)
            ).fetchone()
        ):
            raise Refused("Upload an audio file before choosing Custom")
        db.execute(
            "UPDATE notification_browsers SET sound_preset=? WHERE browser=?",
            (preset, identity),
        )

    def upload(self, identity, name, data):
        self.delivery.browser(identity)
        name = sound_name(name)
        if not isinstance(data, str) or not 0 < len(data) <= MAX_AUDIO_BASE64:
            raise Refused("Choose an audio file no larger than 2 MiB")
        try:
            body = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise Refused("The audio upload is not valid base64") from None
        if not body or len(body) > MAX_AUDIO_BYTES:
            raise Refused("Choose an audio file no larger than 2 MiB")
        if base64.b64encode(body).decode("ascii") != data:
            raise Refused("The audio upload is not valid base64")
        mime, revision = audio_mime(body), uuid.uuid4().hex
        with self.registry.transaction() as db:
            db.execute(
                "INSERT INTO notification_sounds VALUES (?,?,?,?,?) ON CONFLICT(browser) DO UPDATE SET revision=excluded.revision,name=excluded.name,mime=excluded.mime,body=excluded.body",
                (identity, revision, name, mime, body),
            )
            self.select(db, identity, "custom")

    def get(self, identity, revision):
        self.delivery.browser(identity)
        rows = self.registry.rows(
            "SELECT body,mime FROM notification_sounds WHERE browser=? AND revision=?",
            (identity, revision),
        )
        if not rows:
            raise Refused("The custom notification sound is no longer available")
        return rows[0]["body"], rows[0]["mime"]

    @staticmethod
    def forget(db, identity):
        db.execute("DELETE FROM notification_sounds WHERE browser=?", (identity,))
