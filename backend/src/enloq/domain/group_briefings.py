"""Durable group briefing evidence, separate from private rewriting state."""

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from .briefing_contract import BriefingOutput
from .citation_check import CitationCheck
from .models import ChatKey, Model, Refused

NativeID = Annotated[int, Field(strict=True, gt=0)]


class BriefingSpeechLimit(Refused):
    """Only fresh speech is exhausted; prepared originals can still be used."""


class BriefingOmission(Model):
    message_id: NativeID
    reason: str = Field(min_length=1, max_length=256)


class BriefingScope(ChatKey):
    day: str

    @field_validator("peer")
    @classmethod
    def group_peer(cls, value):
        if not value.startswith(("chat:", "channel:")):
            raise ValueError("Briefings require a typed group peer")
        return value

    @field_validator("day")
    @classmethod
    def calendar_day(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("A canonical calendar day is required")
        return value


class BriefingIntakeCoverage(Model):
    """What a bounded native scan proved, without invented missing IDs."""

    native_omitted: int = Field(default=0, strict=True, ge=0)
    intake_complete: bool = False
    range_complete: bool = False
    before: int = Field(default=0, strict=True, ge=0)
    top: int = Field(default=0, strict=True, ge=0)
    count: int = Field(default=0, strict=True, ge=0)
    cutoff: float | None = None
    oldest: float | None = None

    @property
    def partial(self):
        return bool(self.native_omitted or not self.range_complete)


class FrozenBriefing(BriefingScope):
    start: float
    end: float
    cutoff_id: NativeID
    cutoff_date: float
    intended_ids: list[NativeID]
    source_dates: dict[int, float]
    packets: list[dict[str, Any]]
    omissions: list[BriefingOmission] = Field(default_factory=list)
    access_revision: int = Field(strict=True, ge=0)
    epoch: int = Field(strict=True, ge=0)
    gap: bool
    intake_coverage: BriefingIntakeCoverage = Field(
        default_factory=BriefingIntakeCoverage
    )
    guidance_hash: str = Field(min_length=1, max_length=256)
    route: str = Field(min_length=1, max_length=256)
    bot_samples: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("intended_ids")
    @classmethod
    def unique_ids(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("Exact distinct intended sources are required")
        return value


class BriefingAttempt(Model):
    id: str
    snapshot: FrozenBriefing
    state: Literal["active", "failed", "interrupted", "completed", "superseded"]
    automatic: bool
    batch_id: str
    retry_of: str | None = None
    created: float
    updated: float
    error: str | None = None


class BriefingChunk(Model):
    id: str
    attempt_id: str
    fingerprint: str
    snapshot: FrozenBriefing
    covered_ids: list[NativeID]
    output: BriefingOutput
    created: float
    valid: bool = True
    filter_state: str | None = None


class BriefingResult(Model):
    id: str
    attempt_id: str
    snapshot: FrozenBriefing
    covered_ids: list[NativeID]
    chunk_ids: list[str]
    omissions: list[BriefingOmission]
    covered_start: float
    covered_end: float
    output: BriefingOutput
    created: float
    valid: bool = True
    excluded_ids: list[NativeID] = Field(default_factory=list)
    ad_ids: list[NativeID] = Field(default_factory=list)
    local_outcome: Literal["ads_skipped", "bots_skipped"] | None = None
    filter_state: str | None = None
    filter_evidence: list[dict[str, Any]] = Field(default_factory=list)
    # ADR 34's citation check; None for finals published before it.
    citation_check: CitationCheck | None = None

    @property
    def text(self):
        return self.output.text


class BriefingDay(BriefingScope):
    start: float
    end: float
    source_ids: list[NativeID]
    covered_ids: list[NativeID]
    uncovered_ids: list[NativeID]
    omissions: list[BriefingOmission]
    gap: bool
    intake_coverage: BriefingIntakeCoverage
    partial: bool
    cached: bool = False
    final: BriefingResult | None = None
    attempt: BriefingAttempt | None = None


class BriefingRunState(ChatKey):
    last_auto_started: float | None = None
    active_batch: str | None = None


class BriefingBatch(ChatKey):
    id: str
    automatic: bool
    started: float
    finished: float | None = None
    state: Literal["active", "completed", "failed", "interrupted"] = "active"
    requests: int = Field(default=0, strict=True, ge=0, le=5)
    classifier_requests: int = Field(default=0, strict=True, ge=0, le=12)
    # ADR 34 citation check Q6: twelve check requests beside the others.
    check_requests: int = Field(default=0, strict=True, ge=0, le=12)
    source_ids: list[NativeID] = Field(default_factory=list)
    speech_seconds: float = Field(default=0, ge=0, le=1200)
    dispatch_tokens: dict[str, str] = Field(default_factory=dict)
