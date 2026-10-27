"""ADR 59's Ask this group: a member's question about one group, answered from
that group's cached originals with checked citations, or not answered.

Retrieval fuses keyword and vector rankings; the writer may cite only what
retrieval found; the citation check (ADR 34) then confirms each statement.
"""

import math
import re
import unicodedata
from typing import Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from .briefing_contract import BriefingModel, NativeID, meaningful
from .citation_check import ANSWER_CHECK_ROLE
from .models import Model, Refused

QUESTION_ROLE = "group_question"
EMBED_ROLE = "group_embeddings"
RERANK_ROLE = "group_rerank"
ROLES = frozenset({QUESTION_ROLE, EMBED_ROLE, RERANK_ROLE, ANSWER_CHECK_ROLE})
MAX_QUESTION = 500
# ADR 59 Q8: one writer request and at most four check requests.
MAX_STATEMENTS = 4
# How many candidates each stage keeps: keyword and vector rankings, their
# fusion, and what the writer reads.
LEXICAL, DENSE, FUSED, WRITTEN = 50, 50, 40, 25
# Reciprocal rank fusion's usual constant.
RRF_K = 60
WORD = re.compile(r"[^\W_]{3,}", re.UNICODE)


class AnswerError(ValueError):
    """A content-free contract code for receipts."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class AnswerStatement(BriefingModel):
    text: str = Field(min_length=1, max_length=600)
    citations: list[NativeID] = Field(min_length=1, max_length=5)

    _text = field_validator("text")(meaningful)

    @field_validator("citations")
    @classmethod
    def unique(cls, values):
        if len(values) != len(set(values)):
            raise PydanticCustomError("answer_citations", "Citations repeat")
        return values


class GroupAnswer(BriefingModel):
    """No statement means the messages do not answer the question. A writer
    that could say "not found" beside statements contradicted itself, as MiMo
    did on open questions in the Ask check."""

    statements: list[AnswerStatement] = Field(max_length=MAX_STATEMENTS)

    @property
    def outcome(self):
        return "answer" if self.statements else "not_found"


def validate_answer(output, retrieved_ids):
    """The writer's answer, citing only the messages retrieval gave it."""
    try:
        answer = GroupAnswer.model_validate(output)
    except ValidationError:
        raise AnswerError("answer_schema") from None
    except (TypeError, UnicodeError):
        raise AnswerError("answer_schema") from None
    allowed = set(retrieved_ids)
    if any(not set(s.citations) <= allowed for s in answer.statements):
        raise AnswerError("answer_citations")
    return answer


def question_text(value):
    """A member's question: one bounded line of real text."""
    if not isinstance(value, str):
        raise Refused("A question is required")
    value = " ".join(value.split())
    if not value or len(value) > MAX_QUESTION:
        raise Refused(f"A question has 1 to {MAX_QUESTION} characters")
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in value):
        raise Refused("A question cannot contain control characters")
    return value


def searchable(projection):
    """What a search and the writer read of one original: its text or
    caption, cached transcript and image meaning."""
    parts = [projection.get("text") or ""]
    transcript = projection.get("transcript")
    if isinstance(transcript, dict) and transcript.get("text"):
        parts.append(transcript["text"])
    if projection.get("image_context"):
        parts.append(projection["image_context"])
    return "\n".join(p.strip() for p in parts if p and p.strip())


def match_expression(question):
    """An FTS5 trigram query: each word of three or more letters or digits,
    quoted, any of them. A word of six or more loses its last two letters, so
    "квартиру" also finds "квартира" and "watering" "waterings". None when the
    question has no such word."""
    words = list(
        dict.fromkeys(
            w.lower()[:-2] if len(w) >= 6 else w.lower() for w in WORD.findall(question)
        )
    )[:24]
    if not words:
        return None
    return " OR ".join('"' + w.replace('"', '""') + '"' for w in words)


def fuse(*rankings, k=RRF_K):
    """Reciprocal rank fusion of ranked identity lists, best first."""
    scores = {}
    for ranking in rankings:
        for rank, identity in enumerate(ranking):
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda identity: (-scores[identity], identity))


def normalized(vector):
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or norm == 0:
        raise AnswerError("embedding_vector")
    return [v / norm for v in vector]


def nearest(query, vectors, limit):
    """Identities by cosine similarity to the normalized query, best first."""
    scored = [
        (sum(a * b for a, b in zip(query, vector, strict=True)), identity)
        for identity, vector in vectors.items()
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [identity for _, identity in scored[:limit]]


class AskStatement(Model):
    text: str
    # Citations the check kept; those it found unsupported (ADR 59 Q6).
    citations: list[int]
    unsupported: list[int]
    supported: bool


class AskRange(Model):
    """The searched calendar days, first opt-in day or the cache's oldest
    to today, in the server's timezone."""

    start: str
    end: str


class AskView(Model):
    status: Literal["answer", "not_found", "unchecked"]
    searched: AskRange
    statements: list[AskStatement] = Field(default_factory=list)
