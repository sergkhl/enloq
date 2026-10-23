"""Reaction Assessment (ADR 26): emotion and suggestion usefulness from one
confirmed correspondent reaction, its exact target and bounded recent context.

It never fabricates an incoming message, edits a source, reopens an answered
block or prepares media: cached transcripts and image meanings only. Native
addition time orders reactions against incoming communication; observation
time never does.
"""

import asyncio
import hashlib
import json
import uuid

from ..domain.emotion import LIFETIME
from ..domain.forwarding import attribution
from ..domain.models import Message, ProcessingPaused, Refused
from ..domain.reactions import QUIET
from ..domain.suggestions import QUALIFYING
from ..inference.base import ProviderRefused
from ..storage.store import WINDOW
from . import context, decision_state

ROLE = "reaction_assessment"
CONTEXT_MESSAGES = 20
PACKET_BYTES = 32000


def read(store, sql, args, db=None):
    return [dict(r) for r in db.execute(sql, args)] if db else store.rows(sql, args)


def latest_event(store, key, db=None):
    """The newest reaction that may be the current emotion source: an active
    non-baseline trigger reaction on a cached target within seven days of it,
    or a removed one that had already become the assessment subject. A burst
    removed before its assessment began never becomes current."""
    rows = read(
        store,
        "SELECT e.*,m.date AS target_date,m.deleted AS target_deleted,"
        "m.revision AS target_revision FROM reaction_events e "
        "JOIN messages m ON m.key=e.source WHERE e.chat_key=? AND e.baseline=0 "
        "AND (e.removed IS NULL OR e.subject IS NOT NULL) AND m.date>=e.added-? "
        "ORDER BY e.added DESC,e.id DESC LIMIT 1",
        (key, WINDOW),
        db,
    )
    return rows[0] if rows else None


def cached(store, message, db=None):
    """A message's model-visible projection with cached derivatives only."""
    item = context.projection(message)
    rows = read(
        store,
        "SELECT body FROM derivatives WHERE source=? AND revision=?",
        (message.key, message.revision),
        db,
    )
    body = json.loads(rows[0]["body"]) if rows else {}
    transcript = body.get("transcript")
    if transcript and transcript.get("text"):
        item["transcript"] = {
            "text": transcript["text"],
            "language": transcript.get("language"),
            "machine_generated": True,
        }
    if body.get("image_context"):
        item["image_context"] = body["image_context"]
    if message.media and not (item.get("transcript") or item.get("image_context")):
        item["placeholder"] = {"kind": message.media, "reason": "not prepared"}
    return item


