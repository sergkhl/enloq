"""Frozen group attempts, reusable chunks and final-only durable publication."""

import hashlib
import json
import math
import uuid
from contextlib import nullcontext

from pydantic import ValidationError

from ..domain.briefing_contract import GROUP_ROLES, BriefingError, validate_briefing
from ..domain.group_briefings import (
    BriefingAttempt,
    BriefingBatch,
    BriefingChunk,
    BriefingDay,
    BriefingIntakeCoverage,
    BriefingOmission,
    BriefingResult,
    BriefingRunState,
    BriefingScope,
    BriefingSpeechLimit,
    FrozenBriefing,
)
from ..domain.group_filtering import FILTER_ROLES
from ..domain.models import Message, Refused

GROUP_ROLES = GROUP_ROLES | FILTER_ROLES

TABLES = (
    "CREATE TABLE IF NOT EXISTS group_briefing_days (chat_key TEXT NOT NULL REFERENCES chats(key),day TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(chat_key,day))",
    "CREATE TABLE IF NOT EXISTS group_briefing_runs (chat_key TEXT PRIMARY KEY REFERENCES chats(key),body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS group_briefing_batches (id TEXT PRIMARY KEY,chat_key TEXT NOT NULL REFERENCES chats(key),body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS group_briefing_attempts (id TEXT PRIMARY KEY,chat_key TEXT NOT NULL REFERENCES chats(key),day TEXT NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS group_briefing_chunks (id TEXT PRIMARY KEY,chat_key TEXT NOT NULL REFERENCES chats(key),day TEXT NOT NULL,fingerprint TEXT NOT NULL,valid INTEGER NOT NULL,body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS group_briefing_results (id TEXT PRIMARY KEY,chat_key TEXT NOT NULL REFERENCES chats(key),day TEXT NOT NULL,valid INTEGER NOT NULL,body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS group_briefing_dependencies (kind TEXT NOT NULL,owner_id TEXT NOT NULL,chat_key TEXT NOT NULL,source TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(kind,owner_id,source))",
    "CREATE UNIQUE INDEX IF NOT EXISTS group_briefing_active_day ON group_briefing_attempts(chat_key,day) WHERE state='active'",
    "CREATE INDEX IF NOT EXISTS group_briefing_chunk_reuse ON group_briefing_chunks(chat_key,day,fingerprint,valid)",
    "CREATE INDEX IF NOT EXISTS group_briefing_dependency_source ON group_briefing_dependencies(source)",
)


def _withdraw(where):
    statements = []
    for kind, table in (("chunk", "chunks"), ("result", "results")):
        statements.append(
            f"UPDATE group_briefing_{table} SET valid=0 WHERE valid=1 AND id IN "
            f"(SELECT owner_id FROM group_briefing_dependencies WHERE kind='{kind}' AND {where});"
        )
    statements.append(
        "UPDATE group_briefing_attempts SET state='superseded',"
        "body=json_set(body,'$.state','superseded','$.error','Briefing dependencies changed') "
        "WHERE state IN ('active','failed','interrupted') AND id IN "
        f"(SELECT owner_id FROM group_briefing_dependencies WHERE kind='attempt' AND {where});"
    )
    return " ".join(statements)


TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS group_briefing_source_update AFTER UPDATE ON messages "
    "WHEN NEW.revision!=OLD.revision OR NEW.deleted!=OLD.deleted BEGIN "
    + _withdraw("source=NEW.key")
    + " END",
    "CREATE TRIGGER IF NOT EXISTS group_briefing_source_delete BEFORE DELETE ON messages BEGIN "
    + _withdraw("source=OLD.key")
    + " END",
    "CREATE TRIGGER IF NOT EXISTS group_briefing_proof_delete BEFORE DELETE ON group_sources BEGIN "
    + _withdraw("source=OLD.source")
    + " END",
    "CREATE TRIGGER IF NOT EXISTS group_briefing_access_update AFTER UPDATE ON group_state "
    "WHEN json_extract(NEW.body,'$.access_revision')!=json_extract(OLD.body,'$.access_revision') BEGIN "
    + _withdraw("chat_key=NEW.chat_key")
    + " END",
    # ADR 34: a new intake epoch re-reads originals and changes none.
    # Earlier stores withdrew every briefing of the group at that moment.
    "DROP TRIGGER IF EXISTS group_briefing_epoch_update",
    *(
        f"CREATE TRIGGER IF NOT EXISTS group_briefing_{table}_delete AFTER DELETE ON group_briefing_{table} "
        f"BEGIN DELETE FROM group_briefing_dependencies WHERE kind='{kind}' AND owner_id=OLD.id; END"
        for kind, table in (
            ("attempt", "attempts"),
            ("chunk", "chunks"),
            ("result", "results"),
        )
    ),
)
DDL = TABLES + TRIGGERS


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


_OMIT = object()


def _clean(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _OMIT
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"data_url", "guidance"}:
                continue
            clean = _clean(item)
            if clean is not _OMIT:
                result[key] = clean
        return result
    if isinstance(value, (list, tuple)):
        return [clean for item in value if (clean := _clean(item)) is not _OMIT]
    return value


def _ids(values):
    values = list(values)
    if (
        not values
        or any(type(value) is not int or value <= 0 for value in values)
        or len(set(values)) != len(values)
    ):
        raise Refused("Exact distinct native briefing sources are required")
    return values


def _omissions(values):
    try:
        result = [BriefingOmission.model_validate(value) for value in values]
    except ValidationError:
        raise Refused("Invalid briefing omissions") from None
    if len({value.message_id for value in result}) != len(result):
        raise Refused("Duplicate briefing omissions")
    return result


def _coverage(intake, start):
    return BriefingIntakeCoverage(
        native_omitted=intake.omitted,
        intake_complete=intake.complete,
        # A complete seven-day window cannot prove an earlier calendar day.
        # The scan must have covered at least the beginning of this day.
        range_complete=bool(
            intake.complete and intake.cutoff is not None and intake.cutoff <= start
        ),
        before=intake.before,
        top=intake.top,
        count=intake.count,
        cutoff=intake.cutoff,
        oldest=intake.oldest,
    )


