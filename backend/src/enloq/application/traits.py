"""Traits (ADR 55 Q10-Q13, Q22, Q23): lasting tendencies of each chat's person
and of the account's holder, collected from launch and shown only to the
owner. No model reads them.

Three kinds: scales the decision model scores from the starter list, each
shown while its evidence question says yes at 0.7 or more; notes MiMo writes,
each cited from at least three of the subject's own messages on two days; and
habits counted without a model. Scales are judged at every memory update and
notes at most once a day, both from the cached month, as low-priority memory
work behind every rewrite, decision and suggestion.
"""

import json
import re
import statistics
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain import traits as starter
from ..domain.models import Conflict, ProcessingPaused, Refused
from ..inference.base import ProviderRefused
from ..storage.store import RETENTION
from .memory import ACCOUNT

NOTES = 10
NOTE_SOURCES = 3
NOTE_DAYS = 2
NOTES_EVERY = 86400
SCALE_INPUT = 60_000
NOTE_INPUT = 24_000
# A subject with fewer of their own messages in the month gets no request.
MINIMUM = 3
# Habits show once there are this many of the subject's messages, over two
# days or more (the plan's defaults).
HABIT_MESSAGES = 10
REPLY_WINDOW = 12 * 3600
SILENCE = 6 * 3600
HOURS = 4
# Message IDs a model writes into a note's text despite its core, such as
# "(5, 8, 11)" or "(id 2, 6, 8)": they belong to the citations alone.
CITED = re.compile(
    r"\s*\((?:(?:ids?|msgs?|m\.)\s*)?\d+(?:\s*[,;]\s*(?:m\.)?\d+)*\)", re.IGNORECASE
)
# Who wrote each message, as the models read it: the subject's own messages
# are named plainly, never as "own", which a model may take for the owner's.
WRITER = {"person": "recipient", "owner": "owner"}
OTHER = {"person": "owner", "owner": "other"}


