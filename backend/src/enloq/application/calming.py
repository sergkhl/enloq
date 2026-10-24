"""Soften hostile messages (ADR 53).

Only an explicit hostile-wording decision triggers a softened block, at any
length. Mood is independent. The storage key and Python module retain their
existing internal names, preserving each chat's switch and cached outputs.
"""

import asyncio
import json
import logging

from ..domain.models import ProcessingPaused, Refused
from ..inference.base import ProviderRefused
from . import context, decision_state
from .blocks import enqueue
from .suggestions import prepared

_LOG = logging.getLogger("enloq.calming")


class Calming:
    # On unless a chat chooses otherwise (Q4).
    default = True

    def __init__(self, app):
        self.app, self.store = app, app.store

    def read(self, sql, args, db=None):
        if db is not None:
            return [dict(row) for row in db.execute(sql, args)]
        return self.store.rows(sql, args)

    def on(self, key, db=None):
        """Whether a block of this chat is decided first and calmed when
        hostile; groups and Saved Messages never are (Q11)."""
        chat = self.read(
            "SELECT json_extract(body,'$.kind') AS kind FROM chats WHERE key=?",
            (key,),
            db,
        )
        if not chat or chat[0]["kind"] != "private":
            return False
        choice = self.read("SELECT enabled FROM calming WHERE chat_key=?", (key,), db)
        return bool(choice[0]["enabled"]) if choice else self.default

    def view(self, key):
        self.private(key)
        return {"enabled": self.on(key)}

    def set(self, key, enabled):
        """Applies to blocks rewritten from now on: switching on redoes
        nothing already shown, and switching off leaves calmed text (Q9)."""
        self.private(key)
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO calming VALUES (?,?,?) ON CONFLICT(chat_key) DO UPDATE "
                "SET enabled=excluded.enabled,changed=excluded.changed "
                "WHERE enabled!=excluded.enabled",
                (key, int(enabled), self.store.clock()),
            )
        return {"enabled": self.on(key)}

    def private(self, key):
        if self.store.chat(key).kind != "private":
            raise Refused("Softening is for private chats only")

    # ---- Held blocks (ADR 49 Q3) ---------------------------------------------

    def held(self, decision, db=None):
        """The decision's block while it is still held unchanged."""
        payload = json.loads(decision["payload"] or "{}")
        blocks = self.read(
            "SELECT * FROM blocks WHERE id=?", (payload.get("block"),), db
        )
        block = blocks[0] if blocks else None
        if (
            not block
            or block["state"] != "original"
            or block["revision"] != decision["input_revision"]
            or not self.read(
                "SELECT 1 FROM incoming_deferred WHERE scope=? AND input_revision=?",
                (block["id"], block["revision"]),
                db,
            )
        ):
            return None
        return block

    def release(self, db, decision, code, *, sarcastic=False):
        """After a held block's explicit hostile-wording decision, soften it
        when the chat's switch is on, whatever its length (Q6).
        Its decision is made, so that rewrite neither makes another nor
        queues one after its publication. A failure here never undoes the
        decision's publication."""
        db.execute("SAVEPOINT calming_release")
        try:
            attempt = self._release(db, decision, code, sarcastic)
        except Exception:
            db.execute("ROLLBACK TO calming_release")
            db.execute("RELEASE calming_release")
            _LOG.warning("calming_release_unavailable", exc_info=True)
            return None
        db.execute("RELEASE calming_release")
        return attempt

    def _release(self, db, decision, code, sarcastic):
        payload = json.loads(decision["payload"] or "{}")
        if not payload.get("held") or not code:
            return None
        if not self.on(decision["scope"], db):
            return None
        block = self.held(decision, db)
        if not block:
            return None
        waiting = db.execute(
            "SELECT payload FROM incoming_deferred WHERE scope=?", (block["id"],)
        ).fetchone()
        attempt = enqueue(
            db,
            block["id"],
            block["revision"],
            self.store.clock(),
            klass=json.loads(waiting["payload"] or "{}").get("class", "new"),
            # The decision's sarcasm answer goes with it to the rewrite.
            payload={"calm": code, **({"sarcastic": True} if sarcastic else {})},
        )
        db.execute(
            "UPDATE blocks SET state='queued',error=NULL,stage=NULL WHERE id=?",
            (block["id"],),
        )
        return attempt

    async def decide(self, job):
        """The decision for a held block that is no longer its chat's current
        source: it settles the block's calming only, and nothing shows it."""
        rows = self.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not rows or rows[0]["state"] not in ("queued", "claimed"):
            return
        key = job["scope"]
        try:
            self.app.admit_ai()
            # Read before the transaction, like the registry-backed switches.
            eligible = self.app.rewriting.chat_eligible(key)
            with self.store.transaction() as db:
                block = self.held(job, db)
                packet = (
                    block
                    and eligible
                    and self.on(key, db)
                    and context.cached(self.store, db, block)
                )
            if not packet:
                self.end(job, "superseded", "The held block changed or calming ended")
                return
            request = {
                "state": decision_state.incoming(
                    [prepared(m, {}) for m in packet["messages"]],
                    [prepared(m, {}) for m in packet.get("context", [])],
                )
            }

            def valid():
                return bool(
                    self.app.authorized()
                    and self.app.rewriting.chat_eligible(key)
                    and self.held(job)
                    and context.current(self.store, packet)
                )

            result = await self.app.rewriting.work.run(
                self.app.decider, job, request, valid
            )
            # ADR 54: Chat Memory learns from what this decision read.
            self.app.memory.record(key, packet["inputs"])
            self.app.memory.schedule(key)
            with self.store.transaction() as db:
                self.release(db, job, result.hostile, sarcastic=result.sarcastic)
        except asyncio.CancelledError:
            raise
        except ProviderRefused:
            # Nothing was sent: the block keeps its originals.
            _LOG.warning("calming_decision_unavailable")
        except Refused as error:
            dispatched = self.store.rows(
                "SELECT id FROM reservations WHERE id=?", (job["id"],)
            )
            if not dispatched and isinstance(error, ProcessingPaused):
                self.end(job, "queued", str(error))
            elif not dispatched and "budget" in str(error).lower():
                self.end(job, "queued", str(error), due=self.store.clock() + 60)
            else:
                self.end(job, "superseded", str(error))
                if dispatched:
                    _LOG.warning("calming_decision_unavailable")

    def end(self, job, state, error, *, due=None):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state=?,error=?,due=coalesce(?,due) WHERE id=? "
                "AND state IN ('queued','claimed')",
                (state, error, due, job["id"]),
            )

    def deciding(self, key, db=None):
        """The held blocks of a chat with calming on whose decision is not yet
        made. Their readiness and notification wait for it: one found
        frustrated or angry is calmed before either (Q7)."""
        if not self.on(key, db):
            return set()
        held = self.read(
            "SELECT d.scope,d.payload FROM incoming_deferred d JOIN blocks b "
            "ON b.id=d.scope AND b.revision=d.input_revision "
            "WHERE b.chat_key=? AND b.state='original'",
            (key,),
            db,
        )
        if not held:
            return set()
        running = {
            row["block"]
            for row in self.read(
                "SELECT json_extract(payload,'$.block') AS block FROM jobs "
                "WHERE role='emotion_classification' AND scope=? "
                "AND state IN ('queued','claimed','running') "
                "AND json_extract(payload,'$.held')=1",
                (key,),
                db,
            )
        }
        return {
            row["scope"]
            for row in held
            if not json.loads(row["payload"] or "{}").get("decided")
            or row["scope"] in running
        }
