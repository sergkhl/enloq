"""Shared content readiness; alert delivery and local unseen are independent."""

import json

from ..storage.store import WINDOW
from ..storage.viewing import (
    CACHED_SOURCE_SQL,
    INCOMING_SQL,
    RETENTION,
    SOURCE_IDENTITY_SQL,
)

QUIET = 30


def content_kind(blocks, unavailable=False):
    """After quiet, a validated result or known original fallback may be read.
    A held block still being decided for calming is not yet known (ADR 53)."""
    ready = blocks and all(
        b["state"] in {"ready", "nothing"}
        and (b["state"] == "nothing" or b["rendered_revision"] == b["revision"])
        for b in blocks
    )
    failed = not blocks or any(
        (
            b["state"] in {"failed", "unknown", "nothing", "original"}
            and not b.get("deciding")
        )
        or b["stage"] in {"paused", "budget", "format_paused"}
        for b in blocks
    )
    if not ready and not (failed or unavailable):
        return None
    latest = max(blocks, key=lambda b: max(b["sources"])) if blocks else None
    return "rewrite" if ready and latest["state"] == "ready" else "original"


def signature(blocks):
    return sorted(
        (b["id"], b["revision"], b["state"], b["rendered_revision"], b["rendered"])
        for b in blocks
    )


class UnseenReadiness:
    def __init__(self, app):
        self.app, self.store = app, app.store

    def reactions(self):
        """A new correspondent trigger reaction becomes ready after 30 quiet
        seconds, when its chat's assessment has settled or cannot run (AI Off,
        budget, an ineligible chat or a failure). It never waits for
        suggestions, ignores mute and push settings and starts no work."""
        now = self.store.clock()
        unavailable = None
        for chat in self.store.rows(
            "SELECT e.chat_key FROM reaction_events e JOIN messages m ON m.key=e.source "
            "WHERE e.ready IS NULL AND e.viewed IS NULL AND e.baseline=0 "
            "AND e.removed IS NULL AND m.deleted=0 AND m.date>=e.added-? "
            "GROUP BY e.chat_key HAVING max(e.observed)<=?",
            (WINDOW, now - QUIET),
        ):
            key = chat["chat_key"]
            if not self.app.active(key) or self.store.chat(key).kind != "private":
                continue
            if self.store.chat(key).ai_eligible and self.app.assessment.pending(key):
                if unavailable is None:
                    unavailable = self.app.notifications.unavailable()
                if not unavailable:
                    continue
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE reaction_events SET ready=? WHERE chat_key=? AND ready IS NULL "
                    "AND viewed IS NULL AND baseline=0 AND removed IS NULL "
                    "AND observed<=? AND source IN (SELECT key FROM messages "
                    "WHERE chat_key=? AND deleted=0 AND date>=reaction_events.added-?)",
                    (now, key, now - QUIET, key, WINDOW),
                )

    def groups(self, now):
        """Native group sources settle without consulting AI or alert state.

        The source body, author, stored revision and complete quiet burst are
        checked under the latch's write transaction. Native access loss and
        migration do not revoke retained originals' local viewing.
        """
        with self.store.transaction() as db:
            db.execute(
                "WITH waiting AS (SELECT v.source,v.chat_key,v.arrived "
                "FROM source_views v JOIN messages m ON m.key=v.source "
                "JOIN chats c ON c.key=v.chat_key WHERE v.candidate=1 "
                "AND v.ready IS NULL AND m.chat_key=v.chat_key "
                "AND m.message_id=v.message_id AND m.deleted=0 AND m.date>=? "
                "AND json_extract(c.body,'$.kind')='group' "
                f"AND {SOURCE_IDENTITY_SQL} AND {INCOMING_SQL} AND {CACHED_SOURCE_SQL}) "
                "UPDATE source_views SET ready=? WHERE source IN "
                "(SELECT source FROM waiting WHERE chat_key IN "
                "(SELECT chat_key FROM waiting GROUP BY chat_key HAVING max(arrived)<=?))",
                (now - RETENTION, now, now - QUIET),
            )

    def advance(self):
        if not self.app.authorized():
            return
        self.reactions()
        now = self.store.clock()
        self.groups(now)
        chats = self.store.rows(
            "SELECT chat_key FROM source_views WHERE candidate=1 AND ready IS NULL "
            "GROUP BY chat_key HAVING max(arrived)<=?",
            (now - QUIET,),
        )
        for chat in chats:
            key = chat["chat_key"]
            if self.store.chat(key).kind != "private":
                continue
            pending = self.store.rows(
                "SELECT message_id,arrived FROM source_views "
                "WHERE chat_key=? AND candidate=1 AND ready IS NULL ORDER BY message_id",
                (key,),
            )
            if not pending or max(r["arrived"] for r in pending) > now - QUIET:
                continue
            ids = {r["message_id"] for r in pending}
            blocks = [b for b in self.app.stages(key) if ids.intersection(b["sources"])]
            if not content_kind(blocks, self.app.notifications.unavailable()):
                continue
            with self.store.transaction() as db:
                # A worker or intake may change sources/output between projection
                # and latch. Retry next tick; never publish stale readiness.
                current = [
                    dict(r)
                    for r in db.execute(
                        "SELECT message_id,arrived FROM source_views "
                        "WHERE chat_key=? AND candidate=1 AND ready IS NULL ORDER BY message_id",
                        (key,),
                    )
                ]
                current_blocks = [
                    b
                    for b in db.execute("SELECT * FROM blocks WHERE chat_key=?", (key,))
                    if ids.intersection(json.loads(b["sources"]))
                ]
                if current != pending or signature(blocks) != signature(current_blocks):
                    continue
                db.execute(
                    "UPDATE source_views SET ready=? WHERE chat_key=? "
                    "AND candidate=1 AND ready IS NULL",
                    (now, key),
                )