class Traits:
    def __init__(self, app):
        self.app, self.store = app, app.store

    @staticmethod
    def kind(subject):
        return "owner" if subject == ACCOUNT else "person"

    # ---- sources ---------------------------------------------------------

    def chats(self):
        """Remembered private chats with cached messages this month: the
        holder's own messages there teach their traits."""
        rows = self.store.rows(
            "SELECT DISTINCT chat_key FROM messages WHERE date>=? AND deleted=0",
            (self.store.clock() - RETENTION,),
        )
        return [
            r["chat_key"]
            for r in rows
            if self.store.chat(r["chat_key"]).kind == "private"
            and self.app.memory.usable(r["chat_key"])
        ]

    def rows(self, chat_key):
        return self.store.rows(
            "SELECT * FROM messages WHERE chat_key=? AND deleted=0 AND date>=? ORDER BY date,message_id",
            (chat_key, self.store.clock() - RETENTION),
        )

    def item(self, row):
        value = self.app.memory.projection(row)
        body = json.loads(row["body"])
        local = datetime.fromtimestamp(value["date"], ZoneInfo(self.store.timezone))
        text = (
            value["text"] or value.get("transcript") or value.get("image_context") or ""
        )
        return {
            "id": value["id"],
            "from": None,  # Set by the caller, who knows the subject.
            "date": local.strftime("%Y-%m-%d %H:%M"),
            "kind": voice(body) or body.get("media") or "text",
            "text": text,
        }

    def material(self, subject, bound):
        """The subject's month, newest first within the bound, then in time
        order: items, and the (chat, message) each reference stands for."""
        chats = [subject] if subject != ACCOUNT else self.chats()
        rows = sorted(
            ((chat, row) for chat in chats for row in self.rows(chat)),
            key=lambda pair: pair[1]["date"],
            reverse=True,
        )
        kind = self.kind(subject)
        chosen, used = [], 0
        for chat, row in rows:
            item = self.item(row)
            theirs = bool(json.loads(row["body"])["outgoing"]) == (subject == ACCOUNT)
            item["from"] = WRITER[kind] if theirs else OTHER[kind]
            size = len(json.dumps(item, ensure_ascii=False).encode())
            if used + size > bound:
                break
            chosen.append((chat, row, item))
            used += size
        chosen.reverse()
        refs = {}
        if subject == ACCOUNT:
            # Message numbers repeat across chats: the holder's material is
            # numbered afresh, and each chat is named only by its order.
            names = {}
            for number, (chat, row, item) in enumerate(chosen, 1):
                names.setdefault(chat, f"chat {len(names) + 1}")
                item["id"] = number
                item["chat"] = names[chat]
                refs[number] = (chat, row["message_id"])
        else:
            refs = {item["id"]: (chat, row["message_id"]) for chat, row, item in chosen}
        return [item for _, _, item in chosen], refs

    @staticmethod
    def own(items, kind):
        """The subject's own messages among the material, by ID."""
        return {item["id"]: item for item in items if item["from"] == WRITER[kind]}

    @staticmethod
    def script(items):
        """The script most of the subject's own words use, so notes keep it
        (ADR 55: a probe's English chat got Czech notes without it)."""
        text = " ".join(item["text"] for item in items)
        cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
        latin = sum(1 for c in text if c.isascii() and c.isalpha())
        return "Cyrillic" if cyrillic > latin else "Latin" if latin else None

    # ---- scheduling ------------------------------------------------------

    def state(self, subject):
        rows = self.store.rows("SELECT * FROM trait_state WHERE subject=?", (subject,))
        return (
            rows[0]
            if rows
            else {
                "revision": 0,
                "scales_judged": None,
                "notes_judged": None,
                "error": None,
            }
        )

    def queued(self, role, subject):
        return bool(
            self.store.rows(
                "SELECT id FROM jobs WHERE role=? AND scope=? AND state IN ('queued','claimed','running')",
                (role, subject),
            )
        )

    def schedule(self, chat_key):
        """After a published memory update: the chat's person and the
        account's holder are judged again, and their notes at most daily."""
        if not self.app.memory.enabled():
            return
        for subject in (chat_key, ACCOUNT):
            if subject != ACCOUNT and not self.app.memory.usable(subject):
                continue
            if not self.queued("trait_scales", subject):
                self._enqueue("trait_scales", subject)
            judged = self.state(subject)["notes_judged"]
            if (
                judged is None or self.store.clock() - judged >= NOTES_EVERY
            ) and not self.queued("trait_notes", subject):
                self._enqueue("trait_notes", subject)

    def _enqueue(self, role, subject):
        identity = uuid.uuid4().hex
        revision = str(self.state(subject)["revision"])
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,'queued',?,?,NULL)",
                (
                    identity,
                    subject,
                    role,
                    revision,
                    self.store.clock(),
                    json.dumps({"class": "memory"}),
                ),
            )
        return identity

    def usable(self, subject):
        return self.app.memory.enabled() and (
            subject == ACCOUNT or self.app.memory.usable(subject)
        )

    def ready(self, job):
        return self.usable(job["scope"]) and self.app.memory.idle(job["id"])

    # ---- judging ---------------------------------------------------------

    async def run(self, job):
        current = self.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not current or current[0]["state"] not in ("queued", "claimed"):
            return
        subject = job["scope"]
        try:
            self.app.admit_ai()
            if not self.ready(job):
                raise ProcessingPaused("Waiting for idle communication")
            if job["role"] == "trait_scales":
                await self.judge(job, subject)
            else:
                await self.note(job, subject)
        except ProviderRefused:
            # No work was done; the next memory update tries again.
            self._finish(job, "refused")
        except ProcessingPaused:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET state='queued',due=? WHERE id=? AND state IN ('queued','claimed')",
                    (self.store.clock() + 60, job["id"]),
                )
        except Refused as error:
            dispatched = bool(
                self.store.rows("SELECT id FROM reservations WHERE id=?", (job["id"],))
            )
            if dispatched:
                self._error(subject, str(error))
            self._finish(job, "failed" if dispatched else "cancelled", str(error))

    def _valid(self, job):
        def valid():
            return bool(self.app.authorized() and self.usable(job["scope"]))

        return valid

    async def judge(self, job, subject):
        kind = self.kind(subject)
        items, _ = self.material(subject, SCALE_INPUT)
        if len(self.own(items, kind)) < MINIMUM:
            self._judged(subject, "scales_judged")
            self._finish(job, "done")
            return
        state = (
            {"conversation": items}
            if kind == "person"
            else {"conversations": grouped(items)}
        )
        packet = {"state": state, "subject": kind, "inputs": {}}
        answers = await self.app.rewriting.work.run(
            self.app.decider, job, packet, self._valid(job), klass="memory"
        )
        now = self.store.clock()
        with self.store.transaction() as db:
            for name, answer in answers.items():
                row = db.execute(
                    "SELECT source FROM trait_scales WHERE subject=? AND trait=?",
                    (subject, name),
                ).fetchone()
                if row and row["source"] == "owner":
                    continue  # The owner's own value; the model never changes it.
                if (
                    answer["evidence"] < starter.EVIDENCE
                    or starter.label(name, answer["value"]) is None
                ):
                    continue  # Kept as last judged, with its date.
                db.execute(
                    "INSERT INTO trait_scales VALUES (?,?,?,?,?,?,'model') ON CONFLICT(subject,trait) DO UPDATE SET "
                    "value=excluded.value,probability=excluded.probability,evidence=excluded.evidence,"
                    "judged=excluded.judged,source='model'",
                    (
                        subject,
                        name,
                        answer["value"],
                        answer["probability"],
                        answer["evidence"],
                        now,
                    ),
                )
            self._stamp(db, subject, "scales_judged", now)
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (job["id"],))

    async def note(self, job, subject):
        kind = self.kind(subject)
        items, refs = self.material(subject, NOTE_INPUT)
        notes = self.notes(subject)
        own = self.own(items, kind)
        if len(own) < MINIMUM:
            self._judged(subject, "notes_judged")
            self._finish(job, "done")
            return
        back = {pair: number for number, pair in refs.items()}
        owners = [n for n in notes if n["source"] == "owner"]
        packet = {
            "subject": kind,
            "writer": WRITER[kind],
            "script": self.script(list(own.values())),
            "messages": items,
            "notes": [
                {
                    "id": n["id"],
                    "text": n["text"],
                    "source": n["source"],
                    "citations": [
                        back[tuple(c)] for c in n["citations"] if tuple(c) in back
                    ],
                }
                for n in notes
            ],
            "limits": {"notes": NOTES, "generated": max(0, NOTES - len(owners))},
            "inputs": {},
        }
        revision = self.state(subject)["revision"]
        result = await self.app.rewriting.work.run(
            self.app.engine, job, packet, self._valid(job), klass="memory"
        )
        known = {n["id"]: n for n in notes}
        if any(known.get(i, {}).get("source") != "model" for i in result.remove):
            raise Refused("Trait notes removed an unknown note")
        kept = len(notes) - len(set(result.remove))
        additions = []
        for addition in result.add:
            cited = [i for i in dict.fromkeys(addition.citations) if i in own]
            days = {own[i]["date"][:10] for i in cited}
            text = CITED.sub("", addition.text).strip()
            # Q12 C: a pattern across three of their own messages on two days.
            if (
                not text
                or len(cited) < NOTE_SOURCES
                or len(days) < NOTE_DAYS
                or kept >= NOTES
            ):
                continue
            kept += 1
            additions.append((text, addition, [list(refs[i]) for i in cited]))
        now = self.store.clock()
        defaults = (packet.get("guidance") or {}).get("defaults_revision")
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT revision FROM trait_state WHERE subject=?", (subject,)
            ).fetchone()
            if (current[0] if current else 0) != revision:
                db.execute(
                    "UPDATE jobs SET state='superseded',error='Traits changed during the update' WHERE id=?",
                    (job["id"],),
                )
                return
            for identity in result.remove:
                db.execute("DELETE FROM trait_notes WHERE id=?", (identity,))
            for offset, (text, addition, citations) in enumerate(additions):
                db.execute(
                    "INSERT INTO trait_notes VALUES (?,?,?,?,?,'model',?,?,?)",
                    (
                        uuid.uuid4().hex[:12],
                        subject,
                        text,
                        json.dumps(citations),
                        addition.confidence,
                        now + offset * 1e-6,
                        (packet.get("guidance") or {}).get("version"),
                        defaults,
                    ),
                )
            self._stamp(db, subject, "notes_judged", now)
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (job["id"],))

    def _stamp(self, db, subject, column, now):
        db.execute(
            f"INSERT INTO trait_state (subject,revision,{column},error) VALUES (?,1,?,NULL) "
            f"ON CONFLICT(subject) DO UPDATE SET revision=revision+1,{column}=excluded.{column},error=NULL",
            (subject, now),
        )

    def _judged(self, subject, column):
        with self.store.transaction() as db:
            self._stamp(db, subject, column, self.store.clock())

    def _error(self, subject, error):
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO trait_state (subject,revision,error) VALUES (?,0,?) ON CONFLICT(subject) DO UPDATE SET error=excluded.error",
                (subject, error[:200]),
            )

    def _finish(self, job, state, error=None):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state=?,error=? WHERE id=? AND state NOT IN ('superseded','done')",
                (state, error, job["id"]),
            )

    # ---- reading ---------------------------------------------------------

    def notes(self, subject):
        return [
            {**r, "citations": json.loads(r["citations"])}
            for r in self.store.rows(
                "SELECT * FROM trait_notes WHERE subject=? ORDER BY created,id",
                (subject,),
            )
        ]

    def view(self, subject):
        if subject != ACCOUNT:
            self.app.memory.private(subject)
        kind = self.kind(subject)
        state = self.state(subject)
        rows = {
            r["trait"]: r
            for r in self.store.rows(
                "SELECT * FROM trait_scales WHERE subject=?", (subject,)
            )
        }
        scales = []
        for name in starter.traits(kind):
            row = rows.get(name)
            value = row["value"] if row else None
            scales.append(
                {
                    "trait": name,
                    "title": starter.title(name),
                    "value": value,
                    "label": starter.label(name, value),
                    "probability": row["probability"] if row else None,
                    "judged": row["judged"] if row else None,
                    "source": row["source"] if row else None,
                    # A scale whose evidence fell short at the latest judgment
                    # keeps its last level and date.
                    "current": bool(
                        row
                        and row["source"] == "model"
                        and state["scales_judged"] is not None
                        and row["judged"] >= state["scales_judged"]
                    ),
                    "options": starter.options(name),
                }
            )
        return {
            "revision": state["revision"],
            "enabled": self.app.memory.enabled(),
            "scales": scales,
            "notes": [
                {
                    "id": n["id"],
                    "text": n["text"],
                    "confidence": n["confidence"],
                    "source": n["source"],
                    "created": n["created"],
                    "citations": [identity for _, identity in n["citations"]],
                }
                for n in self.notes(subject)
            ],
            "habits": self.habits(subject),
            "error": state["error"],
        }

    def habits(self, subject):
        """Counted from the cached month, without a model."""
        own_outgoing = subject == ACCOUNT
        chats = [subject] if subject != ACCOUNT else self.chats()
        zone = ZoneInfo(self.store.timezone)
        theirs, replies, starts, started = [], [], 0, 0
        for chat in chats:
            previous = None
            for row in self.rows(chat):
                body = json.loads(row["body"])
                if body.get("service"):
                    continue
                mine = bool(body["outgoing"]) == own_outgoing
                gap = row["date"] - previous["date"] if previous else None
                if gap is None or gap >= SILENCE:
                    starts += 1
                    started += mine
                if mine:
                    theirs.append(body)
                    if (
                        previous is not None
                        and bool(json.loads(previous["body"])["outgoing"])
                        != own_outgoing
                        and gap <= REPLY_WINDOW
                    ):
                        replies.append(gap)
                previous = row
        days = {datetime.fromtimestamp(b["date"], zone).date() for b in theirs}
        if len(theirs) < HABIT_MESSAGES or len(days) < 2:
            return None
        hours = [0] * 24
        for body in theirs:
            hours[datetime.fromtimestamp(body["date"], zone).hour] += 1
        start = max(
            range(24), key=lambda h: sum(hours[(h + i) % 24] for i in range(HOURS))
        )
        return {
            "messages": len(theirs),
            "reply_seconds": round(statistics.median(replies)) if replies else None,
            "active_from": start,
            "active_to": (start + HOURS) % 24,
            "voice_share": sum(1 for b in theirs if voice(b)) / len(theirs),
            "starts_share": started / starts if starts else None,
        }

    # ---- the owner's edits and source changes ----------------------------

    def _change(self, subject, revision, change):
        if subject != ACCOUNT:
            self.app.memory.private(subject)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT revision FROM trait_state WHERE subject=?", (subject,)
            ).fetchone()
            if revision is not None and revision != (row[0] if row else 0):
                raise Conflict("Traits changed; reload before editing")
            change(db)
            db.execute(
                "INSERT INTO trait_state (subject,revision) VALUES (?,1) ON CONFLICT(subject) DO UPDATE SET revision=revision+1",
                (subject,),
            )
        return self.view(subject)

    def set_scale(self, subject, revision, trait, value):
        """The owner's own value, or None to clear it; the model never changes
        either."""
        if trait not in starter.traits(self.kind(subject)):
            raise Refused("Unknown trait")
        if value is not None and value not in {
            o["value"] for o in starter.options(trait)
        }:
            raise Refused("Unknown value for this trait")
        return self._change(
            subject,
            revision,
            lambda db: db.execute(
                "INSERT INTO trait_scales VALUES (?,?,?,NULL,NULL,?,'owner') ON CONFLICT(subject,trait) DO UPDATE SET "
                "value=excluded.value,probability=NULL,evidence=NULL,judged=excluded.judged,source='owner'",
                (subject, trait, value, self.store.clock()),
            ),
        )

    def add_note(self, subject, revision, text):
        text = self._text(text)

        def change(db):
            count = db.execute(
                "SELECT count(*) FROM trait_notes WHERE subject=?", (subject,)
            ).fetchone()[0]
            if count >= NOTES:
                raise Refused(f"Traits hold at most {NOTES} notes")
            db.execute(
                "INSERT INTO trait_notes VALUES (?,?,?,'[]',NULL,'owner',?,NULL,NULL)",
                (uuid.uuid4().hex[:12], subject, text, self.store.clock()),
            )

        return self._change(subject, revision, change)

    def edit_note(self, subject, revision, identity, text):
        text = self._text(text)

        def change(db):
            if not db.execute(
                "UPDATE trait_notes SET text=?,source='owner',confidence=NULL WHERE id=? AND subject=?",
                (text, identity, subject),
            ).rowcount:
                raise Refused("Trait note unavailable")

        return self._change(subject, revision, change)

    def remove_note(self, subject, revision, identity):
        return self._change(
            subject,
            revision,
            lambda db: db.execute(
                "DELETE FROM trait_notes WHERE id=? AND subject=?", (identity, subject)
            ),
        )

    def forget(self, message):
        """An edited or deleted cited message drops only its citation; a model
        note goes when fewer than three remain (Q12 C)."""
        chat_key = f"{message.account_id}/{message.peer}"
        cited = [chat_key, message.message_id]
        with self.store.transaction() as db:
            touched = set()
            for row in db.execute(
                "SELECT * FROM trait_notes WHERE subject IN (?,?)", (chat_key, ACCOUNT)
            ).fetchall():
                citations = json.loads(row["citations"])
                if cited not in citations:
                    continue
                remaining = [c for c in citations if c != cited]
                if row["source"] == "model" and len(remaining) < NOTE_SOURCES:
                    db.execute("DELETE FROM trait_notes WHERE id=?", (row["id"],))
                else:
                    db.execute(
                        "UPDATE trait_notes SET citations=? WHERE id=?",
                        (json.dumps(remaining), row["id"]),
                    )
                touched.add(row["subject"])
            for subject in touched:
                db.execute(
                    "INSERT INTO trait_state (subject,revision) VALUES (?,1) ON CONFLICT(subject) DO UPDATE SET revision=revision+1",
                    (subject,),
                )

    @staticmethod
    def _text(text):
        text = (text or "").strip()
        if not text or len(text.encode()) > 300:
            raise Refused("A trait note needs 1 to 300 bytes of text")
        return text


def voice(body):
    """A voice note or a video circle, the spoken kinds a habit counts."""
    if body.get("media") == "video_note":
        return "video_note"
    info = body.get("media_info") or {}
    if body.get("media") == "audio" and info.get("voice"):
        return "voice"
    return None


def grouped(items):
    """The holder's material as one conversation per chat, for the decision
    model's state."""
    chats = {}
    for item in items:
        chats.setdefault(item["chat"], []).append(
            {k: v for k, v in item.items() if k != "chat"}
        )
    return [{"with": name, "messages": messages} for name, messages in chats.items()]
