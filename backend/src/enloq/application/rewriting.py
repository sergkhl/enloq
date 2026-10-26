"""Revision-bound incoming processing; this service never sends a message."""

import asyncio
import json

from ..domain.models import ANALYSED_MEDIA, Message, ProcessingPaused, Refused
from ..domain.selected_media import sticker_selected
from ..domain.source_revision import fingerprint
from ..inference.base import ProviderRefused
from ..inference.work import BudgetReserve, ModelWork
from ..storage.store import RETENTION, WINDOW
from . import context
from .blocks import (
    cached_sources,
    character_admitted,
    defer,
    enqueue,
    processing_threshold,
    reconcile_admission,
    text_rewriting,
)
from .capacity import RANK, rank_sql


class CharacterDeferred(Exception):
    """Current originals remain readable without a waiting or failure status."""


class Rewriting:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.work = ModelWork(self.store)
        self.paused_roles = set

    def block(self, identity):
        rows = self.store.rows("SELECT * FROM blocks WHERE id=?", (identity,))
        if not rows:
            raise Refused("Incoming block unavailable")
        return rows[0]

    def chat_eligible(self, key):
        chat = self.store.chat(key)
        return chat.ai_eligible and chat.kind == "private" and self.app.active(key)

    def eligible(self, block, *, explicit=False):
        """Background work keeps seven days; a click reaches the 30-day cache."""
        oldest = self.store.clock() - (RETENTION if explicit else WINDOW)
        return self.chat_eligible(block["chat_key"]) and all(
            (m := self.store.message(block["chat_key"], i))
            and not m.deleted
            and m.date >= oldest
            for i in json.loads(block["sources"])
        )

    @staticmethod
    def explicit(job):
        payload = json.loads(job["payload"] or "{}")
        return bool(payload.get("explicit_retry") or payload.get("class") == "older")

    @staticmethod
    def _retryable(block, revision):
        if revision is not None and block["revision"] != revision:
            raise Refused("Message group changed; reload before Retry")
        if block["state"] == "nothing":
            raise Refused("Nothing in this block can be rewritten")
        if block["state"] == "ready" or (
            block["rendered_revision"] == block["revision"]
            and (block["rendered"] or "").strip()
        ):
            raise Refused("This block is already rewritten")
        if block["state"] not in ("failed", "unknown"):
            raise Refused("Retry requires a failed or interrupted rewrite")

    def retry(self, identity, *, revision=None):
        """One deliberate attempt for a current failed/interrupted group.
        First-time cached processing stays with the chat-level older run."""
        self.app.admit_ai()
        block = self.block(identity)
        self._retryable(block, revision)
        if not self.eligible(block, explicit=True):
            raise Refused(
                "This block is outside the processing window or data eligibility"
            )
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT * FROM blocks WHERE id=?", (identity,)
            ).fetchone()
            if not current or current["revision"] != block["revision"]:
                raise Refused("Message group changed; reload before Retry")
            block = dict(current)
            self._retryable(block, revision)
            if db.execute(
                "SELECT id FROM jobs WHERE scope=? AND state IN ('queued','claimed','running')",
                (identity,),
            ).fetchone():
                raise Refused("This block already has pending work")
            attempt = db.execute(
                "SELECT state,input_revision FROM jobs WHERE scope=? AND role='incoming_rewrite' ORDER BY rowid DESC LIMIT 1",
                (identity,),
            ).fetchone()
            if (
                not attempt
                or attempt["input_revision"] != block["revision"]
                or attempt["state"] not in ("failed", "unknown", "refused")
            ):
                raise Refused("Retry requires a current failed or interrupted attempt")
            identities = json.loads(block["sources"])
            sources = [
                Message.model_validate_json(row["body"])
                for row in db.execute(
                    "SELECT body FROM messages WHERE chat_key=? AND message_id IN (SELECT value FROM json_each(?)) ORDER BY message_id",
                    (block["chat_key"], block["sources"]),
                )
            ]
            if (
                [source.message_id for source in sources] != identities
                or any(
                    source.deleted
                    or source.outgoing
                    or source.service
                    or source.date < self.store.clock() - RETENTION
                    for source in sources
                )
                or fingerprint(sources) != block["revision"]
            ):
                raise Refused("Source expired or changed; reload before Retry")
            # A per-block click runs with new incoming work (ADR 15).
            job = enqueue(
                db,
                identity,
                block["revision"],
                self.store.clock(),
                payload={"explicit_retry": True},
            )
            db.execute(
                "UPDATE blocks SET state='queued',error=NULL WHERE id=?", (identity,)
            )
        return job

    async def process_due(self):
        try:
            self.app.admit_ai()
        except ProcessingPaused:
            return
        job = self.next_job()
        if job and self.claim(job):
            await self.run(job)

    async def run(self, job):
        if job["role"] == "chat_memory":
            await self.app.memory.fold(job)
        elif job["role"] in ("trait_scales", "trait_notes"):
            await self.app.traits.run(job)
        elif job["role"] == "response_suggestions":
            await self.app.suggestions.run(job)
        elif job["role"] == "reaction_assessment":
            await self.app.assessment.run(job)
        elif job["role"] == "emotion_classification":
            await self.app.emotion.classify(job)
        elif job["role"] == "hostility_screen":
            await self.app.notifications.screen(job)
        else:
            await self.incoming(job)

    def next_job(self, *, low_allowed=True, memory_allowed=True):
        """The highest-priority due job of this account, if any. A role paused
        by repeated format failures runs only deliberate per-block clicks."""
        # Expired arrival screens must not occupy a worker or delay block work.
        self.app.notifications.advance()
        reconcile_admission(self.store)
        # Held blocks get their decision request after the quiet (ADR 49 Q3).
        self.app.emotion.decide_held()
        low = "" if low_allowed else f" AND {rank_sql()}<{RANK['older']}"
        if not memory_allowed:
            low += " AND role NOT IN ('chat_memory','trait_scales','trait_notes')"
        paused = sorted(self.paused_roles())
        hold = (
            f" AND (role NOT IN ({','.join('?' * len(paused))}) OR json_extract(payload,'$.explicit_retry'))"
            if paused
            else ""
        )
        rows = self.store.rows(
            f"SELECT * FROM jobs WHERE role IN ('incoming_rewrite','chat_memory','trait_scales','trait_notes','response_suggestions','reaction_assessment','emotion_classification','hostility_screen') AND state='queued' AND due<=?{low}{hold} ORDER BY {rank_sql()},due,rowid",
            (self.store.clock(), *paused),
        )

        def ready(job):
            if job["role"] == "chat_memory":
                return self.app.memory.ready(job)
            if job["role"] in ("trait_scales", "trait_notes"):
                return self.app.traits.ready(job)
            return True

        return next((r for r in rows if ready(r)), None)

    def claim(self, job):
        """Atomically move a queued job to its pre-dispatch state, so no two
        workers ever take the same job."""
        if job["role"] == "incoming_rewrite":
            try:
                self.character_gate(job)
            except CharacterDeferred:
                return False
        with self.store.transaction() as db:
            return bool(
                db.execute(
                    "UPDATE jobs SET state='claimed' WHERE id=? AND state='queued'",
                    (job["id"],),
                ).rowcount
            )

    def character_gate(self, job):
        """Check before preparation and at the final provider dispatch boundary.

        This gate intentionally never participates in publication validation:
        a valid attempt that has reached the provider may finish after a Save.
        A held block released for calming passes at any length (ADR 53 Q6).
        """
        if self.explicit(job) or json.loads(job["payload"] or "{}").get("calm") is True:
            return
        held = False
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT * FROM jobs WHERE id=?", (job["id"],)
            ).fetchone()
            if not current or current["state"] not in ("queued", "claimed"):
                return
            block = db.execute(
                "SELECT * FROM blocks WHERE id=?", (job["scope"],)
            ).fetchone()
            if not block or block["revision"] != job["input_revision"]:
                return
            sources = cached_sources(db, block)
            if sources and not character_admitted(
                sources,
                processing_threshold(self.store, db),
                text_rewriting(self.store, db),
            ):
                defer(
                    db,
                    job["scope"],
                    job["input_revision"],
                    current["due"],
                    current["payload"],
                )
                held = True
        if held:
            raise CharacterDeferred

    def abandon(self, job):
        """Contain an unexpected error to its own job: work that may have
        reached the provider becomes unknown, anything earlier fails with Retry."""
        rows = self.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not rows or rows[0]["state"] not in ("queued", "claimed", "running"):
            return
        dispatched = rows[0]["state"] == "running"
        state = "unknown" if dispatched else "failed"
        error = (
            "Interrupted attempt; use Retry"
            if dispatched
            else "Preparation unavailable; use Retry"
        )
        if job["role"] == "chat_memory":
            self.app.memory._fail(job["scope"], error)
        with self.store.transaction() as db:
            if job["role"] == "response_suggestions":
                # No Retry exists for suggestions; a later changed source may qualify.
                if dispatched:
                    self.app.suggestions.fail(db, job["scope"], job["id"], "provider")
                else:
                    self.app.suggestions.cancel(db, job["scope"], job["id"])
            if job["role"] == "emotion_classification":
                self.app.emotion.fail(
                    job["scope"],
                    job["id"],
                    "interrupted" if dispatched else "unavailable",
                    db,
                )
            if job["role"] == "reaction_assessment":
                row = db.execute(
                    "SELECT body FROM reaction_state WHERE chat_key=?", (job["scope"],)
                ).fetchone()
                record = json.loads(row[0]) if row else None
                if record and record["attempt"] == job["id"]:
                    record.update(state="failed", failure="interrupted")
                    db.execute(
                        "UPDATE reaction_state SET body=? WHERE chat_key=?",
                        (json.dumps(record), job["scope"]),
                    )
            db.execute(
                "UPDATE jobs SET state=?,error=? WHERE id=?", (state, error, job["id"])
            )
            db.execute(
                "UPDATE blocks SET state=?,error=? WHERE id=? AND revision=? AND state IN ('queued','working')",
                (state, error, job["scope"], job["input_revision"]),
            )

    def refused(self, job, refusal):
        """Q31: a refusal did no work, so it re-queues with backoff up to three
        times as a new attempt, then fails visibly with Retry. A calmed rewrite
        keeps its release (ADR 53 Q6): the decision was made, and text would
        otherwise be held and decided again (ADR 54)."""
        payload = json.loads(job["payload"] or "{}")
        count = payload.get("refusals", 0) + 1
        with self.store.transaction() as db:
            if count > 3:
                db.execute(
                    "UPDATE blocks SET state='failed',error=? WHERE id=? AND revision=?",
                    (
                        "The provider refused this work repeatedly; use Retry",
                        job["scope"],
                        job["input_revision"],
                    ),
                )
                return
            delay = (
                refusal.retry_after
                if refusal.retry_after is not None
                else min(30 * 2 ** (count - 1), 600)
            )
            enqueue(
                db,
                job["scope"],
                job["input_revision"],
                self.store.clock() + delay,
                klass=payload.get("class", "new"),
                payload={
                    k: v
                    for k, v in payload.items()
                    if k in ("explicit_retry", "calm", "sarcastic")
                }
                | {"refusals": count},
            )
            db.execute(
                "UPDATE blocks SET state='queued',stage=NULL,error=? WHERE id=? AND revision=?",
                (
                    "Provider busy; retrying automatically",
                    job["scope"],
                    job["input_revision"],
                ),
            )

    async def incoming(self, job):
        identity = job["scope"]
        current = self.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not current or current[0]["state"] not in ("queued", "claimed"):
            return  # Stopped or superseded while waiting.
        try:
            self.app.admit_ai()
            block = self.block(identity)
            explicit = self.explicit(job)
            if block["revision"] != job["input_revision"] or not self.eligible(
                block, explicit=explicit
            ):
                raise Refused("Source expired, changed or is ineligible")
            self.character_gate(job)
            self.stage(identity, "preparing", state="working", job=job)
            packet = await context.incoming(
                self.app,
                block,
                retry=json.loads(job["payload"]).get("explicit_retry", False),
                on_stage=lambda stage: self.stage(identity, stage, job=job),
                explicit=explicit,
            )
            if not self.store.rows(
                "SELECT id FROM jobs WHERE id=? AND state IN ('queued','claimed')",
                (job["id"],),
            ):
                return  # A Save or source change superseded this preparation.
            items = packet.pop("unanalysed", [])
            unanalysed = json.dumps(items) if items else None
            if packet.pop("nothing", False):
                # No model call: nothing rewritable remains after preparation.
                with self.store.transaction() as db:
                    db.execute(
                        "UPDATE jobs SET state='done',error=NULL WHERE id=?",
                        (job["id"],),
                    )
                    db.execute(
                        "UPDATE blocks SET state='nothing',rendered=NULL,error=NULL,unanalysed=? WHERE id=? AND revision=?",
                        (unanalysed, identity, block["revision"]),
                    )
                return

            def valid():
                rows = self.store.rows(
                    "SELECT revision FROM blocks WHERE id=?", (identity,)
                )
                return bool(
                    self.app.authorized()
                    and self.store.rows(
                        "SELECT id FROM jobs WHERE id=? AND state NOT IN ('cancelled','superseded')",
                        (job["id"],),
                    )
                    and self.store.rows(
                        "SELECT id FROM rewrite_streams WHERE id=? AND state='active'",
                        (job["id"],),
                    )
                    and rows
                    and rows[0]["revision"] == block["revision"]
                    and self.eligible(block, explicit=explicit)
                    and context.current(self.store, packet)
                )

            with self.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET payload=? WHERE id=?",
                    (
                        json.dumps(
                            {
                                **json.loads(job["payload"]),
                                "inputs": packet["inputs"],
                                "transient": packet.get("transient", []),
                                "transient_inputs": packet.get("transient_inputs", {}),
                                "transcript_inputs": packet.get(
                                    "transcript_inputs", {}
                                ),
                            }
                        ),
                        job["id"],
                    ),
                )
            memory = self.app.memory.for_target(
                block["chat_key"],
                packet["messages"][0]["id"],
                packet["messages"][0]["date"],
            )
            plain = {k: v for k, v in packet.items() if k != "images"}
            # Memory has its own allowance; the target is never cut for it.
            if (
                memory
                and len(
                    json.dumps({**plain, "memory": memory}, ensure_ascii=False).encode()
                )
                <= 35000
            ):
                packet["memory"] = memory
            emotion = self.app.emotion.capture(block, packet, job["id"])
            # ADR 53 Q7: in a chat with calming on, the block's decision comes
            # first; a held block released for calming brings its own (Q6).
            calming = json.loads(job["payload"] or "{}")
            code = calming.get("calm")
            sarcastic = bool(calming.get("sarcastic"))
            released = type(code) is bool
            first = None
            if not released and self.app.calming.on(block["chat_key"]):
                first = await self.app.emotion.decide_first(
                    block, packet, job, gate=lambda: self.character_gate(job)
                )
                if not self.store.rows(
                    "SELECT id FROM jobs WHERE id=? AND state IN ('queued','claimed')",
                    (job["id"],),
                ):
                    return  # A Save or source change superseded this attempt.
                code = first and first["result"].code
                sarcastic = bool(first and first["result"].sarcastic)
            calmed = bool(code if released else first and first["result"].hostile)
            if calmed:
                packet["calm"] = True
                if sarcastic:
                    packet["sarcastic"] = True
            elif not self.rewrites_text() and not any(
                m.get("media") in ANALYSED_MEDIA for m in packet["messages"]
            ):
                # ADR 54: text stays combined; only calming rewrites it.
                self.combined(job, block, packet, emotion, first)
                return
            self.stage(identity, "rewriting", job=job)
            self.store.streams.begin(job["id"], block["chat_key"])

            def publish(text):
                if valid():
                    self.store.streams.publish(job["id"], text)

            try:
                result = await self.work.run(
                    # ADR 53: a calmed block is written by its own route.
                    self.app.calmer if calmed else self.app.engine,
                    job,
                    packet,
                    valid,
                    on_text=publish,
                    on_dispatch=lambda: self.character_gate(job),
                )
            finally:
                if context.current(self.store, packet) and self.store.rows(
                    "SELECT id FROM reservations WHERE id=? AND status!='refused'",
                    (job["id"],),
                ):
                    # Sent to the model: these may fold into memory later.
                    self.app.memory.record(block["chat_key"], packet["inputs"])
            if not valid():
                with self.store.transaction() as db:
                    db.execute(
                        "UPDATE jobs SET state='superseded',error='Inputs changed during generation' WHERE id=?",
                        (job["id"],),
                    )
                return
            # Read before the transaction: the registry must never be opened
            # while this store transaction holds its lock.
            suggestions = bool(first) and self.app.suggestions.enabled()
            written = set()
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE blocks SET rendered=?,rendered_revision=revision,state='ready',error=NULL,unanalysed=?,guidance=?,defaults=?,calmed=? WHERE id=? AND revision=?",
                    (
                        result.text,
                        unanalysed,
                        packet["guidance"]["version"],
                        packet["guidance"].get("defaults_revision"),
                        int(calmed),
                        identity,
                        block["revision"],
                    ),
                )
                for derived in result.image_context:
                    source = db.execute(
                        "SELECT key,revision FROM messages WHERE chat_key=? AND message_id=?",
                        (block["chat_key"], derived.message_id),
                    ).fetchone()
                    if not source or source["revision"] != packet["inputs"].get(
                        source["key"]
                    ):
                        continue  # A transient or since-changed context image.
                    db.execute(
                        "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
                        (
                            source["key"],
                            source["revision"],
                            json.dumps({"image_context": derived.text}),
                        ),
                    )
                    written.add(source["key"])
                # ADR 30: emotion is a separate decision after this
                # publication; readiness, notifications and unseen dots never
                # wait for it, and suggestions follow its publication. ADR 53:
                # a decision made first is published here instead, and a
                # released held block's was published before its rewrite.
                if first:
                    self.app.emotion.publish_first(
                        db,
                        block,
                        packet,
                        result.image_context,
                        emotion,
                        first,
                        suggestions=suggestions,
                    )
                elif released:
                    self.app.emotion.keep_current(db, block, written)
                else:
                    self.app.emotion.schedule(
                        db, block, job, packet, result.image_context, emotion
                    )
            # Include the freshly generated cached image interpretation in the
            # exact processed source version, after publication.
            self.app.memory.record(block["chat_key"], packet["inputs"])
            self.app.memory.schedule(block["chat_key"])
        except CharacterDeferred:
            return
        except ProviderRefused as refusal:
            self.refused(job, refusal)
        except BudgetReserve:
            self.end_older(job["scope"].rsplit("/", 1)[0], "budget")
        except asyncio.CancelledError:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE blocks SET state='unknown',error='Interrupted attempt; use Retry',rendered=CASE WHEN rendered_revision=revision THEN rendered ELSE NULL END WHERE id=? AND revision=? AND EXISTS (SELECT 1 FROM jobs WHERE id=? AND state NOT IN ('superseded','cancelled'))",
                    (identity, job["input_revision"], job["id"]),
                )
            raise
        except Refused as error:
            older = json.loads(job["payload"] or "{}").get("class") == "older"
            if older and not self.chat_eligible(job["scope"].rsplit("/", 1)[0]):
                # Losing eligibility ends a chat-level run like turning Off.
                self.end_older(job["scope"].rsplit("/", 1)[0], "ineligible")
            dispatched = bool(
                self.store.rows("SELECT id FROM reservations WHERE id=?", (job["id"],))
            )
            paused = isinstance(error, ProcessingPaused) and not dispatched
            waiting = paused or (not dispatched and "budget" in str(error).lower())
            state = "queued" if waiting else "failed"
            with self.store.transaction() as db:
                # Stopped or superseded work keeps the state Stop or a newer
                # revision gave its block.
                if db.execute(
                    "UPDATE jobs SET state=?,error=?,due=? WHERE id=? AND state NOT IN ('superseded','cancelled')",
                    (
                        state,
                        str(error),
                        job["due"] if paused else self.store.clock() + 60,
                        job["id"],
                    ),
                ).rowcount:
                    db.execute(
                        "UPDATE blocks SET state=?,error=?,stage=NULL,rendered=CASE WHEN rendered_revision=revision THEN rendered ELSE NULL END WHERE id=? AND revision=?",
                        (state, str(error), identity, job["input_revision"]),
                    )
        finally:
            self.store.streams.finish(job["id"])

    def rewrites_text(self):
        with self.store.transaction() as db:
            return text_rewriting(self.store, db)

    def combined(self, job, block, packet, emotion, first):
        """A text block that isn't calmed ends without a rewrite (ADR 54): it
        shows as its combined originals. A decision made first publishes as
        it would with the rewrite, and Chat Memory learns from what it read."""
        suggestions = bool(first) and self.app.suggestions.enabled()
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='done',error=NULL WHERE id=?", (job["id"],)
            )
            db.execute(
                "UPDATE blocks SET state='original',error=NULL,stage=NULL,"
                "rendered=CASE WHEN rendered_revision=revision THEN rendered ELSE NULL END "
                "WHERE id=? AND revision=?",
                (block["id"], block["revision"]),
            )
            if first:
                self.app.emotion.publish_first(
                    db, block, packet, (), emotion, first, suggestions=suggestions
                )
        if first:
            self.app.memory.record(block["chat_key"], packet["inputs"])
            self.app.memory.schedule(block["chat_key"])

    def partial(self, block):
        rows = self.store.rows(
            "SELECT s.id,s.text,j.payload FROM rewrite_streams s JOIN jobs j ON j.id=s.id WHERE s.state='active' AND j.state='running' AND j.role='incoming_rewrite' AND j.scope=? AND j.input_revision=? AND s.text IS NOT NULL",
            (block["id"], block["revision"]),
        )
        if rows and context.current(self.store, json.loads(rows[0]["payload"])):
            return {
                "attempt": rows[0]["id"],
                "text": rows[0]["text"],
                "revision": block["revision"],
            }
        return None

    def older(self, chat_key):
        """Never-processed blocks a click may still process, newest first;
        with text rewriting off (ADR 54), only those with an image, a voice
        note or a video circle, since text stays combined."""
        if not self.chat_eligible(chat_key):
            return []
        with self.store.transaction() as db:
            if text_rewriting(self.store, db):
                rows = db.execute(
                    "SELECT * FROM blocks WHERE chat_key=? AND state='original' "
                    "ORDER BY date DESC,id DESC",
                    (chat_key,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM blocks b WHERE chat_key=? AND state='original' "
                    "AND EXISTS (SELECT 1 FROM messages m WHERE m.chat_key=b.chat_key "
                    "AND m.message_id IN (SELECT value FROM json_each(b.sources)) "
                    "AND m.deleted=0 AND json_extract(m.body,'$.media') IN "
                    f"({','.join('?' * len(ANALYSED_MEDIA))})) "
                    "ORDER BY date DESC,id DESC",
                    (chat_key, *sorted(ANALYSED_MEDIA)),
                ).fetchall()
            rows = [dict(r) for r in rows]
        return [r for r in rows if self.eligible(r, explicit=True)]

    def process_older(self, chat_key):
        """The chat-level action: queue every never-processed cached block as
        the lowest class, newest first. No confirmation; Stop, Off, lost
        eligibility or the budget reserve end it, and it never resumes alone."""
        self.app.admit_ai()
        blocks = self.older(chat_key)
        now = self.store.clock()
        with self.store.transaction() as db:
            db.execute("DELETE FROM cursors WHERE key=?", ("older:" + chat_key,))
            for index, block in enumerate(blocks):
                # Earlier due times run first: the newest block is due earliest.
                enqueue(
                    db,
                    block["id"],
                    block["revision"],
                    now - (len(blocks) - index) * 1e-3,
                    klass="older",
                )
                db.execute(
                    "UPDATE blocks SET state='queued',error=NULL WHERE id=? AND state='original'",
                    (block["id"],),
                )
        return len(blocks)

    def end_older(self, chat_key, reason):
        """End a chat's older-message run, keeping its count for another click."""
        with self.store.transaction() as db:
            scopes = [
                r["scope"]
                for r in db.execute(
                    f"SELECT scope FROM jobs WHERE role='incoming_rewrite' AND state IN ('queued','claimed') AND {rank_sql()}=? AND scope IN (SELECT id FROM blocks WHERE chat_key=?)",
                    (RANK["older"], chat_key),
                )
            ]
            db.execute(
                "UPDATE jobs SET state='cancelled',error='Older-message run ended' WHERE state IN ('queued','claimed') AND scope IN (SELECT value FROM json_each(?))",
                (json.dumps(scopes),),
            )
            db.execute(
                "UPDATE blocks SET state='original',error=NULL,stage=NULL WHERE chat_key=? AND state IN ('queued','working') AND id IN (SELECT value FROM json_each(?))",
                (chat_key, json.dumps(scopes)),
            )
            self.app.emotion.cancel_older(db, chat_key)
            if scopes:
                db.execute(
                    "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                    (
                        "older:" + chat_key,
                        json.dumps({"remaining": len(scopes), "reason": reason}),
                    ),
                )

    def end_all_older(self, reason):
        for row in self.store.rows(
            f"SELECT DISTINCT blocks.chat_key FROM jobs JOIN blocks ON blocks.id=jobs.scope WHERE jobs.state IN ('queued','claimed') AND {rank_sql('jobs.payload')}=?",
            (RANK["older"],),
        ):
            self.end_older(row["chat_key"], reason)

    def older_notice(self, chat_key):
        rows = self.store.rows(
            "SELECT value FROM cursors WHERE key=?", ("older:" + chat_key,)
        )
        return json.loads(rows[0]["value"]) if rows else None

    def stage(self, identity, stage, *, state=None, job=None):
        """The visible Processing Stage of a working block."""
        with self.store.transaction() as db:
            guard = (
                " AND revision=? AND EXISTS (SELECT 1 FROM jobs WHERE id=? "
                "AND state IN ('queued','claimed'))"
                if job
                else ""
            )
            db.execute(
                "UPDATE blocks SET stage=?,state=coalesce(?,state),error=CASE WHEN ? IS NULL THEN error ELSE NULL END WHERE id=?"
                + guard,
                (
                    stage,
                    state,
                    state,
                    identity,
                    *([job["input_revision"], job["id"]] if job else []),
                ),
            )

    def stop(self, chat_key):
        """Cancel this chat's work not yet sent to the model; running attempts
        finish. Returns how many blocks were stopped."""
        self.store.chat(chat_key)
        with self.store.transaction() as db:
            scopes = [
                r["scope"]
                for r in db.execute(
                    "SELECT scope FROM jobs WHERE role='incoming_rewrite' AND state IN ('queued','claimed') AND scope IN (SELECT id FROM blocks WHERE chat_key=?)",
                    (chat_key,),
                )
            ]
            db.execute(
                "UPDATE jobs SET state='cancelled',error='Stopped before dispatch' WHERE role='incoming_rewrite' AND state IN ('queued','claimed') AND scope IN (SELECT id FROM blocks WHERE chat_key=?)",
                (chat_key,),
            )
            db.execute(
                "UPDATE blocks SET state='original',error=NULL,stage=NULL WHERE chat_key=? AND state IN ('queued','working') AND id IN (SELECT value FROM json_each(?))",
                (chat_key, json.dumps(scopes)),
            )
            self.app.emotion.cancel_older(db, chat_key)
        return len(scopes)

    def undo(self, key, revision, attempt=None):
        current = self.store.draft(key)
        if sticker_selected(current):
            raise Refused("Remove the sticker before changing the kept draft")

        def restore(draft):
            if sticker_selected(draft):
                raise Refused("Remove the sticker before changing the kept draft")
            if draft["undo"] is None:
                raise Refused("Undo unavailable after intervening edits")
            draft.update(
                text=draft["undo"], undo=None, state="idle", attempt=None, error=None
            )
            # Undo drops a reminder set's mark with the option (ADR 58 Q3).
            draft.pop("reminders", None)

        return self.store.mutate_draft(key, revision, restore)
