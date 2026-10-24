"""Optional reply wording for the latest qualifying incoming block (ADR 25).

Suggestions are bounded message wording the owner may insert and edit; they
are never sent, never a profile and never a substitute for the owner's answer.
ADR 49 lets them answer a question, a decision or a request, as contrasting
stances only, so the owner's choice supplies the intent.
"""

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

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
# ADR 49 Q4, Q8: answer stances. A set holding a yes or a no holds another
# stance beside it, so the owner's choice supplies the intent; since Q20 a
# deferral may stand alone.
ANSWERS = frozenset({"accept", "decline", "later"})
OPTIONS = 3
OPTION_CHARACTERS = 280
EXPIRY = LIFETIME  # The existing 12-hour source-time lifetime.
# ADR 56: independent per-chat automation and a style shared by both paths.
Style = Literal["friendly", "professional", "idioms"]
IDIOMS_WRITER: Literal["mimo", "muse"] = "muse"
# ADR 56 Q19 A: a block without a usable non-neutral emotion suggests on
# usefulness alone only from 0.7, so a borderline answer that closes the
# owner's own question (0.545 in every check) gets no set.
CALM_USEFULNESS = 0.7


class SuggestionOption(Model):
    purpose: Purpose
    text: str = Field(min_length=1, max_length=OPTION_CHARACTERS)

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

    @model_validator(mode="after")
    def contrasting(self, info: ValidationInfo):
        """Never one-sided (ADR 49 Q4): answers come as different stances.
        Since Q19 the decision model's labels decide this, so the writer's own
        labels are read with ``context={"unlabelled": True}`` and not judged."""
        if (info.context or {}).get("unlabelled") or (info.context or {}).get(
            "draft_intent"
        ):
            return self
        stances = {option.purpose for option in self.options} & ANSWERS
        # Q20: deferring commits to nothing, so later may stand alone; a yes or
        # a no needs another stance beside it.
        if len(stances) == 1 and stances != {"later"}:
            raise ValueError("One answer stance only")
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
