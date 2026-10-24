"""One validity projection for the header's emotion (ADR 53: Adaptive left).

ADR 30: Emotion Classification is a separate decision request queued right
after an incoming rewrite publishes, from the same original wording, local
transcripts, fresh image meanings and bounded Supporting Context. The
rewrite's readiness never waits for it, and a failure pauses no role.

ADR 53 Q7: in a chat with calming on, the decision comes before the rewrite
instead, from the originals, and the rewrite and its readiness wait for it.
"""

import asyncio
import hashlib
import json
import logging
import uuid

from ..domain.emotion import LIFETIME, EmotionView
from ..domain.models import ProcessingPaused, Refused
from ..inference.base import ProviderRefused
from ..inference.work import BudgetReserve
from . import context, decision_state
from .suggestions import prepared

ROLE = "emotion_classification"
_LOG = logging.getLogger("enloq.emotion")


class Emotion:
    def __init__(self, app):
        self.app, self.store = app, app.store

    def read(self, sql, args, db=None):
        return (
            [dict(row) for row in db.execute(sql, args)]
            if db
            else self.store.rows(sql, args)
        )

    def latest(self, key, *, live=False, db=None):
        rows = self.read(
            "SELECT key,message_id,date,revision,deleted FROM messages WHERE chat_key=? "
            "AND json_extract(body,'$.outgoing')=0 AND json_extract(body,'$.service')=0 "
            + ("AND deleted=0 " if live else "")
            + "ORDER BY date DESC,message_id DESC LIMIT 1",
            (key,),
            db,
        )
        return rows[0] if rows else None

    def derived(self, key, db=None):
        rows = self.read("SELECT body FROM derivatives WHERE source=?", (key,), db)
        body = json.loads(rows[0]["body"]) if rows else {}
        value = {k: body.get(k) for k in ("transcript", "image_context")}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def capture(self, block, packet, attempt, db=None):
        return {
            "block": block["id"],
            "barrier": self.latest(block["chat_key"], db=db),
            "sources": [m["id"] for m in packet["messages"]],
            "source_time": max(m["date"] for m in packet["messages"]),
            "attempt": attempt,
            "inputs": dict(packet["inputs"]),
            "transient": packet.get("transient", []),
            "transient_inputs": packet.get("transient_inputs", {}),
            "transcript_inputs": packet.get("transcript_inputs", {}),
            "image_inputs": packet.get("image_inputs", {}),
            "attribution_inputs": packet.get("attribution_inputs", {}),
        }

    def sources_current(self, record, db=None):
        return context.current(self.store, record, db=db) and all(
            self.derived(key, db) == digest
            for key, digest in record.get("derived", {}).items()
        )

    def publishable(self, db, key, record):
        """The deletion-inclusive barrier prevents an older completed
        assessment from reviving after newer content is deleted. A deliberate
        later attempt captures that tombstone and may assess the remaining
        current block."""
        latest = self.latest(key, live=True, db=db)
        return bool(
            record["barrier"] == self.latest(key, db=db)
            and latest
            and latest["message_id"] in record["sources"]
            and context.current(self.store, record, db=db)
        )

    def publish(
        self,
        db,
        key,
        record,
        code,
        guidance,
        *,
        defaults=None,
        decision=None,
        useful=None,
        usefulness=None,
    ):
        """Publish a validated code while its block is still the current
        source; returns whether it published."""
        if not self.publishable(db, key, record):
            return False
        value = {k: v for k, v in record.items() if k != "prepared"}
        value.update(
            state="ready",
            code=code,
            guidance=guidance,
            defaults=defaults,
            failure=None,
            derived={k: self.derived(k, db) for k in record["inputs"]},
            # ADR 49 Q2: whether optional suggestions are useful now. It only
            # gates them and is never displayed.
            useful=useful,
            usefulness=usefulness,
        )
        if decision is not None:
            # Probabilities and confidence for diagnosis; never displayed.
            value["decision"] = decision
        db.execute(
            "INSERT OR REPLACE INTO emotion_state VALUES (?,?,?)",
            (key, record["source_time"], json.dumps(value)),
        )
        return True

    # ---- Emotion Classification (ADR 30) -----------------------------------

    def schedule(self, db, block, job, packet, image_context, record):
        """Called in the incoming ready transaction, after image derivatives:
        queue one classification in the followed work's class, only when this
        block can become the current source. A failure here never undoes the
        incoming publication."""
        db.execute("SAVEPOINT emotion_schedule")
        try:
            attempt = self._schedule(db, block, job, packet, image_context, record)
        except Exception:
            db.execute("ROLLBACK TO emotion_schedule")
            db.execute("RELEASE emotion_schedule")
            _LOG.warning("emotion_schedule_unavailable", exc_info=True)
            return None
        db.execute("RELEASE emotion_schedule")
        return attempt

    def _schedule(self, db, block, job, packet, image_context, record):
        followed = json.loads(job["payload"] or "{}")
        payload = {"class": followed.get("class", "new"), "block": block["id"]}
        if followed.get("explicit_retry"):
            payload["explicit_retry"] = True
        return self._queue(db, block, packet, image_context, record, payload, job["id"])

    def _queue(self, db, block, packet, image_context, record, payload, followed):
        key = block["chat_key"]
        if not self.publishable(db, key, record) or self.expired(record):
            # Never a paid call whose result could not be shown.
            return None
        fresh = {item.message_id: item.text for item in image_context}
        attempt = uuid.uuid4().hex
        value = {
            **record,
            "attempt": attempt,
            "followed": followed,
            "state": "pending",
            "code": None,
            "failure": None,
            "refusals": 0,
            "derived": {k: self.derived(k, db) for k in record["inputs"]},
            # Original wording, transcripts and image meanings only, never raw
            # images or Chat Memory. Suggestions reuse it after publication.
            "prepared": {
                "messages": [prepared(m, fresh) for m in packet["messages"]],
                "context": [prepared(m, fresh) for m in packet.get("context", [])],
                "direct_reply": packet.get("direct_reply"),
            },
        }
        db.execute(
            "UPDATE jobs SET state='superseded',error='A newer source replaced this classification' WHERE role=? AND scope=? AND state='queued' "
            # Another held block's calming decision is not this source's.
            "AND json_extract(payload,'$.calming') IS NULL",
            (ROLE, key),
        )
        self.enqueue(db, key, block["revision"], attempt, self.store.clock(), payload)
        db.execute(
            "INSERT OR REPLACE INTO emotion_state VALUES (?,?,?)",
            (key, record["source_time"], json.dumps(value)),
        )
        return attempt

    # ---- The decision before a calmed rewrite (ADR 53 Q7) ----------------

    async def decide_first(self, block, packet, followed, *, gate=None):
        """In a chat with calming on, the block's decision from its prepared
        originals before its one rewrite: wording, local transcripts and cached
        image meanings, never the meanings that rewrite adds. Returns the
        decision for ``publish_first``, or None when none was made: the
        rewrite then runs uncalmed and ADR 30's decision follows it. ``gate``
        is the rewrite's own check at this dispatch boundary."""
        key = block["chat_key"]
        followed_payload = json.loads(followed["payload"] or "{}")
        payload = {
            "class": followed_payload.get("class", "new"),
            "block": block["id"],
            "first": True,
        }
        if followed_payload.get("explicit_retry"):
            payload["explicit_retry"] = True
        attempt = uuid.uuid4().hex
        with self.store.transaction() as db:
            # Claimed at once: this rewrite runs it, never the queue.
            self.enqueue(
                db,
                key,
                block["revision"],
                attempt,
                self.store.clock(),
                payload,
                state="claimed",
            )
        job = self.store.rows("SELECT * FROM jobs WHERE id=?", (attempt,))[0]
        request = {
            "state": decision_state.incoming(
                [prepared(m, {}) for m in packet["messages"]],
                [prepared(m, {}) for m in packet.get("context", [])],
            )
        }

        def valid():
            rows = self.store.rows(
                "SELECT revision FROM blocks WHERE id=?", (block["id"],)
            )
            return bool(
                self.app.authorized()
                and self.app.rewriting.chat_eligible(key)
                and rows
                and rows[0]["revision"] == block["revision"]
                and context.current(self.store, packet)
            )

        try:
            result = await self.app.rewriting.work.run(
                self.app.decider, job, request, valid, on_dispatch=gate
            )
        except (Refused, ProviderRefused) as error:
            # Never a reason to hold the rewrite: it runs uncalmed.
            self._job(job, "superseded", str(error))
            if isinstance(error, BudgetReserve):
                raise
            if isinstance(error, ProviderRefused) or self.store.rows(
                "SELECT id FROM reservations WHERE id=?", (job["id"],)
            ):
                _LOG.warning("calming_decision_unavailable")
            return None
        except Exception:
            # The rewrite's gate held the block before this dispatch.
            self._job(job, "superseded", "The block was held before dispatch")
            raise
        return {"job": job, "result": result, "guidance": request["guidance"]}

    def publish_first(
        self, db, block, packet, image_context, record, first, *, suggestions
    ):
        """In the rewrite's ready transaction, after its image derivatives:
        publish the decision made before it while the block is still the
        current source, then let suggestions follow it, as ADR 30's decision
        does. An older block's decision only decided its calming. A failure
        here never undoes the incoming publication."""
        db.execute("SAVEPOINT emotion_first")
        try:
            published = self._publish_first(
                db, block, packet, image_context, record, first, suggestions
            )
        except Exception:
            db.execute("ROLLBACK TO emotion_first")
            db.execute("RELEASE emotion_first")
            _LOG.warning("emotion_schedule_unavailable", exc_info=True)
            return False
        db.execute("RELEASE emotion_first")
        return published

    def _publish_first(
        self, db, block, packet, image_context, record, first, suggestions
    ):
        key = block["chat_key"]
        job, result, guidance = first["job"], first["result"], first["guidance"]
        record = {**record, "attempt": job["id"], "followed": record["attempt"]}
        if self.expired(record) or not self.publish(
            db,
            key,
            record,
            result.code,
            guidance["version"],
            defaults=guidance.get("defaults_revision"),
            decision=result.decision.model_dump(),
            useful=result.suggestions_useful,
            usefulness=result.usefulness,
        ):
            return False
        fresh = {item.message_id: item.text for item in image_context}
        source = {
            "messages": [prepared(m, fresh) for m in packet["messages"]],
            "context": [prepared(m, fresh) for m in packet.get("context", [])],
            "direct_reply": packet.get("direct_reply"),
        }
        dependencies = {
            k: record.get(k, default)
            for k, default in (
                ("inputs", {}),
                ("transient", []),
                ("transient_inputs", {}),
                ("transcript_inputs", {}),
                ("image_inputs", {}),
                ("attribution_inputs", {}),
            )
        }
        self.app.suggestions.schedule(
            db, block, job, {**source, **dependencies}, enabled=suggestions
        )
        return True

    def keep_current(self, db, block, written):
        """A held block's decision, and the suggestions after it, came before
        its calmed rewrite (ADR 53 Q6): the image meanings that rewrite adds
        never fed them, so they leave them current."""
        for table in ("emotion_state", "suggestion_state"):
            row = db.execute(
                f"SELECT body FROM {table} WHERE chat_key=?", (block["chat_key"],)
            ).fetchone()
            record = json.loads(row["body"]) if row else None
            if not record or record.get("block") != block["id"]:
                continue
            keys = written & set(record.get("derived") or {})
            if keys:
                record["derived"].update({k: self.derived(k, db) for k in keys})
                db.execute(
                    f"UPDATE {table} SET body=? WHERE chat_key=?",
                    (json.dumps(record), block["chat_key"]),
                )

    # ---- Decisions for held blocks (ADR 49 Q3) ------------------------------

    def decide_held(self):
        """Queue the decision request for automatic blocks held under the
        Processing Character Threshold once their quiet period has passed,
        from the cache only, while their rewrite keeps waiting. Each held
        revision is decided once: a changed block is held anew, and a block
        released for rewriting is classified after its rewrite instead."""
        rows = self.store.rows(
            "SELECT d.scope,d.input_revision,b.chat_key FROM incoming_deferred d "
            "JOIN blocks b ON b.id=d.scope WHERE d.due<=? "
            "AND json_extract(d.payload,'$.decided') IS NULL",
            (self.store.clock(),),
        )
        for waiting in rows:
            try:
                self._decide_held(waiting)
            except Exception:
                # Never a reason to stop dispatching other work; a transient
                # failure leaves the revision undecided for the next pass.
                _LOG.warning("held_decision_unavailable", exc_info=True)

    def _decide_held(self, waiting):
        # Read before the transaction, like the registry-backed switches.
        eligible = self.app.rewriting.chat_eligible(waiting["chat_key"])
        with self.store.transaction() as db:
            held = db.execute(
                "SELECT * FROM incoming_deferred WHERE scope=? AND input_revision=?",
                (waiting["scope"], waiting["input_revision"]),
            ).fetchone()
            payload = json.loads(held["payload"]) if held else {}
            if not held or payload.get("decided"):
                return None
            db.execute(
                "UPDATE incoming_deferred SET payload=? WHERE scope=?",
                (json.dumps({**payload, "decided": True}), waiting["scope"]),
            )
            block = db.execute(
                "SELECT * FROM blocks WHERE id=?", (waiting["scope"],)
            ).fetchone()
            if not eligible or not block or block["revision"] != held["input_revision"]:
                return None
            db.execute("SAVEPOINT held_decision")
            try:
                packet = context.cached(self.store, db, block)
                held = {
                    "class": payload.get("class", "new"),
                    "block": block["id"],
                    "held": True,
                }
                attempt = packet and self._queue(
                    db,
                    block,
                    packet,
                    (),
                    self.capture(block, packet, None, db),
                    held,
                    None,
                )
                if (
                    packet
                    and not attempt
                    and self.app.calming.on(block["chat_key"], db)
                ):
                    # ADR 53 Q6: no longer the chat's current source, the
                    # block is still decided, for its calming only.
                    attempt = uuid.uuid4().hex
                    self.enqueue(
                        db,
                        block["chat_key"],
                        block["revision"],
                        attempt,
                        self.store.clock(),
                        {**held, "calming": True},
                    )
            except Exception:
                # The revision stays decided: an unchanged held block is never
                # resampled, as no processed input is.
                db.execute("ROLLBACK TO held_decision")
                db.execute("RELEASE held_decision")
                _LOG.warning("held_decision_unavailable", exc_info=True)
                return None
            db.execute("RELEASE held_decision")
            return attempt

    def expired(self, record):
        return self.store.clock() >= record["source_time"] + LIFETIME

    def enqueue(self, db, key, revision, attempt, due, payload, *, state="queued"):
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,NULL)",
            (attempt, key, ROLE, revision, state, due, json.dumps(payload)),
        )

    def owned(self, key, attempt, db=None):
        rows = self.read("SELECT body FROM emotion_state WHERE chat_key=?", (key,), db)
        record = json.loads(rows[0]["body"]) if rows else None
        if (
            not record
            or record.get("attempt") != attempt
            or record.get("state") != "pending"
        ):
            return None
        return record

    def current_source(self, key, record, db=None):
        latest = self.latest(key, live=True, db=db)
        return bool(
            record["barrier"] == self.latest(key, db=db)
            and latest
            and latest["message_id"] in record["sources"]
            and self.sources_current(record, db)
        )

    def _job(self, job, state, error, *, due=None):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state=?,error=?,due=coalesce(?,due) WHERE id=? AND state IN ('queued','claimed')",
                (state, error, due, job["id"]),
            )

    async def classify(self, job):
        if json.loads(job["payload"] or "{}").get("calming"):
            return await self.app.calming.decide(job)
        key = job["scope"]
        rows = self.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not rows or rows[0]["state"] not in ("queued", "claimed"):
            return
        try:
            self.app.admit_ai()
            record = self.owned(key, job["id"])
            if record is None:
                self._job(job, "superseded", "Emotion source replaced")
                return
            if (
                not self.app.rewriting.chat_eligible(key)
                or not self.current_source(key, record)
                or self.expired(record)
            ):
                # Freshness at dispatch: nothing was sent, nothing counts.
                self.supersede(job, "Emotion source is no longer current")
                return
            source = record["prepared"]
            request = {
                "state": decision_state.incoming(source["messages"], source["context"])
            }

            def valid():
                owned = self.owned(key, job["id"])
                return bool(
                    self.app.authorized()
                    and self.app.rewriting.chat_eligible(key)
                    and owned
                    and self.current_source(key, owned)
                )

            result = await self.app.rewriting.work.run(
                self.app.decider, job, request, valid
            )
            if json.loads(job["payload"] or "{}").get("held"):
                # ADR 54: a held block isn't rewritten, so Chat Memory learns
                # from what its decision read.
                self.app.memory.record(key, record["inputs"])
                self.app.memory.schedule(key)
            # Read before the transaction: the registry must never be opened
            # while this store transaction holds its lock.
            authorized = self.app.authorized()
            eligible = self.app.rewriting.chat_eligible(key)
            suggestions = self.app.suggestions.enabled()
            with self.store.transaction() as db:
                owned = self.owned(key, job["id"], db)
                if not (
                    authorized
                    and eligible
                    and owned
                    and self.current_source(key, owned, db)
                    and self.publish(
                        db,
                        key,
                        owned,
                        result.code,
                        request["guidance"]["version"],
                        defaults=request["guidance"].get("defaults_revision"),
                        decision=result.decision.model_dump(),
                        useful=result.suggestions_useful,
                        usefulness=result.usefulness,
                    )
                ):
                    # Costs are receipted; late output never replaces newer
                    # evidence.
                    db.execute(
                        "UPDATE jobs SET state='superseded',error='Inputs changed during classification' WHERE id=?",
                        (job["id"],),
                    )
                    self.settle(db, key, job["id"], "superseded")
                    return
                # ADR 25's trigger follows this publication; it never delays or
                # undoes it.
                dependencies = {
                    k: owned.get(k, default)
                    for k, default in (
                        ("inputs", {}),
                        ("transient", []),
                        ("transient_inputs", {}),
                        ("transcript_inputs", {}),
                        ("image_inputs", {}),
                        ("attribution_inputs", {}),
                    )
                }
                self.app.suggestions.schedule(
                    db,
                    {
                        "id": owned["block"],
                        "chat_key": key,
                        "revision": job["input_revision"],
                    },
                    job,
                    {**source, **dependencies},
                    enabled=suggestions,
                )
                # ADR 53 Q6: a held block found frustrated or angry is calmed.
                self.app.calming.release(
                    db, job, result.hostile, sarcastic=result.sarcastic
                )
        except ProviderRefused as refusal:
            self.refused(job, refusal)
        except asyncio.CancelledError:
            # Store recovery turns dispatched work into an interrupted outcome
            # and returns undispatched work to the queue.
            raise
        except Refused as error:
            if self.store.rows("SELECT id FROM reservations WHERE id=?", (job["id"],)):
                self.fail(key, job["id"], self.diagnostic(job))
            elif isinstance(error, ProcessingPaused):
                self._job(job, "queued", str(error))
            elif "budget" in str(error).lower():
                self._job(job, "queued", str(error), due=self.store.clock() + 60)
            elif self.store.rows(
                "SELECT id FROM jobs WHERE id=? AND state IN ('superseded','cancelled')",
                (job["id"],),
            ) or not self.dispatchable(key, job):
                # Changed before dispatch: nothing was sent and nothing failed.
                self.supersede(job, "Emotion source is no longer current")
            else:
                self._job(job, "failed", str(error))
                self.fail(key, job["id"], "unavailable")

    def dispatchable(self, key, job):
        record = self.owned(key, job["id"])
        return bool(
            record
            and self.app.rewriting.chat_eligible(key)
            and self.current_source(key, record)
            and not self.expired(record)
        )

    def supersede(self, job, error):
        self._job(job, "superseded", error)
        with self.store.transaction() as db:
            self.settle(db, job["scope"], job["id"], "superseded")

    def settle(self, db, key, attempt, state, failure=None):
        """End this attempt's pending record without a result. A superseded
        record never shows as a failure; the view's currentness decides."""
        record = self.owned(key, attempt, db)
        if record:
            record = {k: v for k, v in record.items() if k != "prepared"}
            record.update(state=state, failure=failure)
            db.execute(
                "UPDATE emotion_state SET body=? WHERE chat_key=?",
                (json.dumps(record), key),
            )

    def cancel_older(self, db, key):
        """Ending a chat's older-message run also stops a classification it
        queued that has not been sent."""
        for row in db.execute(
            "SELECT id FROM jobs WHERE role=? AND scope=? AND state IN ('queued','claimed') AND json_extract(payload,'$.class')='older'",
            (ROLE, key),
        ).fetchall():
            db.execute(
                "UPDATE jobs SET state='cancelled',error='Older-message run ended' WHERE id=?",
                (row["id"],),
            )
            self.settle(db, key, row["id"], "superseded")

    def diagnostic(self, job):
        """The fixed content-free reason of a dispatched failure."""
        rows = self.store.rows("SELECT usage FROM receipts WHERE id=?", (job["id"],))
        usage = json.loads(rows[0]["usage"]) if rows else {}
        return usage.get("format_failure") or "provider"

    def fail(self, key, attempt, reason, db=None):
        """Emotion stays unavailable with a fixed diagnostic; nothing reruns
        the rewrite or retries automatically, and no role pauses."""
        if db is None:
            with self.store.transaction() as db:
                return self.fail(key, attempt, reason, db)
        self.settle(db, key, attempt, "failed", reason)

    def refused(self, job, refusal):
        """No provider work occurred: a new attempt after the provider's
        backoff or 30/60/120 seconds, at most three times (ADR 15)."""
        key = job["scope"]
        with self.store.transaction() as db:
            record = self.owned(key, job["id"], db)
            if not record:
                return
            count = record.get("refusals", 0) + 1
            if count > 3:
                self.fail(key, job["id"], "refused", db)
                return
            delay = (
                refusal.retry_after
                if refusal.retry_after is not None
                else min(30 * 2 ** (count - 1), 600)
            )
            attempt = uuid.uuid4().hex
            record.update(attempt=attempt, refusals=count)
            db.execute(
                "UPDATE emotion_state SET body=? WHERE chat_key=?",
                (json.dumps(record), key),
            )
            payload = json.loads(job["payload"] or "{}")
            self.enqueue(
                db,
                key,
                job["input_revision"],
                attempt,
                self.store.clock() + delay,
                {**payload, "refusals": count},
            )

    def pending(self, record):
        return bool(
            self.store.rows(
                "SELECT id FROM jobs WHERE id=? AND state IN ('queued','claimed','running')",
                (record["attempt"],),
            )
        )

    def record(self, key):
        rows = self.store.rows(
            "SELECT body FROM emotion_state WHERE chat_key=?", (key,)
        )
        return json.loads(rows[0]["body"]) if rows else None

    def usefulness(self, key, db=None):
        """The published incoming decision's usefulness probability (ADR 49
        Q2), or 0 without one; ADR 56 Q19 holds calm blocks to a higher bar."""
        rows = self.read("SELECT body FROM emotion_state WHERE chat_key=?", (key,), db)
        record = json.loads(rows[0]["body"]) if rows else None
        if not record or record.get("state", "ready") != "ready":
            return 0.0
        return record.get("usefulness") or 0.0

    def useful(self, key, db=None):
        """Whether the published incoming decision found optional suggestions
        useful (ADR 49 Q2); a record from before the question counts as no."""
        rows = self.read("SELECT body FROM emotion_state WHERE chat_key=?", (key,), db)
        record = json.loads(rows[0]["body"]) if rows else None
        return bool(
            record
            and record.get("state", "ready") == "ready"
            and record.get("useful") is True
        )

    def reaction_view(self, key, event):
        """Emotion when the newest communication is a correspondent's
        qualifying reaction: its own validated assessment or nothing. Removal
        never revives an older estimate."""
        assessment = self.app.assessment
        value = EmotionView(
            source="reaction",
            reaction=event["emoji"],
            target_id=event["message_id"],
            source_time=event["added"],
            expires_at=event["added"] + LIFETIME,
        )
        if reason := assessment.withdrawn(key, event):
            return value.model_copy(update={"reason": reason})
        record = assessment.record(key)
        if not record or record["event"] != event["id"]:
            return value.model_copy(
                update={
                    "reason": "pending" if assessment.pending(key) else "unavailable"
                }
            )
        if record["state"] == "pending":
            return value.model_copy(update={"reason": "pending"})
        if record["state"] != "ready":
            return value.model_copy(update={"reason": "unavailable"})
        if not assessment.sources_current(record):
            return value.model_copy(update={"reason": "changed"})
        code = record["code"]
        return value.model_copy(
            update={
                "code": code,
                "reason": "uncertain" if code == "unknown" else "available",
            }
        )

    def view(self, key):
        kind, event = self.app.assessment.current(key)
        if kind == "reaction":
            return self.reaction_view(key, event)
        latest = self.latest(key)
        record = self.record(key)
        source_time = latest["date"] if latest else None
        value = EmotionView(
            source_time=source_time,
            expires_at=source_time + LIFETIME if source_time is not None else None,
        )
        if not latest:
            return value
        if record and record["barrier"] == latest:
            value.source_time = record["source_time"]
            value.expires_at = record["source_time"] + LIFETIME
        if self.store.clock() >= value.expires_at:
            return value.model_copy(update={"reason": "expired"})
        if not record or record["barrier"] != latest:
            pending = self.store.rows(
                "SELECT id FROM blocks WHERE chat_key=? AND state IN ('queued','working') AND ? IN (SELECT value FROM json_each(sources))",
                (key, latest["message_id"]),
            )
            return value.model_copy(
                update={"reason": "pending" if pending else "unavailable"}
            )
        if not self.sources_current(record):
            return value.model_copy(update={"reason": "changed"})
        state = record.get("state", "ready")
        if state == "pending":
            # Classification follows the published rewrite (ADR 30).
            return value.model_copy(
                update={"reason": "pending" if self.pending(record) else "unavailable"}
            )
        if state == "superseded":
            return value.model_copy(update={"reason": "unavailable"})
        code = record["code"]
        return value.model_copy(
            update={
                "code": code,
                # A failed classification keeps its fixed diagnostic.
                "reason": "invalid"
                if state == "failed" or code is None
                else "uncertain"
                if code == "unknown"
                else "available",
            }
        )
