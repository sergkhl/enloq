"""Disposable exact-source decisions and durable owner-correctable bot ignores."""

import json
import re
from itertools import combinations

from ..domain.group_filtering import FILTER_ROLES, RULES_HASH, digest, media_complete
from ..domain.models import Message, Refused, reply_target

DDL = (
    "CREATE TABLE IF NOT EXISTS group_filter_decisions (source TEXT NOT NULL REFERENCES messages(key) ON DELETE CASCADE, chat_key TEXT NOT NULL, fingerprint TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(source,fingerprint))",
    "CREATE TABLE IF NOT EXISTS group_bot_decisions (chat_key TEXT NOT NULL REFERENCES chats(key), author_peer TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(chat_key,author_peer))",
)


class GroupFilters:
    def __init__(self, store):
        self.store = store

    def decision(self, source, fingerprint, *, db=None):
        sql = "SELECT body FROM group_filter_decisions WHERE source=? AND fingerprint=?"
        rows = (
            list(db.execute(sql, (source, fingerprint)))
            if db is not None
            else self.store.rows(sql, (source, fingerprint))
        )
        return json.loads(rows[0]["body"]) if rows else None

    def source_decisions(self, source, *, db=None):
        sql = (
            "SELECT body FROM group_filter_decisions WHERE source=? ORDER BY rowid DESC"
        )
        rows = (
            db.execute(sql, (source,))
            if db is not None
            else self.store.rows(sql, (source,))
        )
        return [json.loads(row["body"]) for row in rows]

    def projection(self, message, *, db=None):
        if db is None:
            with self.store.transaction() as db:
                return self.projection(message, db=db)
        row = db.execute(
            "SELECT body FROM derivatives WHERE source=? AND revision=?",
            (message.key, message.revision),
        ).fetchone()
        cached = json.loads(row["body"]) if row else {}
        consumed = {
            k: cached[k] for k in ("image_context", "transcript") if cached.get(k)
        }
        value = dict(
            id=message.message_id,
            revision=message.revision,
            date=message.date,
            text=message.text,
            media=message.media,
            service=message.service,
            reply_to=reply_target(message),
            author_peer=message.author_peer,
            is_bot=message.sender_is_bot,
            **consumed,
        )
        proof = dict(
            revision=message.revision,
            date=message.date,
            author_peer=message.author_peer,
            sender_is_bot=message.sender_is_bot,
            source_hash=digest(value),
        )
        return value, proof, media_complete(value)

    @staticmethod
    def _gained_media(value, current, proof):
        """Whether the consumed evidence is this source before media prepared since.

        Classification reads the meanings and transcripts cached at the time and
        the same update may then prepare the rest. A changed meaning or
        transcript is different evidence and never matches.
        """
        prepared = [k for k in ("image_context", "transcript") if k in value]
        return any(
            proof
            == {
                **current,
                "source_hash": digest(
                    {k: v for k, v in value.items() if k not in dropped}
                ),
            }
            for size in range(1, len(prepared) + 1)
            for dropped in combinations(prepared, size)
        )

    def evidence_current(
        self, request, *, db=None, admission=False, standing=False, memo=None
    ):
        """Exact retained evidence, independent of latest-ten membership.

        Publication uses the same primitive under its own transaction. A
        remembered ignore deliberately does not depend on this disposable proof.
        ``admission`` asks whether a new request may be dispatched: every
        original is proven in the current intake epoch. Otherwise a decision
        stands through a new epoch until an original is seen to change.
        ``standing`` asks whether an exclusion already consumed by an attempt or
        final still holds: media prepared since leaves it standing too, until a
        later update classifies again. Reuse needs the exact media evidence.
        ``memo`` lets one pass over a day check each decision and each original
        once: neighbouring originals share packets and context.
        """
        if db is None:
            with self.store.transaction() as db:
                return self.evidence_current(
                    request, db=db, admission=admission, standing=standing, memo=memo
                )
        answer = ("evidence", request.get("fingerprint"), admission, standing)
        if memo is not None and answer[1] and answer in memo:
            return memo[answer]
        result = self._evidence_current(request, db, admission, standing, memo)
        if memo is not None and answer[1]:
            memo[answer] = result
        return result

    def _evidence_source(self, db, source, admission, memo):
        answer = ("source", source, admission)
        if memo is not None and answer in memo:
            return memo[answer]
        state = None
        row = db.execute("SELECT body FROM messages WHERE key=?", (source,)).fetchone()
        if row:
            message = Message.model_validate_json(row["body"])
            if message.key == source and (
                self.store.groups.current(message, db=db, admission=True)
                if admission
                else self.store.groups.standing(message, db=db)
            ):
                state = self.projection(message, db=db)[:2]
        if memo is not None:
            memo[answer] = state
        return state

    def _evidence_current(self, request, db, admission, standing, memo):
        try:
            if (
                request.get("rules_hash", request.get("packet", {}).get("rules_hash"))
                != RULES_HASH
                or request.get("role") not in FILTER_ROLES
            ):
                return False
            key = request["key"]
            chat = self.store.groups._chat(db, key)
            if not chat.ai_eligible:
                return False
            evidence = request["evidence"]
            targets = request["target_keys"]
            if (
                not isinstance(evidence, dict)
                or not evidence
                or not isinstance(targets, list)
                or not targets
                or len(set(targets)) != len(targets)
                or any(target not in evidence for target in targets)
            ):
                return False
            for source, proof in evidence.items():
                if not isinstance(source, str) or not source.startswith(key + "/"):
                    return False
                state = self._evidence_source(db, source, admission, memo)
                if state is None:
                    return False
                value, current = state
                if current != proof and not (
                    standing and self._gained_media(value, current, proof)
                ):
                    return False
            return True
        except (Refused, KeyError, TypeError, ValueError):
            return False

    def record(
        self,
        request,
        probabilities,
        *,
        failed=False,
        attempt_id=None,
        pending=False,
        settle=False,
        db=None,
    ):
        if db is None:
            with self.store.transaction() as db:
                return self.record(
                    request,
                    probabilities,
                    failed=failed,
                    attempt_id=attempt_id,
                    pending=pending,
                    settle=settle,
                    db=db,
                )
        self.store.groups._chat(db, request["key"])
        # Persist no source text or image meaning. Exact hashes prove reuse while
        # the originals exist; cascading expiry removes ordinary assessments.
        body = {
            k: request[k]
            for k in (
                "key",
                "day",
                "role",
                "evidence",
                "fingerprint",
                "source_keys",
                "target_keys",
                "author_peer",
            )
        }
        body.update(
            probabilities=probabilities,
            failed=failed,
            rules_hash=request["packet"]["rules_hash"],
            attempt_id=attempt_id,
            pending=pending,
        )
        for source in request["target_keys"]:
            if settle:
                if not attempt_id:
                    raise Refused("A classifier outcome requires its first attempt")
                db.execute(
                    "UPDATE group_filter_decisions SET body=? WHERE source=? AND fingerprint=? "
                    "AND json_extract(body,'$.attempt_id')=? AND json_extract(body,'$.pending')=1",
                    (json.dumps(body), source, request["fingerprint"], attempt_id),
                )
            else:
                db.execute(
                    "INSERT OR IGNORE INTO group_filter_decisions VALUES (?,?,?,?)",
                    (source, request["key"], request["fingerprint"], json.dumps(body)),
                )

    def bot(self, key, author_peer, *, db=None):
        if db is None:
            rows = self.store.rows(
                "SELECT body FROM group_bot_decisions WHERE chat_key=? AND author_peer=?",
                (key, author_peer),
            )
            return json.loads(rows[0]["body"]) if rows else None
        row = db.execute(
            "SELECT body FROM group_bot_decisions WHERE chat_key=? AND author_peer=?",
            (key, author_peer),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def ignore_bot(self, key, author_peer, *, day, fingerprint, probability, db):
        self.store.groups._chat(db, key)
        if self.bot(key, author_peer, db=db):
            return False
        value = dict(
            status="ignored",
            day=day,
            fingerprint=fingerprint,
            probability=probability,
            updated_at=self.store.clock(),
        )
        db.execute(
            "INSERT INTO group_bot_decisions VALUES (?,?,?)",
            (key, author_peer, json.dumps(value)),
        )
        return True

    def allow_bot(self, key, author_peer, *, now=None):
        if not isinstance(author_peer, str) or not re.fullmatch(
            r"user:[1-9][0-9]*", author_peer
        ):
            raise Refused("An exact native user is required")
        with self.store.transaction() as db:
            self.store.groups._chat(db, key)
            prior = self.bot(key, author_peer, db=db)
            if prior and prior["status"] == "allowed":
                return False
            # Hide Bots hides a verified bot that was never judged; the owner
            # can exempt it just like a remembered ignore.
            if (
                not prior
                and not db.execute(
                    "SELECT 1 FROM messages WHERE chat_key=? "
                    "AND json_extract(body,'$.author_peer')=? "
                    "AND json_extract(body,'$.sender_is_bot')=1 LIMIT 1",
                    (key, author_peer),
                ).fetchone()
            ):
                raise Refused("This author is not a verified bot in this group")
            value = dict(
                status="allowed", updated_at=self.store.clock() if now is None else now
            )
            db.execute(
                "INSERT OR REPLACE INTO group_bot_decisions VALUES (?,?,?)",
                (key, author_peer, json.dumps(value)),
            )
        return True

    def state_hash(self, key, *, db=None):
        sql = "SELECT author_peer,body FROM group_bot_decisions WHERE chat_key=? ORDER BY author_peer"
        rows = (
            db.execute(sql, (key,)) if db is not None else self.store.rows(sql, (key,))
        )
        return digest([dict(row) for row in rows])

    def native_bot(self, key, author_peer, is_bot, *, db=None):
        """Trusted native owner only: metadata enrichment leaves content intact."""
        if type(is_bot) is not bool or not re.fullmatch(
            r"user:[1-9][0-9]*", author_peer
        ):
            raise Refused("Native bot provenance requires an exact user and boolean")
        if db is None:
            with self.store.transaction() as db:
                return self.native_bot(key, author_peer, is_bot, db=db)
        self.store.groups._chat(db, key)
        changed = 0
        for row in db.execute(
            "SELECT key,body FROM messages WHERE chat_key=? AND json_extract(body,'$.author_peer')=?",
            (key, author_peer),
        ).fetchall():
            message = Message.model_validate_json(row["body"])
            if message.sender_is_bot is not is_bot:
                updated = message.model_copy(update={"sender_is_bot": is_bot})
                changed += db.execute(
                    "UPDATE messages SET body=? WHERE key=?",
                    (updated.model_dump_json(), row["key"]),
                ).rowcount
        return changed
