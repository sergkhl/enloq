"""One dispatch per attempt, separate purpose budgets, content-free receipts."""

import asyncio
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ..application.capacity import Capacity
from ..domain.briefing_contract import GROUP_ROLES, BriefingError, validate_group_output
from ..domain.group_filtering import FILTER_ROLES, RULES_HASH
from ..domain.models import Refused
from ..domain.suggestions import ResponseSuggestions, bracketed
from ..storage.members import OWNER, account_member, account_setting
from . import decider
from .base import EngineResponse, InvalidOutput, ProviderRefused
from .guidance import briefing_fingerprint, snapshot
from .openrouter import (
    LOGICAL_MODEL,
    PROVIDER,
    STREAMED_ROLES,
    IncomingRewrite,
    MemoryUpdate,
    TraitNotes,
    has_target_text,
)
from .streaming import cost_nanos
from .suggestion_typography import normalize_suggestions

# ADR 15: counted spending is receipts plus a rough flat estimate per call whose
# outcome is unknown (including calls still running); a provider refusal that
# did no work counts nothing. There are no full-context reservations.
FLAT_UNKNOWN = 10_000_000  # $0.01 in nanodollars.
DEFAULT_BUDGET = 1_000_000_000  # $1.00
DEFAULT_GROUP_BUDGET = 2_000_000_000  # $2.00
# ADR 48 Q2: a friend's Daily and Group Briefing Budgets start at $0.50 each.
MEMBER_BUDGET = 500_000_000
MAX_BUDGET = 10_000_000_000  # $10.00
ROLES = (
    frozenset(
        {
            "incoming_rewrite",
            "chat_memory",
            "response_suggestions",
            "suggestion_labels",
            "reaction_assessment",
            "emotion_classification",
            "hostility_screen",
            "trait_scales",
            "trait_notes",
        }
    )
    | GROUP_ROLES
    | FILTER_ROLES
)
# Background roles whose unavailable exact route is a bounded no-work refusal.
BACKGROUND = (
    frozenset(
        {
            "incoming_rewrite",
            "chat_memory",
            "response_suggestions",
            "suggestion_labels",
            "reaction_assessment",
            "emotion_classification",
            "hostility_screen",
            "trait_scales",
            "trait_notes",
        }
    )
    | GROUP_ROLES
    | FILTER_ROLES
)
# ADR 30: roles answered by the decision model's exact route; every other role
# uses ADR 29's MiMo route. ADR 49 Q19 adds the labels of suggestions.
DECIDED = (
    frozenset(
        {
            "emotion_classification",
            "hostility_screen",
            "reaction_assessment",
            "suggestion_labels",
            "trait_scales",
        }
    )
    | FILTER_ROLES
)
ROUTES = {
    role: f"{decider.LOGICAL_MODEL}@{decider.PROVIDER}"
    if role in DECIDED
    else f"{LOGICAL_MODEL}@{PROVIDER}"
    for role in ROLES
}
# Roles whose repeated format failures pause automatic dispatch. Suggestions
# never pause: each normal changed source stays eligible for one attempt and
# cannot touch another role's count (ADR 25).
FORMAT_PAUSED = frozenset({"incoming_rewrite", "chat_memory"})
# Muse wrote an emoji beyond U+FFFF as \u0001F973, five hex digits after
# \u000, where JSON needs a surrogate pair (ADR 56's idioms gate): read it as
# the emoji it names rather than as a control character and four letters.
ASTRAL_ESCAPE = re.compile(r"(?<!\\)\\u0001([0-9A-Fa-f]{4})")


def surrogate_pair(match):
    high, low = divmod(int("1" + match.group(1), 16) - 0x10000, 0x400)
    return f"\\u{0xD800 + high:04x}\\u{0xDC00 + low:04x}"


def instructed(packet):
    """Whether the owner's own words in a manual request, its draft or a
    reminder's Message Idea, carry bracketed instructions (ADR 25, ADR 58)."""
    return bool(
        packet.get("manual")
        and (
            bracketed(packet.get("draft"))
            or any(
                bracketed(reminder.get("idea"))
                for reminder in packet.get("reminders") or ()
                if isinstance(reminder, dict)
            )
        )
    )


