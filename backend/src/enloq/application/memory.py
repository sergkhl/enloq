"""Person Memory and Account Memory (ADR 55): short cited notes of what one
private chat's person cares about, and of what the account's holder cares
about, folded together from messages already sent to a model (ADR 15).

Person Memory is reference context for newer media and calmed rewrites, and
both memories reach Response Suggestions; neither is a rewrite target, a task
list or outgoing input. The owner can see, edit, add and clear them; they are
kept until cleared or the account's local data is removed. Account Memory
lives in the same tables under the subject ``account``; its citations are
(chat, message) pairs.
"""

import hashlib
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain.models import Conflict, ProcessingPaused, Refused
from ..inference.base import ProviderRefused
from ..inference.openrouter import TAKEAWAY_CHARACTERS
from ..storage.store import RETENTION, WINDOW

ENTRIES = 40
BYTES = 3000
FOLD_INPUT = 12000
QUIET = 30 * 60
ACCOUNT = "account"
# Low-priority memory work, traits included: idle-gated, one call at a time
# across the server.
ROLES = ("chat_memory", "trait_scales", "trait_notes")


class Memory:
    # Remember this chat for a chat without its own choice: Off since ADR 56
    # (Q9 A, the owner's "Disable chat memories by default").
    default = False

    def __init__(self, app):
        self.app, self.store = app, app.store
        self.enabled = lambda: True
        self.idle = self.local_idle  # The account manager installs a global check.

    # ---- reading ---------------------------------------------------------

    def state(self, subject):
        rows = self.store.rows(
            "SELECT * FROM memory_state WHERE chat_key=?", (subject,)
        )
        return rows[0] if rows else {"revision": 0, "coverage": 0, "error": None}

    def entries(self, subject):
        return [
            {
                **r,
                "citations": json.loads(r["citations"]),
                "outdated": json.loads(r["outdated"]) if r["outdated"] else None,
            }
            for r in self.store.rows(
                "SELECT * FROM memory_entries WHERE chat_key=? ORDER BY created,id",
                (subject,),
            )
        ]

    def view(self, chat_key):
        """One chat's Person Memory."""
        self.store.chat(chat_key)
        state = self.state(chat_key)
        job = self.job(chat_key)
        return {
            "entries": [
                {
                    **entry,
                    "sources": [
                        self.availability(chat_key, identity)
                        for identity in entry["citations"]
                    ],
                }
                for entry in self.entries(chat_key)
            ],
            "revision": state["revision"],
            "coverage": state["coverage"],
            "error": state["error"],
            "enabled": self.enabled(),
            "waiting": len(self.candidates(chat_key)),
            "updating": bool(job and job["state"] == "running"),
        }

    def account_view(self):
        """The account's own Account Memory; its citations name their chats."""
        state = self.state(ACCOUNT)
        return {
            "entries": [
                {
                    **{
                        k: entry[k]
                        for k in (
                            "id",
                            "text",
                            "source",
                            "outdated",
                            "created",
                            "guidance",
                            "defaults",
                        )
                    },
                    "citations": [identity for _, identity in entry["citations"]],
                    "sources": [
                        {
                            **self.availability(chat, identity),
                            "chat": chat.rsplit("/", 1)[-1],
                        }
                        for chat, identity in entry["citations"]
                    ],
                }
                for entry in self.entries(ACCOUNT)
            ],
            "revision": state["revision"],
            "enabled": self.enabled(),
        }

    def availability(self, chat_key, identity):
        rows = self.store.rows(
            "SELECT deleted,date FROM messages WHERE chat_key=? AND message_id=?",
            (chat_key, identity),
        )
        return {
            "id": identity,
            "availability": "not_cached"
            if not rows or rows[0]["date"] < self.store.clock() - RETENTION
            else "deleted"
            if rows[0]["deleted"]
            else "cached",
        }

    def job(self, chat_key):
        rows = self.store.rows(
            "SELECT * FROM jobs WHERE role='chat_memory' AND scope=? AND state IN ('queued','claimed','running')",
            (chat_key,),
        )
        return rows[0] if rows else None

    def remembered(self, chat_key, db=None):
        """Remember this chat (ADR 55 Q7, Q18): the chat's own choice, or the
        default, Off since ADR 56. Off, the chat teaches neither memory and
        reads neither."""
        sql, args = "SELECT enabled FROM remembering WHERE chat_key=?", (chat_key,)
        rows = (
            [dict(r) for r in db.execute(sql, args)]
            if db is not None
            else self.store.rows(sql, args)
        )
        return bool(rows[0]["enabled"]) if rows else self.default

    def usable(self, chat_key):
        return (
            self.enabled()
            and self.app.rewriting.chat_eligible(chat_key)
            and self.remembered(chat_key)
        )

    def for_target(self, chat_key, first_id, first_date):
        """Person Memory for an incoming target newer than its coverage, framed
        as unverified reference data. Older targets never receive it."""
        if not self.usable(chat_key) or first_date < self.store.clock() - WINDOW:
            return None
        state = self.state(chat_key)
        entries = self.entries(chat_key)
        if not entries or first_id <= state["coverage"]:
            return None
        return {
            "note": "Unverified reference facts from earlier messages",
            "entries": [
                {
                    "id": e["id"],
                    "text": e["text"],
                    "citations": e["citations"],
                    **({"possibly_outdated": True} if e["outdated"] else {}),
                }
                for e in entries
            ],
        }

    def for_suggestions(self, chat_key):
        """ADR 55 Q1 B, Q15, Q16: what the person and the owner care about,
        for optional reply wording; traits never. An owner note may be said
        only in the chat every one of its citations comes from; any other
        only shapes what fits."""
        if not self.usable(chat_key):
            return None
        person, account = self.entries(chat_key), self.entries(ACCOUNT)
        if not person and not account:
            return None
        today = datetime.fromtimestamp(
            self.store.clock(), ZoneInfo(self.store.timezone)
        ).date()
        return {
            "today": today.isoformat(),
            "person": [
                {
                    "text": e["text"],
                    **({"possibly_outdated": True} if e["outdated"] else {}),
                }
                for e in person
            ],
            "owner": [
                {
                    "text": e["text"],
                    "may_say": bool(e["citations"])
                    and all(chat == chat_key for chat, _ in e["citations"]),
                }
                for e in account
            ],
        }

    # ---- intake ----------------------------------------------------------

    def record(self, chat_key, inputs):
        """Messages just sent to the model (targets and context) may fold later.
        Messages received while AI processing is Off are never sent, and those
        sent while Memory or Remember this chat is Off never enter (Q20)."""
        if not self.enabled() or not self.remembered(chat_key):
            return
        for key in inputs:
            rows = self.store.rows(
                "SELECT * FROM messages WHERE key=? AND chat_key=? AND deleted=0",
                (key, chat_key),
            )
            if rows:
                self._admit(rows[0], "sent")

    def _admit(self, row, origin):
        digest = self.source_snapshot(row)
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO analyzed (key,chat_key,message_id,date,revision,digest,origin) VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET revision=excluded.revision,digest=excluded.digest",
                (
                    row["key"],
                    row["chat_key"],
                    row["message_id"],
                    row["date"],
                    row["revision"],
                    digest,
                    origin,
                ),
            )

    def start(self, chat_key):
        """ADR 55 Q20: each memory starts once from the local cache. Cached
        messages dated before the store's start become sources; anything later
        enters only when it is sent to a model. No Telegram history is read,
        and a chat switched off never starts again."""
        cutoff = self.store.rows("SELECT value FROM metadata WHERE key='memory_start'")
        if not cutoff or self.store.rows(
            "SELECT 1 FROM memory_starts WHERE chat_key=? AND closed IS NOT NULL",
            (chat_key,),
        ):
            return 0
        rows = self.store.rows(
            "SELECT * FROM messages WHERE chat_key=? AND deleted=0 AND date<=? AND date>=? AND key NOT IN (SELECT key FROM analyzed WHERE chat_key=?) ORDER BY message_id",
            (
                chat_key,
                float(cutoff[0]["value"]),
                self.store.clock() - RETENTION,
                chat_key,
            ),
        )
        for row in rows:
            self._admit(row, "start")
        return len(rows)

    def start_switched_on(self, chat_key):
        """ADR 56 Q11 B: a chat that was Off only by default learns from its
        30-day cache when switched on, as a new account's memory starts; the
        cache only, never a Telegram read."""
        rows = self.store.rows(
            "SELECT * FROM messages WHERE chat_key=? AND deleted=0 AND date>=? AND key NOT IN (SELECT key FROM analyzed WHERE chat_key=?) ORDER BY message_id",
            (chat_key, self.store.clock() - RETENTION, chat_key),
        )
        for row in rows:
            self._admit(row, "start")
        return len(rows)

    def candidates(self, chat_key):
        """Exact unconsumed source versions (Q19 B: the latest 20 included).
        Coverage guards future targets; it is not a source-consumption cursor."""
        pending = []
        for row in self.store.rows(
            "SELECT analyzed.*,messages.body FROM analyzed JOIN messages ON messages.key=analyzed.key WHERE analyzed.chat_key=? AND messages.deleted=0 AND messages.revision=analyzed.revision ORDER BY analyzed.message_id",
            (chat_key,),
        ):
            digest = self.source_snapshot(row)
            if row["digest"] in (None, digest) and row["consumed"] != digest:
                pending.append(row)
        return pending

    def local_idle(self, job_id=None):
        return (
            not self.store.rows(
                "SELECT id FROM jobs WHERE state IN ('queued','claimed','running') AND role IN ('incoming_rewrite','transcript','emotion_classification','hostility_screen','response_suggestions','suggestion_labels') LIMIT 1"
            )
            and not self.app.media.transcription_lock.locked()
            and (not hasattr(self.app, "briefings") or self.app.briefings.local_idle())
        )

    def quiet(self, chat_key):
        rows = self.store.rows("SELECT activity FROM chats WHERE key=?", (chat_key,))
        return bool(rows and self.store.clock() - rows[0]["activity"] >= QUIET)

    def ready(self, job):
        return (
            self.usable(job["scope"])
            and self.quiet(job["scope"])
            and self.idle(job["id"])
        )

    def due(self, chat_key):
        return self.quiet(chat_key) and bool(self.candidates(chat_key))

    def schedule(self, chat_key):
        """Queue one low-priority fold when due. A failed fold waits for Retry."""
        try:
            self.app.admit_ai()
        except Refused:
            return None
        if not self.usable(chat_key) or self.job(chat_key):
            return None
        self.start(chat_key)
        state = self.state(chat_key)
        if state["error"] or not self.due(chat_key):
            return None
        return self._enqueue(chat_key, state["revision"])

    def schedule_all(self):
        for chat in self.store.recent_chats():
            self.schedule(chat["key"])

    def _enqueue(self, chat_key, revision, *, refusals=0, due=None, explicit=False):
        identity = uuid.uuid4().hex
        payload = {"class": "memory", "refusals": refusals}
        if explicit:
            payload["explicit_retry"] = True  # Runs even while automation waits.
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,'queued',?,?,NULL)",
                (
                    identity,
                    chat_key,
                    "chat_memory",
                    str(revision),
                    due or self.store.clock(),
                    json.dumps(payload),
                ),
            )
        return identity

    # ---- folding ---------------------------------------------------------

    def projection(self, row):
        body = json.loads(row["body"])
        derived = self.store.rows(
            "SELECT body FROM derivatives WHERE source=? AND revision=?",
            (row["key"], body["revision"]),
        )
        extra = json.loads(derived[0]["body"]) if derived else {}
        value = {
            "id": body["message_id"],
            "outgoing": body["outgoing"],
            "date": body["date"],
            "text": body["text"],
        }
        if extra.get("transcript"):
            value["transcript"] = extra["transcript"]["text"]
        if extra.get("image_context"):
            value["image_context"] = extra["image_context"]
        return value

    def source_snapshot(self, row):
        """Bind both native revision and the cached interpretation actually used.
        Historical native versions are never read here."""
        value = {
            "revision": json.loads(row["body"])["revision"],
            **self.projection(row),
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def packet(self, chat_key, person, account):
        messages, used, inputs = [], 0, {}

        def include(row):
            nonlocal used
            value = self.projection(row)
            if value["id"] in {m["id"] for m in messages}:
                return True
            size = len(json.dumps(value, ensure_ascii=False).encode())
            if used + size > FOLD_INPUT:
                return False
            messages.append(value)
            inputs[row["key"]] = self.source_snapshot(row)
            used += size
            return True

        pending = []
        for row in self.candidates(chat_key):
            if not include(row):
                if not pending:
                    raise Refused(
                        "Memory source exceeds the input bound; previous memory kept"
                    )
                break
            pending.append(row)

        def provenance(identities):
            """Retained citations of this chat, checked by exact local ID and
            supplied when they were already admitted."""
            result = []
            for identity in identities:
                availability = "not_cached"
                rows = self.store.rows(
                    "SELECT messages.* FROM messages JOIN analyzed ON analyzed.key=messages.key WHERE messages.chat_key=? AND messages.message_id=? AND messages.deleted=0",
                    (chat_key, identity),
                )
                if rows:
                    availability = "supplied" if include(rows[0]) else "not_supplied"
                result.append({"id": identity, "availability": availability})
            return result

        def memory(entries, pairs):
            items = []
            for entry in entries:
                cited = self._pairs(entry, pairs, chat_key)
                here = [i for chat, i in cited if chat == chat_key]
                item = {
                    "id": entry["id"],
                    "text": entry["text"],
                    "source": entry["source"],
                    "citations": here,
                    "provenance": provenance(here),
                }
                if pairs:
                    # Another chat's originals never reach this chat's fold.
                    item["cited_elsewhere"] = len(cited) - len(here)
                items.append(item)
            return items

        def limits(entries):
            owners = [e for e in entries if e["source"] == "owner"]
            return {
                "entries": ENTRIES,
                "entry_bytes": 300,
                "entry_characters": TAKEAWAY_CHARACTERS,
                "bytes": BYTES,
                "generated_entries": max(0, ENTRIES - len(owners)),
                "generated_bytes": max(
                    0, BYTES - sum(len(e["text"].encode()) for e in owners)
                ),
            }

        return {
            "person": {"memory": memory(person, False), "limits": limits(person)},
            "account": {"memory": memory(account, True), "limits": limits(account)},
            "messages": messages,
            "new_source_ids": [r["message_id"] for r in pending],
            "inputs": inputs,
        }, pending

    @staticmethod
    def _pairs(entry, pairs, chat_key):
        return (
            [tuple(c) for c in entry["citations"]]
            if pairs
            else [(chat_key, identity) for identity in entry["citations"]]
        )

    def current(self, job, packet):
        if not self.app.authorized() or not self.usable(job["scope"]):
            return False
        if str(self.state(job["scope"])["revision"]) != job["input_revision"]:
            return False
        for key, digest in packet["inputs"].items():
            rows = self.store.rows(
                "SELECT * FROM messages WHERE key=? AND deleted=0", (key,)
            )
            if not rows or self.source_snapshot(rows[0]) != digest:
                return False
        return True

    async def fold(self, job):
        chat_key = job["scope"]
        current = self.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not current or current[0]["state"] not in ("queued", "claimed"):
            return
        try:
            self.app.admit_ai()
            if not self.ready(job):
                raise ProcessingPaused(
                    "Waiting for 30 quiet minutes and idle communication"
                )
            if not self.usable(chat_key):
                raise Refused("Memory is Off or this chat is ineligible")
            state = self.state(chat_key)
            if str(state["revision"]) != job["input_revision"]:
                raise Refused("Memory changed before the update")
            person, account = self.entries(chat_key), self.entries(ACCOUNT)
            account_revision = self.state(ACCOUNT)["revision"]
            packet, pending = self.packet(chat_key, person, account)
            if not pending:
                self._finish(job, "done")
                return
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET payload=json_set(payload,'$.publication_pending',1) WHERE id=?",
                    (job["id"],),
                )

            def admit():
                if not self.ready(job):
                    raise ProcessingPaused("Waiting for idle communication")
                return self.current(job, packet)

            result = await self.app.rewriting.work.run(
                self.app.engine,
                job,
                packet,
                admit,
                klass="memory",
            )
            self.publish(
                job,
                person,
                account,
                account_revision,
                packet,
                pending,
                result,
                packet["guidance"]["version"],
            )
        except ProviderRefused as refusal:
            self._refused(job, refusal)
        except ProcessingPaused:
            self._defer(job)
        except Refused as error:
            dispatched = bool(
                self.store.rows("SELECT id FROM reservations WHERE id=?", (job["id"],))
            )
            if not dispatched and "budget" in str(error).lower():
                self._defer(job)
                return
            if "changed" not in str(error):
                self._fail(chat_key, str(error))
            self._finish(job, "failed" if dispatched else "cancelled", str(error))

    def publish(
        self, job, person, account, account_revision, packet, pending, result, guidance
    ):
        """Apply both validated change sets atomically, or reject them whole.
        An Account Memory note that cites none of the holder's own messages is
        dropped (ADR 55 Q5): it would describe someone else."""
        chat_key = job["scope"]
        if not self.current(job, packet):
            self._finish(job, "superseded", "Memory sources changed during the update")
            return
        supplied = {m["id"]: m for m in packet["messages"]}
        own = {i for i, m in supplied.items() if m["outgoing"]}
        changes = {}
        for side, entries, pairs in (
            ("person", person, False),
            ("account", account, True),
        ):
            update = getattr(result, side)
            known = {e["id"]: e for e in entries}
            additions = []
            for addition in update.add:
                retained = set()
                for identity in addition.based_on:
                    if known.get(identity, {}).get("source") != "model":
                        return self._reject(
                            job, "Memory update used unknown retained provenance"
                        )
                    retained.update(self._pairs(known[identity], pairs, chat_key))
                new = {(chat_key, i) for i in addition.citations}
                allowed = {(chat_key, i) for i in supplied}
                if not pairs:
                    allowed |= retained
                if not new <= allowed:
                    return self._reject(
                        job, "Memory update cited messages it was not given"
                    )
                if pairs and not {i for _, i in new} & own:
                    continue
                citations = sorted(new | retained) if pairs else addition.citations
                additions.append((addition.text, citations))
            for identity in update.remove:
                if known.get(identity, {}).get("source") != "model":
                    return self._reject(job, "Memory update removed an unknown entry")
            for flag in update.outdated:
                if known.get(flag.id, {}).get("source") != "owner" or not (
                    set(flag.citations) <= set(supplied)
                ):
                    return self._reject(job, "Memory update flagged an invalid entry")
            owners = [e for e in entries if e["source"] == "owner"]
            generated = [
                e["text"]
                for e in entries
                if e["source"] == "model" and e["id"] not in update.remove
            ] + [text for text, _ in additions]
            if len(generated) > max(0, ENTRIES - len(owners)) or sum(
                len(t.encode()) for t in generated
            ) > max(0, BYTES - sum(len(e["text"].encode()) for e in owners)):
                return self._reject(
                    job, "Memory update exceeds the allowance; previous memory kept"
                )
            changes[side] = (update, additions)
        now = self.store.clock()
        defaults = (packet.get("guidance") or {}).get("defaults_revision")
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT revision FROM memory_state WHERE chat_key=?", (chat_key,)
            ).fetchone()
            theirs = db.execute(
                "SELECT revision FROM memory_state WHERE chat_key=?", (ACCOUNT,)
            ).fetchone()
            if (
                str(row[0] if row else 0) != job["input_revision"]
                or (theirs[0] if theirs else 0) != account_revision
            ):
                # An owner edit or a source deletion arrived meanwhile.
                db.execute(
                    "UPDATE jobs SET state='superseded',error='Memory changed during the update' WHERE id=?",
                    (job["id"],),
                )
                return
            for side, subject in (("person", chat_key), ("account", ACCOUNT)):
                update, additions = changes[side]
                for identity in update.remove:
                    db.execute("DELETE FROM memory_entries WHERE id=?", (identity,))
                for flag in update.outdated:
                    citations = (
                        [[chat_key, i] for i in flag.citations]
                        if side == "account"
                        else flag.citations
                    )
                    db.execute(
                        "UPDATE memory_entries SET outdated=? WHERE id=?",
                        (json.dumps({"citations": citations}), flag.id),
                    )
                for offset, (text, citations) in enumerate(additions):
                    db.execute(
                        "INSERT INTO memory_entries (id,chat_key,text,citations,source,outdated,created,guidance,defaults) VALUES (?,?,?,?,'model',NULL,?,?,?)",
                        (
                            uuid.uuid4().hex[:12],
                            subject,
                            text,
                            json.dumps([list(c) for c in citations])
                            if side == "account"
                            else json.dumps(citations),
                            now + offset * 1e-6,
                            guidance,
                            defaults,
                        ),
                    )
                if update.remove or update.outdated or additions:
                    if side == "account":
                        db.execute(
                            "INSERT INTO memory_state VALUES (?,1,0,NULL) ON CONFLICT(chat_key) DO UPDATE SET revision=revision+1",
                            (ACCOUNT,),
                        )
            coverage = max(
                [m["id"] for m in packet["messages"]]
                + [i for entry in person for i in entry["citations"]],
                default=0,
            )
            db.execute(
                "INSERT INTO memory_state VALUES (?,?,?,NULL) ON CONFLICT(chat_key) DO UPDATE SET revision=revision+1,coverage=max(coverage,excluded.coverage),error=NULL",
                (chat_key, int(job["input_revision"]) + 1, coverage),
            )
            for source in pending:
                db.execute(
                    "UPDATE analyzed SET consumed=? WHERE key=? AND revision=?",
                    (
                        packet["inputs"][source["key"]],
                        source["key"],
                        source["revision"],
                    ),
                )
            db.execute(
                "UPDATE jobs SET state='done',payload=json_remove(payload,'$.publication_pending') WHERE id=?",
                (job["id"],),
            )
        self.on_published(chat_key)

    def on_published(self, chat_key):
        """A published update; traits follow it (ADR 55 Q22)."""
        traits = getattr(self.app, "traits", None)
        if traits is not None:
            traits.schedule(chat_key)

    def _defer(self, job):
        # No dispatch occurred, so retain the same attempt and spend nothing.
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='queued',due=?,payload=json_remove(payload,'$.publication_pending') WHERE id=? AND state IN ('queued','claimed')",
                (self.store.clock() + 60, job["id"]),
            )

    def _reject(self, job, error):
        self._fail(job["scope"], error)
        self._finish(job, "failed", error)

    def _fail(self, chat_key, error):
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO memory_state VALUES (?,0,0,?) ON CONFLICT(chat_key) DO UPDATE SET error=excluded.error",
                (chat_key, error[:200]),
            )

    def _finish(self, job, state, error=None):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state=?,error=? WHERE id=? AND state!='superseded'",
                (state, error, job["id"]),
            )

    def _refused(self, job, refusal):
        payload = json.loads(job["payload"] or "{}")
        count = payload.get("refusals", 0) + 1
        if count > 3:
            self._fail(job["scope"], "The provider refused repeatedly; use Retry")
            return
        delay = (
            refusal.retry_after
            if refusal.retry_after is not None
            else min(30 * 2 ** (count - 1), 600)
        )
        self._enqueue(
            job["scope"],
            job["input_revision"],
            refusals=count,
            explicit=payload.get("explicit_retry", False),
            due=self.store.clock() + delay,
        )

    # ---- owner actions and source changes --------------------------------

    def _change(self, subject, revision, change):
        if subject != ACCOUNT:
            self.store.chat(subject)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT revision FROM memory_state WHERE chat_key=?", (subject,)
            ).fetchone()
            current = row[0] if row else 0
            if revision is not None and revision != current:
                raise Conflict("Memory changed; reload before editing")
            change(db)
            db.execute(
                "INSERT INTO memory_state VALUES (?,1,0,NULL) ON CONFLICT(chat_key) DO UPDATE SET revision=revision+1",
                (subject,),
            )
        return self.account_view() if subject == ACCOUNT else self.view(subject)

    def add(self, subject, revision, text):
        text = self._text(text)

        def change(db):
            count = db.execute(
                "SELECT count(*) FROM memory_entries WHERE chat_key=?", (subject,)
            ).fetchone()[0]
            if count >= ENTRIES:
                raise Refused(f"A memory holds at most {ENTRIES} entries")
            db.execute(
                "INSERT INTO memory_entries (id,chat_key,text,citations,source,outdated,created) VALUES (?,?,?,'[]','owner',NULL,?)",
                (uuid.uuid4().hex[:12], subject, text, self.store.clock()),
            )

        return self._change(subject, revision, change)

    def edit(self, subject, revision, identity, text):
        text = self._text(text)

        def change(db):
            # An edited entry is the owner's; the model never changes it again.
            if not db.execute(
                "UPDATE memory_entries SET text=?,source='owner',outdated=NULL WHERE id=? AND chat_key=?",
                (text, identity, subject),
            ).rowcount:
                raise Refused("Memory entry unavailable")

        return self._change(subject, revision, change)

    def remove(self, subject, revision, identity):
        return self._change(
            subject,
            revision,
            lambda db: db.execute(
                "DELETE FROM memory_entries WHERE id=? AND chat_key=?",
                (identity, subject),
            ),
        )

    def clear(self, subject, revision):
        return self._change(
            subject,
            revision,
            lambda db: db.execute(
                "DELETE FROM memory_entries WHERE chat_key=?", (subject,)
            ),
        )

    def retry(self, chat_key):
        """After a failure: one deliberate new attempt, never an automatic one."""
        self.app.admit_ai()
        if not self.usable(chat_key):
            raise Refused("Memory is Off or this chat is ineligible")
        if self.job(chat_key):
            raise Refused("Memory is already updating")
        if not self.candidates(chat_key):
            raise Refused("No analysed messages are waiting")
        with self.store.transaction() as db:
            db.execute(
                "UPDATE memory_state SET error=NULL WHERE chat_key=?", (chat_key,)
            )
        self._enqueue(chat_key, self.state(chat_key)["revision"], explicit=True)
        return self.view(chat_key)

    def remember_view(self, chat_key):
        self.private(chat_key)
        return {"enabled": self.remembered(chat_key)}

    def remember(self, chat_key, enabled):
        """Remember this chat. Switching it off stops this chat's waiting
        work and drops what it had admitted but not yet learned, so nothing
        from that time is learned later (Q20); what memory already holds stays
        until cleared. Switching on a chat that was Off only by default starts
        it from the cache (ADR 56 Q11 B)."""
        self.private(chat_key)
        now = self.store.clock()
        with self.store.transaction() as db:
            by_default = not db.execute(
                "SELECT 1 FROM remembering WHERE chat_key=?", (chat_key,)
            ).fetchone()
            changed = db.execute(
                "INSERT INTO remembering VALUES (?,?,?) ON CONFLICT(chat_key) DO UPDATE "
                "SET enabled=excluded.enabled,changed=excluded.changed "
                "WHERE enabled!=excluded.enabled",
                (chat_key, int(enabled), now),
            ).rowcount
            if changed and not enabled:
                db.execute(
                    f"UPDATE jobs SET state='cancelled',error='Remember this chat is Off' WHERE role IN ({','.join('?' * len(ROLES))}) AND scope=? AND state='queued'",
                    (*ROLES, chat_key),
                )
                db.execute(
                    "DELETE FROM analyzed WHERE chat_key=? AND consumed IS NULL",
                    (chat_key,),
                )
                db.execute(
                    "INSERT INTO memory_starts VALUES (?,?) ON CONFLICT(chat_key) DO UPDATE SET closed=excluded.closed",
                    (chat_key, now),
                )
        # Read after the transaction: the Memory switch lives in the registry.
        if enabled and by_default and not self.default and self.enabled():
            self.start_switched_on(chat_key)
        return {"enabled": self.remembered(chat_key)}

    def private(self, chat_key):
        if self.store.chat(chat_key).kind != "private":
            raise Refused("Memory is for private chats only")

    def forget(self, message, *, force=False):
        """An edited or deleted cited source removes its entries in both
        memories with no model call, and discards any fold that started from
        the older memory."""
        chat_key = f"{message.account_id}/{message.peer}"
        with self.store.transaction() as db:
            db.execute("DELETE FROM analyzed WHERE key=?", (message.key,))
            touched = set()
            for row in db.execute(
                "SELECT id,chat_key,citations,outdated,source FROM memory_entries WHERE chat_key IN (?,?)",
                (chat_key, ACCOUNT),
            ).fetchall():
                account = row["chat_key"] == ACCOUNT
                cited = (
                    [chat_key, message.message_id] if account else message.message_id
                )
                if row["source"] == "model" and cited in json.loads(row["citations"]):
                    db.execute("DELETE FROM memory_entries WHERE id=?", (row["id"],))
                    touched.add(row["chat_key"])
                elif row["outdated"] and cited in json.loads(row["outdated"]).get(
                    "citations", []
                ):
                    db.execute(
                        "UPDATE memory_entries SET outdated=NULL WHERE id=?",
                        (row["id"],),
                    )
                    touched.add(row["chat_key"])
            if force:
                touched.add(chat_key)
            for subject in touched:
                db.execute(
                    "INSERT INTO memory_state VALUES (?,1,0,NULL) ON CONFLICT(chat_key) DO UPDATE SET revision=revision+1",
                    (subject,),
                )
        traits = getattr(self.app, "traits", None)
        if traits is not None:
            traits.forget(message)

    @staticmethod
    def _text(text):
        text = (text or "").strip()
        if not text or len(text.encode()) > 300:
            raise Refused("A memory entry needs 1 to 300 bytes of text")
        return text
