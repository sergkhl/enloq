"""ADR 34 group output, separate from private rewrites and generated memory.

The source planner owns frozen native identities and currentness. This module
validates the output shape and its exact citation allowlist; it cannot establish
whether a model's summary is faithful to those sources.
"""

from typing import Annotated, Literal

from pydantic import (
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from .models import Model

NO_SUBSTANCE = "No substantive updates in the covered messages."
MAX_TEXT = 65536
GROUP_ROLES = frozenset(
    {"group_briefing_image", "group_briefing_chunk", "group_briefing"}
)
NativeID = Annotated[int, Field(strict=True, gt=0)]


class BriefingError(ValueError):
    """A content-free contract code safe for receipts and factual status."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class BriefingModel(Model):
    model_config = ConfigDict(
        extra="forbid", strict=True, revalidate_instances="always"
    )


def meaningful(value):
    if not value.strip():
        raise PydanticCustomError("briefing_text", "Text is blank")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise PydanticCustomError("briefing_text", "Text contains invalid controls")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise PydanticCustomError(
            "briefing_text", "Text is not valid Unicode"
        ) from None
    return value


class BriefingImageContext(BriefingModel):
    message_id: NativeID
    text: str = Field(min_length=1, max_length=2000)

    _text = field_validator("text")(meaningful)


class BriefingImageOutput(BriefingModel):
    image_context: list[BriefingImageContext] = Field(max_length=4)


class BriefingTopic(BriefingModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=8000)
    citations: list[NativeID] = Field(min_length=1)
    participant_source_ids: list[NativeID] = Field(default_factory=list)

    _text = field_validator("text")(meaningful)

    @field_validator("title")
    @classmethod
    def title_text(cls, value):
        return meaningful(value) if value is not None else value

    @field_validator("citations", "participant_source_ids")
    @classmethod
    def unique(cls, values):
        if len(values) != len(set(values)):
            raise PydanticCustomError("briefing_citations", "Citations repeat")
        return values


class BriefingOutput(BriefingModel):
    contract_version: Literal[1, 2] = 1
    outcome: Literal["briefing", "no_substance"]
    topics: list[BriefingTopic]

    @model_validator(mode="after")
    def substantive(self):
        if (self.outcome == "briefing") != bool(self.topics):
            raise PydanticCustomError("briefing_outcome", "Outcome and topics disagree")
        if self.contract_version == 2 and any(
            not topic.title or not topic.participant_source_ids for topic in self.topics
        ):
            raise PydanticCustomError("briefing_schema", "Topic metadata is missing")
        if (
            len(
                "\n\n".join(
                    (topic.title or "") + topic.text.strip() for topic in self.topics
                )
            )
            > MAX_TEXT
        ):
            raise PydanticCustomError("briefing_text", "Briefing exceeds text bound")
        return self

    @property
    def text(self):
        return render_briefing(self)


def parse_output(model, output):
    try:
        return model.model_validate(output)
    except ValidationError as failure:
        codes = {
            error["type"]
            for error in failure.errors(
                include_input=False, include_context=False, include_url=False
            )
        }
        for code in ("briefing_text", "briefing_outcome", "briefing_citations"):
            if code in codes:
                raise BriefingError(code) from None
        raise BriefingError("briefing_schema") from None
    except (TypeError, UnicodeError):
        raise BriefingError("briefing_schema") from None


def render_briefing(output):
    """Plain topic paragraphs, or the exact adopted no-substance wording."""
    result = parse_output(BriefingOutput, output)
    return (
        NO_SUBSTANCE
        if result.outcome == "no_substance"
        else "\n\n".join(
            ((topic.title.strip() + "\n") if topic.title else "") + topic.text.strip()
            for topic in result.topics
        )
    )


def native_ids(values, *, code="briefing_coverage"):
    """Trusted source allowlists are still strict, positive and unique."""
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise BriefingError(code)
    if any(type(value) is not int or value <= 0 for value in values):
        raise BriefingError(code)
    identities = set(values)
    if len(identities) != len(values):
        raise BriefingError(code)
    return identities


def validate_briefing(output, covered_ids):
    """Validate one chunk/final against its actual target or covered originals."""
    covered = native_ids(covered_ids)
    result = parse_output(BriefingOutput, output)
    if any(not set(topic.citations) <= covered for topic in result.topics):
        raise BriefingError("briefing_citations")
    if any(
        not set(topic.participant_source_ids) <= set(topic.citations)
        for topic in result.topics
    ):
        raise BriefingError("briefing_citations")
    return result


def covered_source_ids(packet):
    """Verify original targets plus individually validated reused chunk coverage.

    Supporting Context is deliberately absent from this membership calculation.
    A final can cite raw sources represented by a validated reusable chunk even
    when those originals are not repeated in its bounded provider input.
    """
    messages, chunks = packet.get("messages", []), packet.get("chunks", [])
    if not isinstance(messages, list) or not isinstance(chunks, list):
        raise BriefingError("briefing_coverage")
    try:
        actual = native_ids([message["id"] for message in messages])
        for chunk in chunks:
            sources = native_ids(chunk["covered_ids"])
            validate_briefing(
                {"outcome": chunk["outcome"], "topics": chunk["topics"]}, sources
            )
            actual |= sources
    except (KeyError, TypeError):
        raise BriefingError("briefing_coverage") from None
    covered = native_ids(packet.get("covered_ids", sorted(actual)))
    if covered != actual:
        raise BriefingError("briefing_coverage")
    return covered


def attached_image_ids(packet):
    try:
        values = [image["message_id"] for image in packet.get("images", [])]
    except (KeyError, TypeError):
        raise BriefingError("briefing_images") from None
    if len(values) > 4:
        raise BriefingError("briefing_images")
    return native_ids(values, code="briefing_images")


def validate_group_output(role, output, packet):
    if role == "group_briefing_image":
        result = parse_output(BriefingImageOutput, output)
        expected = attached_image_ids(packet)
        returned = [image.message_id for image in result.image_context]
        if native_ids(returned, code="briefing_images") != expected:
            raise BriefingError("briefing_images")
        return result
    if role in {"group_briefing_chunk", "group_briefing"}:
        result = validate_briefing(output, covered_source_ids(packet))
        if packet.get("contract_version") == 2 and result.contract_version != 2:
            raise BriefingError("briefing_schema")
        if result.contract_version == 2:
            result.topics.sort(key=lambda topic: min(topic.citations))
        return result
    raise BriefingError("briefing_role")