def validate_output(role, response, packet):
    if role in DECIDED:
        # Decision answers, not generated text (ADR 30).
        return decider.validate(role, response.content, packet)
    if response.usage.get("finish_reason") not in (None, "stop"):
        raise InvalidOutput("incomplete_response")
    content = response.content
    if isinstance(content, str):

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON key")
                result[key] = value
            return result

        # A raw line break or tab inside a string (ADR 29's structured text) is
        # the one tolerated deviation, because its meaning is exact.
        if any(ord(c) < 32 and c not in "\n\r\t" for c in content):
            raise InvalidOutput("invalid_json")
        if role == "response_suggestions":
            content = ASTRAL_ESCAPE.sub(surrogate_pair, content)
        try:
            content = json.loads(content, object_pairs_hook=unique, strict=False)
        except ValueError:
            raise InvalidOutput("invalid_json") from None
    if role in GROUP_ROLES:
        try:
            return validate_group_output(role, content, packet)
        except BriefingError as failure:
            raise InvalidOutput(failure.code) from None
    if role == "response_suggestions":
        content = normalize_suggestions(content, packet)
    model = {
        "incoming_rewrite": IncomingRewrite,
        "chat_memory": MemoryUpdate,
        "response_suggestions": ResponseSuggestions,
        "trait_notes": TraitNotes,
    }[role]
    try:
        # ADR 49 Q19: the decision model replaces suggestion labels afterwards.
        result = model.model_validate(content)
        if hasattr(result, "text"):
            result.text.encode("utf-8")
        if role == "response_suggestions":
            for option in result.options:
                option.text.encode("utf-8")
                # Text the owner may send carries no control characters.
                if any(ord(c) < 32 and c != "\n" for c in option.text):
                    raise InvalidOutput("invalid_text")
                # ADR 25: bracketed draft text instructs the writer and never
                # appears in an option, which Send would otherwise question.
                if instructed(packet) and bracketed(option.text):
                    raise InvalidOutput("bracketed_text")
    except ValidationError as failure:
        if role == "chat_memory":
            codes = {
                error["type"]
                for error in failure.errors(
                    include_input=False, include_context=False, include_url=False
                )
            }
            for code in (
                "memory_text_bytes",
                "memory_text_length",
                "memory_text_empty",
            ):
                if code in codes:
                    raise InvalidOutput(code) from None
        raise InvalidOutput("schema") from None
    except (TypeError, UnicodeError):
        raise InvalidOutput("schema") from None
    if role == "incoming_rewrite":
        # Cached descriptions are inputs. Only bytes attached to this call need
        # fresh descriptions, including any attached supporting-context images.
        expected = {i["message_id"] for i in packet.get("images", [])}
        returned = [v.message_id for v in result.image_context]
        if set(returned) != expected or len(set(returned)) != len(returned):
            raise InvalidOutput("image_references")
        if not result.text.strip() and has_target_text(packet):
            raise InvalidOutput("omitted_target_text")
    return result


def budget_family(role):
    """A request's purpose, independent of its account, chat or current settings."""
    return "group_briefing" if role in GROUP_ROLES | FILTER_ROLES else "daily"


def budget_key(family):
    if family not in ("daily", "group_briefing"):
        raise Refused("Unsupported budget family")
    return family + "_budget"


def counted(db, ledger, bucket, *, family="daily", member=None):
    """Count one family's dispatch day across routes; historical rows stay daily.
    With ``member``, only that member's spending counts (ADR 48). Only offline
    authored fixtures are exempt."""
    budget_key(family)
    where, args = "", (FLAT_UNKNOWN, bucket, family)
    if member is not None:
        where, args = " AND member=?", (*args, member)
    return db.execute(
        f"SELECT coalesce(sum(CASE WHEN status='receipted' THEN coalesce(actual,0) WHEN status='refused' THEN 0 ELSE ? END),0) FROM {ledger} WHERE engine<>'offline' AND bucket=? AND family=?{where}",
        args,
    ).fetchone()[0]


def accounting(db, store):
    """Where an account's spending is counted: its member's rows and limits in
    the attached registry (ADR 48), or a lone store's own tables."""
    if store.shared_path:
        member = account_member(db, store.account_id)
        return "shared.metadata", "shared.model_reservations", member
    return "metadata", "reservations", None


