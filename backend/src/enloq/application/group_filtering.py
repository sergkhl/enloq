"""Cache-only group classifications with immutable evidence and safe reuse."""

import asyncio
import copy
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain.group_filtering import (
    AD_ROLE,
    BOT_ROLE,
    CONTEXT_BYTES,
    FLOOR,
    MAX_TARGETS,
    RULES_HASH,
    digest,
    encoded,
    make_packet,
)
from ..domain.models import Chat, Message, Refused, reply_target
from ..storage.members import account_setting
from ..storage.store import RETENTION, WINDOW
from . import group_context


class GroupFiltering:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.storage = self.store.group_filters

    def _rows(self, sql, args=(), *, db=None):
        return (
            [dict(row) for row in db.execute(sql, args)]
            if db is not None
            else self.store.rows(sql, args)
        )

    def _projection(self, message, *, db=None):
        return self.storage.projection(message, db=db)

    def latest_ten(self, key, author_peer, *, db=None):
        """Latest surviving native sources, with no eligible/media prefilter."""
        values = []
        for row in self._rows(
            "SELECT body FROM messages WHERE chat_key=? AND deleted=0 AND date>=? AND date<=? "
            "AND json_extract(body,'$.author_peer')=? ORDER BY date DESC,message_id DESC",
            (key, self.store.clock() - WINDOW, self.store.clock(), author_peer),
            db=db,
        ):
            message = Message.model_validate_json(row["body"])
            if self.store.groups.current(message, db=db):
                values.append(message)
                if len(values) == 10:
                    break
        return values

    def _context(self, key, targets):
        ids = {m.message_id for m in targets}
        replies = {
            reply_target(m)
            for m in targets
            if reply_target(m) and reply_target(m) not in ids
        }
        if len(replies) > 1:
            raise Refused("Distinct direct replies require separate classifier packets")
        parent = self.store.message(key, next(iter(replies))) if replies else None
        if replies and (
            parent is None
            or parent.deleted
            or parent.service
            or not self.store.groups.current(parent)
        ):
            raise Refused("Classifier direct reply is not currently cached")
        before = min(m.message_id for m in targets)
        candidates = self._rows(
            "SELECT body FROM messages WHERE chat_key=? AND deleted=0 AND date>=? "
            "AND message_id<? ORDER BY message_id DESC LIMIT 20",
            (key, self.store.clock() - WINDOW, before),
        )
        context, size, selected = [], 2, set(ids)
        recent = [Message.model_validate_json(row["body"]) for row in candidates]
        for message in ([parent] if parent else []) + recent:
            if (
                message.message_id in selected
                or message.service
                or not self.store.groups.current(message)
            ):
                continue
            projected, _, _ = self._projection(message)
            used = len(encoded(projected).encode()) + 1
            if size + used > CONTEXT_BYTES:
                if parent and message.key == parent.key:
                    raise Refused("Classifier direct reply exceeds the context bound")
                continue
            context.append(message)
            selected.add(message.message_id)
            size += used
        return sorted(context, key=lambda message: message.message_id)

    def _request(self, key, day, role, targets, *, author_peer=None):
        supporting = self._context(key, targets)
        values, context, evidence, complete = [], [], {}, True
        for message in [*targets, *supporting]:
            value, proof, ready = self._projection(message)
            (values if message in targets else context).append(value)
            evidence[message.key] = proof
            if message in targets:
                complete = complete and ready
        packet = make_packet(role, values, context, evidence_complete=complete)
        result = dict(
            key=key,
            day=day,
            role=role,
            packet=packet,
            evidence=evidence,
            source_keys=[m.key for m in targets],
            target_keys=[m.key for m in targets],
            author_peer=author_peer,
        )
        # Day is intentionally not part of reuse: unchanged originals do not
        # buy another answer merely because an update/date changed.
        result["fingerprint"] = digest(
            dict(role=role, evidence=evidence, packet=packet, author_peer=author_peer)
        )
        return result

    def _seen(self, request, *, db=None):
        return all(
            self.storage.decision(key, request["fingerprint"], db=db)
            for key in request["target_keys"]
        )

    def plan(
        self, key, sources, day, *, include_bots=True, max_requests=12, bot_samples=None
    ):
        """No I/O beyond retained cache reads; caller owns shared work limits.

        Re-plan after a finished request to benefit from a newly remembered bot.
        Missing media and overlarge whole samples produce no bot request.
        """
        if type(max_requests) is not int or not 0 <= max_requests <= 12:
            raise Refused("Invalid group classifier request allowance")
        if bot_samples is not None and not isinstance(bot_samples, dict):
            raise Refused("Invalid frozen bot sample membership")
        group_context.group_chat(self.store, key)
        group_context.day_bounds(self.store, day)
        sources = sorted(sources, key=lambda m: m.message_id)
        if len({m.key for m in sources}) != len(sources) or any(
            f"{m.account_id}/{m.peer}" != key or not self.store.groups.current(m)
            for m in sources
        ):
            raise Refused("Group classifier sources changed")
        requests, memo = [], {}
        # Hide Bots already leaves every verified bot out; judging one costs a
        # request and changes nothing until the owner turns it off.
        if include_bots and not self.hides_bots():
            for author in sorted(
                {
                    m.author_peer
                    for m in sources
                    if m.sender_is_bot is True and m.author_peer
                }
            ):
                if self.storage.bot(key, author):
                    continue
                sample = self.latest_ten(key, author)
                if bot_samples is not None and [
                    message.key for message in sample
                ] != bot_samples.get(author):
                    continue
                if len(sample) != 10 or any(
                    m.sender_is_bot is not True for m in sample
                ):
                    continue
                try:
                    request = self._request(
                        key, day, BOT_ROLE, sample, author_peer=author
                    )
                except Refused:
                    continue
                if not self._seen(request):
                    requests.append(request)
        targets = [
            m
            for m in sources
            if group_context.eligible(m)
            and not self.annotations(m, memo=memo)["ignored_bot"]
        ]
        batch = []
        for message in targets:
            # Existing source decisions are reused independently of newly
            # arriving neighbours or changes in packet partitioning.
            if self._source_decision(message, memo=memo) is not None:
                continue
            try:
                self._request(key, day, AD_ROLE, batch + [message])
            except Refused:
                if batch:
                    requests.append(self._request(key, day, AD_ROLE, batch))
                    batch = []
                try:
                    self._request(key, day, AD_ROLE, [message])
                except Refused:
                    continue
            batch.append(message)
            if len(batch) == MAX_TARGETS:
                requests.append(self._request(key, day, AD_ROLE, batch))
                batch = []
        if batch:
            requests.append(self._request(key, day, AD_ROLE, batch))
        return requests[:max_requests]

    def current(
        self,
        request,
        *,
        db=None,
        admission=True,
        sample=True,
        standing=False,
        memo=None,
    ):
        if not self.storage.evidence_current(
            request, db=db, admission=admission, standing=standing, memo=memo
        ):
            return False
        try:
            if request["role"] == BOT_ROLE and sample:
                latest = self.latest_ten(request["key"], request["author_peer"], db=db)
                if [m.key for m in latest] != request["target_keys"] or len(
                    latest
                ) != 10:
                    return False
                if any(
                    m.sender_is_bot is not True or not self._projection(m, db=db)[2]
                    for m in latest
                ):
                    return False
                if self.storage.bot(request["key"], request["author_peer"], db=db):
                    return False
            return True
        except (Refused, KeyError, TypeError, ValueError):
            return False

    async def run(
        self, request, job, *, group_admission=None, group_accounting=None, valid=None
    ):
        if job["role"] != request["role"] or job["scope"] != request["key"]:
            raise Refused("Classifier job identity mismatch")

        def current():
            # A dispatched request is started work: routine replay and a new
            # intake epoch block only its admission, below, and never discard a
            # paid answer whose originals are unchanged.
            return self.current(request, admission=False, standing=True) and (
                valid is None or valid()
            )

        def admission(db, current_job):
            if not self.current(request, db=db):
                raise Refused("Group classifier evidence changed")
            if self._seen(request, db=db):
                raise Refused("Unchanged group classification was already attempted")
            if group_admission is not None:
                group_admission(db, current_job)

        def accounting(db, current_job):
            if group_accounting is not None:
                group_accounting(db, current_job)
            # This marker shares the request reservation/counter transaction.
            # It remains even if access or native/media evidence changes while
            # HTTP is in flight, including an unknown interrupted outcome.
            self.storage.record(
                request,
                {},
                failed=True,
                attempt_id=current_job["id"],
                pending=True,
                db=db,
            )

        packet = copy.deepcopy(request["packet"])
        try:
            result = await self.app.rewriting.work.run(
                self.app.decider,
                job,
                packet,
                current,
                klass="group",
                group_admission=admission,
                group_accounting=accounting,
                group_guidance=RULES_HASH,
            )
        except (Refused, asyncio.CancelledError) as failure:
            # Even a route refusal keeps unchanged evidence from automatically
            # resampling. No invented probability and no exclusion are stored.
            dispatched = self.store.rows(
                "SELECT 1 FROM reservations WHERE id=?", (job["id"],)
            )
            refused = self.store.rows(
                "SELECT 1 FROM jobs WHERE id=? AND state='refused'", (job["id"],)
            )
            if isinstance(failure, asyncio.CancelledError) or not (
                dispatched or refused
            ):
                raise
            return None
        with self.store.transaction() as db:
            if not self.current(request, db=db, admission=False, standing=True):
                return None
            self.storage.record(
                request,
                result or {},
                failed=result is None,
                attempt_id=job["id"],
                settle=True,
                db=db,
            )
            if (
                request["role"] == BOT_ROLE
                and result
                and result["all_ten_useless"] >= FLOOR
            ):
                self.storage.ignore_bot(
                    request["key"],
                    request["author_peer"],
                    day=datetime.fromtimestamp(
                        self.store.clock(), ZoneInfo(self.store.timezone)
                    )
                    .date()
                    .isoformat(),
                    fingerprint=request["fingerprint"],
                    probability=result["all_ten_useless"],
                    db=db,
                )
        return result

    def _source_decision(self, message, *, db=None, memo=None):
        for result in self.storage.source_decisions(message.key, db=db):
            if (
                result["role"] == AD_ROLE
                and result["evidence"].get(message.key)
                == self._projection(message, db=db)[1]
                and self.current(
                    result, db=db, admission=False, sample=False, memo=memo
                )
            ):
                return result
        return None

    def annotations(self, message, *, db=None, memo=None):
        if db is None:
            with self.store.transaction() as db:
                return self.annotations(message, db=db, memo=memo)
        return self._annotations(message, db=db, memo=memo)[0]

    def annotate(self, messages):
        """Flags for one page of originals under one read of the store."""
        memo = {}
        with self.store.transaction() as db:
            return [
                self._annotations(message, db=db, memo=memo)[0] for message in messages
            ]

    def hides_bots(self, db=None):
        """The member's Hide Bots preference, on unless they turned it off.

        While on, every native-verified bot's originals leave group reading and
        briefings without a model judgment (ADR 34). The owner's allowance of
        one bot in one group exempts it.
        """
        if db is None:
            with self.store.transaction() as db:
                return self.hides_bots(db)
        if self.store.shared_path:
            # Each member's own preference since ADR 48.
            value = account_setting(db, "hide_bots", self.store.account_id)
        else:
            row = db.execute("SELECT value FROM metadata WHERE key='hide_bots'")
            value = (row.fetchone() or [None])[0]
        return value != "0" if value is not None else True

    def _annotations(self, message, *, db, memo=None):
        key = f"{message.account_id}/{message.peer}"
        row = db.execute("SELECT body FROM chats WHERE key=?", (key,)).fetchone()
        if (
            message.account_id != self.store.account_id
            or not row
            or Chat.model_validate_json(row[0]).kind != "group"
        ):
            return dict(ad=False, ignored_bot=False, allowed_bot=False), None
        row = db.execute(
            "SELECT body FROM messages WHERE key=?", (message.key,)
        ).fetchone()
        current = Message.model_validate_json(row[0]) if row else None
        if current is None or any(
            getattr(current, field) != getattr(message, field)
            for field in ("revision", "date", "deleted", "author_peer", "sender_is_bot")
        ):
            # Historical versions never borrow the current original's flags.
            return dict(ad=False, ignored_bot=False, allowed_bot=False), None
        bot = (
            self.storage.bot(key, message.author_peer, db=db)
            if message.author_peer
            else None
        )
        verified = message.sender_is_bot is True
        allowed = bool(verified and bot and bot["status"] == "allowed")
        ignored = bool(
            verified
            and not allowed
            and (
                self.hides_bots(db)
                or (
                    bot
                    and bot["status"] == "ignored"
                    and group_context.calendar_day(self.store, message) >= bot["day"]
                )
            )
        )
        decision = (
            None
            if message.deleted
            else self._source_decision(message, db=db, memo=memo)
        )
        ad = bool(
            decision
            and not decision["failed"]
            and decision["probabilities"].get(f"ad_{message.message_id}", 0) >= FLOOR
        )
        return dict(ad=ad, ignored_bot=ignored, allowed_bot=allowed), decision

    def exclude(self, packet):
        with self.store.transaction() as db:
            return self._exclude(packet, db=db)

    def _exclude(self, packet, *, db):
        value = copy.deepcopy(packet)
        proofs, memo = {}, {}

        def flags_for(source):
            row = db.execute(
                "SELECT body FROM messages WHERE key=?",
                (f"{packet['chat_key']}/{source['id']}",),
            ).fetchone()
            message = Message.model_validate_json(row[0]) if row else None
            if message is None or message.revision != source["revision"]:
                return dict(ad=False, ignored_bot=False, allowed_bot=False)
            flags, proof = self._annotations(message, db=db, memo=memo)
            if flags["ad"]:
                proofs[proof["fingerprint"]] = proof
            return flags

        excluded, ads, kept = [], [], []
        for source in packet.get("messages", []):
            flags = flags_for(source)
            if flags["ad"]:
                ads.append(source["id"])
            if flags["ad"] or flags["ignored_bot"]:
                excluded.append(source["id"])
            else:
                kept.append(source)
        value.update(messages=kept, exclusion_ids=excluded, ad_ids=ads)
        dropped_context = []
        context = []
        for source in packet.get("context", []):
            flags = flags_for(source)
            if flags["ad"] or flags["ignored_bot"]:
                dropped_context.append(source["id"])
            else:
                context.append(source)
        value["context"] = context
        value["excluded_context_ids"] = dropped_context
        value["filter_evidence"] = list(proofs.values())
        for field in (
            "inputs",
            "attribution_inputs",
            "speaker_inputs",
            "image_inputs",
            "transcript_inputs",
        ):
            for identity in dropped_context:
                value.get(field, {}).pop(f"{packet['chat_key']}/{identity}", None)
        if "images" in value:
            value["images"] = [
                image
                for image in value["images"]
                if image["message_id"] not in dropped_context
            ]
        return value

    def state_hash(self, key, *, db=None):
        return self.storage.state_hash(key, db=db)

    def restore_bot(self, key, author_peer):
        if not self.app.authorized():
            raise Refused("This group account is unavailable")
        # Correction is cache-only and remains possible for retained groups.
        with self.store.transaction() as db:
            self.store.groups._chat(db, key)
            if not self.store.groups.get(key, db=db).supported:
                raise Refused("Unsupported group")
        return self.storage.allow_bot(key, author_peer)

    async def enrich(self, key, source_ids=None, *, limit=32):
        """Optional bounded native metadata step; never invoked by reading/plan."""
        group_context.group_chat(self.store, key)
        if not self.app.authorized() or not 1 <= limit <= 32:
            raise Refused("Native bot metadata access unavailable")
        allowed = ""
        args = [key, self.store.clock() - WINDOW, self.store.clock()]
        if source_ids is not None:
            if len(source_ids) > 200 or any(
                type(i) is not int or i <= 0 for i in source_ids
            ):
                raise Refused("Invalid bot metadata source bound")
            allowed = (
                " AND message_id IN (" + ",".join("?" for _ in source_ids) + ")"
                if source_ids
                else " AND 0"
            )
            args.extend(source_ids)
        args.append(limit)
        peers = [
            row["author"]
            for row in self.store.rows(
                "SELECT DISTINCT json_extract(body,'$.author_peer') AS author FROM messages "
                "WHERE chat_key=? AND date>=? AND date<=? AND json_extract(body,'$.sender_is_bot') IS NULL "
                "AND json_extract(body,'$.author_peer') LIKE 'user:%'"
                + allowed
                + " ORDER BY author LIMIT ?",
                args,
            )
        ]
        if len(peers) < limit:
            # Deleted-only authors never enter briefing targets. The remaining
            # allowance may enrich their retained display provenance, without
            # widening the assessment's seven-day surviving-source window.
            for row in self.store.rows(
                "SELECT DISTINCT json_extract(body,'$.author_peer') AS author FROM messages "
                "WHERE chat_key=? AND deleted=1 AND date>=? AND date<=? "
                "AND json_extract(body,'$.sender_is_bot') IS NULL "
                "AND json_extract(body,'$.author_peer') LIKE 'user:%' ORDER BY author LIMIT ?",
                (key, self.store.clock() - RETENTION, self.store.clock(), limit),
            ):
                if row["author"] not in peers:
                    peers.append(row["author"])
                    if len(peers) == limit:
                        break
        if not peers or not hasattr(self.app.transport, "refresh_group_authors"):
            return 0
        return await self.app.transport.refresh_group_authors(
            key.split("/", 1)[1], peers
        )
