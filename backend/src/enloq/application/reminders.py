"""Calendar timing and member digests; no inference is admitted by a tick.

A chat may have several Reminder Schedules (ADR 58). Each keeps its latest
occurrence, created two minutes before its alert so that the chat's reminder
set can be prepared in time, and pending once due. Listing, timing and alerts
make no model calls; reminder sets go through Response Suggestions.
"""

import hashlib
import json
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..domain.models import Chat, Conflict, Message, Refused
from ..domain.reminders import (
    KEPT,
    LEAD,
    ChatReminders,
    DigestSettings,
    KeptStatus,
    PendingReminder,
    ReminderOccurrence,
    ReminderRule,
    ReminderView,
    day_bounds,
    first_line,
    latest_occurrence,
    named_day,
    next_digest,
    next_occurrence,
)
from ..storage import reminders as storage
from ..storage.store import RETENTION

SUBJECT = "reminder:"
# Kept copies follow their cached originals at most this often (Q11).
MIRROR_SECONDS = 30


def recent_language(texts):
    """The chat's recent language when its scripts tell it, else None: a
    new schedule's initial fallback language (ADR 58 Q1). Latin text alone
    names no language."""
    counts = {}
    for text in texts:
        for character in text:
            if not character.isalpha():
                continue
            name = unicodedata.name(character, "")
            for script, language in (
                ("CYRILLIC", "Russian"),
                ("GREEK", "Greek"),
                ("HEBREW", "Hebrew"),
                ("ARABIC", "Arabic"),
                ("HIRAGANA", "Japanese"),
                ("KATAKANA", "Japanese"),
                ("HANGUL", "Korean"),
                ("CJK", "Chinese"),
                ("THAI", "Thai"),
                ("GEORGIAN", "Georgian"),
                ("ARMENIAN", "Armenian"),
                ("DEVANAGARI", "Hindi"),
                ("LATIN", None),
            ):
                if name.startswith(script):
                    counts[language] = counts.get(language, 0) + 1
                    break
    if not counts:
        return None
    language = max(counts, key=counts.get)
    joined = "".join(texts)
    if language == "Russian" and any(c in joined for c in "іїєґІЇЄҐ"):
        return "Ukrainian"
    if language == "Chinese" and counts.get("Japanese"):
        return "Japanese"
    return language


