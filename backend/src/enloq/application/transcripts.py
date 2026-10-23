"""Voice Transcripts beyond block preparation (ADR 51 Q8, Q9).

The owner's own voice notes and circles in private chats are transcribed once
Telegram confirms them, under the incoming notes' rules: the account's AI on,
VPS speech, and the hosted fallback's Daily Budget ledger. They stay raw and
are never rewritten. In groups the day's briefing preparation transcribes
every voice note, the owner's included.

Show transcript starts a missing transcript, for either direction and for
older notes or earlier failures, and reports its state while it runs. Saved
Messages takes no AI work (ADR 50 Q3).
"""

import asyncio

from ..domain.models import SPOKEN_MEDIA, Refused
from ..storage.store import WINDOW
from .media import voice_note


def spoken(message):
    """A voice note or a Video Circle, not a music file."""
    return voice_note(message) or message.media == "video_note"


class Transcripts:
    def __init__(self, app):
        self.app = app
        self.store, self.media = app.store, app.media
        # Requested transcriptions still running in this runtime, and the
        # refusal of one that stopped before its attempt was recorded.
        self.requested: set[str] = set()
        self.refusals: dict[str, str] = {}

    def own(self, message):
        """The owner's new voice note or circle in a private chat."""
        if (
            message.outgoing
            and not message.deleted
            and spoken(message)
            and message.date >= self.store.clock() - WINDOW
        ):
            self.app.run(self._own(message))

    async def _own(self, message):
        try:
            await self.media.transcript(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            return  # Show transcript can start it again (Q9).

    def status(self, message):
        """The transcript, or where its transcription stands."""
        done = self.media.cached(message).get("transcript")
        if done:
            return {
                "state": "done",
                "transcript": {
                    key: done.get(key)
                    for key in ("text", "language", "duration_seconds")
                },
                "reason": None,
            }
        if message.key in self.requested:
            return {"state": "running", "transcript": None, "reason": None}
        rows = self.store.rows(
            "SELECT state FROM jobs WHERE scope=? AND role='transcript' AND input_revision=? ORDER BY due DESC",
            (message.key, str(message.revision)),
        )
        if any(row["state"] == "running" for row in rows):
            return {"state": "running", "transcript": None, "reason": None}
        if message.key in self.refusals:
            reason = self.refusals[message.key]
        elif rows:
            reason = "Transcription failed"
        else:
            return {"state": "none", "transcript": None, "reason": None}
        return {"state": "failed", "transcript": None, "reason": reason}

    def request(self, message):
        """Show transcript's deliberate start of a missing transcript."""
        if message.deleted or message.media not in SPOKEN_MEDIA:
            raise Refused("Voice source unavailable")
        kind = self.store.chat(f"{message.account_id}/{message.peer}").kind
        if kind == "saved":
            raise Refused("Saved Messages has no AI for now")
        current = self.status(message)
        if current["state"] in ("done", "running"):
            return current
        self.app.admit_ai()
        self.refusals.pop(message.key, None)
        self.requested.add(message.key)
        self.app.run(self._requested(message, group=kind == "group"))
        return {"state": "running", "transcript": None, "reason": None}

    async def _requested(self, message, *, group):
        try:
            if group:
                # A deliberate tap is its own admission: no briefing batch is
                # charged, and the hosted fallback still spends Daily Budget.
                await self.media.transcript(
                    message,
                    retry=True,
                    guard=self.app.admit_ai,
                    on_speech_start=lambda _duration: None,
                )
            else:
                await self.media.transcript(message, retry=True)
        except asyncio.CancelledError:
            raise
        except Refused as error:
            reason = str(error)
            # A recorded attempt's failure reads plainly; Show transcript is
            # the way to try again.
            self.refusals[message.key] = (
                "Transcription failed" if "explicit" in reason else reason
            )
        except Exception:
            self.refusals[message.key] = "Transcription failed"
        finally:
            self.requested.discard(message.key)