def budget_day(db, store, metadata):
    """The accounting day never moves backwards with a rolled-back clock."""
    day = (
        datetime.fromtimestamp(store.clock(), ZoneInfo(store.timezone))
        .date()
        .isoformat()
    )
    old = db.execute(f"SELECT value FROM {metadata} WHERE key='budget_day'").fetchone()
    return max(day, old[0] if old else day)


def daily_budget(db, metadata, *, family="daily", member=None):
    """A family's limit: a member's own since ADR 48, which only the owner
    sets; a friend starts at $0.50 and the owner at the shipped defaults."""
    key = budget_key(family)
    default = DEFAULT_GROUP_BUDGET if family == "group_briefing" else DEFAULT_BUDGET
    if member is None:
        row = db.execute(f"SELECT value FROM {metadata} WHERE key=?", (key,)).fetchone()
        return int(row[0]) if row else default
    prefix = metadata.removesuffix("metadata")
    row = db.execute(
        f"SELECT value FROM {prefix}member_settings WHERE member=? AND key=?",
        (member, key),
    ).fetchone()
    if row:
        return int(row[0])
    return default if member == OWNER else MEMBER_BUDGET


def require_budget(spent, limit, family):
    if spent >= limit:
        label = "Group Briefing" if family == "group_briefing" else "Daily"
        raise BudgetReached(
            f"{label} budget reached; waiting for the next budget day or a higher budget in Settings"
        )


class PaidSpeech:
    """ADR 39: a hosted fallback transcription is a paid call. It is admitted
    against the Daily Budget and reserved before dispatch, then receipted like a
    model call. The transcript job row and its states stay Media's."""

    engine = "speech"

    def __init__(self, store, identity, scope, revision):
        self.store, self.identity = store, identity
        self.scope, self.revision = scope, revision

    def reserve(self):
        store = self.store
        with store.transaction() as db:
            metadata, ledger, member = accounting(db, store)
            day = budget_day(db, store, metadata)
            bucket = "day:" + day
            require_budget(
                counted(db, ledger, bucket, member=member),
                daily_budget(db, metadata, member=member),
                "daily",
            )
            db.execute(
                f"INSERT OR REPLACE INTO {metadata} VALUES ('budget_day',?)", (day,)
            )
            db.execute(
                "INSERT INTO reservations (id,bucket,engine,reserved,actual,status,created,family) VALUES (?,?,?,?,NULL,'unknown',?,'daily')",
                (self.identity, bucket, self.engine, FLAT_UNKNOWN, store.clock()),
            )
            if store.shared_path:
                db.execute(
                    "INSERT INTO shared.model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created,family,member) VALUES (?,?,?,'transcript',?,?,?,?,NULL,'unknown',?,'daily',?)",
                    (
                        store.account_id,
                        self.identity,
                        self.scope,
                        self.revision + ":" + self.identity,
                        bucket,
                        self.engine,
                        FLAT_UNKNOWN,
                        store.clock(),
                        member,
                    ),
                )

    def settle(self, model, provider, usage, *, refused=False, error=None):
        """A refusal counts nothing; an unknown cost keeps the flat estimate."""
        store, actual = self.store, None if refused else cost_nanos(usage)
        with store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO receipts (id,role,model,provider,usage,error,family) VALUES (?,'transcript',?,?,?,?,'daily')",
                (self.identity, model, provider, json.dumps(usage), error),
            )
            if store.shared_path:
                db.execute(
                    "INSERT OR REPLACE INTO shared.model_receipts (account_id,id,role,model,provider,usage,error,family) VALUES (?,?,'transcript',?,?,?,?,'daily')",
                    (
                        store.account_id,
                        self.identity,
                        model,
                        provider,
                        json.dumps(usage),
                        error,
                    ),
                )
            if refused:
                status, value = "refused", 0
            elif actual is not None:
                status, value = "receipted", actual
            else:
                return
            db.execute(
                "UPDATE reservations SET actual=?,status=? WHERE id=?",
                (value, status, self.identity),
            )
            if store.shared_path:
                db.execute(
                    "UPDATE shared.model_reservations SET actual=?,status=? WHERE account_id=? AND id=?",
                    (value, status, store.account_id, self.identity),
                )


class BudgetReached(Refused):
    """The family's spending reached its limit for this budget day."""


class BudgetReserve(Refused):
    """Older-message work stops while less than a quarter of the budget remains."""