def derived(store, key, db=None):
    rows = read(store, "SELECT body FROM derivatives WHERE source=?", (key,), db)
    body = json.loads(rows[0]["body"]) if rows else {}
    value = {k: body.get(k) for k in ("transcript", "image_context")}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Assessment:
    def __init__(self, app):
        self.app, self.store = app, app.store
        app.store.reactions.on_change.append(self.schedule)

    # ---- currentness -------------------------------------------------------

    def current(self, key, db=None):
        """The current emotion/suggestion source: the newest of the latest
        incoming communication and the latest eligible reaction."""
        event = latest_event(self.store, key, db)
        incoming = self.app.emotion.latest(key, db=db)
        if event and (incoming is None or event["added"] > incoming["date"]):
            return "reaction", event
        return "incoming", incoming

    def record(self, key, db=None):
        rows = read(
            self.store, "SELECT body FROM reaction_state WHERE chat_key=?", (key,), db
        )
        return json.loads(rows[0]["body"]) if rows else None

    def withdrawn(self, key, event, db=None):
        """Why a current reaction no longer supports a result, if it does not."""
        if event["removed"] is not None or event["target_deleted"]:
            return "changed"
        if self.store.clock() >= event["added"] + LIFETIME:
            return "expired"
        return None

    def sources_current(self, record, db=None):
        return context.current(self.store, record, db=db) and all(
            derived(self.store, k, db) == digest
            for k, digest in record.get("derived", {}).items()
        )

    def pending(self, key):
        return bool(
            self.store.rows(
                "SELECT id FROM jobs WHERE role=? AND scope=? "
                "AND state IN ('queued','claimed','running') LIMIT 1",
                (ROLE, key),
            )
        )

    # ---- scheduling --------------------------------------------------------

    def schedule(self, key):
        """A confirmed qualifying change: (re)start this chat's 30 quiet
        seconds. Coalesced, so a burst makes at most one call."""
        try:
            if not self.app.rewriting.chat_eligible(key):
                return
        except Refused:
            return
        due = self.store.clock() + QUIET
        with self.store.transaction() as db:
            if not db.execute(
                "UPDATE jobs SET due=? WHERE role=? AND scope=? AND state='queued'",
                (due, ROLE, key),
            ).rowcount:
                db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,'queued',?,?,NULL)",
                    (
                        uuid.uuid4().hex,
                        key,
                        ROLE,
                        "",
                        due,
                        json.dumps({"class": "reaction"}),
                    ),
                )

    def evidence_changed(self, message):
        """Automatic handling of changed evidence: an edit or deletion of a
        message the current assessment used schedules a new pass."""
        key = f"{message.account_id}/{message.peer}"
        record = self.record(key)
        if record and message.key in record.get("inputs", {}):
            self.schedule(key)

    def packet(self, key, event):
        """Bounded, cached-only input: the reaction, its exact target and the
        cached conversation as it stood when the reaction was added. No
        Telegram read or media preparation."""
        target = self.store.message(key, event["message_id"])
        if target is None or target.deleted:
            raise Refused("The reacted-to message is no longer available")
        recent = [
            Message.model_validate_json(r["body"])
            for r in self.store.rows(
                "SELECT body FROM messages WHERE chat_key=? AND deleted=0 "
                "AND json_extract(body,'$.service')=0 AND message_id!=? "
                "AND date>=? AND date<=? ORDER BY message_id DESC LIMIT ?",
                (
                    key,
                    target.message_id,
                    event["added"] - WINDOW,
                    event["added"],
                    CONTEXT_MESSAGES,
                ),
            )
        ]
        recent.sort(key=lambda m: m.message_id)
        items = [cached(self.store, m) for m in recent]
        packet = {
            "reaction": {
                "emoji": event["emoji"],
                "added": event["added"],
                "target_id": target.message_id,
                "by": "recipient",
            },
            "target": cached(self.store, target),
            "context": items,
        }
        while (
            len(json.dumps(packet, ensure_ascii=False).encode()) > PACKET_BYTES
            and packet["context"]
        ):
            packet["context"].pop(0)
        if len(json.dumps(packet, ensure_ascii=False).encode()) > PACKET_BYTES:
            raise Refused("The reacted-to message exceeds the assessment bound")
        kept = {m["id"] for m in packet["context"]} | {target.message_id}
        sources = [m for m in recent if m.message_id in kept] + [target]
        dependencies = {
            "inputs": {m.key: m.revision for m in sources},
            "attribution_inputs": {m.key: attribution(m) for m in sources},
            "transient": [],
            "derived": {m.key: derived(self.store, m.key) for m in sources},
        }
        return packet, dependencies

    # ---- generation --------------------------------------------------------

    def _job(self, job, state, error=None, *, due=None):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state=?,error=?,due=coalesce(?,due) WHERE id=? "
                "AND state IN ('queued','claimed')",
                (state, error, due, job["id"]),
            )

    async def run(self, job):
        key = job["scope"]
        rows = self.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not rows or rows[0]["state"] not in ("queued", "claimed"):
            return
        try:
            self.app.admit_ai()
            if not self.app.rewriting.chat_eligible(key):
                self._job(job, "superseded", "This chat is no longer eligible")
                return
            kind, event = self.current(key)
            if kind != "reaction" or self.withdrawn(key, event):
                # Newer communication, a removal or expiry: nothing to assess.
                self._job(job, "done", "No current reaction to assess")
                return
            packet, dependencies = self.packet(key, event)
            processed = hashlib.sha256(
                json.dumps([event["id"], dependencies], sort_keys=True).encode()
            ).hexdigest()
            record = self.record(key)
            if record and record["processed"] == processed:
                if record["state"] in ("ready", "failed"):
                    # An unchanged reaction and context reuse their outcome,
                    # even a failed one: nothing is resampled automatically.
                    self._job(job, "done", "Unchanged reaction outcome reused")
                    return
                if record["attempt"] != job["id"] and self.store.rows(
                    "SELECT id FROM jobs WHERE id=? AND state IN ('queued','claimed','running')",
                    (record["attempt"],),
                ):
                    # The same input is already being assessed.
                    self._job(job, "done", "The same reaction is already assessed")
                    return
                # Otherwise the pending attempt never reached the provider (a
                # wait, a pause or a restart before dispatch): run it now.
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE reaction_events SET subject=coalesce(subject,?) WHERE id=?",
                    (self.store.clock(), event["id"]),
                )
                db.execute(
                    "INSERT OR REPLACE INTO reaction_state VALUES (?,?,?)",
                    (
                        key,
                        event["added"],
                        json.dumps(
                            {
                                "event": event["id"],
                                "added": event["added"],
                                "emoji": event["emoji"],
                                "target": event["source"],
                                "target_id": event["message_id"],
                                "processed": processed,
                                "attempt": job["id"],
                                "state": "pending",
                                "code": None,
                                "useful": None,
                                "failure": None,
                                **dependencies,
                            }
                        ),
                    ),
                )

            def valid():
                kind, now = self.current(key)
                owned = self.record(key)
                return bool(
                    self.app.authorized()
                    and self.app.rewriting.chat_eligible(key)
                    and kind == "reaction"
                    and now["id"] == event["id"]
                    and not self.withdrawn(key, now)
                    and owned
                    and owned["attempt"] == job["id"]
                    and self.sources_current(owned)
                )

            # One decision request (ADR 30): the emotion code and whether
            # optional reply wording is useful now.
            request = {"state": decision_state.reaction(packet)}
            result = await self.app.rewriting.work.run(
                self.app.decider, job, request, valid, klass="reaction"
            )
            # Read before the transaction: the registry and the store's own
            # projections must not open a nested transaction.
            current = valid()
            suggestions = self.app.suggestions.enabled()
            with self.store.transaction() as db:
                owned = self.record(key, db)
                kind, now = self.current(key, db)
                if not (
                    current
                    and owned
                    and owned["attempt"] == job["id"]
                    and kind == "reaction"
                    and now["id"] == event["id"]
                    and now["removed"] is None
                ):
                    # Costs are receipted; late output never replaces newer
                    # evidence or a withdrawn reaction.
                    db.execute(
                        "UPDATE jobs SET state='superseded',error='Inputs changed during assessment' WHERE id=?",
                        (job["id"],),
                    )
                    return
                owned.update(
                    state="ready",
                    code=result.emotion,
                    useful=result.suggestions_useful,
                    # Kept for diagnosis only, never displayed (ADR 30 Q6).
                    decision=result.decision.model_dump() if result.decision else None,
                    usefulness=result.usefulness,
                    guidance=request["guidance"]["version"],
                    defaults=request["guidance"].get("defaults_revision"),
                )
                db.execute(
                    "UPDATE reaction_state SET body=? WHERE chat_key=?",
                    (json.dumps(owned), key),
                )
                if (
                    suggestions
                    and result.suggestions_useful
                    and result.emotion in QUALIFYING
                ):
                    self.app.suggestions.schedule_reaction(
                        db, key, owned, packet, result.emotion
                    )
        except ProviderRefused as refusal:
            self.refused(job, refusal)
        except asyncio.CancelledError:
            raise
        except Refused as error:
            if self.store.rows("SELECT id FROM reservations WHERE id=?", (job["id"],)):
                # The fixed content-free diagnostic of the receipt (ADR 30 Q8).
                self.fail(key, job["id"], self.app.emotion.diagnostic(job))
            elif isinstance(error, ProcessingPaused):
                self._job(job, "queued", str(error))
            elif "budget" in str(error).lower():
                self._job(job, "queued", str(error), due=self.store.clock() + 60)
            else:
                self._job(job, "failed", str(error))
                self.fail(key, job["id"], "provider")

    def fail(self, key, attempt, reason):
        with self.store.transaction() as db:
            record = self.record(key, db)
            if record and record["attempt"] == attempt and record["state"] == "pending":
                record.update(state="failed", failure=reason)
                db.execute(
                    "UPDATE reaction_state SET body=? WHERE chat_key=?",
                    (json.dumps(record), key),
                )

    def refused(self, job, refusal):
        """No provider work occurred: a new attempt after the provider's
        backoff or 30/60/120 seconds, at most three times (ADR 15)."""
        payload = json.loads(job["payload"] or "{}")
        count = payload.get("refusals", 0) + 1
        if count > 3:
            self.fail(job["scope"], job["id"], "refused")
            return
        delay = (
            refusal.retry_after
            if refusal.retry_after is not None
            else min(30 * 2 ** (count - 1), 600)
        )
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,'queued',?,?,NULL)",
                (
                    uuid.uuid4().hex,
                    job["scope"],
                    ROLE,
                    "",
                    self.store.clock() + delay,
                    json.dumps({"class": "reaction", "refusals": count}),
                ),
            )
            # The retained processed digest is dropped so the new attempt runs.
            row = db.execute(
                "SELECT body FROM reaction_state WHERE chat_key=?", (job["scope"],)
            ).fetchone()
            if row:
                record = json.loads(row[0])
                if record["attempt"] == job["id"]:
                    db.execute(
                        "DELETE FROM reaction_state WHERE chat_key=?", (job["scope"],)
                    )
