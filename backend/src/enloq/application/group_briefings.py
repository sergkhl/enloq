"""One guarded group stage per scheduler claim; native history is never fetched."""

import asyncio
import copy
import json
import threading
import uuid
from contextlib import contextmanager

from ..domain import citation_check
from ..domain.briefing_contract import GROUP_ROLES, BriefingOutput
from ..domain.citation_check import CHECK_ROLE, ClaimCheck
from ..domain.group_briefings import BriefingChunk, BriefingResult, FrozenBriefing
from ..domain.group_filtering import FILTER_ROLES
from ..domain.models import (
    Message,
    ProcessingPaused,
    Refused,
    Unavailable,
    reply_target,
)
from ..inference.base import ProviderRefused
from ..inference.guidance import briefing_fingerprint
from ..inference.guidance import snapshot as guidance_snapshot
from ..inference.work import (
    ROUTES,
    BudgetReached,
    GroupGuidanceChanged,
    accounting,
    budget_day,
    counted,
    daily_budget,
    require_budget,
)
from ..storage.events import commits
from ..storage.group_briefings import digest
from ..storage.store import RETENTION, WINDOW
from . import group_context as context
from .group_filtering import GroupFiltering
from .group_planner import Planner
from .group_rollout import GroupBriefingRollout

GROUP_ROLES = GROUP_ROLES | FILTER_ROLES | {CHECK_ROLE}

# Seconds a planning answer may stand with no local commit and no hold ending.
# Sources leaving the automatic window or retention, and writers outside this
# process, are noticed within it.
RECHECK = 15


def checked_view(store, message):
    """What the briefing read of one original, without internal revision and
    peer metadata."""
    value = context._prepared(store, message)
    value.pop("revision", None)
    value["speaker"] = {
        k: v for k, v in value["speaker"].items() if k != "peer" and v is not None
    }
    return value


def clean(packet):
    """Image bytes stay in their private media cache, outside durable job payloads."""
    value = copy.deepcopy(packet)
    value.pop("images", None)
    value.pop("guidance", None)
    return value


def subset(packet, ids):
    value = clean(packet)
    selected = set(ids)
    value["messages"] = [m for m in value["messages"] if m["id"] in selected]
    value["covered_ids"] = [m["id"] for m in value["messages"]]
    retained = selected | {m["id"] for m in value.get("context", [])}
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
                if int(key.rsplit("/", 1)[1]) in retained
            }
    return value


