"""Optional reply wording for a private chat (ADR 25).

Suggestions are complete messages the owner may choose and edit; they are
never sent, never a profile and never a substitute for the owner's answer.
ADR 49 lets them answer a question, a decision or a request. The owner chooses
what to use; a set may offer one answer stance or several.
"""

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .emotion import LIFETIME
from .models import Model

# Usable non-neutral Recipient Emotion codes. Neutral, unknown and every
# unavailable reason (pending, expired, changed, invalid) produce nothing.
QUALIFYING = frozenset(
    {"positive", "excited", "sad", "worried", "frustrated", "angry", "confused"}
)
Purpose = Literal[
    "acknowledge", "support", "clarify", "celebrate", "accept", "decline", "later"
]
# ADR 49 Q4, Q8: answer stances, any of which may stand alone.
ANSWERS = frozenset({"accept", "decline", "later"})
OPTIONS = 3
EXPIRY = LIFETIME  # The existing 12-hour source-time lifetime.
# ADR 56: independent per-chat automation and a style shared by both paths.
Style = Literal["friendly", "professional", "idioms"]
IDIOMS_WRITER: Literal["mimo", "muse"] = "muse"
# ADR 56 Q19 A: a block without a usable non-neutral emotion suggests on
# usefulness alone only from 0.7, so a borderline answer that closes the
# owner's own question (0.545 in every check) gets no set.
CALM_USEFULNESS = 0.7
# ADR 25: square-bracketed draft text instructs the writer and is never
# message text; Send asks first while a draft still holds some.
BRACKETED = re.compile(r"\[[^\[\]]*[^\s\[\]][^\[\]]*\]")


def bracketed(text):
    return bool(isinstance(text, str) and BRACKETED.search(text))


def split_draft(text, cursor):
    """Split at a UTF-16 caret; an unknown or invalid caret means the end."""
    units = text.encode("utf-16-le")
    position = len(text)
    if cursor is not None and 0 <= cursor * 2 <= len(units):
        try:
            position = len(units[: cursor * 2].decode("utf-16-le"))
        except UnicodeDecodeError:
            pass
    return text[:position], text[position:]


class SuggestionOption(Model):
    purpose: Purpose
    # No character limit (ADR 25 Q1): the request's output-token bound is the
    # only one, and Send refuses what Telegram would.
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Blank suggestion")
        return value


class ResponseSuggestions(Model):
    """Zero to three complete options. An empty list is a valid outcome, so
    lack of suitable wording never becomes a reason to ask again."""

    options: list[SuggestionOption] = Field(max_length=OPTIONS)

    @model_validator(mode="after")
    def distinct(self):
        texts = [option.text.strip() for option in self.options]
        if len(set(texts)) != len(texts):
            raise ValueError("Repeated suggestion")
        return self


class SuggestionsView(Model):
    """What a browser may show for one chat; reading it starts no work."""

    state: Literal["none", "preparing", "waiting", "ready", "failed"] = "none"
    reason: (
        Literal[
            # none
            "off",
            "unavailable",
            "replaced",
            "answered",
            "expired",
            "changed",
            "not_qualifying",
            "empty",
            "cancelled",
            # A durably accepted send awaits Telegram's answer (ADR 26).
            "suspended",
            # preparing / waiting
            "queued",
            "running",
            "budget",
            "paused",
            "retrying",
            # failed
            "format",
            "provider",
            "refused",
            "interrupted",
            "too_large",
        ]
        | None
    ) = None
    attempt: str | None = None
    kind: Literal["automatic", "manual"] = "automatic"
    style: Style = "friendly"
    options: list[SuggestionOption] = Field(default_factory=list)
    # ADR 56 Q12 B: folded on any client, the shown set stays folded on
    # every client until the next set arrives.
    folded: bool = False
    source_time: float | None = None
    expires_at: float | None = None
