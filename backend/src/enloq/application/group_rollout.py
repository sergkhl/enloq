"""One durable pass over a frozen day and enabled target set (ADR 34 Q25)."""

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain.group_briefings import BriefingAttempt
from ..domain.models import Refused
from ..storage.events import commits
from ..storage.store import WINDOW
from .group_context import day_bounds
from .group_updates import GroupUpdates

PREFIX = "group-briefing-rollout:"
RECHECK = 15


class GroupBriefingRollout:
    def __init__(self, manager, revision):
        if not isinstance(revision, str) or not revision or len(revision) > 128:
            raise ValueError("A stable rollout revision is required")
        self.manager, self.registry = manager, manager.registry
        self.key = PREFIX + revision
        self._standing = None

    def read(self):
        rows = self.registry.rows("SELECT value FROM metadata WHERE key=?", (self.key,))
        return json.loads(rows[0]["value"]) if rows else None

    def _now(self):
        runtime = next(iter(self.manager.runtimes.values()), None)
        return runtime.assistant.store.clock() if runtime else time.time()

    def start(self):
        """Freeze once; neither later opt-ins nor midnight expand this pass."""
        if existing := self.read():
            return existing
        now = self._now()
        day = (
            datetime.fromtimestamp(now, ZoneInfo(self.manager.settings.timezone))
            .date()
            .isoformat()
        )
        accounts = {
            row["account_id"]
            for row in self.registry.rows(
                "SELECT account_id FROM accounts WHERE state='authorized' AND ai_enabled=1"
            )
        }
        targets = []
        for identity, runtime in sorted(self.manager.runtimes.items()):
            if identity not in accounts or not runtime.accepting:
                continue
            store = runtime.assistant.store
            for row in store.recent_chats("group"):
                key = row["key"]
                state = store.groups.get(key)
                if (
                    state.processing_enabled
                    and state.supported
                    and state.access == "available"
                    and store.chat(key).ai_eligible
                ):
                    targets.append(
                        dict(account_id=identity, key=key, day=day, state="pending")
                    )
        value = dict(
            revision=self.key.removeprefix(PREFIX),
            day=day,
            timezone=self.manager.settings.timezone,
            started=now,
            targets=targets,
        )
        with self.registry.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES (?,?)",
                (self.key, json.dumps(value)),
            )
            return json.loads(
                db.execute(
                    "SELECT value FROM metadata WHERE key=?", (self.key,)
                ).fetchone()[0]
            )

    def _save_target(self, key, **changes):
        with self.registry.transaction() as db:
            row = db.execute(
                "SELECT value FROM metadata WHERE key=?", (self.key,)
            ).fetchone()
            if not row:
                return
            value = json.loads(row[0])
            target = next((t for t in value["targets"] if t["key"] == key), None)
            if target is None:
                return
            changed = any(target.get(field) != item for field, item in changes.items())
            if changed:
                target.update(changes)
                db.execute(
                    "UPDATE metadata SET value=? WHERE key=?",
                    (json.dumps(value), self.key),
                )

    def tick(self):
        """Queue at most one group; the ordinary scheduler owns every stage."""
        now, sequence = self._now(), commits.sequence
        if self._standing and self._standing[0] == sequence and now < self._standing[1]:
            return None
        value = self.start()
        for target in value["targets"]:
            if target["state"] not in {"pending", "started"}:
                continue
            runtime = self.manager.runtimes.get(target["account_id"])
            if not runtime or not runtime.accepting:
                continue
            app = runtime.assistant
            if target["state"] == "started":
                row = app.store.rows(
                    "SELECT state FROM group_briefing_attempts WHERE id=? AND chat_key=? AND day=?",
                    (target["attempt"], target["key"], target["day"]),
                )
                if not row:
                    self._save_target(target["key"], state="unavailable")
                elif row[0]["state"] != "active":
                    self._save_target(
                        target["key"],
                        state="completed"
                        if row[0]["state"] == "completed"
                        else "retry_required"
                        if row[0]["state"] in {"failed", "interrupted"}
                        else "superseded",
                    )
                continue
            # Off/access loss/budget/quiet/threshold pause the frozen target.
            # Expiry is terminal; no automatic history backfill is attempted.
            _, end = day_bounds(app.store, target["day"])
            if end <= app.store.clock() - WINDOW:
                self._save_target(target["key"], state="expired")
                continue
            try:
                with app.store.transaction() as db:
                    cached = app.briefings.planner._candidate(
                        db,
                        target["key"],
                        target["day"],
                        explicit=True,
                        current_rules=True,
                    )
                if not cached.intended_ids:
                    self._save_target(target["key"], state="empty")
                    continue
                selected = app.briefings.planner.rollout(target["key"], target["day"])
                result = GroupUpdates(app.briefings).start(selected)
            except Refused:
                continue
            if result["status"] == "active":
                # Existing work may be a manual/automatic run. Wait for it and
                # recheck current results rather than owning its admission.
                continue
            if result["status"] == "started":
                self._save_target(
                    target["key"], state="started", attempt=result["attempt"]
                )
                return result
            self._save_target(
                target["key"],
                state="completed"
                if result["status"] == "up_to_date"
                else "retry_required",
                **({"attempt": result["attempt"]} if result.get("attempt") else {}),
            )
        self._standing = (commits.sequence, self._now() + RECHECK)
        return None

    @staticmethod
    def started_target(store, identity, *, db):
        """Read exact started ownership through the caller's account transaction."""
        table = "shared.metadata" if store.shared_path else "metadata"
        rows = db.execute(
            f"SELECT value FROM {table} WHERE key LIKE ?", (PREFIX + "%",)
        ).fetchall()
        return next(
            (
                target
                for row in rows
                for target in json.loads(row[0])["targets"]
                if target.get("attempt") == identity and target["state"] == "started"
            ),
            None,
        )

    @staticmethod
    def admit(briefings, payload, *, db=None):
        """Recheck rollout-only gates through the caller's current transaction.

        Ordinary manual updates and later explicit Retry retain their respective
        admission. A stopped counted rollout attempt is never replayed here.
        """
        identity = payload.get("attempt")
        if not identity:
            return
        store = briefings.store
        with briefings.planner._connection(db) as db:
            target = GroupBriefingRollout.started_target(store, identity, db=db)
            if target is None:
                return
            row = db.execute(
                "SELECT body FROM group_briefing_attempts WHERE id=?", (identity,)
            ).fetchone()
            if row is None:
                raise Refused("Frozen rollout attempt is unavailable")
            attempt = BriefingAttempt.model_validate_json(row[0])
            if (
                attempt.snapshot.key != target["key"]
                or attempt.snapshot.day != target["day"]
            ):
                raise Refused("Frozen rollout scope changed")
            briefings.planner.rollout(
                target["key"],
                target["day"],
                target_ids=attempt.snapshot.intended_ids,
                continuation=True,
                db=db,
            )
