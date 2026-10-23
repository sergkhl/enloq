"""Traits (ADR 55): a chat's person's, or the account holder's, shown only to
the owner, who can set, clear and edit them."""

from typing import Literal

from pydantic import Field

from ..domain.models import Model


class TraitOption(Model):
    value: str
    label: str


class TraitScale(Model):
    """One trait of the starter list: its value, the decision model's or the
    owner's, and whether the latest judgment still found its evidence."""

    trait: str
    title: str
    value: str | None
    label: str | None
    probability: float | None
    judged: float | None
    source: Literal["model", "owner"] | None
    current: bool
    options: list[TraitOption]


class TraitNote(Model):
    id: str
    text: str
    confidence: float | None
    source: Literal["model", "owner"]
    created: float
    citations: list[int]


class Habits(Model):
    """Counted from the cached month without a model."""

    messages: int
    reply_seconds: int | None
    active_from: int = Field(ge=0, le=23)
    active_to: int = Field(ge=0, le=23)
    voice_share: float
    starts_share: float | None


class TraitsView(Model):
    revision: int
    enabled: bool
    scales: list[TraitScale]
    notes: list[TraitNote]
    habits: Habits | None
    error: str | None


class ScaleEdit(Model):
    """The owner's own value for a trait, or None to clear it."""

    revision: int = Field(ge=0)
    value: str | None = Field(default=None, max_length=32)
