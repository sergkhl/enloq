"""Observed native reaction snapshots and the correspondent's dated evidence.

Reaction changes are metadata (ADR 26): applying them never changes a message's
content revision, its derivatives, blocks or observed edit history. The event
ledger keeps only the correspondent's dated trigger reactions; local observation
time never substitutes for native addition time.
"""

import json

from ..domain.models import Chat, Message, Model, Reaction, ReactionSnapshot
from ..domain.reactions import TRIGGERS

DDL = [
    """CREATE TABLE IF NOT EXISTS message_reactions (
        source TEXT PRIMARY KEY REFERENCES messages(key) ON DELETE CASCADE,
        chat_key TEXT NOT NULL, message_id INTEGER NOT NULL,
        body TEXT NOT NULL, observed REAL NOT NULL
    )""",
    # One row per dated correspondent trigger reaction. A replacement or a later
    # re-addition has a new native time and therefore a new identity.
    """CREATE TABLE IF NOT EXISTS reaction_events (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL REFERENCES messages(key) ON DELETE CASCADE,
        chat_key TEXT NOT NULL, message_id INTEGER NOT NULL, emoji TEXT NOT NULL,
        added REAL NOT NULL, observed REAL NOT NULL, baseline INTEGER NOT NULL,
        removed REAL, ready REAL, viewed REAL, subject REAL
    )""",
    # The current Reaction Assessment of one chat (ADR 26); no history.
    """CREATE TABLE IF NOT EXISTS reaction_state (
        chat_key TEXT PRIMARY KEY REFERENCES chats(key),
        added REAL NOT NULL, body TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS reaction_events_chat ON reaction_events(chat_key,removed,baseline,added)",
    "CREATE INDEX IF NOT EXISTS reaction_events_source ON reaction_events(source)",
    # ADR 49 Q12: when an own reaction on a private message was first observed.
    # It stays after the reaction is removed, so an answered block never
    # revives its suggestions.
    """CREATE TABLE IF NOT EXISTS own_reactions (
        source TEXT PRIMARY KEY REFERENCES messages(key) ON DELETE CASCADE,
        chat_key TEXT NOT NULL, message_id INTEGER NOT NULL, observed REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS own_reactions_chat ON own_reactions(chat_key,message_id)",
]
INTRODUCED = "reactions_introduced"


class ReactionChipView(Model):
    reaction: Reaction
    count: int
    # None when Telegram's evidence does not say.
    mine: bool | None = None
    peer: bool | None = None


class ReactionView(Model):
    """One message's displayed chips; reading it starts no native work."""

    chips: list[ReactionChipView]
    # Whether the owner's own choices are known, as removal requires.
    own_known: bool


def triggers():
    """Browser change scopes. Observation-time updates alone are not changes."""
    for table, events in (
        (
            "message_reactions",
            (("INSERT", "NEW", "INSERT"), ("DELETE", "OLD", "DELETE"))
            + (("UPDATE", "NEW", "UPDATE OF body"),),
        ),
        (
            "reaction_events",
            (
                ("INSERT", "NEW", "INSERT"),
                ("DELETE", "OLD", "DELETE"),
                ("UPDATE", "NEW", "UPDATE"),
            ),
        ),
        (
            "reaction_state",
            (
                ("INSERT", "NEW", "INSERT"),
                ("DELETE", "OLD", "DELETE"),
                ("UPDATE", "NEW", "UPDATE"),
            ),
        ),
    ):
        for event, row, when in events:
            yield (
                f"CREATE TRIGGER IF NOT EXISTS {table}_{event.lower()}_changes "
                f"AFTER {when} ON {table} BEGIN INSERT INTO changes VALUES "
                f"({row}.chat_key,(SELECT coalesce(max(seq),0)+1 FROM changes)) "
                "ON CONFLICT(scope) DO UPDATE SET seq=excluded.seq; END"
            )


def initialize(db, now):
    for statement in DDL:
        db.execute(statement)
    columns = {r["name"] for r in db.execute("PRAGMA table_info(reaction_events)")}
    if "subject" not in columns:
        # When an event became the current assessment subject (P4).
        db.execute("ALTER TABLE reaction_events ADD COLUMN subject REAL")
    for statement in triggers():
        db.execute(statement)
    # Reactions dated before the feature existed form a display-only baseline.
    db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (INTRODUCED, repr(now)))


def introduced(db):
    return float(
        db.execute("SELECT value FROM metadata WHERE key=?", (INTRODUCED,)).fetchone()[
            0
        ]
    )


def own_reacted(snapshot):
    """Whether an own reaction is known to be present (ADR 49 Q12); a minimal
    snapshot leaves the owner's choices unknown."""
    return not snapshot.minimal and (
        any(chip.mine for chip in snapshot.chips)
        or any(a.actor == "own" for a in snapshot.actors or [])
    )


def peer_has(snapshot):
    """The correspondent's dated trigger reactions: {emoji: native time}."""
    return {
        a.reaction.emoji: a.date
        for a in snapshot.actors or []
        if a.actor == "peer" and a.date is not None and a.reaction.emoji in TRIGGERS
    }