class GroupBriefings:
    def __init__(self, store):
        self.store = store
        # Provisional final wording never enters a durable result or SQL table.
        self._prefixes = {}

    def _scope(self, db, key, day):
        chat = self.store.groups._chat(db, key)
        try:
            return BriefingScope(account_id=chat.account_id, peer=chat.peer, day=day)
        except (ValidationError, ValueError):
            raise Refused(
                "A typed group and calendar briefing day are required"
            ) from None

    def _current(self, db, snapshot, *, admission=False):
        from ..application.group_context import current

        try:
            state = self.store.groups.get(snapshot.key, db=db)
            intake = self.store.groups.intake(snapshot.key, db=db)
            for packet in snapshot.packets:
                self._packet_sources(packet, snapshot.key)
        except (Refused, KeyError, TypeError, ValueError):
            return False
        return bool(
            state.supported
            and state.access == "available"
            and state.access_revision == snapshot.access_revision
            # An epoch change alone withdraws nothing: each packet proves its
            # originals, exactly for admission and as standing otherwise.
            and snapshot.epoch <= intake.epoch
            and (not admission or not intake.replay_pending)
            and snapshot.packets
            and all(
                current(self.store, packet, admission=admission, db=db)
                for packet in snapshot.packets
            )
        )

    def current(self, value, *, admission=False, db=None):
        with nullcontext(db) if db is not None else self.store.transaction() as db:
            if isinstance(value, BriefingAttempt):
                saved = self._attempt(db, value.id)
                return bool(
                    saved
                    and saved.state == "active"
                    and self._current(db, saved.snapshot, admission=admission)
                )
            if isinstance(value, (BriefingChunk, BriefingResult)):
                kind = "chunk" if isinstance(value, BriefingChunk) else "result"
                saved = self._artifact(db, kind, value.id)
                return bool(
                    saved
                    and saved.valid
                    and self._current(db, saved.snapshot, admission=admission)
                    and (kind == "chunk" or self._result_chunks_current(db, saved))
                )
            return self._current(db, value, admission=admission)

    def freeze(
        self,
        key,
        day,
        packets,
        *,
        intended_ids,
        omissions=(),
        guidance_hash,
        route,
    ):
        from ..application.group_context import day_bounds

        packets, intended = _clean(list(packets)), _ids(intended_ids)
        with self.store.transaction() as db:
            scope = self._scope(db, key, day)
            start, end = day_bounds(self.store, day)
            state = self.store.groups.get(key, db=db)
            intake = self.store.groups.intake(key, db=db)
            packet_ids = []
            for packet in packets:
                if packet.get("chat_key") != key or packet.get("day") != day:
                    raise Refused("Briefing packet identity mismatch")
                covered = _ids(packet.get("covered_ids", ()))
                if covered != [
                    message.get("id") for message in packet.get("messages", ())
                ]:
                    raise Refused("Briefing packet coverage differs from its originals")
                packet_ids.extend(covered)
            if not packets or packet_ids != intended:
                raise Refused("Briefing intended coverage differs from its packets")
            dates = []
            for identity in intended:
                row = db.execute(
                    "SELECT date FROM messages WHERE chat_key=? AND message_id=?",
                    (key, identity),
                ).fetchone()
                if not row or not start <= row[0] < end:
                    raise Refused("Briefing source is outside its calendar day")
                dates.append(row[0])
            try:
                frozen = FrozenBriefing(
                    **scope.model_dump(),
                    start=start,
                    end=end,
                    cutoff_id=max(intended),
                    cutoff_date=max(dates),
                    intended_ids=intended,
                    source_dates=dict(zip(intended, dates, strict=True)),
                    packets=packets,
                    omissions=_omissions(omissions),
                    access_revision=state.access_revision,
                    epoch=intake.epoch,
                    gap=intake.gap,
                    intake_coverage=_coverage(intake, start),
                    guidance_hash=guidance_hash,
                    route=route,
                )
            except ValidationError:
                raise Refused("Invalid frozen briefing evidence") from None
            if not self._current(db, frozen, admission=True):
                raise Refused("Briefing sources changed before freezing")
            return frozen

    def _attempt(self, db, identity):
        row = db.execute(
            "SELECT state,body FROM group_briefing_attempts WHERE id=?", (identity,)
        ).fetchone()
        if not row:
            return None
        return BriefingAttempt.model_validate_json(row["body"]).model_copy(
            update={"state": row["state"]}
        )

    def attempt(self, identity, *, db=None):
        with nullcontext(db) if db is not None else self.store.transaction() as db:
            value = self._attempt(db, identity)
            if value:
                self._reconcile(db, value.snapshot.key)
                value = self._attempt(db, identity)
            return value

    def _write_attempt(self, db, attempt):
        db.execute(
            "INSERT INTO group_briefing_attempts VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,body=excluded.body",
            (
                attempt.id,
                attempt.snapshot.key,
                attempt.snapshot.day,
                attempt.state,
                attempt.model_dump_json(),
            ),
        )

    def _dependencies(self, db, kind, identity, snapshot):
        dependencies = {}
        for packet in snapshot.packets:
            dependencies.update(packet["inputs"])
        db.executemany(
            "INSERT INTO group_briefing_dependencies VALUES (?,?,?,?,?)",
            (
                (kind, identity, snapshot.key, source, revision)
                for source, revision in dependencies.items()
            ),
        )

    def _run(self, db, key):
        chat = self.store.groups._chat(db, key)
        row = db.execute(
            "SELECT body FROM group_briefing_runs WHERE chat_key=?", (key,)
        ).fetchone()
        return (
            BriefingRunState.model_validate_json(row[0])
            if row
            else BriefingRunState(account_id=chat.account_id, peer=chat.peer)
        )

    def _write_run(self, db, value):
        db.execute(
            "INSERT OR REPLACE INTO group_briefing_runs VALUES (?,?)",
            (value.key, value.model_dump_json()),
        )

    def run_state(self, key, *, db=None):
        with nullcontext(db) if db is not None else self.store.transaction() as db:
            return self._run(db, key)

    def _batch(self, db, identity):
        row = db.execute(
            "SELECT body FROM group_briefing_batches WHERE id=?", (identity,)
        ).fetchone()
        return BriefingBatch.model_validate_json(row[0]) if row else None

    def batch(self, identity, *, db=None):
        with nullcontext(db) if db is not None else self.store.transaction() as db:
            return self._batch(db, identity)

    def _write_batch(self, db, value):
        db.execute(
            "INSERT OR REPLACE INTO group_briefing_batches VALUES (?,?,?)",
            (value.id, value.key, value.model_dump_json()),
        )

    def _begin_batch(self, db, key, identity, *, automatic):
        run = self._run(db, key)
        existing = self._batch(db, identity)
        if existing:
            if (
                existing.key != key
                or existing.automatic != automatic
                or existing.state != "active"
            ):
                raise Refused("Briefing batch identity is already used")
            return existing
        if run.active_batch:
            previous = self._batch(db, run.active_batch)
            if previous and previous.state == "active":
                roles = sorted(GROUP_ROLES)
                if (
                    db.execute(
                        "SELECT 1 FROM group_briefing_attempts WHERE chat_key=? AND state='active' LIMIT 1",
                        (key,),
                    ).fetchone()
                    or db.execute(
                        f"SELECT 1 FROM jobs WHERE role IN ({','.join('?' * len(roles))}) AND state IN ('queued','claimed','running') AND json_extract(payload,'$.batch')=? LIMIT 1",
                        (*roles, previous.id),
                    ).fetchone()
                ):
                    raise Refused("A briefing update is already active for this group")
                outcomes = {
                    row[0]
                    for row in db.execute(
                        "SELECT state FROM group_briefing_attempts WHERE json_extract(body,'$.batch_id')=?",
                        (previous.id,),
                    )
                }
                outcome = (
                    "failed"
                    if "failed" in outcomes
                    else "interrupted"
                    if outcomes & {"interrupted", "superseded"}
                    else "completed"
                )
                self.finish_batch(previous.id, state=outcome, db=db)
        batch = BriefingBatch(
            id=identity,
            account_id=run.account_id,
            peer=run.peer,
            automatic=automatic,
            started=self.store.clock(),
        )
        self._write_batch(db, batch)
        self._write_run(
            db,
            run.model_copy(
                update={
                    "active_batch": identity,
                    "last_auto_started": max(
                        self.store.clock(), run.last_auto_started or self.store.clock()
                    )
                    if automatic
                    else run.last_auto_started,
                }
            ),
        )
        return batch

    def begin_batch(self, key, identity=None, *, automatic=True):
        if type(automatic) is not bool:
            raise Refused("Invalid briefing batch admission")
        with self.store.transaction() as db:
            self._reconcile(db, key)
            return self._begin_batch(
                db, key, identity or uuid.uuid4().hex, automatic=automatic
            )

    def record_work(
        self,
        identity,
        *,
        requests=0,
        classifier_requests=0,
        source_ids=(),
        speech_seconds=0,
        dispatch_token=None,
        db=None,
    ):
        ids = list(source_ids)
        if (
            type(requests) is not int
            or requests < 0
            or type(classifier_requests) is not int
            or classifier_requests < 0
            or any(type(value) is not int or value <= 0 for value in ids)
            or isinstance(speech_seconds, bool)
            or not isinstance(speech_seconds, (int, float))
            or not math.isfinite(speech_seconds)
            or speech_seconds < 0
            or (
                dispatch_token is not None
                and (
                    not isinstance(dispatch_token, str)
                    or not dispatch_token
                    or (
                        requests + classifier_requests != 1
                        and not (
                            requests == 0
                            and classifier_requests == 0
                            and speech_seconds > 0
                        )
                    )
                )
            )
        ):
            raise Refused("Invalid briefing work evidence")
        proof = digest(
            dict(
                requests=requests,
                classifier_requests=classifier_requests,
                source_ids=sorted(set(ids)),
                speech_seconds=speech_seconds,
            )
        )
        with nullcontext(db) if db is not None else self.store.transaction() as db:
            batch = self._batch(db, identity)
            if batch is None or batch.state != "active":
                raise Refused("Briefing batch is no longer active")
            if dispatch_token in batch.dispatch_tokens:
                if batch.dispatch_tokens[dispatch_token] != proof:
                    raise Refused(
                        "Briefing dispatch token differs from its recorded work"
                    )
                return batch
            sources = sorted(set(batch.source_ids) | set(ids))
            if batch.speech_seconds + speech_seconds > 1200:
                raise BriefingSpeechLimit("Briefing local speech work limit reached")
            if (
                batch.requests + requests > 5
                or batch.classifier_requests + classifier_requests > 12
                or len(sources) > 200
            ):
                raise Refused("Briefing update work limit reached")
            tokens = dict(batch.dispatch_tokens)
            if dispatch_token is not None:
                tokens[dispatch_token] = proof
            value = batch.model_copy(
                update=dict(
                    requests=batch.requests + requests,
                    classifier_requests=batch.classifier_requests + classifier_requests,
                    source_ids=sources,
                    speech_seconds=batch.speech_seconds + speech_seconds,
                    dispatch_tokens=tokens,
                )
            )
            if value != batch:
                self._write_batch(db, value)
            return value

    def record_speech(
        self,
        identity,
        seconds,
        *,
        source_ids=(),
        token=None,
        admission=None,
        db=None,
    ):
        """Charge fresh local work at its guarded start, before the local await."""
        if token is not None and (not isinstance(token, str) or not token):
            raise Refused("Invalid briefing speech token")
        with nullcontext(db) if db is not None else self.store.transaction() as db:
            if admission is not None:
                admission(db)
            return self.record_work(
                identity,
                source_ids=source_ids,
                speech_seconds=seconds,
                dispatch_token="speech:" + token if token is not None else None,
                db=db,
            )

    def finish_batch(self, identity, *, state="completed", db=None):
        if state not in {"completed", "failed", "interrupted"}:
            raise Refused("Invalid briefing batch outcome")
        with nullcontext(db) if db is not None else self.store.transaction() as db:
            batch = self._batch(db, identity)
            if not batch:
                raise Refused("Briefing batch unavailable")
            if batch.state == "active":
                batch = batch.model_copy(
                    update=dict(state=state, finished=self.store.clock())
                )
                self._write_batch(db, batch)
                run = self._run(db, batch.key)
                if run.active_batch == identity:
                    self._write_run(db, run.model_copy(update={"active_batch": None}))
            return batch

    def _check_attempt(self, db, snapshot, *, retry_of=None):
        if not self._current(db, snapshot, admission=True):
            raise Refused("Briefing sources changed before the attempt")
        if db.execute(
            "SELECT 1 FROM group_briefing_attempts WHERE chat_key=? AND state='active' LIMIT 1",
            (snapshot.key,),
        ).fetchone():
            raise Refused("A briefing attempt is already active for this group")
        completed = set()
        for row in db.execute(
            "SELECT id FROM group_briefing_results WHERE chat_key=? AND day=? AND valid=1",
            (snapshot.key, snapshot.day),
        ):
            final = self._artifact(db, "result", row[0])
            if (
                (
                    retry_of is not None
                    or (
                        final.snapshot.guidance_hash == snapshot.guidance_hash
                        and final.snapshot.route == snapshot.route
                    )
                )
                and self._current(db, final.snapshot)
                and self._result_chunks_current(db, final)
                and self._media_current(
                    final.snapshot, db, excluded_ids=final.excluded_ids
                )
            ):
                completed.update(final.covered_ids)
        if set(snapshot.intended_ids) <= completed:
            raise Refused("Completed briefing sources are unchanged")
        if retry_of is None:
            fingerprint = self._source_fingerprint(snapshot)
            for row in db.execute(
                "SELECT body,state FROM group_briefing_attempts WHERE chat_key=? AND day=?",
                (snapshot.key, snapshot.day),
            ):
                previous = BriefingAttempt.model_validate_json(row["body"])
                if self._source_fingerprint(previous.snapshot) == fingerprint and row[
                    "state"
                ] in {"failed", "interrupted"}:
                    raise Refused(
                        "Unchanged briefing work requires failed-attempt Retry"
                    )

    def can_create(self, snapshot):
        """Read frozen intent without starting its clock, batch or attempt."""
        with self.store.transaction() as db:
            try:
                self._check_attempt(db, snapshot)
            except Refused:
                return False
            return True

    def _create_attempt(self, db, snapshot, *, automatic, batch_id, retry_of=None):
        self._check_attempt(db, snapshot, retry_of=retry_of)
        batch = self._batch(db, batch_id) if batch_id else None
        if batch is None:
            batch = self._begin_batch(
                db, snapshot.key, batch_id or uuid.uuid4().hex, automatic=automatic
            )
        if (
            batch.key != snapshot.key
            or batch.automatic != automatic
            or batch.state != "active"
        ):
            raise Refused("Briefing attempt has a different batch")
        attempt = BriefingAttempt(
            id=uuid.uuid4().hex,
            snapshot=snapshot,
            state="active",
            automatic=automatic,
            batch_id=batch.id,
            retry_of=retry_of,
            created=self.store.clock(),
            updated=self.store.clock(),
        )
        db.execute(
            "INSERT OR IGNORE INTO group_briefing_days VALUES (?,?,?)",
            (
                snapshot.key,
                snapshot.day,
                json.dumps(dict(start=snapshot.start, end=snapshot.end)),
            ),
        )
        self._write_attempt(db, attempt)
        self._dependencies(db, "attempt", attempt.id, snapshot)
        return attempt

    def create_attempt(self, snapshot, *, automatic=False, batch_id=None):
        snapshot = FrozenBriefing.model_validate(snapshot.model_dump())
        if type(automatic) is not bool:
            raise Refused("Invalid briefing attempt admission")
        with self.store.transaction() as db:
            self._scope(db, snapshot.key, snapshot.day)
            self._reconcile(db, snapshot.key)
            return self._create_attempt(
                db, snapshot, automatic=automatic, batch_id=batch_id
            )

    def refresh_guidance(self, identity, *, guidance_hash, route, db=None):
        """Refresh pending stages; completed chunks keep their dispatched proof."""
        with nullcontext(db) if db is not None else self.store.transaction() as db:
            attempt = self._active(db, identity)
            try:
                snapshot = FrozenBriefing.model_validate(
                    attempt.snapshot.model_dump()
                    | dict(guidance_hash=guidance_hash, route=route)
                )
            except ValidationError:
                raise Refused("Invalid pending briefing guidance") from None
            if snapshot == attempt.snapshot:
                return attempt
            value = attempt.model_copy(
                update=dict(snapshot=snapshot, updated=self.store.clock())
            )
            self._write_attempt(db, value)
            return value

    def retry(self, identity, *, batch_id=None, guidance_hash=None, route=None):
        with self.store.transaction() as db:
            previous = self._attempt(db, identity)
            if previous is None or previous.state not in {"failed", "interrupted"}:
                raise Refused("Only a failed or interrupted briefing can be retried")
            state = self.store.groups.get(previous.snapshot.key, db=db)
            if not state.processing_enabled:
                raise Refused("Group AI processing is paused")
            if self.store.shared_path:
                account = db.execute(
                    "SELECT ai_enabled FROM shared.accounts WHERE account_id=?",
                    (self.store.account_id,),
                ).fetchone()
                if not account or not account[0]:
                    raise Refused("Account AI processing is paused")
            snapshot = previous.snapshot
            if guidance_hash is not None or route is not None:
                try:
                    snapshot = FrozenBriefing.model_validate(
                        snapshot.model_dump()
                        | dict(
                            guidance_hash=guidance_hash
                            if guidance_hash is not None
                            else snapshot.guidance_hash,
                            route=route if route is not None else snapshot.route,
                        )
                    )
                except ValidationError:
                    raise Refused("Invalid briefing Retry guidance") from None
            attempt = self._create_attempt(
                db,
                snapshot,
                automatic=False,
                batch_id=batch_id,
                retry_of=identity,
            )
            prior_batch = self._batch(db, previous.batch_id)
            batch = self._batch(db, attempt.batch_id)
            if prior_batch and prior_batch.classifier_requests:
                self._write_batch(
                    db,
                    batch.model_copy(
                        update={
                            "classifier_requests": prior_batch.classifier_requests,
                            "source_ids": sorted(
                                set(batch.source_ids) | set(prior_batch.source_ids)
                            ),
                        }
                    ),
                )
            return attempt

    def _artifact(self, db, kind, identity):
        table, model = (
            ("chunks", BriefingChunk)
            if kind == "chunk"
            else ("results", BriefingResult)
        )
        row = db.execute(
            f"SELECT valid,body FROM group_briefing_{table} WHERE id=?", (identity,)
        ).fetchone()
        return (
            model.model_validate_json(row["body"]).model_copy(
                update={"valid": bool(row["valid"])}
            )
            if row
            else None
        )

    def _result_chunks_current(self, db, result):
        if not self._filter_current(db, result):
            return False
        for identity in result.chunk_ids:
            chunk = self._artifact(db, "chunk", identity)
            if (
                chunk is None
                or not chunk.valid
                or not self._current(db, chunk.snapshot)
            ):
                return False
        return True

    def _filter_current(self, db, result):
        if not all(
            proof.get("key") == result.snapshot.key
            and self.store.group_filters.evidence_current(proof, db=db, standing=True)
            for proof in result.filter_evidence
        ):
            return False
        # A remembered decision changes today's work; earlier results retain
        # their adopted policy and native source navigation.
        if result.filter_state is None:
            return True
        from datetime import datetime
        from zoneinfo import ZoneInfo

        today = (
            datetime.fromtimestamp(self.store.clock(), ZoneInfo(self.store.timezone))
            .date()
            .isoformat()
        )
        if result.snapshot.day < today:
            return True
        return (
            self.store.group_filters.state_hash(result.snapshot.key, db=db)
            == result.filter_state
        )

    def _media_current(self, snapshot, db, *, excluded_ids=()):
        from ..application.media import transcript_fingerprint

        for packet in snapshot.packets:
            for source, revision in packet.get("inputs", {}).items():
                if int(source.rsplit("/", 1)[1]) in excluded_ids:
                    continue
                row = db.execute(
                    "SELECT body FROM derivatives WHERE source=? AND revision=?",
                    (source, revision),
                ).fetchone()
                cached = json.loads(row[0]) if row else {}
                for field, proof in (
                    ("image_context", "image_inputs"),
                    ("transcript", "transcript_inputs"),
                ):
                    evidence = (
                        transcript_fingerprint(cached[field])
                        if field == "transcript" and cached.get(field)
                        else cached.get(field)
                    )
                    if evidence and packet.get(proof, {}).get(source) != evidence:
                        return False
        return True

    def _fingerprint(self, snapshot):
        value = snapshot.model_dump()
        # Intake coverage can improve without changing any required source.
        value.pop("gap")
        value.pop("omissions")
        value.pop("intake_coverage")
        for packet in value["packets"]:
            packet.pop("omissions", None)
        return digest(value)

    def _source_fingerprint(self, snapshot):
        return digest(
            dict(
                key=snapshot.key,
                day=snapshot.day,
                intended_ids=snapshot.intended_ids,
                inputs={
                    key: revision
                    for packet in snapshot.packets
                    for key, revision in packet["inputs"].items()
                },
                access_revision=snapshot.access_revision,
                epoch=snapshot.epoch,
            )
        )

    def _packet_sources(self, packet, key):
        if (
            not isinstance(packet, dict)
            or not isinstance(packet.get("messages"), list)
            or not isinstance(packet.get("context", []), list)
            or any(
                not isinstance(message, dict)
                for message in packet["messages"] + packet.get("context", [])
            )
        ):
            raise Refused("Invalid briefing packet originals")
        covered = _ids(packet.get("covered_ids", ()))
        targets = _ids(message.get("id") for message in packet.get("messages", ()))
        if targets != covered:
            raise Refused("Prepared briefing targets differ from their originals")
        context = [message.get("id") for message in packet.get("context", ())]
        if context:
            context = _ids(context)
        if set(targets) & set(context):
            raise Refused("Briefing targets repeat in supporting context")
        expected = {f"{key}/{identity}" for identity in targets + context}
        if set(packet.get("inputs", {})) != expected:
            raise Refused("Briefing native dependency proof is incomplete")
        for field in ("image_inputs", "transcript_inputs"):
            if not set(packet.get(field, {})) <= expected:
                raise Refused("Briefing prepared dependency is outside its originals")
        return targets

    def _subset(self, snapshot, ids, packet=None):
        ids = _ids(ids)
        if not set(ids) <= set(snapshot.intended_ids):
            raise Refused("Briefing coverage expands the frozen attempt")
        if packet is None:
            packets = []
            for original in snapshot.packets:
                targets = {message["id"] for message in original["messages"]}
                selected = targets & set(ids)
                if not selected:
                    continue
                value = _clean(original)
                context_ids = {message["id"] for message in value.get("context", ())}
                keys = {
                    f"{snapshot.key}/{identity}" for identity in selected | context_ids
                }
                value["messages"] = [
                    message
                    for message in value["messages"]
                    if message["id"] in selected
                ]
                value["covered_ids"] = [message["id"] for message in value["messages"]]
                for field in (
                    "inputs",
                    "attribution_inputs",
                    "speaker_inputs",
                    "image_inputs",
                    "transcript_inputs",
                ):
                    if field in value:
                        value[field] = {
                            key: item
                            for key, item in value[field].items()
                            if key in keys
                        }
                packets.append(value)
        else:
            packet = _clean(packet)
            if (
                packet.get("chat_key") != snapshot.key
                or packet.get("day") != snapshot.day
                or packet.get("covered_ids") != ids
            ):
                raise Refused("Prepared briefing packet coverage differs")
            self._packet_sources(packet, snapshot.key)
            frozen_inputs = {
                key: revision
                for value in snapshot.packets
                for key, revision in value["inputs"].items()
            }
            if not packet.get("inputs") or any(
                frozen_inputs.get(key) != revision
                for key, revision in packet["inputs"].items()
            ):
                raise Refused("Prepared briefing packet expands frozen dependencies")
            packets = [packet]
        dates = {identity: snapshot.source_dates[identity] for identity in ids}
        return FrozenBriefing.model_validate(
            snapshot.model_dump()
            | dict(
                intended_ids=ids,
                source_dates=dates,
                cutoff_id=max(ids),
                cutoff_date=max(dates.values()),
                packets=packets,
                omissions=[],
            )
        )

    def chunk(self, snapshot, target_ids, *, packet=None):
        with self.store.transaction() as db:
            wanted = self._subset(snapshot, target_ids, packet)
            self._reconcile(db, snapshot.key)
            if not self._current(db, wanted):
                return None
            for row in db.execute(
                "SELECT id FROM group_briefing_chunks WHERE chat_key=? AND day=? AND valid=1 ORDER BY rowid DESC",
                (snapshot.key, snapshot.day),
            ):
                value = self._artifact(db, "chunk", row[0])
                if (
                    value.covered_ids == wanted.intended_ids
                    and value.snapshot.guidance_hash == wanted.guidance_hash
                    and value.snapshot.route == wanted.route
                    and self._current(db, value.snapshot)
                    and (
                        packet is None or value.fingerprint == self._fingerprint(wanted)
                    )
                ):
                    return value
            return None

    def _active(self, db, identity):
        attempt = self._attempt(db, identity)
        if (
            attempt is None
            or attempt.state != "active"
            or not self._current(db, attempt.snapshot)
        ):
            raise Refused("Briefing attempt changed or is no longer active")
        return attempt

    def store_chunk(self, identity, target_ids, output, *, packet=None):
        ids = _ids(target_ids)
        try:
            output = validate_briefing(output, ids)
        except BriefingError as error:
            raise Refused(f"Invalid briefing chunk ({error.code})") from None
        with self.store.transaction() as db:
            attempt = self._active(db, identity)
            snapshot = self._subset(attempt.snapshot, ids, packet)
            if not self._current(db, snapshot):
                raise Refused("Prepared briefing dependencies changed")
            fingerprint = self._fingerprint(snapshot)
            row = db.execute(
                "SELECT id FROM group_briefing_chunks WHERE chat_key=? AND day=? AND fingerprint=? AND valid=1",
                (snapshot.key, snapshot.day, fingerprint),
            ).fetchone()
            if row:
                existing = self._artifact(db, "chunk", row[0])
                if existing.output != output:
                    raise Refused("A validated chunk already exists for these inputs")
                return existing
            value = BriefingChunk(
                id=uuid.uuid4().hex,
                attempt_id=identity,
                fingerprint=fingerprint,
                snapshot=snapshot,
                covered_ids=ids,
                output=output,
                created=self.store.clock(),
                filter_state=self.store.group_filters.state_hash(snapshot.key, db=db),
            )
            db.execute(
                "INSERT INTO group_briefing_chunks VALUES (?,?,?,?,1,?)",
                (
                    value.id,
                    snapshot.key,
                    snapshot.day,
                    fingerprint,
                    value.model_dump_json(),
                ),
            )
            self._dependencies(db, "chunk", value.id, snapshot)
            return value

    def publish(
        self,
        identity,
        output,
        *,
        covered_ids,
        chunk_ids=(),
        original_ids=None,
        omissions=(),
        packet=None,
        excluded_ids=(),
        ad_ids=(),
        local_outcome=None,
        filter_state=None,
        filter_evidence=(),
    ):
        covered, chunk_ids = _ids(covered_ids), list(chunk_ids)
        excluded = _ids(excluded_ids) if excluded_ids else []
        ads = _ids(ad_ids) if ad_ids else []
        if not set(ads) <= set(excluded) <= set(covered):
            raise Refused("Invalid briefing exclusion coverage")
        if len(set(chunk_ids)) != len(chunk_ids):
            raise Refused("Duplicate briefing chunks")
        try:
            output = validate_briefing(output, sorted(set(covered) - set(excluded)))
        except BriefingError as error:
            raise Refused(f"Invalid final briefing ({error.code})") from None
        with self.store.transaction() as db:
            attempt = self._active(db, identity)
            if (
                filter_state is not None
                and filter_state
                != self.store.group_filters.state_hash(attempt.snapshot.key, db=db)
            ):
                raise Refused("Final briefing filtering decisions changed")
            if not all(
                proof.get("key") == attempt.snapshot.key
                and self.store.group_filters.evidence_current(
                    proof, db=db, standing=True
                )
                for proof in filter_evidence
            ):
                raise Refused("Final briefing excluded evidence changed")
            selected_packets, chunk_coverage = [], set()
            frozen_inputs = {
                key: revision
                for value in attempt.snapshot.packets
                for key, revision in value["inputs"].items()
            }
            for chunk_id in chunk_ids:
                chunk = self._artifact(db, "chunk", chunk_id)
                if (
                    chunk is None
                    or not chunk.valid
                    or not self._current(db, chunk.snapshot)
                    or chunk.snapshot.key != attempt.snapshot.key
                    or chunk.snapshot.day != attempt.snapshot.day
                    or chunk.snapshot.guidance_hash != attempt.snapshot.guidance_hash
                    or chunk.snapshot.route != attempt.snapshot.route
                ):
                    raise Refused("Final briefing uses an unavailable chunk")
                if any(
                    frozen_inputs.get(key) != revision
                    for value in chunk.snapshot.packets
                    for key, revision in value["inputs"].items()
                ):
                    raise Refused("Final briefing chunk expands frozen dependencies")
                chunk_coverage.update(chunk.covered_ids)
                selected_packets.extend(chunk.snapshot.packets)
            originals = (
                list(original_ids)
                if original_ids is not None
                else ([] if chunk_ids else covered)
            )
            if originals:
                selected_packets.extend(
                    self._subset(attempt.snapshot, originals, packet).packets
                )
            if excluded:
                excluded_packets = self._subset(attempt.snapshot, excluded).packets
                for excluded_packet in excluded_packets:
                    for source, revision in excluded_packet["inputs"].items():
                        row = db.execute(
                            "SELECT body FROM derivatives WHERE source=? AND revision=?",
                            (source, revision),
                        ).fetchone()
                        cached = json.loads(row[0]) if row else {}
                        for field, proof in (
                            ("image_context", "image_inputs"),
                            ("transcript", "transcript_inputs"),
                        ):
                            if cached.get(field):
                                from ..application.media import transcript_fingerprint

                                excluded_packet.setdefault(proof, {})[source] = (
                                    transcript_fingerprint(cached[field])
                                    if field == "transcript"
                                    else cached[field]
                                )
                selected_packets.extend(excluded_packets)
            if chunk_coverage | set(originals) | set(excluded) != set(covered):
                raise Refused(
                    "Final coverage differs from its validated chunks and originals"
                )
            snapshot = self._subset(attempt.snapshot, covered).model_copy(
                update={"packets": selected_packets}
            )
            if not self._current(db, snapshot):
                raise Refused("Final briefing dependencies changed")
            missing = _omissions(omissions) if omissions else attempt.snapshot.omissions
            dates = [
                db.execute(
                    "SELECT date FROM messages WHERE key=?",
                    (f"{snapshot.key}/{source}",),
                ).fetchone()[0]
                for source in covered
            ]
            value = BriefingResult(
                id=uuid.uuid4().hex,
                attempt_id=identity,
                snapshot=snapshot,
                covered_ids=covered,
                chunk_ids=chunk_ids,
                omissions=missing,
                covered_start=min(dates),
                covered_end=max(dates),
                output=output,
                created=self.store.clock(),
                excluded_ids=excluded,
                ad_ids=ads,
                local_outcome=local_outcome,
                filter_state=filter_state,
                filter_evidence=list(filter_evidence),
            )
            db.execute(
                "INSERT INTO group_briefing_results VALUES (?,?,?,1,?)",
                (value.id, snapshot.key, snapshot.day, value.model_dump_json()),
            )
            self._dependencies(db, "result", value.id, snapshot)
            self._write_attempt(
                db,
                attempt.model_copy(
                    update=dict(
                        state="completed", updated=self.store.clock(), error=None
                    )
                ),
            )
            self._prefixes.pop(identity, None)
            return value

    def fail(self, identity, error, *, interrupted=False, db=None):
        with nullcontext(db) if db is not None else self.store.transaction() as db:
            attempt = self._attempt(db, identity)
            if attempt is None:
                raise Refused("Briefing attempt unavailable")
            if attempt.state == "active":
                attempt = attempt.model_copy(
                    update=dict(
                        state="interrupted" if interrupted else "failed",
                        error=str(error)[:512],
                        updated=self.store.clock(),
                    )
                )
                self._write_attempt(db, attempt)
            self._prefixes.pop(identity, None)
            return attempt

    def publish_prefix(self, identity, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 65536:
            return False
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            return False
        try:
            text.encode("utf-8")
        except UnicodeError:
            return False
        with self.store.transaction() as db:
            attempt = self._attempt(db, identity)
            if (
                attempt is None
                or attempt.state != "active"
                or not self._current(db, attempt.snapshot)
            ):
                return False
            if self._prefixes.get(identity) != text:
                self._prefixes[identity] = text
                # Prefix text stays ephemeral. The ordinary chat change cursor
                # wakes trusted browsers without persisting provisional wording.
                db.execute(
                    "INSERT INTO changes VALUES (?,(SELECT coalesce(max(seq),0)+1 FROM changes)) ON CONFLICT(scope) DO UPDATE SET seq=excluded.seq",
                    (attempt.snapshot.key,),
                )
            return True

    def prefix(self, identity):
        attempt = self.attempt(identity)
        if attempt is None or attempt.state != "active" or not self.current(attempt):
            self._prefixes.pop(identity, None)
            return None
        return self._prefixes.get(identity)

    def _reconcile(self, db, key=None):
        args = (key,) if key is not None else ()
        where = " WHERE chat_key=?" if key is not None else ""
        for kind, table in (("chunk", "chunks"), ("result", "results")):
            for row in db.execute(
                f"SELECT id FROM group_briefing_{table}{where}"
                + (" AND" if where else " WHERE")
                + " valid=1",
                args,
            ).fetchall():
                value = self._artifact(db, kind, row[0])
                if not self._current(db, value.snapshot) or (
                    kind == "result" and not self._result_chunks_current(db, value)
                ):
                    db.execute(
                        f"UPDATE group_briefing_{table} SET valid=0 WHERE id=?",
                        (value.id,),
                    )
        for row in db.execute(
            "SELECT id FROM group_briefing_attempts"
            + where
            + (" AND" if where else " WHERE")
            + " state IN ('active','failed','interrupted')",
            args,
        ).fetchall():
            attempt = self._attempt(db, row[0])
            if not self._current(db, attempt.snapshot):
                self._write_attempt(
                    db,
                    attempt.model_copy(
                        update=dict(
                            state="superseded",
                            error="Briefing dependencies changed",
                            updated=self.store.clock(),
                        )
                    ),
                )
                self._prefixes.pop(attempt.id, None)

    def reconcile(self, key=None):
        with self.store.transaction() as db:
            if key is not None:
                self.store.groups._chat(db, key)
            self._reconcile(db, key)

    def _cached_current(self, db, snapshot):
        from ..application.context import current

        try:
            state = self.store.groups.get(snapshot.key, db=db)
            if not state.supported:
                return False
            for packet in snapshot.packets:
                self._packet_sources(packet, snapshot.key)
                if not current(self.store, packet, db=db):
                    return False
                for source in packet["inputs"]:
                    row = db.execute(
                        "SELECT body FROM messages WHERE key=?", (source,)
                    ).fetchone()
                    if not row:
                        return False
                    message = Message.model_validate_json(row[0])
                    if (
                        message.deleted
                        or message.service
                        or message.date < self.store.clock() - 30 * 86400
                        or packet["speaker_inputs"].get(source)
                        != [message.author_peer, message.author_signature]
                    ):
                        return False
                    if db.execute(
                        "SELECT 1 FROM cursors WHERE key=?",
                        ("group-effects:" + source,),
                    ).fetchone():
                        return False
            return bool(snapshot.packets)
        except (Refused, KeyError, TypeError, ValueError):
            return False

    def _cached_final(self, db, key, day):
        for row in db.execute(
            "SELECT id FROM group_briefing_results WHERE chat_key=? AND day=? ORDER BY rowid DESC",
            (key, day),
        ):
            value = self._artifact(db, "result", row[0])
            if self._cached_current(db, value.snapshot):
                return value
        return None

    def cached_final(self, key, day):
        """Retained wording for reading, without admitting reuse or native work."""
        with self.store.transaction() as db:
            self._scope(db, key, day)
            return self._cached_final(db, key, day)

    def state(self, key, day, *, cached_access=False):
        from ..application.group_context import day_bounds, eligible

        with self.store.transaction() as db:
            scope = self._scope(db, key, day)
            self._reconcile(db, key)
            start, end = day_bounds(self.store, day)
            sources = [
                message.message_id
                for row in db.execute(
                    "SELECT body FROM messages WHERE chat_key=? AND date>=? AND date<? AND deleted=0 ORDER BY message_id",
                    (key, max(start, self.store.clock() - 30 * 86400), end),
                )
                if eligible(message := Message.model_validate_json(row[0]))
            ]
            row = db.execute(
                "SELECT id FROM group_briefing_results WHERE chat_key=? AND day=? AND valid=1 ORDER BY rowid DESC LIMIT 1",
                (key, day),
            ).fetchone()
            final = self._artifact(db, "result", row[0]) if row else None
            cached = False
            if (
                final is None
                and cached_access
                and self.store.groups.get(key, db=db).access != "available"
            ):
                final = self._cached_final(db, key, day)
                cached = final is not None
            row = db.execute(
                "SELECT id FROM group_briefing_attempts WHERE chat_key=? AND day=? ORDER BY rowid DESC LIMIT 1",
                (key, day),
            ).fetchone()
            attempt = self._attempt(db, row[0]) if row else None
            covered = final.covered_ids if final else []
            uncovered = [source for source in sources if source not in set(covered)]
            omitted = (
                final.omissions
                if final
                else (attempt.snapshot.omissions if attempt else [])
            )
            intake = self.store.groups.intake(key, db=db)
            coverage = _coverage(intake, start)
            frozen = (
                final.snapshot if final else (attempt.snapshot if attempt else None)
            )
            gap = intake.gap or bool(frozen and frozen.gap)
            coverage_partial = coverage.partial or bool(
                frozen and frozen.intake_coverage.partial
            )
            return BriefingDay(
                **scope.model_dump(),
                start=start,
                end=end,
                source_ids=sources,
                covered_ids=covered,
                uncovered_ids=uncovered,
                omissions=omitted,
                gap=gap,
                intake_coverage=coverage,
                partial=bool(gap or omitted or uncovered or coverage_partial),
                cached=cached,
                final=final,
                attempt=attempt,
            )

    def recover(self, db):
        self._prefixes.clear()
        for row in db.execute(
            "SELECT id FROM group_briefing_attempts WHERE state='active'"
        ).fetchall():
            attempt = self._attempt(db, row[0])
            self._write_attempt(
                db,
                attempt.model_copy(
                    update=dict(
                        state="interrupted",
                        error="Briefing interrupted; use Retry",
                        updated=self.store.clock(),
                    )
                ),
            )
        for row in db.execute("SELECT id FROM group_briefing_batches").fetchall():
            batch = self._batch(db, row[0])
            if batch.state == "active":
                self._write_batch(
                    db,
                    batch.model_copy(
                        update=dict(state="interrupted", finished=self.store.clock())
                    ),
                )
        for row in db.execute("SELECT chat_key FROM group_briefing_runs").fetchall():
            run = self._run(db, row[0])
            if run.active_batch is not None:
                self._write_run(db, run.model_copy(update={"active_batch": None}))
        self._reconcile(db)

    def expire(self, db, cutoff):
        self._reconcile(db)
        for kind, table in (
            ("attempt", "attempts"),
            ("chunk", "chunks"),
            ("result", "results"),
        ):
            rows = db.execute(
                "SELECT DISTINCT owner_id FROM group_briefing_dependencies WHERE kind=? AND source NOT IN (SELECT key FROM messages WHERE date>=?)",
                (kind, cutoff),
            ).fetchall()
            for row in rows:
                db.execute(f"DELETE FROM group_briefing_{table} WHERE id=?", (row[0],))
                if kind == "attempt":
                    self._prefixes.pop(row[0], None)
        db.execute(
            "DELETE FROM group_briefing_days WHERE json_extract(body,'$.end')<=?",
            (cutoff,),
        )
