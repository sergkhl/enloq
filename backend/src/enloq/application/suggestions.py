"""Response Suggestions: one source-bound outcome per chat (ADR 25).

Automatic input comes from a validated incoming result; manual input captures
the draft and bounded cached conversation on request. Neither path reads
Telegram or prepares media. Both read enabled memory at dispatch, use the
chat's captured Style and change the draft only on selection: a manual option
replaces its text, an automatic one is inserted at the caret.
"""

import asyncio
import hashlib
import json
import logging
import uuid

from pydantic import ValidationError

from ..domain import suggestions as domain
from ..domain.models import Conflict, ProcessingPaused, Refused
from ..domain.selected_media import sticker_selected
from ..domain.suggestions import (
    CALM_USEFULNESS,
    EXPIRY,
    QUALIFYING,
    ResponseSuggestions,
    SuggestionOption,
    SuggestionsView,
)
from ..inference.base import ProviderRefused
from ..storage.store import RETENTION, blank_draft
from ..storage.suggestions import settle
from . import context, decision_state

_LOG = logging.getLogger("enloq.suggestions")

# The incoming request's enriched-text cap plus the four fresh image meanings
# (at most 2,000 characters each) that replace image bytes in this request.
PACKET_BYTES = 32000 + 4 * 6000
# A reminder set reads the 20 newest cached messages of the 30-day cache
# (ADR 58 Q6), within a wider byte bound than an ordinary manual request.
REMINDER_CONTEXT_BYTES = 12000


def prepared(source, fresh):
    """Original wording, local transcript and image meaning only."""
    value = {
        k: source.get(k)
        for k in (
            "id",
            "date",
            "outgoing",
            "text",
            "media",
            "reply_to",
            "forwarded",
            "forwarded_from",
        )
    }
    for field in ("forward_date", "forward_signature", "imported"):
        if source.get(field) is not None:
            value[field] = source[field]
    if source.get("links"):
        value["links"] = source["links"]
    transcript = source.get("transcript")
    if transcript and transcript.get("text"):
        value["transcript"] = {
            "text": transcript["text"],
            "language": transcript.get("language"),
            "machine_generated": True,
        }
    meaning = fresh.get(source["id"]) or source.get("image_context")
    if meaning:
        value["image_context"] = meaning
    if source.get("placeholder"):
        value["placeholder"] = source["placeholder"]
    return value


def splice(text, option, cursor):
    """Insert at a browser caret (UTF-16 code units) or, when none is known or
    it no longer fits, at the end. Existing characters are kept; one space
    separates the option from adjacent words. Returns the text and new caret."""
    before, after = domain.split_draft(text, cursor)
    if (
        before
        and not before[-1].isspace()
        and option
        and not option[0].isspace()
        and option[0] not in ".,;:!?)]}"
    ):
        option = " " + option
    if (
        after
        and not after[0].isspace()
        and after[0] not in ".,;:!?)]}"
        and option
        and not option[-1].isspace()
    ):
        option += " "
    return before + option + after, len((before + option).encode("utf-16-le")) // 2


