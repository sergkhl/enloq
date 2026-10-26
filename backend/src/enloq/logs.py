"""Logs allow fixed event codes only. Library messages, arguments and tracebacks
are suppressed; each line carries only safe metadata: UTC time, level, logger
name and the exception class, which are code identifiers, never content."""

import json
import logging
import math
import re
import time

EVENTS = frozenset(
    {
        # The scheduler tick failed; jobs keep their durable state and retry.
        "dispatch_unavailable",
        # Expiring week-old Join Requests failed; the next minute tries again.
        "request_expiry_unavailable",
        # Reconnect recovery paused: a wait exceeded its allowance or incoming
        # handlers had not reached their durable boundary; cursors are kept.
        "mtproto_recovery_paused",
        # An unexpected failure stopped connection recovery; Reconnect is needed.
        "mtproto_reconnect_stopped",
        # Revoked authorization ends recovery and asks for sign-in.
        "mtproto_sign_in_required",
        # Telegram does not know the session's authorization key; the client
        # stays disconnected until Reconnect.
        "mtproto_auth_key_unknown",
        # Telegram reported a gap too long to replay; sync restarted from the
        # latest state and reopened the seven-day catch-up of recent chats.
        "sync_gap_restarted",
        # A revoked or changed identity stopped the account's sync.
        "sync_sign_in_required",
        # A send's hand-off record could not be saved; it stays unknown.
        "send_evidence_unsaved",
        # Check delivery could not reach Telegram; the send stays unknown.
        "delivery_check_unavailable",
        # A chat's send queue stopped unexpectedly; its submissions keep their
        # durable state and the next queue pass tries again.
        "delivery_queue_unavailable",
        # Deletion's queue stopped unexpectedly; durable outcomes remain and
        # recovery checks exact IDs without automatically repeating mutation.
        "deletion_queue_unavailable",
        "deletion_effects_unavailable",
        # An acknowledged group deletion could not finish its local sync effects;
        # durable evidence retains the exact sources for guarded recovery.
        "group_effects_incomplete",
        # Preparing response suggestions failed; the published emotion is
        # kept and no suggestion work was queued for that source.
        "suggestion_schedule_unavailable",
        "suggestion_decision",
        # Queueing Emotion Classification failed; the ready incoming result is
        # kept and that block's emotion stays unavailable.
        "emotion_schedule_unavailable",
        # Queueing the decision for a block held under the character threshold
        # failed (ADR 49 Q3); its rewrite still waits and its emotion stays
        # unavailable until the block changes.
        "held_decision_unavailable",
        # A calming decision could not be made (ADR 53): the block's rewrite
        # runs uncalmed, or a held block keeps its originals.
        "calming_decision_unavailable",
        # Queueing a held block's calm rewrite failed (ADR 53 Q6); its
        # published decision is kept and the block keeps its originals.
        "calming_release_unavailable",
        # Recording whether the decision model's route answered failed (ADR 49
        # Q14); the call's own outcome is kept and the next call records again.
        "decision_state_unavailable",
        # A post to the Owner Account's Saved Messages failed; it is reported
        # and nothing is sent again on its own (ADR 43 Q4).
        "saved_messages_post_failed",
        # Replacing the Saved Build message's file failed; the previous build
        # stays and nothing is sent again on its own (ADR 50 Q5).
        "saved_build_edit_failed",
        # Pinning a newly posted Saved Build failed; it is posted, and the next
        # publish pins it again (ADR 50 Q5).
        "saved_build_pin_failed",
    }
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")
_ATTEMPT = re.compile(r"[a-f0-9]{32}")
_REASONS = frozenset(
    {
        "requested",
        "published",
        "failed",
        "cancelled",
        "automatic_off",
        "manual_owns_area",
        "schedule_unavailable",
        "explicit_or_older_processing",
        "newer_reaction",
        "automatic_signal",
        "no_current_qualifying_signal",
        "chat_automatic_off",
        "source_replaced",
        "unchanged_source",
        "scheduled",
        "input_too_large",
        "waiting_paused",
        "waiting_budget",
        "no_qualifying_reaction_signal",
        # ADR 58 Q5: a chat's reminder set owns its area, and one that still
        # fits, or failed on unchanged input, is never prepared again.
        "reminder_owns_area",
        "reminder_unchanged",
    }
)


def suggestion_fields(record):
    """An allowlist, independent of caller formatting, never arbitrary content."""
    raw = getattr(record, "suggestion_decision", {})
    if not isinstance(raw, dict):
        return {}
    safe = {}
    choices = {
        "trigger": {"manual", "automatic"},
        "reason": _REASONS,
        "source": {"incoming", "reaction", "prepare", "refresh", "open", "press"},
        "style": {"friendly", "professional", "idioms"},
        "emotion": {
            "neutral",
            "positive",
            "excited",
            "sad",
            "worried",
            "frustrated",
            "angry",
            "confused",
            "unknown",
        },
        "outcome": {
            "format",
            "provider",
            "refused",
            "interrupted",
            "too_large",
            "changed",
            "replaced",
            "answered",
            "expired",
            "ineligible",
        },
    }
    for field, values in choices.items():
        value = raw.get(field)
        if isinstance(value, str) and value in values:
            safe[field] = value
    attempt = raw.get("attempt")
    if isinstance(attempt, str) and _ATTEMPT.fullmatch(attempt):
        safe["attempt"] = attempt
    for field in ("probability", "usefulness", "threshold", "emotion_threshold"):
        value = raw.get(field)
        if type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 1:
            safe[field] = value
    count = raw.get("options")
    if type(count) is int and 0 <= count <= 3:
        safe["options"] = count
    return safe


def _identifier(value):
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else "-"


class SafeFormatter(logging.Formatter):
    def format(self, record):
        app = record.name == "enloq" or record.name.startswith("enloq.")
        event = (
            record.msg
            if app
            and isinstance(record.msg, str)
            and record.msg in EVENTS
            and not record.args
            else "event_redacted"
        )
        line = " ".join(
            (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                _identifier(record.levelname),
                _identifier(record.name),
                event,
            )
        )
        if record.exc_info and record.exc_info[0] is not None:
            line += " error=" + _identifier(record.exc_info[0].__name__)
        if event == "suggestion_decision" and record.name == "enloq.suggestions":
            line += " " + json.dumps(suggestion_fields(record), sort_keys=True)
        return line


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.WARNING)
    logging.getLogger("enloq.suggestions").setLevel(logging.DEBUG)
    # Python warnings go through the same redaction instead of raw stderr.
    logging.captureWarnings(True)
    for name in ("telethon", "httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
