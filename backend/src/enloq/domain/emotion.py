"""A tentative, short-lived reading of communication, never a person profile."""

from typing import Literal, get_args

from pydantic import Field

from .models import Model

EmotionCode = Literal[
    "neutral",
    "positive",
    "excited",
    "sad",
    "worried",
    "frustrated",
    "angry",
    "confused",
    "unknown",
]
CODES = get_args(EmotionCode)
LIFETIME = 12 * 3600
# ADR 30 Q6: a selected option below this probability is stored as unknown.
FLOOR = 0.35
# An implementation default, like suggestions' usefulness: a block is
# sarcastic at or above this probability (ADR 53).
SARCASTIC = 0.5


class EmotionDecision(Model):
    """One validated Emotion Classification answer. The probabilities and the
    model's self-reported confidence are kept for diagnosis, never displayed."""

    code: EmotionCode
    choice: EmotionCode
    probability: float = Field(ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    probabilities: dict[EmotionCode, float]


class Classification(Model):
    """One validated incoming decision request (ADR 49 Q2): the emotion and
    whether optional reply suggestions are useful now. The answer only gates
    suggestions; it is never displayed."""

    decision: EmotionDecision
    suggestions_useful: bool
    usefulness: float = Field(ge=0, le=1)
    # ADR 53: the probability that the latest block is sarcastic, told to its
    # calmed rewrite; None when the answer was not given.
    sarcasm: float | None = Field(default=None, ge=0, le=1)
    hostility: float | None = Field(default=None, ge=0, le=1)

    @property
    def hostile(self):
        return self.hostility is not None and self.hostility >= 0.5

    @property
    def code(self):
        return self.decision.code

    @property
    def sarcastic(self):
        return self.sarcasm is not None and self.sarcasm >= SARCASTIC


class EmotionView(Model):
    code: EmotionCode | None = None
    reason: Literal[
        "available",
        "uncertain",
        "pending",
        "expired",
        "changed",
        "invalid",
        "unavailable",
    ] = "unavailable"
    source_time: float | None = None
    expires_at: float | None = None
    # The current source: the latest incoming block, or a correspondent's
    # reaction to one exact message (ADR 26).
    source: Literal["incoming", "reaction"] = "incoming"
    reaction: str | None = Field(default=None, max_length=32)
    target_id: int | None = Field(default=None, gt=0)