class Suggestions:
    def __init__(self, app):
        self.app, self.store = app, app.store
        # The account manager installs the global Suggestions switch.
        self.enabled = lambda: True

    # ---- reading ---------------------------------------------------------

    def read(self, sql, args, db=None):
        return (
            [dict(row) for row in db.execute(sql, args)]
            if db
            else self.store.rows(sql, args)
        )

    def automatic(self, key, db=None):
        rows = self.read(
            "SELECT automatic,style FROM suggestion_settings WHERE chat_key=?",
            (key,),
            db,
        )
        return (
            {"automatic": bool(rows[0]["automatic"]), "style": rows[0]["style"]}
            if rows
            else {"automatic": True, "style": "friendly"}
        )

    def settings(self, key):
        self.private(key)
        return self.automatic(key)

    def set_settings(self, key, automatic, style):
        self.private(key)
        if style not in ("friendly", "professional", "idioms"):
            raise Refused("Unknown suggestion style")
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO suggestion_settings VALUES (?,?,?,?) ON CONFLICT(chat_key) DO UPDATE SET automatic=excluded.automatic,style=excluded.style,changed=excluded.changed WHERE automatic!=excluded.automatic OR style!=excluded.style",
                (key, int(automatic), style, self.store.clock()),
            )
        if not automatic:
            self.cancel_queued(key)
        self.app.reminders.tick()
        return self.settings(key)

    def permitted(self, key, record, db=None):
        return bool(
            record
            and (
                record.get("kind") == "manual"
                or (self.enabled() and self.automatic(key, db)["automatic"])
            )
        )

    def diagnose(self, record=None, *, reason, **values):
        _LOG.debug(
            "suggestion_decision",
            extra={
                "suggestion_decision": {
                    "attempt": record and record.get("attempt"),
                    "trigger": "manual"
                    if record and record.get("kind") == "manual"
                    else "automatic",
                    "reason": reason,
                    **values,
                }
            },
        )

    def manual_current(self, key, record, db=None):
        if record.get("reminders") is not None:
            if db is None:
                with self.store.transaction() as db:
                    return self.manual_current(key, record, db)
            if not self.reminder_fits(key, record, db):
                return False
        rows = self.read("SELECT body FROM drafts WHERE chat_key=?", (key,), db)
        draft = json.loads(rows[0]["body"]) if rows else blank_draft(key)
        latest = self.read(
            "SELECT coalesce(max(message_id),0) AS identity FROM messages WHERE chat_key=?",
            (key,),
            db,
        )[0]["identity"]
        count = self.read(
            "SELECT count(*) AS count FROM messages WHERE chat_key=? AND date>=?",
            (key, record["context_since"]),
            db,
        )[0]["count"]
        return (
            draft["revision"] == record["draft_revision"]
            and latest == record["latest_message"]
            and count == record["context_count"]
            and self.sources_current(record, db)
        )

    def reminder_fits(self, key, record, db):
        """A reminder set fits while it covers every occurrence of the chat
        and its Kept Messages are as they were (ADR 58 Q5)."""
        covered = self.app.reminders.covered(key, db)
        return [o["id"] for o in covered] == record[
            "reminders"
        ] and self.app.reminders.kept_identity(db, record["schedules"]) == (
            record["kept"]
        )

    def generate(self, key, revision):
        self.private(key)
        self.app.admit_ai()
        if not self.app.authorized():
            raise Refused("Account is signed out")
        if self.app.reminders.owns(key):
            # With reminders pending, the button requests their set (Q5).
            return self.reminder_set(key, "press", revision)
        now = self.store.clock()
        with self.store.transaction() as db:
            rows = self.read("SELECT body FROM drafts WHERE chat_key=?", (key,), db)
            draft = json.loads(rows[0]["body"]) if rows else blank_draft(key)
            if draft["revision"] != revision:
                raise Conflict("Draft changed; keep your edits and reload")
            if draft.get("state") == "rewriting" or sticker_selected(draft):
                raise Conflict("The composer is busy")
            if len(draft["text"].encode()) > context.TARGET_BYTES:
                raise Refused(
                    "Draft exceeds the suggestion input bound; your text is retained"
                )
            previous = self.record(key, db)
            if (
                previous
                and previous.get("kind") == "manual"
                and previous["state"] == "pending"
                and previous.get("reminders") is None
                and not self.withdrawn(key, previous, db)
            ):
                return SuggestionsView(
                    state="preparing",
                    reason="queued",
                    kind="manual",
                    style=previous["style"],
                    attempt=previous["attempt"],
                    source_time=previous["source_time"],
                    expires_at=previous["source_time"] + EXPIRY,
                )
            captured = context.cached_recent(
                self.store, key, reply_id=draft.get("reply_to"), db=db
            )
            messages = captured.pop("messages")
            if not messages and not draft["text"].strip():
                raise Refused("Write a draft or wait for conversation context")
            latest = self.read(
                "SELECT coalesce(max(message_id),0) AS identity FROM messages WHERE chat_key=?",
                (key,),
                db,
            )[0]["identity"]
            attempt = uuid.uuid4().hex
            record = {
                "kind": "manual",
                "source": attempt,
                "attempt": attempt,
                "source_time": now,
                "state": "pending",
                "failure": None,
                "refusals": 0,
                "draft_revision": revision,
                "latest_message": latest,
                "context_since": now - 7 * 86400,
                "context_count": self.read(
                    "SELECT count(*) AS count FROM messages WHERE chat_key=? AND date>=?",
                    (key, now - 7 * 86400),
                    db,
                )[0]["count"],
                **captured,
                "derived": {
                    k: self.app.emotion.derived(k, db) for k in captured["inputs"]
                },
                "sources": [m["id"] for m in messages],
                "emotion": "unknown",
                "style": self.automatic(key, db)["style"],
                "packet": {
                    "messages": [prepared(m, {}) for m in messages],
                    "context": [],
                    "manual": True,
                    "draft": draft["text"],
                },
            }
            record["idioms"] = record["style"] == "idioms"
            db.execute(
                "UPDATE jobs SET state='superseded',error='Replaced by a manual request' WHERE role='response_suggestions' AND scope=? AND state='queued'",
                (key,),
            )
            record = self.store_record(db, key, record)
            if record["state"] == "pending":
                self.enqueue(db, key, record, attempt, now)
            self.diagnose(
                record,
                reason="requested"
                if record["state"] == "pending"
                else "input_too_large",
            )
        return self.view(key)

    def reminder_set(self, key, trigger, revision=None):
        """One manual set covering every occurrence of the chat (ADR 58 Q5,
        Q6): prepared two minutes before the alert, refreshed after quiet
        seconds, replaced on opening only when it no longer fits, and
        requested by the Suggestions button. Only a press resamples an
        unchanged input; an unchanged failure is never prepared again."""
        press = trigger == "press"
        if not press:
            try:
                self.private(key)
                self.app.admit_ai()
            except (Refused, Conflict):
                return self.view(key)
            if not self.app.authorized():
                return self.view(key)
        with self.store.transaction() as db:
            self.prepare_reminder_set(db, key, trigger, revision)
        return self.view(key)

    def prepare_reminder_set(self, db, key, trigger, revision):
        press = trigger == "press"
        now = self.store.clock()
        covered = self.app.reminders.covered(key, db)
        if not covered:
            if press:
                raise Conflict("Reminders changed; reload the conversation")
            return
        rows = self.read("SELECT body FROM drafts WHERE chat_key=?", (key,), db)
        draft = json.loads(rows[0]["body"]) if rows else blank_draft(key)
        if revision is not None and draft["revision"] != revision:
            raise Conflict("Draft changed; keep your edits and reload")
        busy = draft.get("state") == "rewriting" or sticker_selected(draft)
        large = len(draft["text"].encode()) > context.TARGET_BYTES
        if press and busy:
            raise Conflict("The composer is busy")
        if press and large:
            raise Refused(
                "Draft exceeds the suggestion input bound; your text is retained"
            )
        if busy or large:
            return
        captured = context.cached_recent(
            self.store,
            key,
            reply_id=draft.get("reply_to"),
            db=db,
            since=RETENTION,
            budget=REMINDER_CONTEXT_BYTES,
        )
        messages = captured.pop("messages")
        schedules = list(dict.fromkeys(o["schedule"] for o in covered))
        kept, kept_identity = self.app.reminders.kept(db, schedules)
        latest = self.read(
            "SELECT coalesce(max(message_id),0) AS identity FROM messages WHERE chat_key=?",
            (key,),
            db,
        )[0]["identity"]
        since = now - RETENTION
        count = self.read(
            "SELECT count(*) AS count FROM messages WHERE chat_key=? AND date>=?",
            (key, since),
            db,
        )[0]["count"]
        identity = hashlib.sha256(
            json.dumps(
                [
                    [o["id"] for o in covered],
                    draft["revision"],
                    latest,
                    count,
                    captured["inputs"],
                    captured["transcript_inputs"],
                    captured["image_inputs"],
                    kept_identity,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        previous = self.record(key, db)
        if (
            previous
            and previous.get("reminders") is not None
            and not self.withdrawn(key, previous, db)
        ):
            if press and previous["state"] == "pending":
                # Duplicate requests share the active attempt (ADR 25).
                return
            if (
                not press
                and previous.get("identity") == identity
                and previous["state"] != "cancelled"
            ):
                self.diagnose(previous, reason="reminder_unchanged", source=trigger)
                return
        # Messages that arrived after a reminder fell due are answered;
        # earlier ones are reference only (Q6).
        first = covered[0]["due_at"]
        attempt = uuid.uuid4().hex
        record = {
            "kind": "manual",
            "reminders": [o["id"] for o in covered],
            "schedules": schedules,
            "kept": kept_identity,
            "identity": identity,
            "trigger": trigger,
            "source": attempt,
            "attempt": attempt,
            "source_time": now,
            "state": "pending",
            "failure": None,
            "refusals": 0,
            "draft_revision": draft["revision"],
            "latest_message": latest,
            "context_since": since,
            "context_count": count,
            **captured,
            "derived": {k: self.app.emotion.derived(k, db) for k in captured["inputs"]},
            "sources": [m["id"] for m in messages],
            "emotion": "unknown",
            "style": self.automatic(key, db)["style"],
            "packet": {
                "manual": True,
                "draft": draft["text"],
                "reminders": [
                    {
                        "intent": "message_idea" if o["idea"].strip() else "check_in",
                        "idea": o["idea"],
                        "fallback_language": o["language"],
                    }
                    for o in covered
                ],
                "messages": [prepared(m, {}) for m in messages if m["date"] >= first],
                "context": [prepared(m, {}) for m in messages if m["date"] < first],
                "kept": kept,
                "fallback_language": covered[0]["language"],
            },
        }
        record["idioms"] = record["style"] == "idioms"
        db.execute(
            "UPDATE jobs SET state='superseded',error='Replaced by a reminder set' WHERE role='response_suggestions' AND scope=? AND state='queued'",
            (key,),
        )
        record = self.store_record(db, key, record)
        if record["state"] == "pending":
            self.enqueue(db, key, record, attempt, now)
        self.diagnose(
            record,
            reason="requested" if record["state"] == "pending" else "input_too_large",
            source=trigger,
        )

    def open_reminders(self, key):
        """Opening a chat with pending reminders prepares their set when the
        current one no longer fits (ADR 58 Q5)."""
        self.private(key)
        if self.app.reminders.owns(key):
            return self.reminder_set(key, "open")
        return self.view(key)

    def record(self, key, db=None):
        rows = self.read(
            "SELECT body FROM suggestion_state WHERE chat_key=?", (key,), db
        )
        return json.loads(rows[0]["body"]) if rows else None

    def answered(self, key, record, db=None):
        """Any later outgoing message, including one sent from another client
        or since deleted, answers the block; deletion never revives options.
        So does an own reaction, from any client, on one of the block's
        messages, first observed after its latest one (ADR 49 Q12); removing
        it revives nothing either. A reaction is answered by outgoing text or
        images from its native addition time on; the owner's own reactions
        never answer it."""
        if record.get("kind") == "reaction":
            return bool(
                self.read(
                    "SELECT 1 FROM messages WHERE chat_key=? AND date>=? "
                    "AND json_extract(body,'$.outgoing')=1 "
                    "AND json_extract(body,'$.service')=0 LIMIT 1",
                    (key, record["source_time"]),
                    db,
                )
            )
        return bool(
            self.read(
                "SELECT 1 FROM messages WHERE chat_key=? AND message_id>? "
                "AND json_extract(body,'$.outgoing')=1 "
                "AND json_extract(body,'$.service')=0 LIMIT 1",
                (key, record["barrier"]["message_id"]),
                db,
            )
            or self.read(
                "SELECT 1 FROM own_reactions WHERE chat_key=? AND observed>=? "
                "AND message_id IN (SELECT value FROM json_each(?)) LIMIT 1",
                (key, record["source_time"], json.dumps(record["sources"])),
                db,
            )
        )

    def suspended(self, key, record, db=None):
        """A submission durably accepted after this source suspends its options
        (ADR 26). Native confirmation then answers them; a definite non-send or
        Cancel restores still-current options without another call, while an
        uncertain delivery keeps them suspended."""
        return bool(
            self.read(
                "SELECT 1 FROM operations WHERE chat_key=? "
                "AND state IN ('queued','dispatching','unknown','confirmed') "
                "AND json_extract(body,'$.submitted')=1 "
                "AND json_extract(body,'$.created')>=? LIMIT 1",
                (key, record["source_time"]),
                db,
            )
        )

    def sources_current(self, record, db=None):
        return context.current(self.store, record, db=db) and all(
            self.app.emotion.derived(k, db) == digest
            for k, digest in record["derived"].items()
        )

    def withdrawn(self, key, record, db=None):
        """Why a record no longer belongs to the conversation, if it does not."""
        if record.get("kind") == "manual":
            if self.store.clock() >= record["source_time"] + EXPIRY:
                return "expired"
            return None if self.manual_current(key, record, db) else "changed"
        kind, event = self.app.assessment.current(key, db)
        if record.get("kind") == "reaction":
            if kind != "reaction" or event["id"] != record["event"]:
                return "replaced"
            if event["removed"] is not None or event["target_deleted"]:
                return "changed"
        elif kind == "reaction" or record["barrier"] != self.app.emotion.latest(
            key, db=db
        ):
            return "replaced"
        if self.store.clock() >= record["source_time"] + EXPIRY:
            return "expired"
        if self.answered(key, record, db):
            return "answered"
        if not self.sources_current(record, db):
            return "changed"
        return None

    def qualifies(self, key, record, signal=None, db=None):
        """ADR 56, the owner's "either Emotional or Useful": an incoming
        block's options need a usable non-neutral emotion or, while its
        decision is current, its emotion known or not, usefulness at
        CALM_USEFULNESS or more (Q19 A). A reaction's were scheduled only
        after a yes and keep needing a non-neutral emotion too (Q13 A)."""
        if record.get("kind") == "manual":
            return True
        signal = signal or self.app.emotion.view(key)
        emotional = signal.reason == "available" and signal.code in QUALIFYING
        if emotional or record.get("kind") == "reaction":
            return emotional
        return (
            signal.reason in ("available", "uncertain")
            and self.app.emotion.usefulness(key, db) >= CALM_USEFULNESS
        )

    def view(self, key):
        """The shared validity projection. It never queues work, reads
        Telegram or prepares media."""
        record = self.record(key)
        if not self.permitted(key, record):
            return SuggestionsView(reason="off" if record else "unavailable")
        if not record:
            return SuggestionsView(reason="unavailable")
        times = dict(
            kind="manual" if record.get("kind") == "manual" else "automatic",
            style=record.get("style", "idioms" if record.get("idioms") else "friendly"),
            source_time=record["source_time"],
            expires_at=record["source_time"] + EXPIRY,
        )
        if record.get("reminders") is None and self.app.reminders.owns(key):
            # A reminder set owns the area while any is pending (ADR 58 Q5).
            return SuggestionsView(reason="replaced", **times)
        if reason := self.withdrawn(key, record):
            return SuggestionsView(reason=reason, **times)
        if self.suspended(key, record):
            return SuggestionsView(reason="suspended", **times)
        if not self.qualifies(key, record):
            return SuggestionsView(reason="not_qualifying", **times)
        state = record["state"]
        if state == "ready":
            if not record["options"]:
                return SuggestionsView(reason="empty", **times)
            return SuggestionsView(
                state="ready",
                attempt=record["attempt"],
                options=record["options"],
                folded=bool(record.get("folded")),
                **times,
            )
        if state == "failed":
            return SuggestionsView(state="failed", reason=record["failure"], **times)
        if state != "pending":
            return SuggestionsView(reason="cancelled", **times)
        return self.progress(record, times)

    def progress(self, record, times):
        times = {**times, "attempt": record["attempt"]}
        rows = self.store.rows(
            "SELECT state,error FROM jobs WHERE id=?", (record["attempt"],)
        )
        if (
            rows
            and rows[0]["state"] == "done"
            and self.store.rows(
                "SELECT 1 FROM jobs WHERE role='suggestion_labels' "
                "AND state IN ('claimed','running') "
                "AND json_extract(payload,'$.suggestion')=? LIMIT 1",
                (record["attempt"],),
            )
        ):
            # Written, and the decision model is labelling it (ADR 49 Q19).
            return SuggestionsView(state="preparing", reason="running", **times)
        if not rows or rows[0]["state"] not in ("queued", "claimed", "running"):
            return SuggestionsView(reason="cancelled", **times)
        if rows[0]["state"] == "running":
            return SuggestionsView(state="preparing", reason="running", **times)
        try:
            self.app.admit_ai()
        except Exception:
            return SuggestionsView(state="waiting", reason="paused", **times)
        error = rows[0]["error"] or ""
        if "budget" in error.lower():
            return SuggestionsView(state="waiting", reason="budget", **times)
        if record.get("refusals"):
            return SuggestionsView(state="waiting", reason="retrying", **times)
        return SuggestionsView(state="preparing", reason="queued", **times)

    # ---- preparation -----------------------------------------------------

    def capture(self, db, block, packet, image_context, code, attempt):
        """The prepared snapshot and dependency identity of one validated
        incoming result, read in its ready transaction after image derivatives
        and emotion publication. Memory, raw images and guidance stay out."""
        key = block["chat_key"]
        fresh = {item.message_id: item.text for item in image_context}
        provider = {
            "messages": [prepared(m, fresh) for m in packet["messages"]],
            "context": [prepared(m, fresh) for m in packet.get("context", [])],
            "direct_reply": packet.get("direct_reply"),
        }
        dependencies = {
            "inputs": dict(packet["inputs"]),
            "transient": list(packet.get("transient", [])),
            "transient_inputs": dict(packet.get("transient_inputs", {})),
            "transcript_inputs": dict(packet.get("transcript_inputs", {})),
            "image_inputs": dict(packet.get("image_inputs", {})),
            "attribution_inputs": dict(packet.get("attribution_inputs", {})),
            "derived": {k: self.app.emotion.derived(k, db) for k in packet["inputs"]},
        }
        barrier = self.app.emotion.latest(key, db=db)
        identity = hashlib.sha256(
            json.dumps(
                {
                    "block": block["id"],
                    "revision": block["revision"],
                    "barrier": barrier,
                    **dependencies,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return {
            "source": identity,
            "block": block["id"],
            "barrier": barrier,
            "sources": [m["id"] for m in packet["messages"]],
            "source_time": max(m["date"] for m in packet["messages"]),
            "emotion": code,
            "attempt": attempt,
            "state": "pending",
            "failure": None,
            "refusals": 0,
            "packet": provider,
            **dependencies,
        }

    def reusable(self, existing, record):
        """An unchanged source keeps its outcome, including an empty or failed
        one and work still in progress; only no-work cancellation is replaced."""
        return (
            existing is not None
            and existing["source"] == record["source"]
            and existing["state"] != "cancelled"
        )

    def store_record(self, db, key, record):
        if len(json.dumps(record["packet"], ensure_ascii=False).encode()) > (
            PACKET_BYTES
        ):
            record = {k: v for k, v in record.items() if k != "packet"}
            record.update(state="failed", failure="too_large")
        db.execute(
            "INSERT OR REPLACE INTO suggestion_state VALUES (?,?,?)",
            (key, record["source_time"], json.dumps(record)),
        )
        return record

    def publish(self, db, key, attempt, options, guidance, *, defaults=None):
        """Complete validated options only, while this attempt still owns a
        current record. Late output never restores withdrawn options."""
        record = self.record(key, db)
        if (
            not record
            or record["attempt"] != attempt
            or record["state"] != "pending"
            or self.withdrawn(key, record, db)
        ):
            return False
        record = {k: v for k, v in record.items() if k != "packet"}
        self.diagnose(record, reason="published", options=len(options))
        record.update(
            state="ready",
            options=[option.model_dump() for option in options],
            guidance=guidance,
            defaults=defaults,
            failure=None,
        )
        db.execute(
            "UPDATE suggestion_state SET body=? WHERE chat_key=?",
            (json.dumps(record), key),
        )
        return True

    def fail(self, db, key, attempt, reason):
        record = self.record(key, db)
        if record and record["attempt"] == attempt and record["state"] == "pending":
            self.diagnose(record, reason="failed", outcome=reason)
            settle(db, key, record, "failed", reason)

    def cancel(self, db, key, attempt, reason=None):
        record = self.record(key, db)
        if record and record["attempt"] == attempt and record["state"] == "pending":
            self.diagnose(record, reason="cancelled", outcome=reason)
            settle(db, key, record, "cancelled", reason)

    # ---- automatic scheduling ----------------------------------------------

    def schedule(self, db, block, job, packet, image_context=(), *, enabled):
        """Called in the Emotion Classification publication transaction (ADR
        30), with the classification job and the prepared incoming packet.

        Only normal new/changed incoming processing qualifies: deliberate
        Retry/Rewrite clicks and older-message runs never become suggestion
        controls. A failure here can never undo the emotion publication.
        ``enabled`` is read before the transaction: the shared registry must
        never be opened while this store transaction holds its lock."""
        if not enabled:
            self.diagnose({"attempt": job["id"]}, reason="automatic_off")
            return None
        db.execute("SAVEPOINT suggestion_schedule")
        try:
            record = self._schedule(db, block, job, packet, image_context)
        except Exception:
            db.execute("ROLLBACK TO suggestion_schedule")
            db.execute("RELEASE suggestion_schedule")
            _LOG.warning("suggestion_schedule_unavailable")
            self.diagnose(reason="schedule_unavailable")
            return None
        db.execute("RELEASE suggestion_schedule")
        return record

    def _schedule(self, db, block, job, packet, image_context):
        decision = {"attempt": job["id"]}
        payload = json.loads(job["payload"] or "{}")
        if payload.get("explicit_retry") or payload.get("class", "new") not in (
            "new",
            "catchup",
        ):
            self.diagnose(decision, reason="explicit_or_older_processing")
            return None
        key = block["chat_key"]
        if self.app.reminders.owns(key, db):
            self.diagnose(decision, reason="reminder_owns_area")
            return None
        previous = self.record(key, db)
        if (
            previous
            and previous.get("kind") == "manual"
            and not self.withdrawn(key, previous, db)
        ):
            self.diagnose(decision, reason="manual_owns_area")
            return None
        if self.app.assessment.current(key, db)[0] == "reaction":
            self.diagnose(decision, reason="newer_reaction")
            return None  # A newer reaction is the current source (ADR 26).
        row = db.execute(
            "SELECT body FROM emotion_state WHERE chat_key=?", (key,)
        ).fetchone()
        emotion = json.loads(row[0]) if row else None
        # This attempt must have just published a decision with a usable
        # non-neutral emotion or, for a calm block, usefulness at
        # CALM_USEFULNESS or more (ADR 56, either signal; Q19 A).
        self.diagnose(
            decision,
            reason="automatic_signal",
            emotion=emotion and emotion.get("code"),
            usefulness=emotion and emotion.get("usefulness"),
            probability=emotion and (emotion.get("decision") or {}).get("probability"),
            emotion_threshold=0.35,
            threshold=CALM_USEFULNESS,
        )
        if (
            not emotion
            or emotion["attempt"] != job["id"]
            or emotion.get("state", "ready") != "ready"
            or not (
                emotion["code"] in QUALIFYING
                or (emotion.get("usefulness") or 0) >= CALM_USEFULNESS
            )
        ):
            self.diagnose(decision, reason="no_current_qualifying_signal")
            return None
        settings = self.automatic(key, db)
        if not settings["automatic"]:
            self.diagnose(decision, reason="chat_automatic_off")
            return None
        attempt = uuid.uuid4().hex
        record = self.capture(
            db, block, packet, image_context, emotion["code"], attempt
        )
        if record["barrier"] != emotion["barrier"]:
            self.diagnose(record, reason="source_replaced")
            return None
        record["style"] = self.automatic(key, db)["style"]
        # The chat's choice when the set is scheduled (ADR 56 Q5 A): a later
        # change applies from the next block, never to this one.
        if settings["style"] == "idioms":
            record["idioms"] = True
        if self.reusable(self.record(key, db), record):
            self.diagnose(self.record(key, db), reason="unchanged_source")
            return None
        db.execute(
            "UPDATE jobs SET state='superseded',error='A newer source replaced this suggestion attempt' WHERE role='response_suggestions' AND scope=? AND state='queued'",
            (key,),
        )
        record = self.store_record(db, key, record)
        if record["state"] == "pending":
            self.enqueue(db, key, record, attempt, self.store.clock())
            self.diagnose(record, reason="scheduled", style=record["style"])
        else:
            self.diagnose(record, reason="input_too_large")
        return record

    def schedule_reaction(self, db, key, assessment, packet, code):
        """After a validated Reaction Assessment found options useful: a
        separate bounded request grounded in the reaction, its exact target and
        the conversation as it stood (ADR 26). Called in the publication
        transaction; it never delays or undoes the assessment."""
        db.execute("SAVEPOINT reaction_suggestions")
        try:
            if self.app.reminders.owns(key, db):
                self.diagnose(reason="reminder_owns_area", source="reaction")
                db.execute("RELEASE reaction_suggestions")
                return None
            previous = self.record(key, db)
            if (
                previous
                and previous.get("kind") == "manual"
                and not self.withdrawn(key, previous, db)
            ):
                self.diagnose(previous, reason="manual_owns_area")
                db.execute("RELEASE reaction_suggestions")
                return None
            settings = self.automatic(key, db)
            if not settings["automatic"]:
                self.diagnose(reason="chat_automatic_off", source="reaction")
                db.execute("RELEASE reaction_suggestions")
                return None
            identity = hashlib.sha256(
                json.dumps(
                    ["reaction", assessment["event"], assessment["processed"]]
                ).encode()
            ).hexdigest()
            record = {
                "style": self.automatic(key, db)["style"],
                "kind": "reaction",
                "source": identity,
                "event": assessment["event"],
                "block": None,
                "barrier": {"event": assessment["event"]},
                "sources": [assessment["target_id"]],
                "source_time": assessment["added"],
                "emotion": code,
                "attempt": uuid.uuid4().hex,
                "state": "pending",
                "failure": None,
                "refusals": 0,
                "packet": {
                    "messages": [prepared(packet["target"], {})],
                    "context": [prepared(m, {}) for m in packet["context"]],
                    "direct_reply": None,
                    "reaction": {
                        "emoji": assessment["emoji"],
                        "target_id": assessment["target_id"],
                        "by": "recipient",
                    },
                },
                "inputs": dict(assessment["inputs"]),
                "transient": [],
                "transient_inputs": {},
                "transcript_inputs": {},
                "image_inputs": {},
                "attribution_inputs": dict(assessment["attribution_inputs"]),
                "derived": dict(assessment["derived"]),
            }
            if settings["style"] == "idioms":
                record["idioms"] = True
            if self.reusable(self.record(key, db), record):
                self.diagnose(self.record(key, db), reason="unchanged_source")
                db.execute("RELEASE reaction_suggestions")
                return None
            db.execute(
                "UPDATE jobs SET state='superseded',error='A newer source replaced this suggestion attempt' WHERE role='response_suggestions' AND scope=? AND state='queued'",
                (key,),
            )
            record = self.store_record(db, key, record)
            if record["state"] == "pending":
                self.enqueue(db, key, record, record["attempt"], self.store.clock())
                self.diagnose(
                    record, reason="scheduled", source="reaction", style=record["style"]
                )
        except Exception:
            db.execute("ROLLBACK TO reaction_suggestions")
            db.execute("RELEASE reaction_suggestions")
            _LOG.warning("suggestion_schedule_unavailable")
            self.diagnose(reason="schedule_unavailable", source="reaction")
            return None
        db.execute("RELEASE reaction_suggestions")
        return record

    def enqueue(self, db, key, record, attempt, due, refusals=0):
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,'queued',?,?,NULL)",
            (
                attempt,
                key,
                "response_suggestions",
                record["source"],
                due,
                json.dumps({"class": "suggestions", "refusals": refusals}),
            ),
        )

    def cancel_queued(self, key=None):
        """Suggestions Off stops new work; started calls may finish into cache."""
        with self.store.transaction() as db:
            for row in db.execute(
                "SELECT j.id,j.scope FROM jobs j JOIN suggestion_state s ON s.chat_key=j.scope WHERE j.role='response_suggestions' AND j.state='queued' AND coalesce(json_extract(s.body,'$.kind'),'automatic')!='manual' AND (? IS NULL OR j.scope=?)",
                (key, key),
            ).fetchall():
                db.execute(
                    "UPDATE jobs SET state='cancelled',error='Suggestions turned Off' WHERE id=? AND state='queued'",
                    (row["id"],),
                )
                self.cancel(db, row["scope"], row["id"])

    # ---- the chat's choice (ADR 56) ------------------------------------------

    def private(self, key):
        chat = self.store.chat(key)
        if chat.kind != "private" or not chat.ai_eligible:
            raise Refused("Suggestions are for private chats only")

    def fold(self, key, attempt, folded):
        """ADR 56 Q12 B: a shown set folded on one client stays folded on
        every client and after reloads; the next set arrives unfolded."""
        self.store.chat(key)
        with self.store.transaction() as db:
            record = self.record(key, db)
            if not record or record["attempt"] != attempt or record["state"] != "ready":
                raise Conflict("Suggestions changed; reload the conversation")
            if bool(record.get("folded")) != folded:
                record["folded"] = folded
                db.execute(
                    "UPDATE suggestion_state SET body=? WHERE chat_key=?",
                    (json.dumps(record), key),
                )
        return self.view(key)

    # ---- generation ------------------------------------------------------

    def eligible(self, key, record):
        return (
            (
                self.app.authorized()
                and self.store.chat(key).kind == "private"
                and self.store.chat(key).ai_eligible
            )
            if record and record.get("kind") == "manual"
            else self.app.rewriting.chat_eligible(key)
        )

    def owned(self, key, attempt):
        record = self.record(key)
        if not record or record["attempt"] != attempt or record["state"] != "pending":
            return None
        return record

    def dispatched(self, job):
        return bool(
            self.store.rows("SELECT id FROM reservations WHERE id=?", (job["id"],))
        )

    def _job(self, job, state, error, *, due=None):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state=?,error=?,due=coalesce(?,due) WHERE id=? AND state IN ('queued','claimed')",
                (state, error, due, job["id"]),
            )

    async def run(self, job):
        key = job["scope"]
        current = self.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not current or current[0]["state"] not in ("queued", "claimed"):
            return
        stale = False
        try:
            self.app.admit_ai()
            record = self.record(key)
            if not self.permitted(key, record):
                self._job(job, "cancelled", "Suggestions are Off")
                with self.store.transaction() as db:
                    self.cancel(db, key, job["id"])
                return
            record = self.owned(key, job["id"])
            if record is None:
                self._job(job, "superseded", "Suggestion source replaced")
                return
            reason = self.withdrawn(key, record)
            if reason or not self.eligible(key, record):
                # Freshness at dispatch: nothing was sent, nothing counts.
                self._job(job, "superseded", "Suggestion source is no longer current")
                with self.store.transaction() as db:
                    self.cancel(db, key, job["id"], reason or "ineligible")
                return
            packet = {
                **record["packet"],
                "recipient_emotion": record["emotion"],
                "style": record.get("style", "friendly"),
            }
            # ADR 55: memory as it stands at dispatch; it is not part of the
            # source, so a changed memory never regenerates options.
            if memory := self.app.memory.for_suggestions(key):
                packet["memory"] = memory
            # ADR 56: an idiom set, written by the gate's winner (Q16).
            engine = self.app.engine
            if record.get("idioms"):
                packet["idioms"] = True
                if domain.IDIOMS_WRITER == "muse":
                    engine = self.app.calmer

            def valid():
                nonlocal stale
                current = self.owned(key, job["id"])
                ok = bool(
                    self.app.authorized()
                    and self.permitted(key, current)
                    and self.eligible(key, current)
                    and current
                    and not self.withdrawn(key, current)
                )
                stale = stale or not ok
                return ok

            result = await self.app.rewriting.work.run(
                engine, job, packet, valid, klass="suggestions"
            )
            options = await self.label(job, key, record, result.options, valid)
            if options is None:
                return  # Labelling settled the attempt.
            # Read before the transaction: authorization lives in the registry.
            authorized = self.app.authorized()
            with self.store.transaction() as db:
                # Costs are already receipted; only current work may publish.
                if not (
                    authorized
                    and self.publish(
                        db,
                        key,
                        job["id"],
                        options,
                        packet["guidance"]["version"],
                        defaults=packet["guidance"].get("defaults_revision"),
                    )
                ):
                    db.execute(
                        "UPDATE jobs SET state='superseded',error='Inputs changed during generation' WHERE id=?",
                        (job["id"],),
                    )
                    # Processed output that cannot publish is never resampled.
                    if record := self.record(key, db):
                        if reason := self.withdrawn(key, record, db):
                            self.cancel(db, key, job["id"], reason)
                        else:
                            self.fail(db, key, job["id"], "interrupted")
        except ProviderRefused as refusal:
            self.refused(job, refusal)
        except asyncio.CancelledError:
            # Store recovery turns dispatched work into an interrupted outcome
            # and returns undispatched work to the queue.
            raise
        except Refused as error:
            if self.dispatched(job):
                reason = "format" if "structured output" in str(error) else "provider"
                with self.store.transaction() as db:
                    self.fail(db, key, job["id"], reason)
            elif isinstance(error, ProcessingPaused):
                self.diagnose(self.record(key), reason="waiting_paused")
                self._job(job, "queued", str(error))
            elif "budget" in str(error).lower():
                self.diagnose(self.record(key), reason="waiting_budget")
                self._job(job, "queued", str(error), due=self.store.clock() + 60)
            elif stale or self.store.rows(
                "SELECT id FROM jobs WHERE id=? AND state IN ('superseded','cancelled')",
                (job["id"],),
            ):
                self._job(job, "superseded", "Suggestion source is no longer current")
                with self.store.transaction() as db:
                    self.cancel(db, key, job["id"], "changed")
            else:
                # A changed route contract fails visibly, without a Retry.
                self._job(job, "failed", str(error))
                with self.store.transaction() as db:
                    self.fail(db, key, job["id"], "provider")

    async def label(self, job, key, record, options, valid):
        """ADR 49 Q19: the decision model labels each option by what it says,
        since MiMo's own labels proved unreliable. Returns the labelled
        options, or None once a failure has settled the attempt. Processed
        output is never resampled. A set may hold just one answer stance."""
        if not options or not valid():
            # Nothing to label, or no longer current: publication decides.
            return options
        labels = {
            "id": uuid.uuid4().hex,
            "scope": key,
            "role": "suggestion_labels",
            "input_revision": record["source"],
            "state": "claimed",
            "due": self.store.clock(),
            "payload": json.dumps({"class": "suggestions", "suggestion": job["id"]}),
            "error": None,
        }
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", tuple(labels.values())
            )
        request = {
            "state": decision_state.suggestions(record["packet"], options),
            "replies": len(options),
        }
        try:
            purposes = await self.app.rewriting.work.run(
                self.app.decider, labels, request, valid, klass="suggestions"
            )
            return ResponseSuggestions.model_validate(
                {
                    "options": [
                        SuggestionOption(purpose=purpose, text=option.text)
                        for purpose, option in zip(purposes, options, strict=True)
                    ]
                },
            ).options
        except ValidationError:
            reason = "format"
        except ProviderRefused:
            reason = "refused"
        except Refused as error:
            dispatched = self.store.rows(
                "SELECT id FROM reservations WHERE id=?", (labels["id"],)
            )
            reason = (
                "format"
                if "structured output" in str(error)
                else "provider"
                if dispatched
                else "refused"
            )
        # Read before the transaction: the switch lives in the registry.
        enabled = self.enabled()
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='cancelled',error='Labelling never started' WHERE id=? AND state IN ('queued','claimed')",
                (labels["id"],),
            )
            if current := self.record(key, db):
                if withdrawn := self.withdrawn(key, current, db):
                    db.execute(
                        "UPDATE jobs SET state='superseded',error='Inputs changed during generation' WHERE id=?",
                        (job["id"],),
                    )
                    self.cancel(db, key, job["id"], withdrawn)
                elif not enabled and current.get("kind") != "manual":
                    self.cancel(db, key, job["id"])
                else:
                    self.fail(db, key, job["id"], reason)
        return None

    def refused(self, job, refusal):
        """No provider work occurred: re-queue a new attempt after the
        provider's backoff or 30/60/120 seconds, at most three times."""
        key = job["scope"]
        with self.store.transaction() as db:
            record = self.record(key, db)
            if (
                not record
                or record["attempt"] != job["id"]
                or record["state"] != "pending"
            ):
                return
            count = record.get("refusals", 0) + 1
            if count > 3:
                settle(db, key, record, "failed", "refused")
                return
            delay = (
                refusal.retry_after
                if refusal.retry_after is not None
                else min(30 * 2 ** (count - 1), 600)
            )
            attempt = uuid.uuid4().hex
            record.update(attempt=attempt, refusals=count)
            db.execute(
                "UPDATE suggestion_state SET body=? WHERE chat_key=?",
                (json.dumps(record), key),
            )
            self.enqueue(db, key, record, attempt, self.store.clock() + delay, count)

    # ---- owner selection -------------------------------------------------

    def insert(self, key, revision, attempt, index, cursor=None):
        """Put one current option into the current draft revision: a manual
        option, a complete message, replaces the draft's text, keeping its
        images, GIF and reply target, with the caret at its end; an automatic
        one is inserted at the caret (ADR 25 Q8).

        Validity, the chat's own record and the draft revision are checked in
        the mutation transaction, so a stale tab can neither use another
        conversation's options nor overwrite newer text. Selection never
        rewrites or sends; Undo restores the complete previous text."""
        self.store.chat(key)
        if not self.permitted(key, self.record(key)):
            raise Conflict("Automatic suggestions are Off; reload the conversation")
        # Read before the transaction, as the view reads it.
        signal = self.app.emotion.view(key)
        with self.store.transaction() as db:
            record = self.record(key, db)
            if (
                not record
                or record["attempt"] != attempt
                or record["state"] != "ready"
                or index >= len(record["options"])
                or self.withdrawn(key, record, db)
                or self.suspended(key, record, db)
                or not self.qualifies(key, record, signal, db)
                or (
                    record.get("reminders") is None and self.app.reminders.owns(key, db)
                )
            ):
                raise Conflict("Suggestions changed; choose from the current set")
            row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (key,)
            ).fetchone()
            draft = json.loads(row[0]) if row else blank_draft(key)
            if sticker_selected(draft):
                raise Refused("Remove the sticker before inserting a response")
            if draft.get("state") == "rewriting":
                raise Conflict("Stop or undo this rewrite before editing or sending")
            if draft["revision"] != revision:
                raise Conflict("Draft changed; keep your edits and reload")
            option = record["options"][index]["text"]
            if record.get("kind") == "manual":
                text, caret = option, len(option.encode("utf-16-le")) // 2
            else:
                text, caret = splice(draft["text"], option, cursor)
            if len(text) > 65536:
                raise Refused("The draft would exceed its length limit")
            draft.update(
                text=text,
                undo=draft["text"],
                state="inserted",
                attempt=None,
                error=None,
                revision=revision + 1,
            )
            # A send of a draft that took a reminder set's option completes its
            # occurrences once Telegram confirms it; edits keep the mark, Undo
            # and clearing drop it (ADR 58 Q3).
            if record.get("reminders"):
                draft["reminders"] = list(record["reminders"])
            else:
                draft.pop("reminders", None)
            db.execute(
                "INSERT OR REPLACE INTO drafts VALUES (?,?)", (key, json.dumps(draft))
            )
        return {"draft": draft, "caret": caret}
