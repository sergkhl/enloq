"""Cached group-day selection and current admission; lookup starts no work."""

import contextlib
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain.group_briefings import BriefingAttempt, BriefingResult
from ..domain.models import (
    ANALYSED_MEDIA,
    Message,
    ProcessingPaused,
    Refused,
    Syncing,
)
from ..storage.store import RETENTION, WINDOW
from . import group_context
from .blocks import processing_threshold
from .group_rollout import GroupBriefingRollout
from .media import transcript_fingerprint

QUIET_SECONDS = 30
AUTO_INTERVAL = 4 * 3600
MAX_BACKOFF = 300


class CharacterDeferred(ProcessingPaused):
    """Below-threshold originals have no runnable job or waiting status."""


@dataclass(frozen=True)
class Selection:
    key: str
    day: str
    sources: tuple[Message, ...]
    intended_ids: tuple[int, ...]
    new_ids: tuple[int, ...]
    omissions: tuple = ()


class Planner:
    def __init__(self, app):
        self.app, self.store = app, app.store

    def _connection(self, db):
        return (
            contextlib.nullcontext(db) if db is not None else self.store.transaction()
        )

    def _day(self, timestamp):
        return (
            datetime.fromtimestamp(timestamp, ZoneInfo(self.store.timezone))
            .date()
            .isoformat()
        )

    @staticmethod
    def _marker(message):
        # Display-name and filled-in forward details do not change model evidence.
        # Native content edits, deletion and speaker identity do.
        return [
            message.revision,
            message.author_peer,
            message.author_signature,
            message.deleted,
        ]

    @staticmethod
    def _cursor(db, key, default):
        row = db.execute("SELECT value FROM cursors WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def changed(self, message, arrival=True):
        """Remember observed material changes once, including history/replay intake.

        ``arrival`` follows the intake owner's live/history flag. Both can bring
        genuinely new cached evidence; unchanged replay never extends quiet.
        """
        key = f"{message.account_id}/{message.peer}"
        with self.store.transaction() as db:
            self.store.groups._chat(db, key)
            state = self._cursor(db, "group-quiet:" + key, {"seen": {}, "changed": 0})
            identity = str(message.message_id)
            marker = self._marker(message)
            if state["seen"].get(identity) == marker:
                return False
            state["seen"][identity] = marker
            # Prune markers with disposable source rows, retaining one group clock.
            retained = {
                str(row[0])
                for row in db.execute(
                    "SELECT message_id FROM messages WHERE chat_key=?", (key,)
                )
            }
            state["seen"] = {
                identity: value
                for identity, value in state["seen"].items()
                if identity in retained
            }
            state["changed"] = max(state["changed"], self.store.clock())
            db.execute(
                "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                ("group-quiet:" + key, json.dumps(state)),
            )
            db.execute("DELETE FROM cursors WHERE key=?", ("group-backoff:" + key,))
            return True

    def refuse(self, key, *, delay=30, db=None):
        """A bounded no-work refusal hold; counted failures use frozen Retry."""
        if (
            isinstance(delay, bool)
            or not isinstance(delay, (int, float))
            or not math.isfinite(delay)
        ):
            raise Refused("Invalid group refusal backoff")
        with self._connection(db) as db:
            self.store.groups._chat(db, key)
            until = self.store.clock() + max(1, min(MAX_BACKOFF, delay))
            previous = self._cursor(db, "group-backoff:" + key, 0)
            until = max(previous, until)
            db.execute(
                "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                ("group-backoff:" + key, json.dumps(until)),
            )
            return until

    def _policy(self, db, key):
        # The manager hook is runtime-only: Registry transactions must never
        # reenter the shared account database while this connection holds it.
        getattr(self.app, "group_runtime_admit", lambda: None)()
        state = group_context.group_chat(self.store, key, db=db)
        if not state.processing_enabled or state.first_enabled_at is None:
            raise ProcessingPaused("Group AI processing is Off")
        if self.store.shared_path:
            account = db.execute(
                "SELECT ai_enabled,state FROM shared.accounts WHERE account_id=?",
                (state.account_id,),
            ).fetchone()
            if account is None or account[1] != "authorized":
                raise Refused("This group account is unavailable")
            enabled = bool(account[0])
        else:
            enabled = self.app.account_processing_enabled()
        if not enabled:
            raise ProcessingPaused("AI processing is Off for this account")
        if self.store.groups.intake(key, db=db).replay_pending:
            raise Syncing("Waiting for current group intake", transient=True)
        return state

    def eligible(self, key, *, db=None):
        """Read current account/group/native gates, independently of cadence."""
        try:
            with self._connection(db) as db:
                self._policy(db, key)
            return True
        except Refused:
            return False

    def _sources(self, db, key, day, *, automatic):
        start, end = group_context.day_bounds(self.store, day)
        cutoff = self.store.clock() - (WINDOW if automatic else RETENTION)
        return tuple(
            message
            for row in db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND date>=? AND date<? AND deleted=0 ORDER BY message_id",
                (key, max(start, cutoff), end),
            )
            if group_context.eligible(message := Message.model_validate_json(row[0]))
            and self.store.groups.current(message, admission=True, db=db)
        )

    def media_current(self, snapshot, *, excluded_ids=(), db=None):
        """Newly available consumed media makes cached wording updateable.

        Ordinary source currency detects edits to an already-consumed meaning.
        An absent meaning later becoming available needs the complementary read
        here. Never prepare/download media merely to answer a reading question.
        """
        excluded = set(excluded_ids)
        with self._connection(db) as db:
            for packet in snapshot.packets:
                for projected in packet["messages"] + packet.get("context", []):
                    if projected["id"] in excluded:
                        continue
                    source = f"{snapshot.key}/{projected['id']}"
                    rows = db.execute(
                        "SELECT body FROM derivatives WHERE source=? AND revision=?",
                        (source, packet["inputs"][source]),
                    ).fetchone()
                    cached = json.loads(rows[0]) if rows else {}
                    if projected.get("media") == "image":
                        if (cached.get("image_context") or None) != packet.get(
                            "image_inputs", {}
                        ).get(source):
                            return False
                    elif projected.get("media") in {"audio", "video_note"}:
                        actual = cached.get("transcript")
                        consumed = packet.get("transcript_inputs", {}).get(source)
                        if (
                            transcript_fingerprint(actual) if actual else None
                        ) != consumed:
                            return False
            return True

    def rules_current(self, value, day=None, *, db=None):
        """Whether new work can reuse this snapshot under the current rules."""
        snapshot = value.snapshot if hasattr(value, "snapshot") else value
        day = snapshot.day if day is None else day
        briefings = getattr(self.app, "briefings", None)
        return bool(
            briefings is None
            or (
                snapshot.guidance_hash == briefings._guide(db=db)
                and self.media_current(
                    snapshot, excluded_ids=getattr(value, "excluded_ids", ()), db=db
                )
            )
        )

    def _covered(self, db, key, day, *, current_rules=False):
        covered = set()
        for row in db.execute(
            "SELECT body FROM group_briefing_results WHERE chat_key=? AND day=? AND valid=1 ORDER BY rowid DESC",
            (key, day),
        ):
            value = BriefingResult.model_validate_json(row[0])
            if self.store.group_briefings.current(value, db=db) and (
                not current_rules or self.rules_current(value, day, db=db)
            ):
                covered.update(value.covered_ids)
        return covered

    def _candidate(self, db, key, day, *, explicit=False, current_rules=None):
        state = self._policy(db, key)
        group_context.day_bounds(self.store, day)
        if not explicit and not self._day(state.first_enabled_at) <= day <= self._day(
            self.store.clock()
        ):
            raise Refused("This day is outside automatic group coverage")
        sources = self._sources(db, key, day, automatic=not explicit)
        covered = self._covered(
            db,
            key,
            day,
            current_rules=(day == self._day(self.store.clock()))
            if current_rules is None
            else current_rules,
        )
        return Selection(
            key=key,
            day=day,
            sources=sources,
            intended_ids=tuple(message.message_id for message in sources),
            new_ids=tuple(
                message.message_id
                for message in sources
                if message.message_id not in covered
            ),
        )

    def _character_admitted(self, db, selection, target_ids=None):
        # The threshold counts the entire cached eligible day, even when the
        # rolling target window cuts through that day's earlier originals.
        whole_day = self._sources(db, selection.key, selection.day, automatic=False)
        newly_targeted = set(selection.new_ids)
        if target_ids is not None:
            newly_targeted &= set(target_ids)
        return bool(
            group_context.original_characters(whole_day)
            >= processing_threshold(self.store, db)
            or any(
                message.message_id in newly_targeted
                and message.media in ANALYSED_MEDIA
                and not self._media_covered(db, selection, message)
                for message in selection.sources
            )
        )

    def _media_covered(self, db, selection, message):
        # An unrelated changed dependency can withdraw a daily result without
        # making its already covered, unchanged media a fresh exemption.
        for row in db.execute(
            "SELECT body FROM group_briefing_results WHERE chat_key=? AND day=?",
            (selection.key, selection.day),
        ):
            result = BriefingResult.model_validate_json(row[0])
            if message.message_id not in result.covered_ids:
                continue
            for packet in result.snapshot.packets:
                if packet["inputs"].get(message.key) == message.revision and packet.get(
                    "speaker_inputs", {}
                ).get(message.key) == [message.author_peer, message.author_signature]:
                    return True
        return False

    def _continuation(self, db, key):
        run = self.store.group_briefings.run_state(key, db=db)
        batch = (
            self.store.group_briefings.batch(run.active_batch, db=db)
            if run.active_batch
            else None
        )
        return batch if batch and batch.state == "active" and batch.automatic else None

    def _timing(self, db, selection, *, continuation, bypass_interval=False):
        state = self.store.groups.get(selection.key, db=db)
        quiet = self._cursor(db, "group-quiet:" + selection.key, {"changed": 0})[
            "changed"
        ]
        if self.store.clock() < max(quiet, state.first_enabled_at) + QUIET_SECONDS:
            raise ProcessingPaused("Waiting for quiet group originals")
        run = self.store.group_briefings.run_state(selection.key, db=db)
        if continuation and not bypass_interval:
            if self._continuation(db, selection.key) is None:
                raise Refused(
                    "An automatic briefing continuation requires its active batch"
                )
        elif not continuation:
            batch = (
                self.store.group_briefings.batch(run.active_batch, db=db)
                if run.active_batch
                else None
            )
            if batch and batch.state == "active":
                raise ProcessingPaused("A group briefing batch is already active")
            if (
                not bypass_interval
                and run.last_auto_started is not None
                and self.store.clock() < run.last_auto_started + AUTO_INTERVAL
            ):
                raise ProcessingPaused("Waiting for the next automatic group update")
        if self.store.clock() < self._cursor(db, "group-backoff:" + selection.key, 0):
            raise ProcessingPaused("Waiting after a no-work group refusal")

    def reverifying(self, key, day, *, db=None):
        """Whether the current intake epoch has yet to read this day again.

        Until it has, the day's proven originals are an incomplete subset and
        saved work cannot be admitted into a new attempt. New work for the day
        waits; nothing published is withdrawn meanwhile.
        """
        start, end = group_context.day_bounds(self.store, day)
        with self._connection(db) as db:
            return any(
                group_context.eligible(message)
                and self.store.groups.awaiting_reread(message, db=db)
                for row in db.execute(
                    "SELECT body FROM messages WHERE chat_key=? AND date>=? AND date<? "
                    "AND deleted=0",
                    (key, start, end),
                )
                if (message := Message.model_validate_json(row[0]))
            )

    def manual(self, key, *, day=None, db=None):
        """A deliberate today click freezes cached intent without timer gates."""
        selected_day = self._day(self.store.clock())
        if day is not None and day != selected_day:
            raise Refused("Manual briefing updates are available for today only")
        with self._connection(db) as db:
            return self._candidate(
                db, key, selected_day, explicit=True, current_rules=True
            )

    def rollout(self, key, day, *, target_ids=None, continuation=False, db=None):
        """One frozen rollout day bypasses cadence, retaining automatic gates."""
        with self._connection(db) as db:
            selected = self._candidate(db, key, day, explicit=True, current_rules=True)
            # A paused rollout never turns into 30-day explicit history work.
            sources = tuple(
                source
                for source in selected.sources
                if source.date >= self.store.clock() - WINDOW
            )
            ids = tuple(source.message_id for source in sources)
            selected = replace(
                selected,
                sources=sources,
                intended_ids=ids,
                new_ids=tuple(i for i in selected.new_ids if i in set(ids)),
            )
            if not ids:
                raise Refused("No current cached originals remain for rollout day")
            if target_ids is not None and not set(target_ids) <= set(ids):
                raise Refused("Frozen rollout originals changed or expired")
            if not self._character_admitted(db, selected, target_ids):
                raise CharacterDeferred("Group character admission is deferred")
            self._timing(db, selected, continuation=continuation, bypass_interval=True)
            return selected

    def admit(
        self, key, day, target_ids, *, explicit=False, continuation=False, db=None
    ):
        """Recheck each unstarted stage; callers separately prove frozen Retry.

        Retry can only use the prior failed attempt's snapshot in the storage
        owner. This guard checks its current cached targets and ordinary gates,
        while omitting automatic quiet/cadence/character/window admission.
        """
        ids = tuple(target_ids)
        if (
            not ids
            or any(type(identity) is not int or identity <= 0 for identity in ids)
            or len(set(ids)) != len(ids)
        ):
            raise Refused("Exact distinct group stage targets are required")
        with self._connection(db) as db:
            selection = self._candidate(db, key, day, explicit=explicit)
            if not set(ids) <= set(selection.intended_ids):
                raise Refused("Group stage originals changed or are unavailable")
            if not explicit:
                if not self._character_admitted(db, selection, ids):
                    raise CharacterDeferred("Group character admission is deferred")
                self._timing(db, selection, continuation=continuation)
            return selection

    def _failed_same(self, db, selection):
        """Unchanged native evidence stays Retry-only, independent of guidance."""
        for row in db.execute(
            "SELECT body FROM group_briefing_attempts WHERE chat_key=? AND day=? AND state IN ('failed','interrupted')",
            (selection.key, selection.day),
        ):
            snapshot = BriefingAttempt.model_validate_json(row[0]).snapshot
            known_ids = set(snapshot.intended_ids) | {
                omission.message_id for omission in snapshot.omissions
            }
            bounded_ids = (set(selection.intended_ids) - set(selection.new_ids)) | set(
                sorted(selection.new_ids)[:200]
            )
            if (
                known_ids != set(selection.intended_ids)
                and set(snapshot.intended_ids) != bounded_ids
            ):
                continue
            state = self.store.groups.get(selection.key, db=db)
            intake = self.store.groups.intake(selection.key, db=db)
            if (
                snapshot.access_revision != state.access_revision
                or snapshot.epoch != intake.epoch
            ):
                continue
            same = True
            for packet in snapshot.packets:
                for source, revision in packet["inputs"].items():
                    message = db.execute(
                        "SELECT body FROM messages WHERE key=?", (source,)
                    ).fetchone()
                    message = (
                        Message.model_validate_json(message[0]) if message else None
                    )
                    if (
                        message is None
                        or message.revision != revision
                        or not self.store.groups.current(message, admission=True, db=db)
                        or packet.get("speaker_inputs", {}).get(source)
                        != [message.author_peer, message.author_signature]
                    ):
                        same = False
                        break
                if not same:
                    break
            if same:
                return True
        return False

    def pending(self, key=None, *, continuation=False, include_deferred=False, db=None):
        """Newest pending cached days first, with no job/batch/cadence writes."""
        with self._connection(db) as db:
            keys = (
                [key]
                if key is not None
                else [
                    row[0]
                    for row in db.execute(
                        "SELECT key FROM chats WHERE json_extract(body,'$.kind')='group' ORDER BY key"
                    )
                ]
            )
            result = []
            for key in keys:
                try:
                    state = self._policy(db, key)
                except Refused:
                    continue
                if db.execute(
                    "SELECT 1 FROM group_briefing_attempts WHERE chat_key=? AND state='active' LIMIT 1",
                    (key,),
                ).fetchone():
                    continue
                since = max(
                    self.store.clock() - WINDOW,
                    group_context.day_bounds(
                        self.store, self._day(state.first_enabled_at)
                    )[0],
                )
                days = sorted(
                    {
                        self._day(row[0])
                        for row in db.execute(
                            "SELECT date FROM messages WHERE chat_key=? AND date>=? AND date<=?",
                            (key, since, self.store.clock()),
                        )
                    },
                    reverse=True,
                )
                for day in days:
                    selection = self._candidate(db, key, day)
                    if not selection.new_ids or self._failed_same(db, selection):
                        continue
                    if not include_deferred:
                        try:
                            if not self._character_admitted(db, selection):
                                continue
                            self._timing(db, selection, continuation=continuation)
                        except Refused:
                            continue
                    result.append(selection)
            return sorted(result, key=lambda selection: selection.day, reverse=True)

    def holds(self):
        """When each eligible group's quiet, cadence and refusal hold lifts.

        Between local commits these are the instants at which the clock alone
        turns waiting group work into runnable work.
        """
        with self.store.transaction() as db:
            result = []
            for row in db.execute(
                "SELECT key FROM chats WHERE json_extract(body,'$.kind')='group'"
            ).fetchall():
                try:
                    state = self._policy(db, row[0])
                except Refused:
                    continue
                quiet = self._cursor(db, "group-quiet:" + row[0], {"changed": 0})
                result.append(
                    max(quiet["changed"], state.first_enabled_at) + QUIET_SECONDS
                )
                run = self.store.group_briefings.run_state(row[0], db=db)
                if run.last_auto_started is not None:
                    result.append(run.last_auto_started + AUTO_INTERVAL)
                result.append(self._cursor(db, "group-backoff:" + row[0], 0))
            return result

    def selection(self, key, *, continuation=False, db=None):
        return next(iter(self.pending(key, continuation=continuation, db=db)), None)

    def has_pending(self, *, ignore_threshold=False):
        """Quiet/cadence/backoff holds count as pending; threshold holds do not."""
        with self.store.transaction() as db:
            for selection in self.pending(include_deferred=True, db=db):
                if ignore_threshold or self._character_admitted(db, selection):
                    return True
            return self.active_pending(ignore_threshold=ignore_threshold, db=db)

    def footprint(self):
        """Eligible pending intent, including quiet/cadence/budget holds."""
        with self.store.transaction() as db:
            return [
                selection
                for selection in self.pending(include_deferred=True, db=db)
                if self._character_admitted(db, selection)
            ]

    def active_pending(self, *, ignore_threshold=False, db=None):
        with self._connection(db) as db:
            for row in db.execute(
                "SELECT body FROM group_briefing_attempts WHERE state='active'"
            ):
                attempt = BriefingAttempt.model_validate_json(row[0])
                try:
                    selection = self._candidate(
                        db,
                        attempt.snapshot.key,
                        attempt.snapshot.day,
                        explicit=not attempt.automatic,
                    )
                except Refused:
                    continue
                # Rollout is stored as explicit work to preserve its frozen day,
                # but retains the automatic character gate. A threshold-held
                # rollout releases memory just like held automatic work; actual
                # manual Update and Retry keep their explicit bypass.
                character_required = attempt.automatic or (
                    GroupBriefingRollout.started_target(self.store, attempt.id, db=db)
                    is not None
                )
                if self.store.group_briefings.current(
                    attempt, admission=True, db=db
                ) and (
                    ignore_threshold
                    or not character_required
                    or self._character_admitted(
                        db, selection, attempt.snapshot.intended_ids
                    )
                ):
                    return True
            return False
