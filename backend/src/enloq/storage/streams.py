"""Latest provisional text for one durable, account-local rewrite attempt."""

import json

DDL = """CREATE TABLE IF NOT EXISTS rewrite_streams (
    id TEXT PRIMARY KEY, chat_key TEXT NOT NULL, text TEXT, base TEXT,
    state TEXT NOT NULL, draft_revision INTEGER
)"""


class RewriteStreams:
    def __init__(self, store):
        self.store = store

    def begin(self, identity, key, *, base=None, revision=None):
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO rewrite_streams VALUES (?,?,NULL,?,'active',?)",
                (identity, key, json.dumps(base) if base else None, revision),
            )

    def publish(self, identity, text):
        if not isinstance(text, str) or not text or len(text) > 65536:
            return
        with self.store.transaction() as db:
            db.execute(
                "UPDATE rewrite_streams SET text=? WHERE id=? AND state='active' AND id IN (SELECT id FROM jobs WHERE state='running')",
                (text, identity),
            )

    def finish(self, identity):
        with self.store.transaction() as db:
            db.execute("DELETE FROM rewrite_streams WHERE id=?", (identity,))

    def recover(self, db):
        # `base` contains the complete pre-attempt draft. Restoration is
        # revision/attempt-checked so a newer edit or a stopped draft survives.
        for row in db.execute(
            "SELECT * FROM rewrite_streams WHERE base IS NOT NULL AND state='active'"
        ).fetchall():
            draft_row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (row["chat_key"],)
            ).fetchone()
            draft = json.loads(draft_row[0]) if draft_row else {}
            if (
                draft.get("attempt") == row["id"]
                and draft.get("revision") == row["draft_revision"]
            ):
                base = json.loads(row["base"])
                base.update(
                    revision=draft["revision"] + 1,
                    state="failed",
                    error="Rewrite interrupted; use explicit Retry",
                )
                db.execute(
                    "UPDATE drafts SET body=? WHERE chat_key=?",
                    (json.dumps(base), row["chat_key"]),
                )
        # An explicit outgoing attempt cannot be dispatched automatically after
        # interruption, even if it was still waiting for capacity.
        db.execute(
            "UPDATE jobs SET state='failed',error='Rewrite interrupted; use explicit Retry' WHERE role='outgoing_rewrite' AND state IN ('queued','claimed')"
        )
        db.execute("DELETE FROM rewrite_streams")
