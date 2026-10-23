"""Deliberate today-only briefing admission, separate from reading and Retry."""

import json

from ..domain.group_briefings import BriefingAttempt
from ..domain.models import Refused, Syncing
from ..inference.work import BudgetReached


class GroupUpdates:
    def __init__(self, briefings):
        self.briefings = briefings
        self.app, self.store = briefings.app, briefings.store
        self.planner, self.storage = briefings.planner, briefings.storage

    def _account(self):
        self.app.admit_ai()
        if not self.app.authorized():
            raise Refused("Account authorization changed")

    def _active(self, key):
        rows = self.store.rows(
            "SELECT body FROM group_briefing_attempts "
            "WHERE chat_key=? AND state='active' ORDER BY rowid DESC LIMIT 1",
            (key,),
        )
        if rows:
            attempt = BriefingAttempt.model_validate_json(rows[0]["body"])
            return dict(status="active", day=attempt.snapshot.day, attempt=attempt.id)
        run = self.storage.run_state(key)
        batch = self.storage.batch(run.active_batch) if run.active_batch else None
        if batch and batch.state == "active":
            return dict(status="active", day=self.planner._day(self.store.clock()))
        return None

    def _unchanged(self, selected):
        final = self.storage.state(selected.key, selected.day).final
        return bool(
            final
            and self.storage.current(final)
            and self.planner.rules_current(final)
            and set(selected.intended_ids) <= set(final.covered_ids)
        )

    def _verified(self, selected):
        if self.planner.reverifying(selected.key, selected.day):
            raise Syncing("Telegram messages are being verified again; try in a minute")

    def _failed(self, selected):
        with self.store.transaction() as db:
            same = self.planner._failed_same(db, selected)
        if not same:
            return None
        for row in self.store.rows(
            "SELECT body FROM group_briefing_attempts WHERE chat_key=? AND day=? "
            "AND state IN ('failed','interrupted') ORDER BY rowid DESC",
            (selected.key, selected.day),
        ):
            previous = BriefingAttempt.model_validate_json(row["body"])
            known = set(previous.snapshot.intended_ids) | {
                omission.message_id for omission in previous.snapshot.omissions
            }
            bounded = (set(selected.intended_ids) - set(selected.new_ids)) | set(
                sorted(selected.new_ids)[:200]
            )
            if (
                known == set(selected.intended_ids)
                or set(previous.snapshot.intended_ids) == bounded
            ) and self.storage.current(previous.snapshot, admission=True):
                return dict(
                    status="retry_required",
                    day=selected.day,
                    attempt=previous.id,
                )
        return None

    def view(self, key):
        """Today's band status, read without starting model work.

        One status per state of the slot (ADR 34): active
        (Waiting), up_to_date, ready (Update), retry_required (Retry when
        enabled), budget, syncing, and unavailable when AI is Off or nothing
        is eligible today. Enabled marks the two that a click acts on.
        """
        day = self.planner._day(self.store.clock())
        state = dict(day=day, enabled=False, reason=None)
        try:
            self._account()
            if active := self._active(key):
                return {**state, **active}
            selected = self.planner.manual(key)
            # First: while a new intake epoch reads the day again, a standing
            # briefing is up to date before any original is proven again.
            if self._unchanged(selected):
                return {**state, "status": "up_to_date"}
            self._verified(selected)
            if not selected.intended_ids:
                raise Refused("No eligible messages today")
            self.briefings.work.check_budget("group_briefing")
            if failed := self._failed(selected):
                retry = self.briefings._retry_view(
                    self.storage.attempt(failed["attempt"])
                )
                return {**state, **failed, **retry}
            return {**state, "status": "ready", "enabled": True}
        except Syncing as error:
            return {
                **state,
                "status": "syncing",
                "transient": error.transient,
                "reason": str(error),
            }
        except BudgetReached as error:
            return {**state, "status": "budget", "reason": str(error)}
        except Refused as error:
            return {**state, "status": "unavailable", "reason": str(error)}

    def start(self, selected):
        """Admit one already-selected day; rollout supplies its extra gates."""
        self._account()
        self.briefings.reconcile(selected.key)
        if active := self._active(selected.key):
            return active
        if self._unchanged(selected):
            return dict(status="up_to_date", day=selected.day)
        self._verified(selected)
        if not selected.intended_ids:
            raise Refused("No eligible messages today")
        if failed := self._failed(selected):
            return failed
        self.briefings.work.check_budget("group_briefing")
        days = self.briefings._freeze_days([selected])
        if not days:
            # A concurrent deliberate click may have won admission meanwhile.
            if active := self._active(selected.key):
                return active
            if self._unchanged(selected):
                return dict(status="up_to_date", day=selected.day)
            if failed := self._failed(selected):
                return failed
            raise Refused("Briefing sources changed before the update")
        if len(days) != 1:
            raise Refused("A deliberate update must target exactly one day")
        try:
            job = self.briefings._start_day(days[0], None, automatic=False)
        except Refused:
            if active := self._active(selected.key):
                return active
            raise
        payload = json.loads(job["payload"])
        return dict(status="started", day=selected.day, attempt=payload["attempt"])

    def update(self, key):
        """Freeze today and the cached cutoff at this accepted click."""
        self._account()
        return self.start(self.planner.manual(key))
