"""Native reaction evidence (ADR 26): who reacted with what and when.

Snapshots are message metadata; only the fixed set below can trigger
assessment. ADR 30 lets an emoji's conventional meaning inform the assessed
emotion only; suggestions never read it as agreement, a commitment or a fact.
"""

from pydantic import Field

from .emotion import EmotionCode, EmotionDecision
from .models import Model, Reaction, ReactionActor, ReactionChip, ReactionSnapshot

__all__ = [
    "TRIGGERS",
    "AssessmentResult",
    "Reaction",
    "ReactionActor",
    "ReactionChip",
    "ReactionSnapshot",
]

# Correspondent additions of these can trigger Reaction Assessment,
# independently of the account's currently selectable palette.
TRIGGERS = frozenset({"👍", "🤝", "👌", "👎"})
# A confirmed change waits for this many quiet seconds before assessment.
QUIET = 30


class AssessmentResult(Model):
    """A tentative reading of one reaction in context, never a profile: the
    expressed emotion and whether optional reply suggestions are useful now."""

    emotion: EmotionCode
    suggestions_useful: bool
    # ADR 30: the decision behind both answers, kept for diagnosis only.
    decision: EmotionDecision | None = None
    usefulness: float | None = Field(default=None, ge=0, le=1)