class Reactions:
    def __init__(self, store):
        self.store = store
        # Called with a chat key after a correspondent trigger change commits.
        self.on_change = []

    def apply(self, key, snapshot: ReactionSnapshot, *, record_empty=False):
        """Record one observed snapshot of a cached native message. An older
        observation never replaces a newer one. Returns whether it applied.

        ``record_empty`` keeps the time of a reaction update that leaves none,
        so a read that started earlier cannot restore what it removed."""
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT chat_key,message_id,deleted,body FROM messages WHERE key=?",
                (key,),
            ).fetchone()
            if not row or row["deleted"]:
                return False
            if Message.model_validate_json(row["body"]).service:
                return False
            prior = db.execute(
                "SELECT body,observed FROM message_reactions WHERE source=?", (key,)
            ).fetchone()
            if prior and prior["observed"] > snapshot.observed:
                return False
            body = snapshot.model_dump_json(exclude={"observed"})
            if prior:
                db.execute(
                    "UPDATE message_reactions SET observed=? WHERE source=?",
                    (snapshot.observed, key),
                )
                if prior["body"] != body:
                    db.execute(
                        "UPDATE message_reactions SET body=? WHERE source=?",
                        (body, key),
                    )
            elif snapshot.chips or record_empty:
                db.execute(
                    "INSERT INTO message_reactions VALUES (?,?,?,?,?)",
                    (key, row["chat_key"], row["message_id"], body, snapshot.observed),
                )
            else:
                # Nothing to display and no earlier evidence to order against.
                return True
            changed = self._ledger(db, key, row, snapshot)
        if changed:
            for hook in self.on_change:
                hook(row["chat_key"])
        return True

    def _ledger(self, db, key, row, snapshot):
        """Record dated additions and proven removals. Returns whether a
        non-baseline event changed, which may need a Reaction Assessment."""
        chat_row = db.execute(
            "SELECT body FROM chats WHERE key=?", (row["chat_key"],)
        ).fetchone()
        if not chat_row:
            return False
        chat = Chat.model_validate_json(chat_row["body"])
        if chat.kind != "private" or not chat.peer.startswith("user:"):
            # Group actors are aggregate display evidence. A channel actor or
            # an upstream 'peer' label cannot adopt private correspondent work.
            return False
        if own_reacted(snapshot):
            # The first observation stays; removal never erases it.
            db.execute(
                "INSERT OR IGNORE INTO own_reactions VALUES (?,?,?,?)",
                (key, row["chat_key"], row["message_id"], snapshot.observed),
            )
        now, start = self.store.clock(), introduced(db)
        present = peer_has(snapshot)
        changed = False
        for event in db.execute(
            "SELECT id,emoji,added,baseline FROM reaction_events WHERE source=? AND removed IS NULL",
            (key,),
        ).fetchall():
            replaced = (
                event["emoji"] in present
                and present[event["emoji"]] != (event["added"])
            )
            if replaced or (
                event["emoji"] not in present
                and snapshot.peer_lacks(Reaction(emoji=event["emoji"]))
            ):
                db.execute(
                    "UPDATE reaction_events SET removed=? WHERE id=?",
                    (now, event["id"]),
                )
                changed = changed or not event["baseline"]
        for emoji, added in present.items():
            identity = f"{key}/{emoji}/{added!r}"
            existing = db.execute(
                "SELECT removed,baseline FROM reaction_events WHERE id=?", (identity,)
            ).fetchone()
            if existing:
                if existing["removed"] is not None:
                    # Present again at the same native time: the same reaction.
                    db.execute(
                        "UPDATE reaction_events SET removed=NULL WHERE id=?",
                        (identity,),
                    )
                    changed = changed or not existing["baseline"]
                continue
            db.execute(
                "INSERT INTO reaction_events "
                "(id,source,chat_key,message_id,emoji,added,observed,baseline) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    identity,
                    key,
                    row["chat_key"],
                    row["message_id"],
                    emoji,
                    added,
                    now,
                    int(added < start),
                ),
            )
            changed = changed or added >= start
        return changed

    def snapshot(self, key, db=None):
        query = "SELECT body,observed FROM message_reactions WHERE source=?"
        found = (
            db.execute(query, (key,)).fetchone()
            if db
            else next(iter(self.store.rows(query, (key,))), None)
        )
        if not found:
            return None
        return ReactionSnapshot.model_validate(
            {**json.loads(found["body"]), "observed": found["observed"]}
        )

    def view(self, key, db=None):
        """Display chips for one native message; None when it has none."""
        snapshot = self.snapshot(key, db)
        if not snapshot or not snapshot.chips:
            return None
        chips = []
        for chip in snapshot.chips:
            peer = None
            if snapshot.complete and snapshot.actors is not None:
                peer = any(
                    a.actor == "peer" and a.reaction == chip.reaction
                    for a in snapshot.actors
                )
            elif chip.mine is not None:
                peer = chip.count > int(chip.mine)
            chips.append(
                ReactionChipView(
                    reaction=chip.reaction, count=chip.count, mine=chip.mine, peer=peer
                )
            )
        return ReactionView(
            chips=chips, own_known=snapshot.own() is not None
        ).model_dump()


def recover(db):
    """After job recovery: a dispatched Reaction Assessment is an unknown
    outcome that is never resampled; undispatched work simply runs again."""
    for row in db.execute("SELECT chat_key,body FROM reaction_state").fetchall():
        body = json.loads(row["body"])
        if body["state"] != "pending":
            continue
        job = db.execute(
            "SELECT state FROM jobs WHERE id=?", (body["attempt"],)
        ).fetchone()
        if job and job["state"] in ("queued", "claimed", "running"):
            continue
        body.update(state="failed", failure="interrupted")
        db.execute(
            "UPDATE reaction_state SET body=? WHERE chat_key=?",
            (json.dumps(body), row["chat_key"]),
        )


def expire(db, cutoff):
    db.execute("DELETE FROM reaction_state WHERE added<?", (cutoff,))