class Reminders:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.preferences = lambda: DigestSettings()
        self.global_epoch = lambda: "0"
        self.lock = threading.RLock()
        self.mirrored = 0.0

    def private(self, key):
        self.app.suggestions.private(key)

    def control(self, key, epoch, enabled, db):
        automatic = self.app.suggestions.automatic(key, db)["automatic"]
        return f"{epoch}:{int(enabled)}:{int(automatic)}", not (enabled and automatic)

    # ---- timing ----------------------------------------------------------

    def tick(self):
        """Advance every schedule, create occurrences two minutes before their
        alerts, copy Kept Messages whose day has ended, then prepare the sets
        of chats whose occurrences or conversation changed."""
        settings = self.preferences()
        epoch, enabled = self.global_epoch(), self.app.suggestions.enabled()
        now = self.store.clock()
        prepare, refresh = set(), set()
        with self.lock, self.store.transaction() as db:
            for row in db.execute("SELECT * FROM reminder_schedules").fetchall():
                if self.advance(db, row, settings, epoch, enabled, now):
                    prepare.add(row["chat_key"])
            for row in db.execute(
                "SELECT chat_key FROM reminder_refresh WHERE due<=?", (now,)
            ).fetchall():
                db.execute(
                    "DELETE FROM reminder_refresh WHERE chat_key=?", (row["chat_key"],)
                )
                refresh.add(row["chat_key"])
            self.copy_due(db, now)
            if now - self.mirrored >= MIRROR_SECONDS:
                self.mirrored = now
                storage.mirror(db)
        for key in sorted(prepare):
            self.app.suggestions.reminder_set(key, "prepare")
        for key in sorted(refresh - prepare):
            self.app.suggestions.reminder_set(key, "refresh")

    def advance(self, db, row, settings, epoch, enabled, now):
        """One schedule's tick; True when it created an occurrence."""
        key, schedule = row["chat_key"], row["id"]
        rule = ReminderRule.model_validate_json(row["body"])
        control, paused = self.control(key, epoch, enabled, db)
        if control != row["control"] or paused != bool(row["paused"]):
            # Pausing keeps the schedule; On resumes at the next future
            # delivery, without backfill (Q4).
            upcoming = next_occurrence(rule, now, settings) or (None, None)
            db.execute("DELETE FROM reminder_occurrences WHERE schedule=?", (schedule,))
            db.execute(
                "UPDATE reminder_schedules SET next_at=?,next_date=?,paused=?,control=?,digest_revision=? WHERE id=?",
                (*upcoming, int(paused), control, settings.revision, schedule),
            )
            return False
        if paused:
            return False
        if row["digest_revision"] != settings.revision and rule.delivery == "digest":
            upcoming = next_occurrence(rule, now, settings) or (None, None)
            db.execute(
                "UPDATE reminder_schedules SET next_at=?,next_date=?,digest_revision=? WHERE id=?",
                (*upcoming, settings.revision, schedule),
            )
            # A pending occurrence waits for the new digest time; one being
            # prepared for the old time is made again for the new one.
            db.execute(
                "DELETE FROM reminder_occurrences WHERE schedule=? AND due_at>?",
                (schedule, now),
            )
            db.execute(
                "UPDATE reminder_occurrences SET notify_at=? WHERE schedule=? AND notice IS NULL",
                (next_digest(now, settings), schedule),
            )
            return False
        if row["next_at"] is None or row["next_at"] - LEAD > now:
            return False
        if row["next_at"] > now:
            due, day = row["next_at"], row["next_date"]
            notify = due
        else:
            # After downtime the latest missed occurrence waits for the next
            # digest; an exact alert recovers it once (Q3).
            latest = latest_occurrence(rule, now, settings)
            due, day = latest or (row["next_at"], row["next_date"])
            notify = (
                next_digest(now, settings)
                if rule.delivery == "digest" and now - due > 60
                else due
            )
        sent = db.execute(
            "SELECT last_sent FROM reminder_outbox WHERE chat_key=?", (key,)
        ).fetchone()
        created = False
        if rule.idea or not sent or sent[0] <= due:
            db.execute("DELETE FROM reminder_occurrences WHERE schedule=?", (schedule,))
            db.execute(
                "INSERT INTO reminder_occurrences VALUES (?,?,?,?,?,?,?,NULL,?)",
                (
                    schedule,
                    uuid.uuid4().hex,
                    key,
                    row["revision"],
                    due,
                    day,
                    notify,
                    now,
                ),
            )
            created = True
        upcoming = next_occurrence(rule, max(due, now), settings) or (None, None)
        db.execute(
            "UPDATE reminder_schedules SET next_at=?,next_date=? WHERE id=?",
            (*upcoming, schedule),
        )
        return created

    # ---- Kept Messages (Q11) -----------------------------------------------

    def name_day(self, db, schedule, key, rule, previous, next_at, now):
        """Keep the day the idea names: copy it now when it has ended, or
        when it ends; a changed date or timezone starts afresh."""
        due = (
            datetime.fromtimestamp(next_at, ZoneInfo(rule.timezone)).date()
            if next_at
            else datetime.fromtimestamp(now, ZoneInfo(rule.timezone)).date()
        )
        day = named_day(rule.idea, due)
        current = db.execute(
            "SELECT day FROM reminder_kept_days WHERE schedule=?", (schedule,)
        ).fetchone()
        if day is None:
            storage.forget_kept(db, schedule)
            return
        if (
            current
            and current["day"] == day.isoformat()
            and previous is not None
            and previous.timezone == rule.timezone
        ):
            return
        storage.forget_kept(db, schedule)
        start, end = day_bounds(day, rule.timezone)
        state = "waiting"
        if end <= now - RETENTION:
            state = "expired"
        elif end <= now:
            storage.keep(db, schedule, key, start, end, KEPT)
            state = "kept"
        db.execute(
            "INSERT INTO reminder_kept_days VALUES (?,?,?,?)",
            (schedule, day.isoformat(), state, now if state == "kept" else None),
        )

    def copy_due(self, db, now):
        for row in db.execute(
            "SELECT k.schedule,k.day,s.chat_key,s.body FROM reminder_kept_days k "
            "JOIN reminder_schedules s ON s.id=k.schedule WHERE k.state='waiting'"
        ).fetchall():
            rule = ReminderRule.model_validate_json(row["body"])
            start, end = day_bounds(date.fromisoformat(row["day"]), rule.timezone)
            if end > now:
                continue
            storage.keep(db, row["schedule"], row["chat_key"], start, end, KEPT)
            db.execute(
                "UPDATE reminder_kept_days SET state='kept',copied=? WHERE schedule=?",
                (now, row["schedule"]),
            )

    def preview(self, key, rule, settings):
        """The next delivery and what saving would keep (Q11, Q12)."""
        self.private(key)
        now = self.store.clock()
        upcoming = next_occurrence(rule, now, settings)
        due = (
            date.fromisoformat(upcoming[1])
            if upcoming
            else datetime.fromtimestamp(now, ZoneInfo(rule.timezone)).date()
        )
        day = named_day(rule.idea, due)
        kept = None
        if day is not None:
            start, end = day_bounds(day, rule.timezone)
            state, count = "waiting", 0
            if end <= now - RETENTION:
                state = "expired"
            elif end <= now:
                state = "kept"
                count = self.store.rows(
                    "SELECT count(*) AS count FROM (SELECT 1 FROM messages WHERE chat_key=? "
                    "AND deleted=0 AND date>=? AND date<? LIMIT ?)",
                    (key, start, end, KEPT),
                )[0]["count"]
            kept = KeptStatus(day=day.isoformat(), state=state, count=count)
        return {"next_at": upcoming[0] if upcoming else None, "kept": kept}

    def kept(self, db, schedules):
        """The Kept Messages of these schedules for a reminder set, and their
        identity: a mirrored edit makes the set no longer fit."""
        storage.mirror(db)
        result, identity = [], []
        for schedule in schedules:
            day = db.execute(
                "SELECT day FROM reminder_kept_days WHERE schedule=? AND state='kept'",
                (schedule,),
            ).fetchone()
            if not day:
                continue
            messages, rows = storage.kept(db, schedule)
            if messages:
                result.append({"day": day["day"], "messages": messages})
                identity.append([schedule, rows])
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()
        return result, digest

    def kept_identity(self, db, schedules):
        rows = []
        for schedule in schedules:
            if not db.execute(
                "SELECT 1 FROM reminder_kept_days WHERE schedule=? AND state='kept'",
                (schedule,),
            ).fetchone():
                continue
            messages, kept = storage.kept(db, schedule)
            if messages:
                rows.append([schedule, kept])
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()

    # ---- reading -----------------------------------------------------------

    def view(self, db, row, now=None):
        now = self.store.clock() if now is None else now
        occurrence = db.execute(
            "SELECT * FROM reminder_occurrences WHERE schedule=?", (row["id"],)
        ).fetchone()
        due = bool(
            occurrence
            and occurrence["due_at"] <= now
            and occurrence["schedule_revision"] == row["revision"]
            and not row["paused"]
        )
        kept = db.execute(
            "SELECT day,state FROM reminder_kept_days WHERE schedule=?", (row["id"],)
        ).fetchone()
        count = (
            db.execute(
                "SELECT count(*) FROM reminder_kept WHERE schedule=?", (row["id"],)
            ).fetchone()[0]
            if kept
            else 0
        )
        return ReminderView(
            id=row["id"],
            revision=row["revision"],
            schedule=ReminderRule.model_validate_json(row["body"]),
            # An occurrence being prepared is the next delivery until due.
            next_at=occurrence["due_at"]
            if occurrence and occurrence["due_at"] > now
            else row["next_at"],
            status="paused" if row["paused"] else "active",
            occurrence=ReminderOccurrence(
                id=occurrence["id"],
                due_at=occurrence["due_at"],
                calendar_date=occurrence["calendar_date"],
            )
            if due
            else None,
            kept=KeptStatus(day=kept["day"], state=kept["state"], count=count)
            if kept
            else None,
        )

    def chat(self, key):
        """A chat's schedules, oldest first, and its recent language."""
        self.private(key)
        with self.store.transaction() as db:
            items = [
                self.view(db, row)
                for row in db.execute(
                    "SELECT * FROM reminder_schedules WHERE chat_key=? ORDER BY created,rowid",
                    (key,),
                ).fetchall()
            ]
            texts = []
            for row in db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND deleted=0 AND date>=? "
                "ORDER BY message_id DESC LIMIT 20",
                (key, self.store.clock() - RETENTION),
            ).fetchall():
                message = Message.model_validate_json(row["body"])
                texts.append(message.text)
        return ChatReminders(items=items, language=recent_language(texts))

    def get(self, key, identity):
        self.private(key)
        with self.store.transaction() as db:
            row = self.schedule(db, key, identity)
            return self.view(db, row)

    def schedule(self, db, key, identity):
        row = db.execute(
            "SELECT * FROM reminder_schedules WHERE id=? AND chat_key=?",
            (identity, key),
        ).fetchone()
        if not row:
            raise Conflict("This reminder was removed; reload the reminders")
        return row

    def pending(self, key, db=None):
        """The chat's pending occurrences, for its Reminder Bar (Q13)."""
        sql = (
            "SELECT o.*,s.body FROM reminder_occurrences o JOIN reminder_schedules s ON s.id=o.schedule "
            "WHERE o.chat_key=? AND o.due_at<=? AND s.paused=0 AND o.schedule_revision=s.revision "
            "ORDER BY o.due_at,o.id"
        )
        args = (key, self.store.clock())
        rows = db.execute(sql, args).fetchall() if db else self.store.rows(sql, args)
        return [
            PendingReminder(
                schedule=row["schedule"],
                occurrence=row["id"],
                idea=ReminderRule.model_validate_json(row["body"]).idea,
                due_at=row["due_at"],
            )
            for row in rows
        ]

    def covered(self, key, db):
        """Every occurrence of the chat a reminder set covers: the pending
        ones and any being prepared, earliest first (Q5)."""
        rows = db.execute(
            "SELECT o.id,o.schedule,o.due_at,s.body FROM reminder_occurrences o "
            "JOIN reminder_schedules s ON s.id=o.schedule "
            "WHERE o.chat_key=? AND s.paused=0 AND o.schedule_revision=s.revision "
            "ORDER BY o.due_at,o.id",
            (key,),
        ).fetchall()
        result = []
        for row in rows:
            rule = ReminderRule.model_validate_json(row["body"])
            result.append(
                {
                    "id": row["id"],
                    "schedule": row["schedule"],
                    "due_at": row["due_at"],
                    "idea": rule.idea,
                    "language": rule.language,
                }
            )
        return result

    def owns(self, key, db=None):
        """While any schedule has an occurrence, the chat's suggestion area is
        its reminder set's and no automatic set is prepared (Q5)."""
        sql = (
            "SELECT 1 FROM reminder_occurrences o JOIN reminder_schedules s ON s.id=o.schedule "
            "WHERE o.chat_key=? AND s.paused=0 AND o.schedule_revision=s.revision LIMIT 1"
        )
        return bool(
            db.execute(sql, (key,)).fetchone() if db else self.store.rows(sql, (key,))
        )

    # ---- changes ------------------------------------------------------------

    def create(self, key, rule):
        self.private(key)
        settings = self.preferences()
        epoch, enabled = self.global_epoch(), self.app.suggestions.enabled()
        now = self.store.clock()
        identity = uuid.uuid4().hex
        with self.lock, self.store.transaction() as db:
            upcoming = next_occurrence(rule, now, settings)
            if upcoming is None:
                raise Refused("Choose a date and time still ahead")
            control, paused = self.control(key, epoch, enabled, db)
            db.execute(
                "INSERT INTO reminder_schedules VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    key,
                    1,
                    rule.model_dump_json(),
                    *upcoming,
                    int(paused),
                    control,
                    settings.revision,
                    now,
                ),
            )
            self.name_day(db, identity, key, rule, None, upcoming[0], now)
            return self.view(db, self.schedule(db, key, identity))

    def edit(self, key, identity, revision, rule):
        """Edits begin at the next future delivery: a pending occurrence and
        its options leave, drafts stay as they are (Q1, Q5)."""
        self.private(key)
        settings = self.preferences()
        epoch, enabled = self.global_epoch(), self.app.suggestions.enabled()
        now = self.store.clock()
        with self.lock, self.store.transaction() as db:
            row = self.schedule(db, key, identity)
            if row["revision"] != revision:
                raise Conflict("Reminder changed; reload its schedule")
            upcoming = next_occurrence(rule, now, settings)
            if upcoming is None:
                raise Refused("Choose a date and time still ahead")
            control, paused = self.control(key, epoch, enabled, db)
            db.execute("DELETE FROM reminder_occurrences WHERE schedule=?", (identity,))
            db.execute(
                "UPDATE reminder_schedules SET revision=?,body=?,next_at=?,next_date=?,paused=?,control=?,digest_revision=? WHERE id=?",
                (
                    revision + 1,
                    rule.model_dump_json(),
                    *upcoming,
                    int(paused),
                    control,
                    settings.revision,
                    identity,
                ),
            )
            previous = ReminderRule.model_validate_json(row["body"])
            self.name_day(db, identity, key, rule, previous, upcoming[0], now)
            return self.view(db, self.schedule(db, key, identity))

    def remove(self, key, identity, revision):
        self.private(key)
        with self.lock, self.store.transaction() as db:
            row = self.schedule(db, key, identity)
            if row["revision"] != revision:
                raise Conflict("Reminder changed; reload its schedule")
            storage.end(db, identity)
        return self.chat(key)

    def dismiss(self, key, identity, occurrence):
        """Dismiss completes the pending occurrence either way (Q3)."""
        self.private(key)
        with self.lock, self.store.transaction() as db:
            row = self.schedule(db, key, identity)
            current = db.execute(
                "SELECT 1 FROM reminder_occurrences WHERE schedule=? AND id=? AND due_at<=? AND schedule_revision=?",
                (identity, occurrence, self.store.clock(), row["revision"]),
            ).fetchone()
            if not current or row["paused"]:
                raise Conflict("This reminder is no longer pending")
            storage.complete(db, occurrence)
        return self.chat(key)

    def current(self, key, occurrence, db=None):
        sql = (
            "SELECT 1 FROM reminder_occurrences o JOIN reminder_schedules s ON s.id=o.schedule "
            "WHERE o.chat_key=? AND o.id=? AND o.schedule_revision=s.revision AND s.paused=0 AND o.due_at<=?"
        )
        args = (key, occurrence, self.store.clock())
        return bool(
            db.execute(sql, args).fetchone() if db else self.store.rows(sql, args)
        )