class GroupBriefings:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.storage = self.store.group_briefings
        self.work = app.rewriting.work
        self.planner = Planner(app)
        self.filtering = GroupFiltering(app)
        app.group_filtering = self.filtering
        self._served = {}
        self._speech_started = set()
        self._settled = {}
        self._held, self._hold_lock = {}, threading.Lock()
        # ADR 34's citation check; the generative briefing check script, whose
        # closed role set has no decision calls, publishes unchecked instead.
        self.check_citations = True

    @contextmanager
    def _holding(self, batch):
        """Keep reconciliation from interrupting a batch this process has in hand.

        Reconciliation treats an active batch with no queued, claimed or running
        job as abandoned. A batch is briefly in that state while it starts and
        between one finished stage and the next queued one, and reading requests
        and deliberate clicks reconcile from other threads than the scheduler.
        """
        if batch is None:
            yield
            return
        with self._hold_lock:
            self._held[batch] = self._held.get(batch, 0) + 1
        try:
            yield
        finally:
            with self._hold_lock:
                if self._held[batch] == 1:
                    del self._held[batch]
                else:
                    self._held[batch] -= 1

    def changed(self, message, *, arrival=True):
        self.planner.changed(message, arrival=arrival)
        self.reconcile(f"{message.account_id}/{message.peer}")

    def _retry_view(self, attempt):
        """Read Retry admission without changing frozen intent, jobs or clocks."""
        if attempt is None or attempt.state not in {"failed", "interrupted"}:
            return None
        reason = None
        try:
            self.app.admit_ai()
            if not self.app.authorized():
                raise Refused("Account authorization changed")
            self.planner.admit(
                attempt.snapshot.key,
                attempt.snapshot.day,
                attempt.snapshot.intended_ids,
                explicit=True,
            )
            if not self.storage.current(attempt.snapshot, admission=True):
                raise Refused("Briefing originals changed or expired")
            if self.store.rows(
                "SELECT 1 FROM group_briefing_attempts WHERE chat_key=? AND state='active' LIMIT 1",
                (attempt.snapshot.key,),
            ):
                raise Refused("A briefing attempt is already active for this group")
            run = self.storage.run_state(attempt.snapshot.key)
            batch = self.storage.batch(run.active_batch) if run.active_batch else None
            roles = sorted(GROUP_ROLES)
            if (
                batch
                and batch.state == "active"
                and self.store.rows(
                    f"SELECT 1 FROM jobs WHERE role IN ({','.join('?' * len(roles))}) AND state IN ('queued','claimed','running') AND json_extract(payload,'$.batch')=? LIMIT 1",
                    (*roles, batch.id),
                )
            ):
                raise Refused("A briefing update is already active for this group")
            completed = set()
            for row in self.store.rows(
                "SELECT body FROM group_briefing_results WHERE chat_key=? AND day=? AND valid=1",
                (attempt.snapshot.key, attempt.snapshot.day),
            ):
                final = BriefingResult.model_validate_json(row["body"])
                if self.storage.current(final):
                    completed.update(final.covered_ids)
            if set(attempt.snapshot.intended_ids) <= completed:
                raise Refused("Completed briefing sources are unchanged")
            self.work.check_budget("group_briefing")
        except Refused as error:
            reason = str(error)
        return dict(attempt=attempt.id, enabled=reason is None, reason=reason)

    def _analyzing(self, attempt, prefix):
        if (
            attempt is None
            or attempt.state != "active"
            or not self._current({"attempt": attempt.id})
        ):
            return False
        if prefix:
            return True
        roles = sorted(GROUP_ROLES)
        for job in self.store.rows(
            f"SELECT state,due,payload FROM jobs WHERE role IN ({','.join('?' * len(roles))}) AND state IN ('queued','claimed','running')",
            roles,
        ):
            payload = json.loads(job["payload"])
            if (
                payload.get("attempt") != attempt.id
                or payload.get("stage") == "start_day"
            ):
                continue
            # A dispatched model call or actual local Whisper worker may finish
            # after a setting changes. Merely queued work still needs admission.
            if job["state"] == "running" or attempt.id in self._speech_started:
                return True
            if job["due"] > self.store.clock() or not self.work.capacity.admits(
                "group"
            ):
                continue
            try:
                self._admit(payload, budget=payload.get("stage") != "check")
            except Refused:
                continue
            return True
        return False

    def view(self, key):
        """Dated cached presentation; reading starts no preparation or generation."""
        state = self.store.groups.get(key)
        cutoff = self.store.clock() - RETENTION
        days = {
            context.calendar_day(self.store, message)
            for row in self.store.rows(
                "SELECT body FROM messages WHERE chat_key=? AND date>=? AND deleted=0 ORDER BY message_id",
                (key, cutoff),
            )
            if context.eligible(message := Message.model_validate_json(row["body"]))
        }
        days.update(
            row["day"]
            for row in self.store.rows(
                "SELECT day FROM group_briefing_days WHERE chat_key=?", (key,)
            )
            if context.day_bounds(self.store, row["day"])[1] > cutoff
        )
        result = []
        for day in sorted(days):
            value = self.storage.state(
                key, day, cached_access=state.access != "available"
            )
            final, attempt = value.final, value.attempt
            if not value.source_ids and final is None:
                continue
            prefix = (
                self.storage.prefix(attempt.id)
                if attempt and self.app.authorized() and state.access == "available"
                else None
            )
            result.append(
                dict(
                    day=day,
                    start=value.start,
                    end=value.end,
                    source_ids=value.source_ids,
                    covered_ids=value.covered_ids,
                    uncovered_ids=value.uncovered_ids,
                    partial=value.partial,
                    gap=value.gap,
                    intake_coverage=value.intake_coverage.model_dump(),
                    omissions=[item.model_dump() for item in value.omissions],
                    cached=value.cached,
                    final=dict(
                        id=final.id,
                        text=final.text,
                        outcome=final.output.outcome,
                        topics=self._topics(key, final),
                        excluded_ids=final.excluded_ids,
                        ad_ids=final.ad_ids,
                        local_outcome=final.local_outcome,
                        covered_ids=final.covered_ids,
                        covered_start=final.covered_start,
                        covered_end=final.covered_end,
                    )
                    if final
                    else None,
                    attempt=dict(
                        id=attempt.id, state=attempt.state, error=attempt.error
                    )
                    if attempt
                    else None,
                    stream=dict(attempt=attempt.id, text=prefix) if prefix else None,
                    analyzing=self._analyzing(attempt, prefix),
                    retry=self._retry_view(attempt),
                )
            )
        return result

    def _topics(self, key, final):
        """Topics as shown: citations the check found unsupported are gone,
        with their participants (ADR 34 citation check Q3), and a topic left
        with none is hidden (Q4)."""
        checks = final.citation_check.topics if final.citation_check else []

        def author_of(identity):
            message = self.store.message(key, identity)
            return message.author_peer if message and not message.deleted else None

        result = []
        for index, topic in enumerate(final.output.topics):
            removed = checks[index].removed if index < len(checks) else set()
            citations, participant_ids = citation_check.kept(
                topic.citations, topic.participant_source_ids, removed, author_of
            )
            if not citations:
                continue
            result.append(
                {
                    **topic.model_dump(),
                    "citations": citations,
                    "participant_source_ids": participant_ids,
                    "participants": self._participants(key, participant_ids),
                }
            )
        return result

    def _participants(self, key, ids):
        participants = {}
        for identity in sorted(ids):
            message = self.store.message(key, identity)
            if message is None or message.deleted:
                continue
            peer = message.author_peer or f"unknown:{identity}"
            if peer not in participants:
                participants[peer] = dict(
                    peer=peer,
                    name=(message.author or "Unknown")
                    if message.author_peer
                    else "Unknown",
                )
        return list(participants.values())

    def annotate(self, messages, *, quotes=False):
        # Deleted originals show their captured content or are left out
        # (ADR 52); filtering still reads the message as stored.
        messages = [
            m if isinstance(m, Message) else Message.model_validate(m) for m in messages
        ]
        values = self.store.history.annotate(messages, quotes=quotes)
        stored = {message.message_id: message for message in messages}
        flags = self.filtering.annotate([stored[v["message_id"]] for v in values])
        for value, flag in zip(values, flags, strict=True):
            value["group_filter"] = flag
        return values

    def _source_final(self, key, day, identity):
        context.day_bounds(self.store, day)
        state = self.store.groups.get(key)
        rows = self.store.rows(
            "SELECT body FROM group_briefing_results WHERE id=? AND chat_key=? AND day=?",
            (identity, key, day),
        )
        final = BriefingResult.model_validate_json(rows[0]["body"]) if rows else None
        if final and self.storage.current(final):
            return final
        cached = (
            self.storage.cached_final(key, day) if state.access != "available" else None
        )
        if (
            cached
            and cached.id == identity
            and self.store.groups.get(key).access != "available"
        ):
            return cached
        raise Refused(
            "Briefing sources changed or expired; return to the current briefing"
        )

    def sources(self, key, day, identity, source):
        """Reveal one validated citation in at most 100 cached same-day originals."""
        if type(source) is not int or not 0 < source < 2**63:
            raise Refused("An exact cited original is required")
        final = self._source_final(key, day, identity)
        cited = {
            identity
            for topic in self._topics(key, final)
            for identity in topic["citations"]
        }
        if source not in cited:
            raise Refused("This original is not a validated briefing citation")
        return self._original_window(key, day, final, source)

    def originals(self, key, day, identity, *, around=None):
        """Open cached day originals even when a final has no topic citations."""
        final = self._source_final(key, day, identity)
        source = final.covered_ids[0] if around is None else around
        if type(source) is not int or not 0 < source < 2**63:
            raise Refused("An exact cached original is required")
        return self._original_window(key, day, final, source)

    def _original_window(self, key, day, final, source):
        start, end = context.day_bounds(self.store, day)
        start = max(start, self.store.clock() - RETENTION)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND message_id=? AND date>=? AND date<? AND deleted=0",
                (key, source, start, end),
            ).fetchone()
            if row is None:
                raise Refused("Requested original is missing or expired")
            target = Message.model_validate_json(row[0])
            revisions = {
                original: revision
                for packet in final.snapshot.packets
                for original, revision in packet["inputs"].items()
            }
            if (
                source in final.covered_ids
                and revisions.get(target.key) != target.revision
            ):
                raise Refused(
                    "Requested original changed; return to the current briefing"
                )
            previous = db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND message_id<? AND date>=? AND date<? AND deleted=0 ORDER BY message_id DESC LIMIT 26",
                (key, source, start, end),
            ).fetchall()
            following = db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND message_id>=? AND date>=? AND date<? AND deleted=0 ORDER BY message_id LIMIT 76",
                (key, source, start, end),
            ).fetchall()
        messages = [
            Message.model_validate_json(row[0])
            for row in [*reversed(previous[:25]), *following[:75]]
        ]
        result = dict(
            day=day,
            result=final.id,
            source=source,
            messages=self.annotate(messages),
            before=len(previous) > 25,
            after=len(following) > 75,
        )
        # The cache window may race an edit/access change; only the finalized
        # source proof can authorize this navigation, never a streamed prefix.
        self._source_final(key, day, final.id)
        return result

    def retry_for(self, key, identity):
        previous = self.storage.attempt(identity)
        if previous is None or previous.snapshot.key != key:
            raise Refused("Briefing attempt is unavailable for this group")
        permission = self._retry_view(previous)
        if permission is None:
            raise Refused("Only a failed or interrupted briefing can be retried")
        if not permission["enabled"]:
            raise Refused(permission["reason"])
        job = self.retry(identity)
        return json.loads(job["payload"])["attempt"]

    def _guide(self, role="group_briefing", *, db=None):
        if db is None:
            guidance = self.work.guidance()
        else:
            metadata = "shared.metadata" if self.store.shared_path else "metadata"
            values = dict(
                db.execute(
                    f"SELECT key,value FROM {metadata} WHERE key IN ('guidance','guidance_version')"
                ).fetchall()
            )
            guidance = guidance_snapshot(
                json.loads(values.get("guidance", "{}")),
                int(values.get("guidance_version", 0)),
            )
        return briefing_fingerprint(
            guidance,
            role=role,
            route=ROUTES[role],
            hide_bots=self.filtering.hides_bots(db),
        )

    def _budget(self, db):
        metadata, ledger, member = accounting(db, self.store)
        bucket = "day:" + budget_day(db, self.store, metadata)
        require_budget(
            counted(db, ledger, bucket, family="group_briefing", member=member),
            daily_budget(db, metadata, family="group_briefing", member=member),
            "group_briefing",
        )

    def _admit(self, payload, *, db=None, budget=True):
        GroupBriefingRollout.admit(self, payload, db=db)
        attempt = self.storage.attempt(payload["attempt"], db=db)
        if not attempt or not self.storage.current(attempt, admission=True, db=db):
            raise Refused("Briefing native dependencies changed")
        if payload.get("filter_state") is not None and payload[
            "filter_state"
        ] != self.filtering.state_hash(attempt.snapshot.key, db=db):
            raise Refused("Briefing filtering decisions changed")
        if not self._filter_evidence_current(payload, db=db):
            raise Refused("Briefing excluded evidence changed")
        self.planner.admit(
            attempt.snapshot.key,
            attempt.snapshot.day,
            attempt.snapshot.intended_ids,
            explicit=not attempt.automatic,
            continuation=True,
            db=db,
        )
        if db is None:
            self.app.admit_ai()
            if not self.app.authorized():
                raise Refused("Account authorization changed")
            if budget:
                self.work.check_budget("group_briefing")
        elif budget:
            self._budget(db)
        batch = self.storage.batch(attempt.batch_id, db=db)
        if not batch or batch.state != "active":
            raise Refused("Briefing update is no longer active")
        return attempt

    def _current(self, payload):
        attempt = self.storage.attempt(payload["attempt"])
        return bool(
            attempt
            and self.app.authorized()
            and self.storage.current(attempt)
            and self._filter_evidence_current(payload)
            and (
                payload.get("filter_state") is None
                or payload["filter_state"]
                == self.filtering.state_hash(attempt.snapshot.key)
            )
        )

    def _filter_evidence_current(self, payload, *, db=None):
        return all(
            self.store.group_filters.evidence_current(proof, db=db, standing=True)
            for proof in payload.get("filter_evidence", [])
        )

    def _chunks(self, identities):
        result = []
        for identity in identities:
            rows = self.store.rows(
                "SELECT body FROM group_briefing_chunks WHERE id=? AND valid=1",
                (identity,),
            )
            value = BriefingChunk.model_validate_json(rows[0]["body"]) if rows else None
            if not value or not self.storage.current(value):
                raise Refused("Reusable briefing chunk changed")
            result.append(value)
        return result

    def _reusable(self, selection, guide):
        """Prior validated final/chunks are evidence; no fresh source work is charged."""
        state = self.storage.state(selection.key, selection.day)
        allowed = set(selection.intended_ids)
        reused, covered = [], set()
        if (
            state.final
            and state.final.snapshot.guidance_hash == guide
            and state.final.snapshot.route == ROUTES["group_briefing"]
            and set(state.final.covered_ids) <= allowed
            and self.planner.media_current(
                state.final.snapshot, excluded_ids=state.final.excluded_ids
            )
            and not state.final.excluded_ids
            and not any(
                self._has_exclusions(packet) for packet in state.final.snapshot.packets
            )
        ):
            reused.append(dict(kind="final", value=state.final.model_dump()))
            covered.update(state.final.covered_ids)
        for row in self.store.rows(
            "SELECT body FROM group_briefing_chunks WHERE chat_key=? AND day=? AND valid=1 ORDER BY rowid",
            (selection.key, selection.day),
        ):
            chunk = BriefingChunk.model_validate_json(row["body"])
            ids = set(chunk.covered_ids)
            if (
                ids <= allowed
                and not ids & covered
                and chunk.snapshot.guidance_hash == guide
                and chunk.snapshot.route == ROUTES["group_briefing"]
                and self.storage.current(chunk)
                and self.planner.media_current(chunk.snapshot)
                and (
                    chunk.filter_state is None
                    or chunk.filter_state == self.filtering.state_hash(selection.key)
                )
                and not any(
                    self._has_exclusions(packet) for packet in chunk.snapshot.packets
                )
            ):
                reused.append(dict(kind="chunk", value=chunk.model_dump()))
                covered.update(ids)
        return reused, covered

    def _has_exclusions(self, packet):
        filtered = self.filtering.exclude(packet)
        return bool(
            filtered.get("exclusion_ids") or filtered.get("excluded_context_ids")
        )

    def _freeze_days(self, selections):
        guide, capacity, days = self._guide(), 200, []
        for selection in selections:
            # A new intake epoch is still reading this day again: wait for it.
            if self.planner.reverifying(selection.key, selection.day):
                continue
            reused, covered = self._reusable(selection, guide)
            new = set(selection.new_ids) - covered
            selected_new = set(sorted(new)[:capacity])
            capacity -= len(selected_new)
            raw = [
                m
                for m in selection.sources
                if m.message_id not in covered
                and (m.message_id not in new or m.message_id in selected_new)
            ]
            omitted = [
                dict(message_id=i, reason="source work limit")
                for i in sorted(new - selected_new)
            ]
            packets = []
            # Each partition keeps its per-request 200 ceiling. Reused earlier
            # native targets may exceed the update's 200 *fresh* source allowance.
            for start in range(0, len(raw), 200):
                plan = context.partition(raw[start : start + 200], store=self.store)
                omitted.extend(plan.omitted)
                packets.extend(
                    context.packet(self.store, selection.key, selection.day, part)
                    for part in plan.chunks
                )
            dependencies = [
                p for item in reused for p in item["value"]["snapshot"]["packets"]
            ] + packets
            if not dependencies:
                continue
            intended = [i for p in dependencies for i in p["covered_ids"]]
            try:
                snapshot = self.storage.freeze(
                    selection.key,
                    selection.day,
                    dependencies,
                    intended_ids=intended,
                    omissions=omitted,
                    guidance_hash=guide,
                    route=ROUTES["group_briefing"],
                )
            except Refused:
                # An original changed between selection and freezing. The
                # scheduler asks again; a refusal here must not stop its tick.
                continue
            snapshot = snapshot.model_copy(
                update={
                    "bot_samples": {
                        author: [
                            message.key
                            for message in self.filtering.latest_ten(
                                selection.key, author
                            )
                        ]
                        for author in sorted(
                            {
                                message.author_peer
                                for message in selection.sources
                                if message.author_peer
                                and message.author_peer.startswith("user:")
                            }
                        )
                    }
                }
            )
            if not self.storage.can_create(snapshot):
                capacity += len(selected_new)
                continue
            days.append(
                dict(
                    snapshot=snapshot.model_dump(),
                    packets=packets,
                    reused=reused,
                    fresh_ids=sorted(selected_new),
                    omissions=omitted,
                )
            )
        return days

    def _enqueue(self, payload, *, due=None):
        payload = copy.deepcopy(payload)
        stage = payload["stage"]
        role = (
            payload["filter_requests"][0]["role"]
            if stage == "filter" and payload.get("filter_requests")
            else "group_briefing_image"
            if stage == "image"
            else "group_briefing_chunk"
            if stage in {"prepare", "chunk"}
            else CHECK_ROLE
            if stage == "check"
            else "group_briefing"
        )
        identity = uuid.uuid4().hex
        revision = digest(
            dict(
                stage=stage,
                attempt=payload.get("attempt"),
                packet=payload.get("packet"),
                chunks=payload.get("chunks"),
                days=payload.get("days"),
            )
        )
        payload["class"] = "group"
        job = dict(
            id=identity,
            scope=payload["filter_requests"][0]["key"]
            if stage == "filter"
            else payload.get("attempt") or payload["batch"],
            role=role,
            input_revision=revision,
            state="queued",
            due=self.store.clock() if due is None else due,
            payload=json.dumps(payload),
            error=None,
        )
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
                tuple(
                    job[k]
                    for k in (
                        "id",
                        "scope",
                        "role",
                        "input_revision",
                        "state",
                        "due",
                        "payload",
                        "error",
                    )
                ),
            )
        return job

    def _start_day(self, day, batch, *, automatic, remaining=(), retry=None):
        snapshot = FrozenBriefing.model_validate(day["snapshot"])
        # Planning the classifier requests reads every frozen source, so a new
        # batch stays without a queued stage for seconds on a large day.
        held = batch.id if batch else retry.batch_id if retry else uuid.uuid4().hex
        with self._holding(held):
            attempt = retry or self.storage.create_attempt(
                snapshot, automatic=automatic, batch_id=held
            )
            try:
                return self._first_stage(day, attempt, snapshot, remaining)
            except BaseException:
                # Nothing was dispatched. Leave a Retry, not an attempt that
                # stays active with no stage to run.
                try:
                    self.storage.fail(
                        attempt.id, "Briefing interrupted; use Retry", interrupted=True
                    )
                    self.storage.finish_batch(attempt.batch_id, state="interrupted")
                except Exception:
                    # Reconciliation ends an attempt left without a stage.
                    pass
                raise

    def _first_stage(self, day, attempt, snapshot, remaining):
        batch = self.storage.batch(attempt.batch_id)
        chunks = []
        for reuse in day.get("reused", []):
            value = reuse["value"]
            if reuse["kind"] == "chunk":
                chunks.append(value["id"])
            else:
                chunks.append(
                    self.storage.store_chunk(
                        attempt.id, value["covered_ids"], value["output"]
                    ).id
                )
        payload = dict(
            attempt=attempt.id,
            batch=batch.id,
            stage="prepare" if day["packets"] else "final",
            packets=day["packets"],
            chunks=chunks,
            fresh_ids=day["fresh_ids"],
            omissions=day.get("omissions", []),
            days=list(remaining),
            refusals=0,
            exclusion_ids=[],
            ad_ids=[],
            filter_evidence=[],
        )
        sources = [
            self.store.message(snapshot.key, identity)
            for identity in snapshot.intended_ids
        ]
        payload["filter_requests"] = self.filtering.plan(
            snapshot.key,
            sources,
            snapshot.day,
            max_requests=12 - batch.classifier_requests,
            bot_samples=snapshot.bot_samples,
        )
        if payload["packets"]:
            payload["packet"] = payload["packets"].pop(0)
        if hasattr(self.app.transport, "refresh_group_authors"):
            payload["stage"] = "native"
        elif payload["filter_requests"]:
            payload["stage"] = "filter"
        else:
            self._filtered(payload)
        return self._enqueue(payload)

    def _mark(self):
        """What a planning answer is read against: local commits, the account's
        in-memory connection state, which no commit announces, and the clock."""
        try:
            getattr(self.app, "group_runtime_admit", lambda: None)()
            admitted = True
        except Refused:
            admitted = False
        return commits.sequence, admitted, self.store.clock()

    def _standing(self, question):
        """An earlier planning answer, while nothing it was read from changed.

        The scheduler asks several times a second, and each answer proves every
        cached briefing source again. Re-deriving it on every tick kept the
        event loop busy for as long as a group waited for its next update.
        """
        held = self._settled.get(question)
        if held:
            sequence, admitted, now = self._mark()
            if held[0] == (sequence, admitted) and held[1] <= now < held[2]:
                return held
        return None

    def _stand(self, question, mark, answer):
        sequence, admitted, started = mark
        if sequence != commits.sequence:
            # A commit during the derivation, its own included, may be newer
            # than what the answer read. The next tick derives it again.
            self._settled.pop(question, None)
            return answer
        roles = sorted(GROUP_ROLES)
        wakes = [
            *self.planner.holds(),
            *(
                row["due"]
                for row in self.store.rows(
                    f"SELECT due FROM jobs WHERE role IN ({','.join('?' * len(roles))}) AND state='queued'",
                    roles,
                )
            ),
            # A reached budget frees its work at the next accounting day.
            context.day_bounds(self.store, self.planner._day(started))[1],
        ]
        # Measured from before the derivation: a wake that passed while it ran
        # leaves nothing standing, instead of being mistaken for a past one.
        until = min([started + RECHECK, *(wake for wake in wakes if wake > started)])
        self._settled[question] = ((sequence, admitted), started, until, answer)
        return answer

    def next_job(self, *, low_allowed=True):
        if not low_allowed or self._standing("next"):
            return None
        mark = self._mark()
        job = self._next_job()
        # Only "nothing to run" stands: a candidate is claimed or re-derived.
        return job or self._stand("next", mark, None)

    def _next_job(self):
        self.reconcile()
        roles = sorted(GROUP_ROLES)
        candidates = []
        for job in self.store.rows(
            f"SELECT * FROM jobs WHERE role IN ({','.join('?' * len(roles))}) AND state='queued' AND due<=? ORDER BY due,rowid",
            (*roles, self.store.clock()),
        ):
            payload = json.loads(job["payload"])
            try:
                if payload["stage"] == "start_day":
                    day = payload["days"][0]
                    snapshot = FrozenBriefing.model_validate(day["snapshot"])
                    self.planner.admit(
                        snapshot.key,
                        snapshot.day,
                        snapshot.intended_ids,
                        continuation=True,
                    )
                    self.work.check_budget("group_briefing")
                else:
                    # ADR 34 citation check Q5: a spent budget leaves a
                    # finished final unchecked rather than waiting.
                    self._admit(payload, budget=payload["stage"] != "check")
            except Refused:
                continue
            key = (
                snapshot.key
                if payload["stage"] == "start_day"
                else self.storage.attempt(payload["attempt"]).snapshot.key
            )
            candidates.append((key, job))
        for selection in self.planner.pending():
            try:
                self.work.check_budget("group_briefing")
            except Refused:
                return None
            if not self._freeze_days([selection]):
                continue
            candidate = dict(
                id="prospective:"
                + digest(
                    dict(
                        key=selection.key, day=selection.day, ids=selection.intended_ids
                    )
                ),
                scope=selection.key,
                role="group_briefing",
                input_revision="prospective",
                state="queued",
                due=self.store.clock(),
                payload=json.dumps(
                    dict(
                        prospective=True,
                        key=selection.key,
                        **{"class": "group"},
                    )
                ),
                error=None,
            )
            candidates.append((selection.key, candidate))
        return (
            min(
                candidates,
                key=lambda item: (
                    self._served.get(item[0], 0),
                    item[1]["due"],
                    item[0],
                ),
            )[1]
            if candidates
            else None
        )

    def claim(self, job):
        payload = json.loads(job["payload"])
        try:
            if payload.get("prospective"):
                selections = self.planner.pending(payload["key"])
                if not selections:
                    return False
                self.app.admit_ai()
                if not self.app.authorized():
                    return False
                self.work.check_budget("group_briefing")
                days = self._freeze_days(selections)
                if not days:
                    return False
                actual = self._start_day(
                    days[0], None, automatic=True, remaining=days[1:]
                )
                job.clear()
                job.update(actual)
                payload = json.loads(job["payload"])
            if payload["stage"] == "start_day":
                snapshot = FrozenBriefing.model_validate(payload["days"][0]["snapshot"])
                self.planner.admit(
                    snapshot.key, snapshot.day, snapshot.intended_ids, continuation=True
                )
                self.work.check_budget("group_briefing")
            else:
                self._admit(payload, budget=payload["stage"] != "check")
        except Refused:
            return False
        with self.store.transaction() as db:
            claimed = bool(
                db.execute(
                    "UPDATE jobs SET state='claimed' WHERE id=? AND state='queued'",
                    (job["id"],),
                ).rowcount
            )
        if claimed:
            key = (
                snapshot.key
                if payload["stage"] == "start_day"
                else self.storage.attempt(payload["attempt"]).snapshot.key
            )
            self._served[key] = self._served.get(key, 0) + 1
        return claimed

    def _done(self, job):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='done',error=NULL WHERE id=? AND state IN ('queued','claimed')",
                (job["id"],),
            )

    def _hold(self, job, *, delay=0):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='queued',due=? WHERE id=? AND state='claimed'",
                (self.store.clock() + delay, job["id"]),
            )

    def _omit(self, payload, packets, reason):
        existing = {o["message_id"] for o in payload["omissions"]}
        for packet in packets:
            for identity in packet["covered_ids"]:
                if identity not in existing:
                    payload["omissions"].append(
                        dict(message_id=identity, reason=reason)
                    )
                    existing.add(identity)

    def _advance(self, payload, prepared=None):
        batch = self.storage.batch(payload["batch"])
        remaining = 5 - batch.requests
        if prepared is not None:
            payload["packet"] = clean(prepared)
            for item in prepared.get("unanalysed", []):
                self._omit(
                    payload,
                    [dict(covered_ids=[item["id"]])],
                    item.get("reason", "media not analysed"),
                )
            unusable = {item["id"] for item in prepared.get("unanalysed", [])}
            retained = [
                m["id"]
                for m in prepared["messages"]
                if m["id"] not in unusable
                or m["text"].strip()
                or m.get("transcript")
                or m.get("image_context")
            ]
            payload["packet"] = subset(prepared, retained)
            if prepared.get("nothing"):
                self._omit(payload, [prepared], "no analysable prepared content")
                payload.pop("packet", None)
            elif prepared.get("images") and remaining >= 2:
                payload["stage"] = "image"
                return self._enqueue(payload)
            elif prepared.get("images"):
                self._omit(payload, [prepared], "image request work limit")
                payload.pop("packet", None)
        if not payload.get("packet") and payload["packets"] and remaining >= 1:
            payload["packet"] = payload["packets"].pop(0)
            payload["stage"] = "prepare"
        elif payload.get("packet") and payload["packets"] and remaining >= 2:
            payload["stage"] = "chunk"
        elif payload.get("packet") or payload["chunks"]:
            if payload.get("packet") and payload["chunks"] and remaining >= 2:
                attempt = self.storage.attempt(payload["attempt"])
                try:
                    context.synthesis(
                        self.store,
                        attempt.snapshot.key,
                        attempt.snapshot.day,
                        packets=[payload["packet"]],
                        chunks=self._chunks(payload["chunks"]),
                    )
                except Refused:
                    if not self._current(payload):
                        raise
                    payload["stage"] = "chunk"
                    return self._enqueue(payload)
            self._omit(payload, payload["packets"], "model request work limit")
            payload["packets"] = []
            payload["stage"] = "final"
        else:
            raise Refused("No analysable briefing sources remain within work limits")
        return self._enqueue(payload)

    def _filtered(self, payload):
        """Intentional exclusions remain handled originals, outside model input."""
        packets = [payload.pop("packet")] if payload.get("packet") else []
        packets += payload.get("packets", [])
        kept_chunks = []
        for chunk in self._chunks(payload["chunks"]):
            if any(self._has_exclusions(packet) for packet in chunk.snapshot.packets):
                packets.extend(chunk.snapshot.packets)
            else:
                kept_chunks.append(chunk.id)
        payload["chunks"] = kept_chunks
        retained = []
        for original in packets:
            filtered = self.filtering.exclude(original)
            excluded = filtered.get("exclusion_ids", [])
            payload["exclusion_ids"] = sorted(
                set(payload.get("exclusion_ids", [])) | set(excluded)
            )
            payload["ad_ids"] = sorted(
                set(payload.get("ad_ids", [])) | set(filtered.get("ad_ids", []))
            )
            proofs = {
                proof["fingerprint"]: proof
                for proof in payload.get("filter_evidence", [])
            }
            proofs.update(
                (proof["fingerprint"], proof)
                for proof in filtered.get("filter_evidence", [])
            )
            payload["filter_evidence"] = list(proofs.values())
            # Publication revalidates these proofs from the durable payload.
            # They are application bookkeeping, not model context or source
            # bytes, and must not consume the unchanged request packet bound.
            for field in (
                "filter_evidence",
                "exclusion_ids",
                "ad_ids",
                "excluded_context_ids",
            ):
                filtered.pop(field, None)
            ids = [message["id"] for message in filtered["messages"]]
            if ids:
                retained.append(subset(filtered, ids))
        payload["packets"] = retained
        attempt = self.storage.attempt(payload["attempt"])
        payload["filter_state"] = self.filtering.state_hash(attempt.snapshot.key)
        if retained:
            payload["packet"] = payload["packets"].pop(0)
            payload["stage"] = "prepare"
        else:
            payload["stage"] = "final"
        return payload

    async def _filter(self, job, payload):
        self._admit(payload)
        request = payload["filter_requests"].pop(0)
        batch = self.storage.batch(payload["batch"])
        ids = [int(key.rsplit("/", 1)[1]) for key in request["source_keys"]]
        reserved = set(payload["fresh_ids"]) | {
            identity for day in payload.get("days", []) for identity in day["fresh_ids"]
        }
        if (
            self.filtering.current(request)
            and batch.classifier_requests < 12
            and len(set(batch.source_ids) | reserved | set(ids)) <= 200
        ):

            def admission(db, _):
                self._admit(payload, db=db)
                if not self.filtering.current(request, db=db):
                    raise Refused("Group filter evidence changed")

            def accounting(db, identity):
                self.storage.record_work(
                    payload["batch"],
                    classifier_requests=1,
                    source_ids=ids,
                    dispatch_token=identity["id"],
                    db=db,
                )

            await self.filtering.run(
                request, job, group_admission=admission, group_accounting=accounting
            )
        self._done(job)
        if request["role"] == "group_bot_classification":
            attempt = self.storage.attempt(payload["attempt"])
            sources = [
                self.store.message(attempt.snapshot.key, identity)
                for identity in attempt.snapshot.intended_ids
            ]
            bots = [
                item
                for item in payload["filter_requests"]
                if item["role"] == "group_bot_classification"
            ]
            payload["filter_requests"] = bots + self.filtering.plan(
                attempt.snapshot.key,
                sources,
                attempt.snapshot.day,
                include_bots=False,
                max_requests=max(
                    0,
                    12
                    - self.storage.batch(payload["batch"]).classifier_requests
                    - len(bots),
                ),
            )
        if (
            payload["filter_requests"]
            and self.storage.batch(payload["batch"]).classifier_requests < 12
        ):
            return self._enqueue(payload)
        payload["filter_requests"] = []
        self._filtered(payload)
        return self._enqueue(payload)

    async def _native(self, job, payload):
        attempt = self._admit(payload)
        try:
            async with asyncio.timeout(60):
                await self.filtering.enrich(
                    attempt.snapshot.key, attempt.snapshot.intended_ids, limit=32
                )
        except (Unavailable, TimeoutError):
            # Missing profile proof keeps authors Unknown; source/access
            # admission below still refuses a revoked group.
            pass
        self._admit(payload)
        sources = [
            self.store.message(attempt.snapshot.key, identity)
            for identity in attempt.snapshot.intended_ids
        ]
        payload["filter_requests"] = self.filtering.plan(
            attempt.snapshot.key,
            sources,
            attempt.snapshot.day,
            max_requests=12 - self.storage.batch(payload["batch"]).classifier_requests,
            bot_samples=attempt.snapshot.bot_samples,
        )
        self._done(job)
        if payload["filter_requests"]:
            payload["stage"] = "filter"
        else:
            self._filtered(payload)
        return self._enqueue(payload)

    async def _prepare(self, job, payload):
        attempt = self._admit(payload)
        packet = payload["packet"]
        fresh = sorted(set(packet["covered_ids"]) & set(payload["fresh_ids"]))
        with self.store.transaction() as db:
            self._admit(payload, db=db)
            self.storage.record_work(payload["batch"], source_ids=fresh, db=db)

        def charge(duration):
            self.storage.record_speech(
                payload["batch"],
                duration,
                source_ids=fresh,
                admission=lambda db: self._admit(payload, db=db),
            )
            self._speech_started.add(attempt.id)

        try:
            prepared = await context.prepare(
                self.app,
                packet,
                retry=not attempt.automatic,
                guard=lambda: self._admit(payload),
                valid=lambda: self._current(payload),
                on_speech_start=charge,
            )
        except Refused as error:
            if "bound" not in str(error) or not self._current(payload):
                raise
            sources = [
                self.store.message(attempt.snapshot.key, i)
                for i in packet["covered_ids"]
            ]
            plan = context.partition(sources, store=self.store)
            payload["omissions"].extend(
                o for o in plan.omitted if o not in payload["omissions"]
            )
            parts = [
                subset(packet, [m.message_id for m in chunk]) for chunk in plan.chunks
            ]
            if len(parts) == 1 and parts[0] == packet:
                raise
            payload["packets"] = parts + payload["packets"]
            payload.pop("packet", None)
            self._done(job)
            return self._advance(payload)
        finally:
            self._speech_started.discard(attempt.id)
        self._done(job)
        return self._advance(payload, prepared)

    def _save_images(self, payload, packet, output):
        output = output.model_dump() if hasattr(output, "model_dump") else output
        with self.store.transaction() as db:
            if not self.storage.current(
                self.storage.attempt(payload["attempt"], db=db), db=db
            ) or not context.current(self.store, packet, db=db):
                raise Refused("Briefing images changed during understanding")
            for image in output["image_context"]:
                source = f"{packet['chat_key']}/{image['message_id']}"
                revision = packet["inputs"][source]
                row = db.execute(
                    "SELECT body FROM derivatives WHERE source=? AND revision=?",
                    (source, revision),
                ).fetchone()
                body = json.loads(row[0]) if row else {}
                body["image_context"] = image["text"]
                db.execute(
                    "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
                    (source, revision, json.dumps(body)),
                )
        for message in packet["messages"] + packet.get("context", []):
            image = next(
                (
                    i
                    for i in output["image_context"]
                    if i["message_id"] == message["id"]
                ),
                None,
            )
            if image:
                message["image_context"] = image["text"]
                packet.setdefault("image_inputs", {})[
                    f"{packet['chat_key']}/{message['id']}"
                ] = image["text"]
        packet["images"] = []

    async def _model(self, job, payload):
        attempt = self._admit(payload)
        if self._guide() != attempt.snapshot.guidance_hash:
            raise GroupGuidanceChanged("Group briefing guidance changed")
        role_hash = self._guide(job["role"])
        chunks = self._chunks(payload["chunks"])
        packet = payload.get("packet")
        if payload["stage"] == "image":
            packet = await context.prepare(
                self.app,
                packet,
                retry=not attempt.automatic,
                guard=lambda: self._admit(payload),
                valid=lambda: self._current(payload),
                on_speech_start=lambda duration: self.storage.record_speech(
                    payload["batch"],
                    duration,
                    admission=lambda db: self._admit(payload, db=db),
                ),
            )
        if payload["stage"] == "final":
            if not packet and not chunks and payload.get("exclusion_ids"):
                excluded = payload["exclusion_ids"]
                complete = (
                    set(attempt.snapshot.intended_ids) <= set(excluded)
                    and not payload["omissions"]
                )
                self.storage.publish(
                    attempt.id,
                    {"contract_version": 2, "outcome": "no_substance", "topics": []},
                    covered_ids=excluded,
                    original_ids=[],
                    excluded_ids=excluded,
                    ad_ids=payload["ad_ids"],
                    local_outcome=(
                        "ads_skipped"
                        if set(excluded) <= set(payload["ad_ids"])
                        else "bots_skipped"
                    )
                    if complete
                    else None,
                    filter_state=payload.get("filter_state"),
                    filter_evidence=payload.get("filter_evidence", []),
                    omissions=payload["omissions"],
                )
                self._done(job)
                self._next_day(payload)
                return
            while True:
                try:
                    request = context.synthesis(
                        self.store,
                        attempt.snapshot.key,
                        attempt.snapshot.day,
                        packets=[packet] if packet else [],
                        chunks=chunks,
                        omissions=payload["omissions"],
                    )
                    break
                except Refused:
                    if not self._current(payload) or not chunks:
                        raise
                    reusable = [
                        chunk
                        for chunk in chunks
                        if not set(chunk.covered_ids) & set(payload["fresh_ids"])
                    ]
                    if reusable:
                        omitted = max(
                            reusable, key=lambda chunk: len(chunk.covered_ids)
                        )
                        chunks.remove(omitted)
                        payload["chunks"].remove(omitted.id)
                        self._omit(
                            payload,
                            [dict(covered_ids=omitted.covered_ids)],
                            "final request bound",
                        )
                        continue
                    if packet:
                        self._omit(payload, [packet], "final request bound")
                        packet = None
                        continue
                    omitted = chunks.pop()
                    payload["chunks"].remove(omitted.id)
                    self._omit(
                        payload,
                        [dict(covered_ids=omitted.covered_ids)],
                        "final request bound",
                    )
        else:
            request = packet
        request["contract_version"] = 2

        def admission(db, _):
            self._admit(payload, db=db)
            if not context.current(self.store, request, db=db):
                raise Refused("Prepared briefing dependencies changed")

        def accounting(db, identity):
            fresh = set(request["covered_ids"]) & set(payload["fresh_ids"])
            self.storage.record_work(
                payload["batch"],
                requests=1,
                source_ids=sorted(fresh),
                dispatch_token=identity["id"],
                db=db,
            )

        output = await self.work.run(
            self.app.engine,
            job,
            request,
            klass="group",
            valid=lambda: (
                self._current(payload) and context.current(self.store, request)
            ),
            on_text=(lambda text: self.storage.publish_prefix(attempt.id, text))
            if payload["stage"] == "final"
            else None,
            group_admission=admission,
            group_accounting=accounting,
            group_guidance=role_hash,
        )
        if not self._current(payload):
            raise Refused("Briefing native dependencies changed during generation")
        if payload["stage"] == "image":
            self._save_images(payload, packet, output)
            return self._advance(payload, packet)
        if payload["stage"] == "chunk":
            chunk = self.storage.store_chunk(
                attempt.id, packet["covered_ids"], output, packet=packet
            )
            payload["chunks"].append(chunk.id)
            payload.pop("packet", None)
            return self._advance(payload)
        payload["final"] = dict(
            output=output.model_dump(mode="json"),
            covered_ids=sorted(
                set(request["covered_ids"]) | set(payload.get("exclusion_ids", []))
            ),
        )
        if output.topics and self.check_citations:
            # ADR 34 citation check Q6: between validation and publishing.
            payload.update(stage="check", checks=[])
            return self._enqueue(payload)
        self._publish(payload)

    def _publish(self, payload, check=None):
        final, packet = payload.pop("final"), payload.get("packet")
        self.storage.publish(
            payload["attempt"],
            final["output"],
            covered_ids=final["covered_ids"],
            chunk_ids=payload["chunks"],
            original_ids=packet["covered_ids"] if packet else [],
            packet=packet,
            omissions=payload["omissions"],
            excluded_ids=payload.get("exclusion_ids", []),
            ad_ids=payload.get("ad_ids", []),
            filter_state=payload.get("filter_state"),
            filter_evidence=payload.get("filter_evidence", []),
            citation_check=check,
        )
        self._next_day(payload)

    def _check_packet(self, key, topic, *, role=CHECK_ROLE):
        """A topic and its cited originals as the briefing read them, with the
        cached messages they reply to as bounded context."""
        cited, replies = [], []
        for identity in topic.citations:
            message = self.store.message(key, identity)
            if message is None or message.deleted:
                raise Refused("A cited original is no longer cached")
            cited.append(message)
        used = 0
        for message in cited:
            parent = reply_target(message)
            if (
                parent is None
                or parent in topic.citations
                or any(m.message_id == parent for m in replies)
            ):
                continue
            parent = self.store.message(key, parent)
            if (
                parent is None
                or parent.deleted
                or not context.eligible(parent)
                or not self.store.groups.current(parent)
                # Ads and ignored bots never reach a model as context either.
                or any(self.filtering.annotations(parent).values())
            ):
                continue
            size = context.encoded(checked_view(self.store, parent))
            if used + size <= citation_check.CONTEXT_BYTES:
                replies.append(parent)
                used += size
        return citation_check.make_packet(
            role,
            {"title": topic.title, "text": topic.text},
            [checked_view(self.store, m) for m in cited],
            [checked_view(self.store, m) for m in replies],
        )

    async def _check(self, job, payload):
        """One topic's citation check per job (ADR 34 citation check Q2). A
        check that cannot run leaves the topic unchecked (Q5); a route or budget
        failure leaves the rest unchecked too, without further calls."""
        attempt = self._admit(payload, budget=False)
        output = BriefingOutput.model_validate(payload["final"]["output"])
        checks = payload["checks"]
        topic = output.topics[len(checks)]
        result = None
        if self.storage.batch(payload["batch"]).check_requests >= 12:
            result = ClaimCheck(status="unchecked", reason="limit")
        else:
            try:
                packet = self._check_packet(attempt.snapshot.key, topic)
            except Refused:
                if not self._current(payload):
                    raise
                result = ClaimCheck(status="unchecked", reason="bound")
        if result is None:

            def admission(db, _):
                self._admit(payload, db=db)

            def accounting(db, identity):
                self.storage.record_work(
                    payload["batch"],
                    check_requests=1,
                    dispatch_token=identity["id"],
                    db=db,
                )

            try:
                answers = await self.work.run(
                    self.app.decider,
                    job,
                    packet,
                    lambda: self._current(payload),
                    klass="group",
                    group_admission=admission,
                    group_accounting=accounting,
                    group_guidance=citation_check.RULES_HASH,
                )
                result = citation_check.answered(packet, answers)
            except (GroupGuidanceChanged, ProcessingPaused):
                raise
            except ProviderRefused:
                result = ClaimCheck(status="unchecked", reason="refused")
            except BudgetReached:
                result = ClaimCheck(status="unchecked", reason="budget")
            except Refused as error:
                if not self._current(payload):
                    raise
                result = ClaimCheck(
                    status="unchecked",
                    reason="format"
                    if str(error).startswith("Invalid structured output")
                    else "unavailable",
                )
        # A refusal before dispatch leaves the job claimed; a dispatched one
        # already settled it.
        self._done(job)
        checks.append(result.model_dump())
        if result.reason in {"limit", "refused", "budget", "unavailable"}:
            # Further calls would meet the same refusal or count more unknowns.
            checks.extend(
                result.model_dump() for _ in range(len(output.topics) - len(checks))
            )
        if len(checks) < len(output.topics):
            return self._enqueue(payload)
        self._publish(
            payload,
            dict(rules_hash=citation_check.RULES_HASH, topics=checks),
        )

    def _next_day(self, payload):
        batch = self.storage.batch(payload["batch"])
        if payload["days"] and batch.requests < 5 and len(batch.source_ids) < 200:
            payload["stage"] = "start_day"
            payload.pop("packet", None)
            return self._enqueue(payload)
        self.storage.finish_batch(batch.id)

    async def run(self, job):
        with self._holding(json.loads(job["payload"]).get("batch")):
            await self._run(job)

    async def _run(self, job):
        payload = json.loads(job["payload"])
        try:
            if payload["stage"] == "start_day":
                batch = self.storage.batch(payload["batch"])
                day = payload["days"].pop(0)
                snapshot = FrozenBriefing.model_validate(day["snapshot"])
                self.planner.admit(
                    snapshot.key, snapshot.day, snapshot.intended_ids, continuation=True
                )
                self.work.check_budget("group_briefing")
                self._done(job)
                self._start_day(
                    day, batch, automatic=batch.automatic, remaining=payload["days"]
                )
                return
            attempt = self._admit(payload, budget=payload["stage"] != "check")
            if attempt.snapshot.guidance_hash != self._guide():
                self.storage.refresh_guidance(
                    attempt.id,
                    guidance_hash=self._guide(),
                    route=ROUTES["group_briefing"],
                )
                payload.update(
                    chunks=[],
                    packets=copy.deepcopy(attempt.snapshot.packets),
                    refusals=0,
                    exclusion_ids=[],
                    ad_ids=[],
                    filter_evidence=[],
                )
                payload.pop("packet", None)
                self._filtered(payload)
                self._done(job)
                self._enqueue(payload)
                return
            if payload["stage"] == "native":
                await self._native(job, payload)
            elif payload["stage"] == "filter":
                await self._filter(job, payload)
            elif payload["stage"] == "prepare":
                await self._prepare(job, payload)
            elif payload["stage"] == "check":
                await self._check(job, payload)
            else:
                await self._model(job, payload)
        except asyncio.CancelledError:
            self.abandon(job)
            raise
        except GroupGuidanceChanged:
            self._hold(job)
        except ProcessingPaused:
            self._hold(job)
        except ProviderRefused as refusal:
            payload["refusals"] += 1
            batch = self.storage.batch(payload["batch"])
            if (
                payload["refusals"] < 3
                and batch.requests < 5
                and self._current(payload)
            ):
                delay = min(300, max(30, refusal.retry_after or 30))
                self.planner.refuse(
                    self.storage.attempt(payload["attempt"]).snapshot.key, delay=delay
                )
                self._enqueue(payload, due=self.store.clock() + delay)
            else:
                self._fail(job, "Exact briefing route unavailable; use Retry")
        except Refused:
            if payload.get("attempt") and self._current(payload):
                try:
                    self._admit(payload, budget=payload["stage"] != "check")
                except Refused:
                    self._hold(job)
                    return
            self._fail(job, "Briefing preparation or generation failed; use Retry")
        except Exception:
            self.abandon(job)

    def _fail(self, job, error, *, interrupted=False):
        payload = json.loads(job["payload"])
        if payload.get("attempt"):
            self.storage.fail(payload["attempt"], error, interrupted=interrupted)
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state=?,error=? WHERE id=? AND state IN ('queued','claimed','running')",
                ("unknown" if interrupted else "failed", error, job["id"]),
            )
        self.storage.finish_batch(
            payload["batch"], state="interrupted" if interrupted else "failed"
        )

    def abandon(self, job):
        rows = self.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not rows:
            return
        self._fail(job, "Briefing interrupted; use Retry", interrupted=True)

    def reconcile(self, key=None):
        self.storage.reconcile(key)
        roles = sorted(GROUP_ROLES)
        with self.store.transaction() as db:
            jobs = db.execute(
                f"SELECT id,payload FROM jobs WHERE role IN ({','.join('?' * len(roles))}) AND state IN ('queued','claimed')",
                roles,
            ).fetchall()
            for row in jobs:
                payload = json.loads(row["payload"])
                if payload.get("attempt"):
                    attempt = self.storage.attempt(payload["attempt"], db=db)
                    if not attempt or (key is not None and attempt.snapshot.key != key):
                        continue
                    if attempt.state == "active" and (
                        (
                            payload.get("filter_state") is not None
                            and payload["filter_state"]
                            != self.filtering.state_hash(attempt.snapshot.key, db=db)
                        )
                        or not all(
                            proof.get("key") == attempt.snapshot.key
                            and self.store.group_filters.evidence_current(
                                proof, db=db, standing=True
                            )
                            for proof in payload.get("filter_evidence", [])
                        )
                    ):
                        attempt = attempt.model_copy(
                            update=dict(
                                state="superseded",
                                error="Briefing filtering evidence changed; update again",
                                updated=self.store.clock(),
                            )
                        )
                        self.storage._write_attempt(db, attempt)
                        self.storage._prefixes.pop(attempt.id, None)
                    if (
                        attempt.state == "active"
                        and attempt.automatic
                        and attempt.id not in self._speech_started
                        and payload.get("stage") != "start_day"
                        and any(
                            value < self.store.clock() - WINDOW
                            for value in attempt.snapshot.source_dates.values()
                        )
                    ):
                        attempt = self.storage.fail(
                            attempt.id,
                            "Automatic source window expired; frozen Retry remains available",
                            interrupted=True,
                            db=db,
                        )
                    if (
                        attempt.state != "active"
                        and payload.get("stage") != "start_day"
                    ):
                        db.execute(
                            "UPDATE jobs SET state='superseded' WHERE id=?",
                            (row["id"],),
                        )
                    elif payload.get("stage") == "start_day":
                        snapshot = FrozenBriefing.model_validate(
                            payload["days"][0]["snapshot"]
                        )
                        if not self.storage.current(snapshot, db=db) or any(
                            value < self.store.clock() - WINDOW
                            for value in snapshot.source_dates.values()
                        ):
                            db.execute(
                                "UPDATE jobs SET state='superseded' WHERE id=?",
                                (row["id"],),
                            )
            live = {
                json.loads(row[0])["batch"]
                for row in db.execute(
                    f"SELECT payload FROM jobs WHERE role IN ({','.join('?' * len(roles))}) AND state IN ('queued','claimed','running')",
                    roles,
                )
            }
            for row in db.execute(
                "SELECT id,chat_key FROM group_briefing_batches WHERE json_extract(body,'$.state')='active'"
            ).fetchall():
                if (
                    (key is None or row["chat_key"] == key)
                    and row["id"] not in live
                    # Read after the jobs: a stage in hand queues its successor
                    # before it lets go.
                    and row["id"] not in self._held
                ):
                    self.storage.finish_batch(row["id"], state="interrupted", db=db)
            # An attempt whose batch ended without a stage would stay active,
            # and its group could never be updated again.
            for row in db.execute(
                "SELECT id,chat_key,json_extract(body,'$.batch_id') AS batch "
                "FROM group_briefing_attempts WHERE state='active'"
            ).fetchall():
                if (
                    (key is None or row["chat_key"] == key)
                    and row["batch"] not in live
                    and row["batch"] not in self._held
                ):
                    self.storage.fail(
                        row["id"],
                        "Briefing interrupted; use Retry",
                        interrupted=True,
                        db=db,
                    )

    def retry(self, identity):
        previous = self.storage.attempt(identity)
        if not previous:
            raise Refused("Briefing attempt is unavailable")
        self.app.admit_ai()
        if not self.app.authorized():
            raise Refused("Account authorization changed")
        self.planner.admit(
            previous.snapshot.key,
            previous.snapshot.day,
            previous.snapshot.intended_ids,
            explicit=True,
        )
        self.work.check_budget("group_briefing")
        held = uuid.uuid4().hex
        with self._holding(held):
            attempt = self.storage.retry(
                identity,
                batch_id=held,
                guidance_hash=self._guide(),
                route=ROUTES["group_briefing"],
            )
            day = dict(
                snapshot=attempt.snapshot.model_dump(),
                packets=copy.deepcopy(attempt.snapshot.packets),
                fresh_ids=attempt.snapshot.intended_ids,
                omissions=[o.model_dump() for o in attempt.snapshot.omissions],
                reused=[],
            )
            reused, covered = self._reusable(
                attempt.snapshot, attempt.snapshot.guidance_hash
            )
            remaining = [
                subset(packet, [i for i in packet["covered_ids"] if i not in covered])
                for packet in day["packets"]
                if set(packet["covered_ids"]) - covered
            ]
            day.update(
                packets=remaining,
                reused=reused,
                fresh_ids=sorted(set(attempt.snapshot.intended_ids) - covered),
            )
            return self._start_day(
                day,
                self.storage.batch(attempt.batch_id),
                automatic=False,
                retry=attempt,
            )

    def local_idle(self):
        held = self._standing("idle")
        if held:
            return held[3]
        mark = self._mark()
        return self._stand("idle", mark, self._local_idle())

    def _local_idle(self):
        self.reconcile()
        roles = sorted(GROUP_ROLES)
        if self.store.rows(
            f"SELECT 1 FROM jobs WHERE role IN ({','.join('?' * len(roles))}) AND state IN ('claimed','running') LIMIT 1",
            roles,
        ):
            return False
        footprint = self.planner.footprint()
        if self.planner.active_pending():
            return False
        return not footprint or not self._freeze_days(footprint)

    async def process_due(self):
        job = self.next_job()
        if job and self.claim(job):
            await self.run(job)
