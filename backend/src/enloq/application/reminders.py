"""Calendar timing and member digests; no inference is admitted by a tick."""

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain.models import Conflict, Refused
from ..domain.reminders import (
    DigestSettings,
    ReminderOccurrence,
    ReminderRule,
    ReminderView,
    next_digest,
    next_occurrence,
    occurrences,
)
from ..domain.suggestions import SuggestionsView
from ..storage.store import blank_draft

SUBJECT = "reminder:"


class Reminders:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.preferences = lambda: DigestSettings()
        self.global_epoch = lambda: "0"
        self.lock = threading.RLock()

    def private(self, key):
        self.app.suggestions.private(key)

    def read(self, key, db):
        return db.execute(
            "SELECT * FROM reminder_schedules WHERE chat_key=?", (key,)
        ).fetchone()

    def control(self, key, epoch, enabled, db):
        automatic = self.app.suggestions.automatic(key, db)["automatic"]
        return f"{epoch}:{int(enabled)}:{int(automatic)}", not (enabled and automatic)

    def tick(self):
        settings = self.preferences()
        epoch, enabled = self.global_epoch(), self.app.suggestions.enabled()
        now = self.store.clock()
        with self.lock, self.store.transaction() as db:
            for row in db.execute(
                "SELECT * FROM reminder_schedules WHERE body IS NOT NULL"
            ).fetchall():
                key = row["chat_key"]
                rule = ReminderRule.model_validate_json(row["body"])
                control, paused = self.control(key, epoch, enabled, db)
                if control != row["control"] or paused != bool(row["paused"]):
                    due, day = next_occurrence(rule, now, settings)
                    db.execute(
                        "DELETE FROM reminder_occurrences WHERE chat_key=?", (key,)
                    )
                    db.execute(
                        "UPDATE reminder_schedules SET next_at=?,next_date=?,paused=?,control=?,digest_revision=? WHERE chat_key=?",
                        (due, day, int(paused), control, settings.revision, key),
                    )
                    continue
                if paused:
                    continue
                if (
                    row["digest_revision"] != settings.revision
                    and rule.delivery == "digest"
                ):
                    due, day = next_occurrence(rule, now, settings)
                    db.execute(
                        "UPDATE reminder_schedules SET next_at=?,next_date=?,digest_revision=? WHERE chat_key=?",
                        (due, day, settings.revision, key),
                    )
                    db.execute(
                        "UPDATE reminder_occurrences SET notify_at=? WHERE chat_key=? AND notice IS NULL",
                        (next_digest(now, settings), key),
                    )
                    continue
                if row["next_at"] > now:
                    continue
                due, day = max(
                    item for item in occurrences(rule, now, settings) if item[0] <= now
                )
                sent = db.execute(
                    "SELECT last_sent FROM reminder_outbox WHERE chat_key=?", (key,)
                ).fetchone()
                db.execute("DELETE FROM reminder_occurrences WHERE chat_key=?", (key,))
                if not sent or sent[0] <= due:
                    # A late digest waits for its next future daily slot, never
                    # an immediate restart alert. Exact alerts remain separate.
                    notify = (
                        next_digest(now, settings)
                        if rule.delivery == "digest" and now - due > 60
                        else due
                    )
                    db.execute(
                        "INSERT INTO reminder_occurrences (chat_key,id,schedule_revision,due_at,calendar_date,notify_at) VALUES (?,?,?,?,?,?)",
                        (key, uuid.uuid4().hex, row["revision"], due, day, notify),
                    )
                future, future_day = next_occurrence(rule, now, settings)
                db.execute(
                    "UPDATE reminder_schedules SET next_at=?,next_date=? WHERE chat_key=?",
                    (future, future_day, key),
                )

    def identity(self, key):
        rows = self.store.rows(
            "SELECT o.id FROM reminder_occurrences o JOIN reminder_schedules s USING(chat_key) WHERE o.chat_key=? AND o.schedule_revision=s.revision AND s.paused=0 AND s.body IS NOT NULL",
            (key,),
        )
        return rows[0]["id"] if rows else None

    def view(self, key):
        self.private(key)
        with self.store.transaction() as db:
            row = self.read(key, db)
            if not row:
                return ReminderView()
            occurrence = db.execute(
                "SELECT * FROM reminder_occurrences WHERE chat_key=?", (key,)
            ).fetchone()
            return ReminderView(
                revision=row["revision"],
                schedule=ReminderRule.model_validate_json(row["body"])
                if row["body"]
                else None,
                next_at=row["next_at"],
                status="paused" if row["paused"] else "active",
                occurrence=ReminderOccurrence(
                    **{
                        k: occurrence[k]
                        for k in ("id", "due_at", "calendar_date", "reviewed")
                    }
                )
                if occurrence
                else None,
            )

    def edit(self, key, revision, rule):
        self.private(key)
        settings = self.preferences()
        epoch, enabled = self.global_epoch(), self.app.suggestions.enabled()
        with self.lock, self.store.transaction() as db:
            row = self.read(key, db)
            if (row["revision"] if row else 0) != revision:
                raise Conflict("Reminder changed; reload its schedule")
            control, paused = self.control(key, epoch, enabled, db)
            due, day = (
                next_occurrence(rule, self.store.clock(), settings)
                if rule
                else (None, None)
            )
            db.execute("DELETE FROM reminder_occurrences WHERE chat_key=?", (key,))
            db.execute(
                "INSERT INTO reminder_schedules VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(chat_key) DO UPDATE SET revision=excluded.revision,body=excluded.body,next_at=excluded.next_at,next_date=excluded.next_date,paused=excluded.paused,control=excluded.control,digest_revision=excluded.digest_revision",
                (
                    key,
                    revision + 1,
                    rule.model_dump_json() if rule else None,
                    due,
                    day,
                    int(paused),
                    control,
                    settings.revision,
                ),
            )
        return self.view(key)

    def current(self, key, identity, db=None):
        sql = (
            "SELECT o.* FROM reminder_occurrences o JOIN reminder_schedules s USING(chat_key) "
            "WHERE o.chat_key=? AND o.id=? AND o.schedule_revision=s.revision AND s.body IS NOT NULL AND s.paused=0"
        )
        return (
            bool(db.execute(sql, (key, identity)).fetchone())
            if db
            else bool(self.store.rows(sql, (key, identity)))
        )

    def dismiss(self, key, identity):
        self.private(key)
        with self.lock, self.store.transaction() as db:
            if not self.current(key, identity, db):
                raise Conflict("This reminder is no longer pending")
            db.execute(
                "DELETE FROM reminder_occurrences WHERE chat_key=? AND id=?",
                (key, identity),
            )
        return self.view(key)

    def review(self, key, identity, revision, cursor=None, regenerate=False):
        self.private(key)
        self.tick()
        with self.lock:
            reuse = False
            with self.store.transaction() as db:
                if not self.current(key, identity, db):
                    raise Conflict("This reminder is no longer pending")
                occurrence = db.execute(
                    "SELECT reviewed FROM reminder_occurrences WHERE id=?", (identity,)
                ).fetchone()
                if occurrence[0] and not regenerate:
                    record = self.app.suggestions.record(key, db)
                    reuse = True
                else:
                    row = db.execute(
                        "SELECT body FROM drafts WHERE chat_key=?", (key,)
                    ).fetchone()
                    draft = json.loads(row[0]) if row else blank_draft(key)
                    if draft["revision"] != revision:
                        raise Conflict("Draft changed; keep your edits and reload")
                    # A failed first admission still needs explicit Retry.
                    db.execute(
                        "UPDATE reminder_occurrences SET reviewed=1 WHERE id=?",
                        (identity,),
                    )
                    rule = ReminderRule.model_validate_json(self.read(key, db)["body"])
            if reuse:
                return (
                    self.app.suggestions.view(key)
                    if record and record.get("reminder") == identity
                    else SuggestionsView(kind="manual", reason="changed")
                )
            return self.app.suggestions.generate(
                key, revision, reminder=identity, language=rule.language
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
                for row in store.rows(
                    "SELECT chat_key FROM reminder_schedules WHERE body IS NOT NULL"
                ):
                    if available:
                        chat = store.chat(row["chat_key"])
                        view = runtime.assistant.reminders.view(row["chat_key"])
                        peer, title = chat.peer, chat.title
                    else:
                        # Logout hides correspondence; only the account and
                        # the existence of an unavailable reminder remain.
                        view, peer, title = (
                            ReminderView(status="unavailable"),
                            "",
                            "Unavailable chat",
                        )
                    result.append(
                        {
                            **view.model_dump(),
                            "account_id": entry["account_id"],
                            "account": entry["name"],
                            "peer": peer,
                            "title": title,
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
                item["next_at"] or float("inf"),
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
                "SELECT o.*,s.body FROM reminder_occurrences o JOIN reminder_schedules s USING(chat_key) WHERE s.paused=0 AND o.notice IS NULL AND o.notify_at<=?",
                (now,),
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
            body = {"exact": group.startswith("exact:"), "targets": targets}
            with self.registry.transaction() as db:
                db.execute(
                    "INSERT OR IGNORE INTO reminder_notices VALUES (?,?,?,?)",
                    (identity, member, json.dumps(body), now + 300),
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
                app.reminders.tick()
                if app.reminders.current(
                    f"{target['account_id']}/{target['peer']}", target["occurrence"]
                ):
                    targets.append(target)
            except Refused:
                pass
        return {**row, **body, "targets": targets} if targets else None

    def event(self, identity, member):
        notice = self.notice(identity, member)
        if not notice or notice["expires"] <= self.clock():
            return None
        return {
            "id": identity,
            "member": member,
            "account_id": None,
            "peer": None,
            "subject": SUBJECT + identity,
            "kind": "reminders",
            "sender": "Enloq",
            "account": "All accounts",
            "count": len(notice["targets"]),
            "text": "Review your message reminders.",
            "expires": notice["expires"],
        }

    def target(self, identity, member):
        notice = self.notice(identity, member)
        if not notice:
            return {"kind": "unavailable"}
        return (
            {"kind": "reminder", **notice["targets"][0]}
            if notice["exact"]
            else {"kind": "reminders"}
        )
