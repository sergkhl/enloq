"""Group policy/access and intake evidence survive disposable source expiry."""

import json

from ..domain.models import Chat, GroupIntake, GroupState, Refused

WINDOW = 7 * 86400
RETENTION = 30 * 86400
DDL = (
    "CREATE TABLE IF NOT EXISTS group_state (chat_key TEXT PRIMARY KEY REFERENCES chats(key), body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS group_intake (chat_key TEXT PRIMARY KEY REFERENCES chats(key), body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS group_sources (source TEXT PRIMARY KEY REFERENCES messages(key) ON DELETE CASCADE, chat_key TEXT NOT NULL, epoch INTEGER NOT NULL, revision INTEGER NOT NULL)",
)


class GroupStates:
    def __init__(self, store):
        self.store = store
        self.on_access_change = lambda state: None

    def _chat(self, db, key):
        row = db.execute("SELECT body FROM chats WHERE key=?", (key,)).fetchone()
        if not row:
            raise Refused("Group unavailable for this account")
        chat = Chat.model_validate_json(row[0])
        if (
            chat.key != key
            or chat.account_id != self.store.account_id
            or chat.kind != "group"
        ):
            raise Refused("Group unavailable for this account")
        return chat

    def ensure(self, db, key):
        chat = self._chat(db, key)
        for table, model in (
            ("group_state", GroupState),
            ("group_intake", GroupIntake),
        ):
            db.execute(
                f"INSERT OR IGNORE INTO {table} VALUES (?,?)",
                (
                    key,
                    model(account_id=chat.account_id, peer=chat.peer).model_dump_json(),
                ),
            )

    def _read(self, db, key, table, model):
        # One currency proof reads these two rows for every source; the
        # connection drops what it holds at its next write.
        held = getattr(db, "scratch", None)
        if held is not None and (table, key) in held:
            return held[table, key]
        select = f"SELECT body FROM {table} WHERE chat_key=?"
        self._chat(db, key)
        row = db.execute(select, (key,)).fetchone()
        if row is None:
            self.ensure(db, key)
            row = db.execute(select, (key,)).fetchone()
        value = model.model_validate_json(row[0])
        if value.key != key:
            raise Refused("Group evidence identity mismatch")
        if held is not None:
            held[table, key] = value
        return value

    def _state(self, db, key):
        return self._read(db, key, "group_state", GroupState)

    def _intake(self, db, key):
        return self._read(db, key, "group_intake", GroupIntake)

    def _write(self, db, table, value):
        if self._read(db, value.key, table, type(value)) == value:
            return
        # Native intake polls every recent group continuously. Group planning
        # reads only groups whose AI is On, so these rows of an Off group
        # change no planning answer and need not discard one.
        state = value if table == "group_state" else self._state(db, value.key)
        was = self._state(db, value.key)
        quiet = not state.processing_enabled and not was.processing_enabled
        if quiet and hasattr(db, "muted"):
            db.muted = True
        try:
            db.execute(
                f"UPDATE {table} SET body=? WHERE chat_key=?",
                (value.model_dump_json(), value.key),
            )
        finally:
            if hasattr(db, "muted"):
                db.muted = False

    def get(self, key, *, db=None):
        if db is not None:
            return self._state(db, key)
        with self.store.transaction() as db:
            return self._state(db, key)

    def intake(self, key, *, db=None):
        if db is not None:
            return self._intake(db, key)
        with self.store.transaction() as db:
            return self._intake(db, key)

    def visible_in_list(self, key, *, cached_access=False):
        """Cached browser rows do not widen native sync/action admission."""
        with self.store.transaction() as db:
            state = self._state(db, key)
            if not state.supported:
                return False
            if state.access == "available":
                return True
            if not cached_access:
                return False
            return bool(
                db.execute(
                    "SELECT 1 FROM messages WHERE chat_key=? AND date>=? "
                    "UNION ALL SELECT 1 FROM drafts WHERE chat_key=? AND "
                    "(trim(json_extract(body,'$.text'))!='' OR "
                    "json_array_length(json_extract(body,'$.attachments'))>0 OR "
                    "json_extract(body,'$.selected_media') IS NOT NULL OR "
                    "json_extract(body,'$.reply_to') IS NOT NULL) "
                    "UNION ALL SELECT 1 FROM operations WHERE chat_key=? "
                    "AND state NOT IN ('confirmed','cancelled') LIMIT 1",
                    (key, self.store.clock() - RETENTION, key, key),
                ).fetchone()
            )

    def set_processing_enabled(self, key, enabled):
        if type(enabled) is not bool:
            raise Refused("Group processing requires On or Off")
        with self.store.transaction() as db:
            state = self._state(db, key)
            if state.processing_enabled != enabled or (
                enabled and state.first_enabled_at is None
            ):
                self._write(
                    db,
                    "group_state",
                    state.model_copy(
                        update={
                            "processing_enabled": enabled,
                            "first_enabled_at": (
                                state.first_enabled_at
                                if state.first_enabled_at is not None or not enabled
                                else self.store.clock()
                            ),
                        }
                    ),
                )

    def observe(
        self,
        key,
        *,
        access,
        supported=True,
        reason,
        observed_at=None,
        migrated_to=None,
        expected_revision=None,
    ):
        """Only native membership/read evidence changes access, never connectivity.

        A response started before a newer observation may not restore access.
        Migration is a relationship, never an alias or an operation retarget.
        """
        with self.store.transaction() as db:
            state = self._state(db, key)
            observed = self.store.clock() if observed_at is None else observed_at
            if (
                expected_revision is not None
                and state.access_revision != expected_revision
            ) or (
                observed_at is not None
                and expected_revision is None
                and observed < state.observed_at
            ):
                return state
            # Arrival order and matching captured material revisions establish
            # freshness even if the system wall clock has moved backwards.
            observed = max(observed, state.observed_at)
            values = dict(
                access=access,
                supported=supported,
                reason=reason,
                observed_at=observed,
                migrated_to=migrated_to or state.migrated_to,
            )
            next_state = GroupState.model_validate(state.model_dump() | values)
            if next_state.migrated_to:
                next_state = next_state.model_copy(
                    update={"access": "lost", "reason": "migrated"}
                )
            changed = any(
                getattr(state, field) != getattr(next_state, field)
                for field in ("access", "supported", "migrated_to")
            )
            if changed:
                next_state.access_revision += 1
                self._gap(db, key)
            if next_state != state:
                self._write(db, "group_state", next_state)
        if changed:
            self.on_access_change(next_state)
        return next_state

    def _gap(self, db, key, *, pts=None):
        intake = self._intake(db, key)
        if pts is not None and (
            type(pts) is not int
            or pts < 0
            or (intake.pts is not None and pts < intake.pts)
        ):
            raise Refused("Invalid native group gap checkpoint")
        next_intake = intake.model_copy(
            update=dict(
                epoch=intake.epoch + 1,
                before=0,
                count=0,
                omitted=0,
                top=0,
                cutoff=None,
                oldest=None,
                complete=False,
                gap=True,
                pts=intake.pts if pts is None else pts,
            )
        )
        self._write(db, "group_intake", next_intake)
        db.execute(
            "DELETE FROM cursors WHERE key IN (?,?)",
            ("recent:" + key, "coverage:" + key),
        )
        return next_intake

    def mark_gap(self, key, *, pts=None):
        with self.store.transaction() as db:
            return self._gap(db, key, pts=pts)

    def checkpoint(self, key, pts, *, epoch, final=True):
        if type(pts) is not int or pts < 0:
            raise Refused("Invalid native group checkpoint")
        with self.store.transaction() as db:
            intake = self._intake(db, key)
            if intake.epoch != epoch:
                raise Refused("Group intake changed during recovery")
            if intake.pts is not None and pts < intake.pts:
                raise Refused("Group checkpoint moved backwards")
            self._write(
                db,
                "group_intake",
                intake.model_copy(
                    update={
                        "pts": pts,
                        "replay_pending": not final,
                        "gap": intake.gap and not (intake.complete and final),
                    }
                ),
            )

    def begin_replay(self, key, *, epoch):
        with self.store.transaction() as db:
            intake = self._intake(db, key)
            if intake.epoch != epoch:
                raise Refused("Group intake changed during recovery")
            self._write(
                db, "group_intake", intake.model_copy(update={"replay_pending": True})
            )

    def scanned(
        self, key, *, epoch, before, top, cutoff, oldest, count, complete, omitted=0
    ):
        with self.store.transaction() as db:
            intake = self._intake(db, key)
            if intake.epoch != epoch:
                raise Refused("Group intake changed during recent read")
            next_intake = intake.model_copy(
                update=dict(
                    before=before,
                    top=top,
                    cutoff=cutoff,
                    oldest=oldest,
                    count=count,
                    omitted=omitted,
                    complete=complete,
                    gap=intake.gap and (not complete or intake.replay_pending),
                )
            )
            self._write(db, "group_intake", next_intake)
            db.execute(
                "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                (
                    "recent:" + key,
                    json.dumps(dict(before=before, complete=complete, count=count)),
                ),
            )
            return next_intake

    def permit_source(self, db, chat, message, epoch):
        state, intake = self._state(db, chat.key), self._intake(db, chat.key)
        old = db.execute(
            "SELECT date FROM messages WHERE key=?", (message.key,)
        ).fetchone()
        if epoch is not None and epoch != intake.epoch:
            return False
        if message.deleted:
            return bool(old)
        if not state.supported or state.access != "available":
            return False
        return (
            (old[0] >= self.store.clock() - RETENTION)
            if old
            else message.date >= self.store.clock() - WINDOW
        )

    def record_source(self, db, message, epoch):
        key = f"{message.account_id}/{message.peer}"
        intake = self._intake(db, key)
        if epoch is not None and epoch != intake.epoch:
            raise Refused("Group source observation changed")
        db.execute(
            "INSERT OR REPLACE INTO group_sources VALUES (?,?,?,?)",
            (message.key, key, intake.epoch, message.revision),
        )

    def current(self, message, *, admission=True, db=None):
        """Exact native source proof; routine replay blocks only new admission.

        Already-started work uses ``admission=False`` while still checking
        access, epoch, the exact material revision and pending durable effects.
        """
        if db is not None:
            return self._current(db, message, admission=admission)
        with self.store.transaction() as db:
            return self._current(db, message, admission=admission)

    def _current(self, db, message, *, admission):
        key = f"{message.account_id}/{message.peer}"
        state, intake = self._state(db, key), self._intake(db, key)
        return bool(
            state.supported
            and state.access == "available"
            and (not admission or not intake.replay_pending)
            and not message.deleted
            and message.date >= self.store.clock() - RETENTION
            and not db.execute(
                "SELECT 1 FROM cursors WHERE key=?",
                ("group-effects:" + message.key,),
            ).fetchone()
            and db.execute(
                "SELECT 1 FROM group_sources WHERE source=? AND chat_key=? AND epoch=? AND revision=?",
                (message.key, key, intake.epoch, message.revision),
            ).fetchone()
        )

    def _earlier_proof(self, db, message):
        """An unchanged original's proof from before the current intake epoch."""
        key = f"{message.account_id}/{message.peer}"
        state, intake = self._state(db, key), self._intake(db, key)
        if (
            not state.supported
            or state.access != "available"
            or message.deleted
            or message.date < self.store.clock() - RETENTION
            or db.execute(
                "SELECT 1 FROM cursors WHERE key=?", ("group-effects:" + message.key,)
            ).fetchone()
        ):
            return None
        proof = db.execute(
            "SELECT epoch FROM group_sources WHERE source=? AND chat_key=? AND revision=?",
            (message.key, key, message.revision),
        ).fetchone()
        return (proof[0], intake) if proof else None

    def awaiting_reread(self, message, *, db=None):
        """Proven before this intake epoch and not reached by its read yet."""
        if db is None:
            with self.store.transaction() as db:
                return self.awaiting_reread(message, db=db)
        earlier = self._earlier_proof(db, message)
        if not earlier or earlier[0] == earlier[1].epoch:
            return False
        intake = earlier[1]
        return (
            message.date >= max(intake.cutoff or 0, self.store.clock() - WINDOW)
            and not intake.complete
            and (not intake.before or message.message_id < intake.before)
        )

    def standing(self, message, *, db=None):
        """Whether an original that work already consumed is still unchanged.

        A new intake epoch means Telegram could not replay what was missed, so
        the recent window is read again from the newest original down. That
        alone changes no original. An earlier proof of this exact revision
        stands until the read reaches it, and for originals older than the
        window it reads. An original the read has passed without finding is gone.
        New admission still needs ``current``.
        """
        if db is None:
            with self.store.transaction() as db:
                return self.standing(message, db=db)
        earlier = self._earlier_proof(db, message)
        if not earlier:
            return False
        epoch, intake = earlier
        return (
            epoch == intake.epoch
            or message.date < max(intake.cutoff or 0, self.store.clock() - WINDOW)
            or self.awaiting_reread(message, db=db)
        )

    def withdraw_source(self, source):
        if not source.startswith(f"{self.store.account_id}/"):
            raise Refused("Group source belongs to another account")
        with self.store.transaction() as db:
            db.execute("DELETE FROM group_sources WHERE source=?", (source,))