class ReminderClock:
    """Member settings/list plus a durable outbox feeding existing push paths."""

    def __init__(self, manager):
        self.manager, self.registry = manager, manager.registry
        self.clock = time.time
        with self.registry.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS reminder_notices (id TEXT PRIMARY KEY, member TEXT NOT NULL, body TEXT NOT NULL, expires REAL NOT NULL)"
            )

    def settings(self, member, initial_timezone=None):
        saved = self.registry.member_setting(member, "reminder_digest")
        return (
            DigestSettings.model_validate_json(saved)
            if saved
            else DigestSettings(timezone=initial_timezone or "UTC")
        )

    def save_settings(self, member, value, *, initialize=False):
        with self.registry.transaction() as db:
            row = db.execute(
                "SELECT value FROM member_settings WHERE member=? AND key='reminder_digest'",
                (member,),
            ).fetchone()
            old = (
                DigestSettings.model_validate_json(row[0]) if row else DigestSettings()
            )
            if initialize and row:
                return old
            if old.revision != value.revision:
                raise Conflict("Digest settings changed; reload them")
            value = value.model_copy(update={"revision": old.revision + 1})
            self.registry.set_member_setting(
                member, "reminder_digest", value.model_dump_json(), db=db
            )
        return value

    def items(self, member):
        """The member's schedules as rows: pending first by when they fell
        due, then upcoming by their next delivery (Q7)."""
        result = []
        for entry in self.registry.accounts(member):
            runtime = self.manager.runtimes.get(entry["account_id"])
            available = bool(
                runtime and runtime.accepting and entry["state"] == "authorized"
            )
            store = runtime.assistant.store if available else self.manager._store(entry)
            try:
                if available:
                    runtime.assistant.reminders.tick()
                with store.transaction() as db:
                    rows = db.execute(
                        "SELECT * FROM reminder_schedules ORDER BY created,rowid"
                    ).fetchall()
                    for row in rows:
                        if available:
                            view = runtime.assistant.reminders.view(db, row)
                            chat = Chat.model_validate_json(
                                db.execute(
                                    "SELECT body FROM chats WHERE key=?",
                                    (row["chat_key"],),
                                ).fetchone()["body"]
                            )
                            peer, title, photo = chat.peer, chat.title, chat.photo_id
                        else:
                            # Logout hides correspondence; only the account and
                            # the existence of an unavailable reminder remain.
                            view = ReminderView(
                                id=row["id"],
                                revision=row["revision"],
                                schedule=ReminderRule.model_validate_json(
                                    row["body"]
                                ).model_copy(update={"idea": ""}),
                                status="unavailable",
                            )
                            peer, title, photo = "", "Unavailable chat", None
                        result.append(
                            {
                                **view.model_dump(),
                                "account_id": entry["account_id"],
                                "account": entry["name"],
                                "peer": peer,
                                "title": title,
                                "photo_id": photo,
                                "ai_enabled": bool(entry["ai_enabled"]),
                            }
                        )
            finally:
                if not available:
                    store.close()
        return sorted(
            result,
            key=lambda item: (
                item["occurrence"] is None,
                (item["occurrence"] or {}).get("due_at")
                or item["next_at"]
                or float("inf"),
                item["title"],
            ),
        )

    def tick(self):
        pending = {}
        now = self.clock()
        for runtime in tuple(self.manager.runtimes.values()):
            app = runtime.assistant
            if not runtime.accepting or not app.authorized():
                continue
            app.reminders.tick()
            for row in app.store.rows(
                "SELECT o.*,s.body FROM reminder_occurrences o JOIN reminder_schedules s ON s.id=o.schedule WHERE s.paused=0 AND o.notice IS NULL AND o.notify_at<=? AND o.due_at<=?",
                (now, now),
            ):
                rule = ReminderRule.model_validate_json(row["body"])
                settings = self.settings(runtime.member)
                day = (
                    datetime.fromtimestamp(now, ZoneInfo(settings.timezone))
                    .date()
                    .isoformat()
                )
                group = (
                    f"exact:{row['id']}"
                    if rule.delivery == "exact"
                    else f"digest:{runtime.member}:{day}"
                )
                pending.setdefault(group, []).append((runtime.member, app, row))
        for group, entries in pending.items():
            identity = hashlib.md5(group.encode(), usedforsecurity=False).hexdigest()
            member = entries[0][0]
            targets = [
                {
                    "account_id": app.store.account_id,
                    "peer": row["chat_key"].split("/", 1)[1],
                    "occurrence": row["id"],
                }
                for _, app, row in entries
            ]
            with self.registry.transaction() as db:
                existing = db.execute(
                    "SELECT body FROM reminder_notices WHERE id=?", (identity,)
                ).fetchone()
                if existing:
                    # A digest delivered once already alerts no more today;
                    # later occurrences of the same day join it unseen.
                    body = json.loads(existing["body"])
                    known = {t["occurrence"] for t in body["targets"]}
                    body["targets"] += [
                        t for t in targets if t["occurrence"] not in known
                    ]
                    db.execute(
                        "UPDATE reminder_notices SET body=? WHERE id=?",
                        (json.dumps(body), identity),
                    )
                else:
                    db.execute(
                        "INSERT INTO reminder_notices VALUES (?,?,?,?)",
                        (
                            identity,
                            member,
                            json.dumps(
                                {
                                    "exact": group.startswith("exact:"),
                                    "targets": targets,
                                }
                            ),
                            now + 300,
                        ),
                    )
            for _, app, row in entries:
                with app.store.transaction() as db:
                    db.execute(
                        "UPDATE reminder_occurrences SET notice=? WHERE id=? AND notice IS NULL",
                        (identity, row["id"]),
                    )

    def remove_account(self, identity):
        with self.registry.transaction() as db:
            for row in db.execute("SELECT id,body FROM reminder_notices").fetchall():
                body = json.loads(row["body"])
                body["targets"] = [
                    target
                    for target in body["targets"]
                    if target["account_id"] != identity
                ]
                if body["targets"]:
                    db.execute(
                        "UPDATE reminder_notices SET body=? WHERE id=?",
                        (json.dumps(body), row["id"]),
                    )
                else:
                    db.execute("DELETE FROM reminder_notices WHERE id=?", (row["id"],))

    def notice(self, identity, member):
        """A notice's targets still pending in signed-in accounts, each with
        its person's name and its Message Idea's first line."""
        rows = self.registry.rows(
            "SELECT * FROM reminder_notices WHERE id=? AND member=?", (identity, member)
        )
        if not rows:
            return None
        row = rows[0]
        body = json.loads(row["body"])
        targets = []
        for target in body["targets"]:
            if self.registry.member_of(target["account_id"]) != member:
                continue
            try:
                app = self.manager.get(target["account_id"])
                key = f"{target['account_id']}/{target['peer']}"
                app.reminders.tick()
                found = app.store.rows(
                    "SELECT s.body FROM reminder_occurrences o JOIN reminder_schedules s ON s.id=o.schedule "
                    "WHERE o.chat_key=? AND o.id=? AND o.schedule_revision=s.revision AND s.paused=0 AND o.due_at<=?",
                    (key, target["occurrence"], app.store.clock()),
                )
                if found:
                    targets.append(
                        {
                            **target,
                            "name": app.store.chat(key).title,
                            "idea": first_line(
                                ReminderRule.model_validate_json(found[0]["body"]).idea
                            ),
                            "account": app.store.account_id,
                        }
                    )
            except Refused:
                pass
        return {**row, **body, "targets": targets}

    def event(self, identity, member):
        """The alert's fetched text (ADR 58 Q2): an exact alert shows the
        person's name and its idea's first line, or "Check in"; a digest
        shows "Reminders" with up to three names and "+N"."""
        notice = self.notice(identity, member)
        if not notice or not notice["targets"] or notice["expires"] <= self.clock():
            return None
        targets = notice["targets"]
        if notice["exact"]:
            sender, text = targets[0]["name"], targets[0]["idea"] or "Check in"
            account = next(
                (
                    entry["name"]
                    for entry in self.registry.accounts(member)
                    if entry["account_id"] == targets[0]["account_id"]
                ),
                "",
            )
        else:
            names = list(dict.fromkeys(target["name"] for target in targets))
            text = ", ".join(names[:3]) + (
                f" +{len(names) - 3}" if len(names) > 3 else ""
            )
            sender, account = "Reminders", "All accounts"
        return {
            "id": identity,
            "member": member,
            "account_id": None,
            "peer": None,
            "subject": SUBJECT + identity,
            "kind": "reminders",
            "sender": sender[:100],
            "account": account,
            "count": len(targets),
            "text": text[:200],
            "expires": notice["expires"],
        }

    def target(self, identity, member):
        """A tap opens the chat of the one reminder an alert names while it is
        pending; otherwise the Reminders list (ADR 36)."""
        notice = self.notice(identity, member)
        targets = notice["targets"] if notice else []
        if len(targets) == 1:
            return {
                "kind": "reminder",
                "account_id": targets[0]["account_id"],
                "peer": targets[0]["peer"],
            }
        return {"kind": "reminders"}
