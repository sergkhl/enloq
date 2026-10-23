"""Private account registry and application-wide accounting; no login secrets."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from ..config import private_directory
from ..domain.models import Refused, profile_photo_id
from .members import DDL as MEMBER_DDL
from .members import MIGRATED, OWNER, READING_VIEWS, TONES
from .store import Connections, change_triggers

DEFAULT_PROCESSING_THRESHOLD = 200
MAX_PROCESSING_THRESHOLD = 10_000


class _Clock:
    """The store-shaped clock and timezone that budget-day accounting reads."""

    def __init__(self, clock, timezone):
        self.clock, self.timezone = clock, timezone


class AccountRegistry:
    def __init__(self, root: Path):
        self.root = private_directory(root)
        self.path = root / "accounts.sqlite3"
        self.connections = Connections(self.path)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self.transaction() as db:
            for statement in (
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS accounts (account_id INTEGER PRIMARY KEY, directory TEXT NOT NULL, session TEXT NOT NULL, name TEXT NOT NULL, username TEXT NOT NULL, phone TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS pending_logins (id TEXT PRIMARY KEY, session TEXT NOT NULL, expected_id INTEGER, created REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS model_reservations (account_id INTEGER NOT NULL,id TEXT NOT NULL,scope TEXT NOT NULL,role TEXT NOT NULL,input_revision TEXT NOT NULL,bucket TEXT NOT NULL,engine TEXT NOT NULL,reserved INTEGER NOT NULL,actual INTEGER,status TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(account_id,id),UNIQUE(account_id,scope,role,input_revision,engine))",
                "CREATE TABLE IF NOT EXISTS account_imports (account_id INTEGER PRIMARY KEY)",
                "CREATE TABLE IF NOT EXISTS model_leases (id TEXT PRIMARY KEY,deadline REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS model_receipts (account_id INTEGER NOT NULL,id TEXT NOT NULL,role TEXT NOT NULL,model TEXT NOT NULL,provider TEXT NOT NULL,usage TEXT NOT NULL,error TEXT,PRIMARY KEY(account_id,id))",
                "CREATE TABLE IF NOT EXISTS budget_changes (at REAL NOT NULL,old INTEGER NOT NULL,new INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS changes (scope TEXT PRIMARY KEY, seq INTEGER NOT NULL)",
                *MEMBER_DDL,
            ):
                db.execute(statement)
            columns = {r["name"] for r in db.execute("PRAGMA table_info(accounts)")}
            if "member" not in columns:
                # ADR 48: every account before members existed is the owner's.
                db.execute(
                    f"ALTER TABLE accounts ADD COLUMN member TEXT NOT NULL DEFAULT '{OWNER}'"
                )
            db.execute(
                "INSERT OR IGNORE INTO members (id,role,state,approved_at) VALUES (?,?,?,?)",
                (OWNER, "owner", "active", time.time()),
            )
            # The owner's own preferences move once from the shared metadata.
            db.execute(
                "INSERT OR IGNORE INTO member_settings SELECT ?,key,value FROM metadata WHERE key IN (%s)"
                % ",".join("?" * len(MIGRATED)),
                (OWNER, *MIGRATED),
            )
            # ADR 53 Q2: Adaptive left with no backward compatibility; a
            # remembered one becomes Neutral, as a change of tone.
            db.execute(
                "UPDATE member_settings SET value=json_object('tone','neutral',"
                "'revision',json_extract(value,'$.revision')+1)"
                " WHERE key='tone' AND json_extract(value,'$.tone')='adaptive'"
            )
            if "ai_enabled" not in columns:
                db.execute(
                    "ALTER TABLE accounts ADD COLUMN ai_enabled INTEGER NOT NULL DEFAULT 1"
                )
            if "photo_id" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN photo_id TEXT")
            for table in ("model_reservations", "model_receipts", "budget_changes"):
                columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if "family" not in columns:
                    db.execute(
                        f"ALTER TABLE {table} ADD COLUMN family TEXT NOT NULL DEFAULT 'daily'"
                    )
                if table != "model_receipts" and "member" not in columns:
                    # ADR 48: spending and its limits are each member's; all
                    # earlier spending was the owner's.
                    db.execute(
                        f"ALTER TABLE {table} ADD COLUMN member TEXT NOT NULL DEFAULT '{OWNER}'"
                    )
            for statement in (
                *change_triggers("accounts", "'accounts'"),
                *change_triggers("metadata", "key"),
                *change_triggers("model_reservations", "'spending'"),
                # A member's setting announces its own key, as metadata does;
                # scopes name settings, never whose they are.
                *change_triggers("member_settings", "key"),
                *change_triggers("members", "'people'"),
            ):
                db.execute(statement)

    def transaction(self):
        return self.connections.transaction()

    def rows(self, query, args=()):
        with self.transaction() as db:
            return [dict(r) for r in db.execute(query, args)]

    def path_for(self, relative: str) -> Path:
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise Refused("Account storage must belong to this application")
        for part in (path, *path.parents):
            if part == self.root.parent:
                break
            if part.is_symlink():
                raise Refused("Account storage cannot contain symlinks")
        return path

    def get(self, account_id):
        rows = self.rows("SELECT * FROM accounts WHERE account_id=?", (account_id,))
        if not rows:
            raise Refused("Account unavailable")
        return rows[0]

    def accounts(self, member=None):
        if member is None:
            return self.rows("SELECT * FROM accounts ORDER BY created,account_id")
        return self.rows(
            "SELECT * FROM accounts WHERE member=? ORDER BY created,account_id",
            (member,),
        )

    def member(self, identity):
        rows = self.rows("SELECT * FROM members WHERE id=?", (identity,))
        return rows[0] if rows else None

    def members(self):
        return self.rows("SELECT * FROM members ORDER BY approved_at,requested_at,id")

    def member_of(self, account_id):
        """The member holding an account, or None for an account the registry
        doesn't hold (ADR 48)."""
        rows = self.rows(
            "SELECT member FROM accounts WHERE account_id=?", (account_id,)
        )
        return rows[0]["member"] if rows else None

    def member_setting(self, member, key, default=None):
        rows = self.rows(
            "SELECT value FROM member_settings WHERE member=? AND key=?", (member, key)
        )
        return rows[0]["value"] if rows else default

    def set_member_setting(self, member, key, value, *, db=None):
        """Changes nothing, so announces nothing, when the value is the same."""
        statement = (
            "INSERT INTO member_settings VALUES (?,?,?) "
            "ON CONFLICT(member,key) DO UPDATE SET value=excluded.value "
            "WHERE member_settings.value!=excluded.value"
        )
        if db is not None:
            db.execute(statement, (member, key, value))
            return
        with self.transaction() as db:
            db.execute(statement, (member, key, value))

    def clear_member_setting(self, member, key, value=None, *, db=None):
        statement, args = (
            "DELETE FROM member_settings WHERE member=? AND key=?",
            (member, key),
        )
        if value is not None:
            statement, args = statement + " AND value=?", (*args, value)
        if db is not None:
            db.execute(statement, args)
            return
        with self.transaction() as db:
            db.execute(statement, args)

    def set_state(self, account_id, state):
        with self.transaction() as db:
            db.execute(
                "UPDATE accounts SET state=? WHERE account_id=?", (state, account_id)
            )

    def processing(self, account_id):
        return {
            "account_id": account_id,
            "ai_enabled": bool(self.get(account_id)["ai_enabled"]),
        }

    def set_processing(self, account_id, enabled):
        self.get(account_id)
        with self.transaction() as db:
            db.execute(
                "UPDATE accounts SET ai_enabled=? WHERE account_id=?",
                (enabled, account_id),
            )
        return self.processing(account_id)

    def identity(self, account_id, user):
        name = " ".join(
            filter(
                None,
                (getattr(user, "first_name", None), getattr(user, "last_name", None)),
            )
        )
        phone = getattr(user, "phone", "") or ""
        with self.transaction() as db:
            db.execute(
                "UPDATE accounts SET name=?,username=?,phone=?,photo_id=? WHERE account_id=?",
                (
                    name[:256] or f"Account {account_id}",
                    (getattr(user, "username", "") or "")[:64],
                    "••• " + phone[-4:] if phone else "",
                    profile_photo_id(user),
                    account_id,
                ),
            )

    def register(self, account_id, session, *, directory=None, name=None, member=OWNER):
        """A signed-in account joins its member; signing in again keeps the
        member it has (ADR 48)."""
        if account_id <= 0:
            raise Refused("A verified Telegram user is required")
        self.path_for(session)
        existing = self.rows("SELECT * FROM accounts WHERE account_id=?", (account_id,))
        if existing and existing[0]["member"] != member:
            raise Refused("This account belongs to another person on this server")
        directory = (
            existing[0]["directory"]
            if existing
            else (directory or f"accounts/{account_id}")
        )
        self.path_for(directory)
        with self.transaction() as db:
            db.execute(
                "INSERT INTO accounts (account_id,directory,session,name,username,phone,state,created,member) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET session=excluded.session,state='authorized'",
                (
                    account_id,
                    directory,
                    session,
                    name or f"Account {account_id}",
                    "",
                    "",
                    "authorized",
                    time.time(),
                    member,
                ),
            )
        return self.get(account_id)

    def migrate_legacy(self, settings):
        if self.rows("SELECT value FROM metadata WHERE key='legacy_registered'"):
            return
        expected = int(settings.refs.get("EXPECTED_ACCOUNT_ID") or 0)
        session = settings.refs.get("PERSONAL_SESSION_REF")
        if expected and session:
            path = Path(session)
            if (
                path.resolve() != (self.root / "personal.session").resolve()
                or path.is_symlink()
            ):
                raise Refused("Legacy authorization is not owned by this app")
            database = self.root / "assistant.sqlite3"
            if database.exists():
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
                    identity = dict(
                        db.execute(
                            "SELECT key,value FROM metadata WHERE key IN ('account_id','mode','timezone')"
                        )
                    )
                if identity != {
                    "account_id": str(expected),
                    "mode": settings.mode,
                    "timezone": settings.timezone,
                }:
                    raise Refused("Legacy storage identity mismatch")
            self.register(expected, "personal.session", directory=".")
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('legacy_registered','1')"
            )

    def usage(self, *, family="daily", member=OWNER):
        from ..inference.work import budget_key

        budget_key(family)
        return self.rows(
            "SELECT engine,count(*) AS calls,sum(reserved) AS reserved,sum(actual) AS actual,sum(CASE WHEN status='unknown' THEN reserved ELSE 0 END) AS unknown FROM model_reservations WHERE family=? AND member=? GROUP BY engine",
            (family, member),
        )

    def budget(
        self,
        clock=time.time,
        timezone="Asia/Ho_Chi_Minh",
        *,
        family="daily",
        member=OWNER,
    ):
        """Today's counted spending and changes for one of a member's purpose
        budgets (ADR 48)."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from ..inference.work import FLAT_UNKNOWN, budget_day, counted, daily_budget

        zone = ZoneInfo(timezone)
        with self.transaction() as db:
            day = budget_day(db, _Clock(clock, timezone), "metadata")
            bucket = "day:" + day
            spent = counted(
                db, "model_reservations", bucket, family=family, member=member
            )
            limit = daily_budget(db, "metadata", family=family, member=member)
            row = db.execute(
                "SELECT count(*),sum(CASE WHEN status NOT IN ('receipted','refused') THEN 1 ELSE 0 END) FROM model_reservations WHERE engine<>'offline' AND bucket=? AND family=? AND member=?",
                (bucket, family, member),
            ).fetchone()
            changes = [
                dict(r)
                for r in db.execute(
                    "SELECT at,old,new FROM budget_changes WHERE family=? AND member=? ORDER BY rowid DESC LIMIT 5",
                    (family, member),
                )
            ]
        start = datetime.fromisoformat(day).replace(tzinfo=zone)
        return {
            "day": day,
            "limit": limit,
            "spent": spent,
            "calls": row[0],
            "unknown": row[1] or 0,
            "flat_unknown": FLAT_UNKNOWN,
            "reached": spent >= limit,
            "resets_at": (start + timedelta(days=1)).timestamp(),
            "changes": changes,
        }

    def set_budget(self, value, clock=time.time, *, family="daily", member=OWNER):
        """Only the owner sets a budget, theirs or a member's (ADR 48 Q2)."""
        from ..inference.work import MAX_BUDGET, budget_key, daily_budget

        key = budget_key(family)
        if type(value) is not int or not 0 <= value <= MAX_BUDGET:
            label = "group briefing" if family == "group_briefing" else "daily"
            raise Refused(f"The {label} budget must be between $0.00 and $10.00")
        with self.transaction() as db:
            old = daily_budget(db, "metadata", family=family, member=member)
            if old != value:
                self.set_member_setting(member, key, str(value), db=db)
                db.execute(
                    "INSERT INTO budget_changes (at,old,new,family,member) VALUES (?,?,?,?,?)",
                    (clock(), old, value, family, member),
                )

    def changes(self, after=0):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT scope,seq FROM changes WHERE seq>?", (after,)
            ).fetchall()
            latest = db.execute("SELECT coalesce(max(seq),0) FROM changes").fetchone()[
                0
            ]
        return [r["scope"] for r in rows], latest

    def guidance(self):
        """The current Processing Guidance snapshot and its version."""
        from ..inference.guidance import snapshot

        rows = dict(
            (r["key"], r["value"])
            for r in self.rows(
                "SELECT key,value FROM metadata WHERE key IN ('guidance','guidance_version')"
            )
        )
        return snapshot(
            json.loads(rows.get("guidance", "{}")), int(rows.get("guidance_version", 0))
        )

    def set_guidance(self, fields):
        """Save owner fields (None restores a default) as one new version,
        applied from the next model call; format-failure counts restart."""
        from ..inference.guidance import DEFAULTS, LIMIT

        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM metadata WHERE key='guidance'"
            ).fetchone()
            stored = json.loads(row[0]) if row else {}
            for name, text in fields.items():
                if name not in DEFAULTS:
                    raise Refused("Unknown guidance field")
                if text is None:
                    stored.pop(name, None)
                elif len(text) > LIMIT:
                    raise Refused(f"Guidance fields hold at most {LIMIT} characters")
                else:
                    stored[name] = text
            version = db.execute(
                "SELECT value FROM metadata WHERE key='guidance_version'"
            ).fetchone()
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('guidance',?)",
                (json.dumps(stored, ensure_ascii=False),),
            )
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('guidance_version',?)",
                (str(int(version[0]) + 1 if version else 1),),
            )
            db.execute("DELETE FROM metadata WHERE key='format_failures'")
        return self.guidance()

    def format_failures(self):
        rows = self.rows("SELECT value FROM metadata WHERE key='format_failures'")
        return json.loads(rows[0]["value"]) if rows else {}

    def record_format(self, role, ok):
        """Consecutive format failures per role since the last guidance change."""
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM metadata WHERE key='format_failures'"
            ).fetchone()
            counts = json.loads(row[0]) if row else {}
            counts[role] = 0 if ok else counts.get(role, 0) + 1
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('format_failures',?)",
                (json.dumps(counts),),
            )

    def bind_routes(self, routes):
        """Format-failure counts measure one route and guidance pairing per
        role (ADR 29, ADR 30): a role whose Model Route changed restarts its
        count once; a restart or another role's route change keeps it."""
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM metadata WHERE key='format_route'"
            ).fetchone()
            try:
                bound = json.loads(row[0]) if row else {}
            except ValueError:
                bound = None
            if not isinstance(bound, dict):
                # Before ADR 30 one route string served every role.
                bound = dict.fromkeys(routes, row[0])
            changed = {
                role for role, route in routes.items() if bound.get(role) != route
            }
            if not changed:
                return set()
            counts = db.execute(
                "SELECT value FROM metadata WHERE key='format_failures'"
            ).fetchone()
            counts = json.loads(counts[0]) if counts else {}
            for role in changed:
                counts.pop(role, None)
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('format_failures',?)",
                (json.dumps(counts),),
            )
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('format_route',?)",
                (json.dumps({**bound, **routes}, sort_keys=True),),
            )
        return changed

    def provider_user(self, member=OWNER):
        """One fixed random pseudonym per member, sent with every model request
        for their accounts (ADR 48 Q16); it carries nothing about the member,
        the key or Telegram accounts. The owner keeps the one from before."""
        import secrets

        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO member_settings VALUES (?,'provider_user',?)",
                (member, secrets.token_hex(16)),
            )
            return db.execute(
                "SELECT value FROM member_settings WHERE member=? AND key='provider_user'",
                (member,),
            ).fetchone()[0]

    def paused_roles(self):
        """After three consecutive format failures a role's automatic dispatch
        waits for the owner to reset or keep the guidance."""
        return {role for role, count in self.format_failures().items() if count >= 3}

    def keep_guidance(self, role):
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM metadata WHERE key='format_failures'"
            ).fetchone()
            counts = json.loads(row[0]) if row else {}
            counts.pop(role, None)
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('format_failures',?)",
                (json.dumps(counts),),
            )

    def memory_enabled(self):
        rows = self.rows("SELECT value FROM metadata WHERE key='memory_enabled'")
        return rows[0]["value"] != "0" if rows else True

    def set_memory_enabled(self, enabled):
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('memory_enabled',?)",
                ("1" if enabled else "0",),
            )
        return self.memory_enabled()

    def decision_route(self):
        """ADR 49 Q14: since when the decision model's route has failed, and
        how, or None while it answers. One state for every account."""
        rows = self.rows("SELECT value FROM metadata WHERE key='decision_model'")
        return json.loads(rows[0]["value"]) if rows else None

    def set_decision_route(self, available, reason=None):
        """Record the route's state; True only when an outage starts, so its
        owner hears once."""
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM metadata WHERE key='decision_model'"
            ).fetchone()
            if available:
                if row:
                    db.execute("DELETE FROM metadata WHERE key='decision_model'")
                return False
            if row:
                return False
            db.execute(
                "INSERT INTO metadata VALUES ('decision_model',?)",
                (json.dumps({"since": time.time(), "reason": reason}),),
            )
            return True

    def suggestions_enabled(self):
        """ADR 25: one global switch, initially On, under Account AI Processing."""
        rows = self.rows("SELECT value FROM metadata WHERE key='suggestions_enabled'")
        return rows[0]["value"] != "0" if rows else True

    def set_suggestions_enabled(self, enabled):
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('suggestions_enabled',?)",
                ("1" if enabled else "0",),
            )
        return self.suggestions_enabled()

    def concurrency(self):
        from ..application.capacity import DEFAULT_LIMIT

        rows = self.rows("SELECT value FROM metadata WHERE key='concurrency'")
        return int(rows[0]["value"]) if rows else DEFAULT_LIMIT

    def processing_threshold(self):
        """ADR 35: one saved original-character minimum across all accounts."""
        rows = self.rows("SELECT value FROM metadata WHERE key='processing_threshold'")
        return int(rows[0]["value"]) if rows else DEFAULT_PROCESSING_THRESHOLD

    def set_processing_threshold(self, value):
        if type(value) is not int or not 0 <= value <= MAX_PROCESSING_THRESHOLD:
            raise Refused(
                "The processing threshold must be an integer from 0 to 10,000"
            )
        with self.transaction() as db:
            db.execute(
                "INSERT INTO metadata VALUES ('processing_threshold',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                "WHERE metadata.value!=excluded.value",
                (str(value),),
            )
        return value

    def text_rewriting(self):
        """ADR 54: whether incoming text is rewritten automatically. Off, the
        shipped choice, text bursts stay combined originals and only calming
        rewrites them; voice notes, video circles and images still are."""
        rows = self.rows("SELECT value FROM metadata WHERE key='text_rewriting'")
        return bool(rows and rows[0]["value"] == "1")

    def set_text_rewriting(self, enabled):
        if type(enabled) is not bool:
            raise Refused("Text rewriting is on or off")
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('text_rewriting',?)",
                ("1" if enabled else "0",),
            )
        return enabled

    def set_concurrency(self, value):
        from ..application.capacity import MAX_LIMIT

        if not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
            raise Refused(f"The concurrency limit must be between 1 and {MAX_LIMIT}")
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('concurrency',?)",
                (str(value),),
            )
        return value

    def preferences(self, member=OWNER):
        """The member's last tone (ADR 48 Q20); the definitions stay shared."""
        value = self.member_setting(member, "tone")
        return json.loads(value) if value else {"tone": "neutral", "revision": 0}

    def reading_view(self, member=OWNER):
        return {"reading_view": self.member_setting(member, "reading_view", "ai")}

    def set_reading_view(self, value, member=OWNER):
        if value not in READING_VIEWS:
            raise Refused("Unknown reading view")
        self.set_member_setting(member, "reading_view", value)
        return self.reading_view(member)

    def hide_bots(self, member=OWNER):
        """ADR 34: a reading choice, independent of AI eligibility; each
        member's own since ADR 48."""
        return {"hide_bots": self.member_setting(member, "hide_bots", "1") != "0"}

    def set_hide_bots(self, value, member=OWNER):
        if type(value) is not bool:
            raise Refused("Hide bots must be On or Off")
        self.set_member_setting(member, "hide_bots", "1" if value else "0")
        return self.hide_bots(member)

    def set_tone(self, tone, member=OWNER):
        if tone not in TONES:
            raise Refused("Unknown tone")
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM member_settings WHERE member=? AND key='tone'",
                (member,),
            ).fetchone()
            old = json.loads(row[0]) if row else {"tone": "neutral", "revision": 0}
            value = {"tone": tone, "revision": old["revision"] + (tone != old["tone"])}
            self.set_member_setting(member, "tone", json.dumps(value), db=db)
        return value