class GroupGuidanceChanged(Refused):
    """Refresh pending group work; no request or spending has been admitted."""


class ModelWork:
    def __init__(self, store):
        self.store = store
        # The account manager replaces this with the one global capacity.
        self.capacity = Capacity()
        self.admit = lambda: None
        # Processing Guidance and format outcomes live in the shared registry.
        self.guidance = lambda: snapshot()
        self.on_format = lambda role, ok: None

    def check_budget(self, role):
        """Hold a new preparation stage without creating spending evidence."""
        family = budget_family(role)
        store = self.store
        with store.transaction() as db:
            metadata, ledger, member = accounting(db, store)
            bucket = "day:" + budget_day(db, store, metadata)
            require_budget(
                counted(db, ledger, bucket, family=family, member=member),
                daily_budget(db, metadata, family=family, member=member),
                family,
            )

    def _group_options(self, job, admission, accounting, guidance):
        if any(value is not None for value in (admission, accounting, guidance)):
            if job["role"] not in GROUP_ROLES | FILTER_ROLES:
                raise Refused("Group stage hooks require a group briefing role")
            if guidance is not None and (not isinstance(guidance, str) or not guidance):
                raise Refused("Invalid group stage guidance proof")
            return True
        return False

    def _group_guidance(self, db, job, expected):
        metadata = "shared.metadata" if self.store.shared_path else "metadata"
        values = dict(
            db.execute(
                f"SELECT key,value FROM {metadata} WHERE key IN ('guidance','guidance_version')"
            ).fetchall()
        )
        guidance = snapshot(
            json.loads(values.get("guidance", "{}")),
            int(values.get("guidance_version", 0)),
        )
        if self.store.shared_path:
            # The member's own Hide Bots (ADR 48 Q20).
            hidden = account_setting(db, "hide_bots", self.store.account_id)
        else:
            row = db.execute("SELECT value FROM metadata WHERE key='hide_bots'")
            hidden = (row.fetchone() or [None])[0]
        fingerprint = (
            RULES_HASH
            if job["role"] in FILTER_ROLES
            else briefing_fingerprint(
                guidance,
                role=job["role"],
                route=ROUTES[job["role"]],
                hide_bots=hidden != "0" if hidden is not None else True,
            )
        )
        if expected is not None and expected != fingerprint:
            raise GroupGuidanceChanged(
                "Group briefing guidance changed before dispatch"
            )
        return guidance

    def _group_preparation(self, db, job, engine, admission, expected):
        if db.execute("SELECT 1 FROM reservations WHERE id=?", (job["id"],)).fetchone():
            raise Refused("Attempt already dispatched; use explicit Retry")
        if not db.execute(
            "SELECT 1 FROM jobs WHERE id=? AND state IN ('queued','claimed')",
            (job["id"],),
        ).fetchone():
            raise Refused("Work was stopped before dispatch")
        if engine != "offline":
            metadata, ledger, member = accounting(db, self.store)
            bucket = "day:" + budget_day(db, self.store, metadata)
            require_budget(
                counted(db, ledger, bucket, family="group_briefing", member=member),
                daily_budget(db, metadata, family="group_briefing", member=member),
                "group_briefing",
            )
        if admission is not None:
            admission(db, job)
        return self._group_guidance(db, job, expected)

    def reserve(
        self,
        job,
        engine,
        maximum=None,
        *,
        reserve=0,
        group_admission=None,
        group_accounting=None,
        group_guidance=None,
    ):
        """Record one dispatch if today's counted spending is below the budget,
        less a ``reserve`` fraction kept for other work. ``maximum`` is retained
        for callers; it no longer affects accounting."""
        group = self._group_options(
            job, group_admission, group_accounting, group_guidance
        )
        store, family = self.store, budget_family(job["role"])
        with store.transaction() as db:
            if db.execute(
                "SELECT id FROM reservations WHERE id=?", (job["id"],)
            ).fetchone():
                raise Refused("Attempt already dispatched; use explicit Retry")
            metadata, ledger, member = accounting(db, store)
            day = budget_day(db, store, metadata)
            bucket = "day:" + day
            spent = counted(db, ledger, bucket, family=family, member=member)
            limit = daily_budget(db, metadata, family=family, member=member)
            if engine != "offline":
                require_budget(spent, limit, family)
            if (
                family == "daily"
                and engine != "offline"
                and reserve
                and spent >= limit * (1 - reserve)
            ):
                raise BudgetReserve("Keeping budget for new messages")
            if not db.execute(
                "UPDATE jobs SET state='running',error=NULL WHERE id=? AND state IN ('queued','claimed')",
                (job["id"],),
            ).rowcount:
                raise Refused("Work was stopped before dispatch")
            guidance = None
            if group:
                if group_admission is not None:
                    group_admission(db, job)
                guidance = self._group_guidance(db, job, group_guidance)
            db.execute(
                f"INSERT OR REPLACE INTO {metadata} VALUES ('budget_day',?)", (day,)
            )
            db.execute(
                "INSERT INTO reservations (id,bucket,engine,reserved,actual,status,created,family) VALUES (?,?,?,?,NULL,'unknown',?,?)",
                (job["id"], bucket, engine, FLAT_UNKNOWN, store.clock(), family),
            )
            if store.shared_path:
                db.execute(
                    "INSERT INTO shared.model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created,family,member) VALUES (?,?,?,?,?,?,?,?,NULL,'unknown',?,?,?)",
                    (
                        store.account_id,
                        job["id"],
                        job["scope"],
                        job["role"],
                        job["input_revision"] + ":" + job["id"],
                        bucket,
                        engine,
                        FLAT_UNKNOWN,
                        store.clock(),
                        family,
                        member,
                    ),
                )
            if group_accounting is not None:
                group_accounting(db, job)
            return guidance

    async def run(
        self,
        engine,
        job,
        packet,
        valid,
        *,
        klass=None,
        on_text=None,
        on_dispatch=None,
        group_admission=None,
        group_accounting=None,
        group_guidance=None,
    ):
        if job["role"] not in ROLES:
            raise Refused("Unsupported model role")
        group = self._group_options(
            job, group_admission, group_accounting, group_guidance
        )
        klass = klass or json.loads(job["payload"] or "{}").get("class", "new")
        async with self.capacity.slot(klass):
            self.admit()
            if group:
                with self.store.transaction() as db:
                    packet["guidance"] = self._group_preparation(
                        db, job, engine.name, group_admission, group_guidance
                    )
            elif (
                engine.name != "offline"
                and budget_family(job["role"]) == "group_briefing"
            ):
                self.check_budget(job["role"])
            try:
                async with asyncio.timeout(120):
                    # Verifies the exact route before dispatch; the engine re-reads
                    # the listing only when its verification is over 24 hours old.
                    if hasattr(engine, "prepare"):
                        await engine.prepare(packet)
            except ProviderRefused as refusal:
                if job["role"] in BACKGROUND:
                    self.admit()
                    current = valid()
                    # Background no-work retries need a terminal old attempt,
                    # but a route listing is not a model dispatch or a receipt.
                    with self.store.transaction() as db:
                        if group and current:
                            self._group_preparation(
                                db, job, engine.name, group_admission, group_guidance
                            )
                        changed = db.execute(
                            "UPDATE jobs SET state=?,error=?,payload=json_remove(payload,'$.publication_pending') WHERE id=? AND state IN ('queued','claimed')",
                            (
                                "refused" if current else "superseded",
                                str(refusal)
                                if current
                                else "Inputs changed before dispatch",
                                job["id"],
                            ),
                        ).rowcount
                        if (
                            group
                            and changed
                            and current
                            and group_accounting is not None
                        ):
                            group_accounting(db, job)
                    if not changed or not current:
                        raise Refused(
                            "Work was stopped or changed before dispatch"
                        ) from None
                # Outgoing keeps its immediate failure and draft restoration.
                raise
            self.admit()
            if on_dispatch is not None:
                on_dispatch()
            if not valid():
                raise Refused("Inputs changed before dispatch")
            # Q25: the chat-level older run leaves a quarter of the day's budget.
            guidance = self.reserve(
                job,
                engine.name,
                reserve=0.25 if klass == "older" else 0,
                **(
                    {
                        "group_admission": group_admission,
                        "group_accounting": group_accounting,
                        "group_guidance": group_guidance,
                    }
                    if group
                    else {}
                ),
            )
            # A saved guidance change applies from the next call; this call keeps
            # the version it was dispatched with.
            packet["guidance"] = guidance if group else self.guidance()
            actual = None
            usage = {}
            error = None
            refused = None
            result = None
            # ADR 29: background reasoning gets a deadline that fits its cap.
            seconds = (
                engine.attempt_seconds(job["role"])
                if hasattr(engine, "attempt_seconds")
                else 120
            )
            try:
                async with asyncio.timeout(seconds):
                    response = (
                        await engine.generate_stream(job["role"], packet, on_text)
                        if on_text is not None
                        and job["role"] in STREAMED_ROLES
                        and hasattr(engine, "generate_stream")
                        else await engine.generate(job["role"], packet)
                    )
                if not isinstance(response, EngineResponse):
                    response = EngineResponse(
                        response, actual_nanos=0 if engine.name == "offline" else None
                    )
                actual, usage = response.actual_nanos, response.usage
                if self.store.rows(
                    "SELECT id FROM jobs WHERE id=? AND state IN ('cancelled','superseded')",
                    (job["id"],),
                ):
                    raise asyncio.CancelledError()
                result = validate_output(job["role"], response, packet)
            except asyncio.CancelledError as interrupted:
                if receipt := getattr(interrupted, "receipt", None):
                    actual, usage = receipt.actual_nanos, receipt.usage
                error = "Attempt interrupted; use explicit Retry"
                raise
            except InvalidOutput as failure:
                usage["format_failure"] = str(failure)
                error = f"Invalid structured output ({failure}); semantics unassessable"
                if job["role"] in FORMAT_PAUSED:
                    self.on_format(job["role"], False)
            except ProviderRefused as refusal:
                error, refused = "Provider refused before processing", refusal
            except Exception as failure:
                if receipt := getattr(failure, "receipt", None):
                    actual, usage = receipt.actual_nanos, receipt.usage
                error = "Provider unavailable; use explicit Retry"
            finally:
                usage = {
                    **usage,
                    "guidance_version": packet["guidance"]["version"],
                    "defaults_revision": packet["guidance"].get("defaults_revision"),
                }
                with self.store.transaction() as db:
                    family = db.execute(
                        "SELECT family FROM reservations WHERE id=?", (job["id"],)
                    ).fetchone()[0]
                    db.execute(
                        "INSERT OR REPLACE INTO receipts (id,role,model,provider,usage,error,family) VALUES (?,?,?,?,?,?,?)",
                        (
                            job["id"],
                            job["role"],
                            engine.model,
                            engine.provider,
                            json.dumps(usage),
                            error,
                            family,
                        ),
                    )
                    if self.store.shared_path:
                        db.execute(
                            "INSERT OR REPLACE INTO shared.model_receipts (account_id,id,role,model,provider,usage,error,family) VALUES (?,?,?,?,?,?,?,?)",
                            (
                                self.store.account_id,
                                job["id"],
                                job["role"],
                                engine.model,
                                engine.provider,
                                json.dumps(usage),
                                error,
                                family,
                            ),
                        )
                    if refused:
                        # No provider work happened: this attempt counts nothing.
                        db.execute(
                            "UPDATE reservations SET actual=0,status='refused' WHERE id=?",
                            (job["id"],),
                        )
                        if self.store.shared_path:
                            db.execute(
                                "UPDATE shared.model_reservations SET actual=0,status='refused' WHERE account_id=? AND id=?",
                                (self.store.account_id, job["id"]),
                            )
                    elif actual is not None and actual >= 0:
                        db.execute(
                            "UPDATE reservations SET actual=?,status='receipted' WHERE id=?",
                            (actual, job["id"]),
                        )
                        if self.store.shared_path:
                            db.execute(
                                "UPDATE shared.model_reservations SET actual=?,status='receipted' WHERE account_id=? AND id=?",
                                (actual, self.store.account_id, job["id"]),
                            )
                    db.execute(
                        "UPDATE jobs SET state=?,error=? WHERE id=? AND state NOT IN ('cancelled','superseded')",
                        (
                            "refused" if refused else "failed" if error else "done",
                            error,
                            job["id"],
                        ),
                    )
            if refused:
                raise refused
            if error:
                raise Refused(error)
            if job["role"] in FORMAT_PAUSED:
                self.on_format(job["role"], True)
            return result
