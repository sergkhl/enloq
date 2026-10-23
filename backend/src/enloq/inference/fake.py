import asyncio

from ..domain.briefing_contract import GROUP_ROLES, validate_briefing
from ..domain.group_filtering import FILTER_ROLES
from ..domain.group_filtering import questions as filter_questions


class FakeEngine:
    """Identity transformations demonstrate mechanics, not semantic quality."""

    name, model, provider = "offline", "authored-fixtures", "local"

    def __init__(self):
        self.calls = []

    async def generate(self, role, packet):
        self.calls.append((role, packet))
        await asyncio.sleep(0.03)
        if role == "group_briefing_image":
            return {
                "image_context": [
                    {
                        "message_id": image["message_id"],
                        "text": "An authored synthetic image.",
                    }
                    for image in packet.get("images", [])
                ]
            }
        if role in GROUP_ROLES:
            topics = [
                topic.model_dump()
                for chunk in packet.get("chunks", [])
                for topic in validate_briefing(
                    {"outcome": chunk["outcome"], "topics": chunk["topics"]},
                    chunk["covered_ids"],
                ).topics
            ]
            for message in packet.get("messages", []):
                text = "\n".join(
                    value
                    for value in (
                        message.get("text", ""),
                        message.get("transcript", {}).get("text", ""),
                        message.get("image_context", ""),
                    )
                    if value.strip()
                )
                if text:
                    topics.append(
                        {
                            "title": "Participant update",
                            "text": text,
                            "citations": [message["id"]],
                            "participant_source_ids": [message["id"]],
                        }
                    )
            return {
                "contract_version": 2,
                "outcome": "briefing" if topics else "no_substance",
                "topics": topics,
            }
        if role == "outgoing_rewrite":
            return {"text": packet["draft"]["text"]}
        if role == "trait_notes":
            # One authored tendency cited from the subject's first three own
            # messages; the service checks the pattern rule.
            own = [
                m["id"] for m in packet["messages"] if m["from"] == packet["writer"]
            ][:3]
            known = {n["text"] for n in packet["notes"]}
            note = "Writes in short authored messages."
            return {
                "add": []
                if len(own) < 3 or note in known
                else [{"text": note, "citations": own, "confidence": 0.8}],
                "remove": [],
            }
        if role == "response_suggestions":
            return {
                "options": [
                    {"purpose": "acknowledge", "text": "Thank you for telling me."}
                ]
            }
        if role == "chat_memory":
            # ADR 55: the person's messages note what they care about, the
            # owner's own what the owner cares about.
            def notes(outgoing):
                return {
                    "add": [
                        {"text": m["text"][:120], "citations": [m["id"]]}
                        for m in packet["messages"]
                        if m["text"].strip()
                        and m["id"] in packet["new_source_ids"]
                        and m["outgoing"] == outgoing
                    ][:10],
                    "remove": [],
                    "outdated": [],
                }

            return {"person": notes(False), "account": notes(True)}
        return {
            "text": "\n".join(
                filter(
                    None,
                    (
                        m["text"] or m.get("transcript", {}).get("text", "")
                        for m in packet["messages"]
                    ),
                )
            ),
            "image_context": [
                {"message_id": identity, "text": "An authored synthetic image."}
                for identity in dict.fromkeys(
                    i["message_id"] for i in packet.get("images", [])
                )
            ],
        }


class FakeDecider:
    """Authored decision answers in the provider's shape; mechanics only."""

    name, model, provider = "offline", "authored-decisions", "local"

    def __init__(self):
        self.calls = []
        # The authored incoming code (None returns no answer), its
        # probability and usefulness (ADR 49), then the authored Reaction
        # Assessment. Not useful by default: under ADR 56's either signal a
        # block suggests only once a test authors an emotion or a yes.
        self.emotion = "unknown"
        self.probability = 0.9
        self.useful = 0.1
        self.assessment = {"emotion": "unknown", "suggestions_useful": False}
        # ADR 53: the latest block's authored sarcasm probability.
        self.sarcasm = 0.0
        self.group_probability = 0.0
        # ADR 49 Q19: each suggestion's label, in order; None labels them all
        # acknowledge, the authored engine's only purpose.
        self.labels = None
        # ADR 55: every trait's authored evidence and level.
        self.trait_evidence = 0.9
        self.trait_level = "2"

    async def generate(self, role, packet):
        self.calls.append((role, packet))
        await asyncio.sleep(0.01)
        if role in FILTER_ROLES:
            return {
                name: {"type": "noul", "noul": self.group_probability}
                for name in filter_questions(role, packet)
            }
        if role == "trait_scales":
            from ..domain import traits

            answers = {}
            for name, question in traits.questions(packet["subject"]).items():
                if question["type"] == "noul":
                    answers[name] = {"type": "noul", "noul": self.trait_evidence}
                elif question["type"] == "score":
                    levels = [str(i) for i in range(len(question["criteria"]))]
                    answers[name] = {
                        "type": "score",
                        "score": float(self.trait_level),
                        "probabilities": {
                            level: 0.8 if level == self.trait_level else 0.05
                            for level in levels
                        },
                        "confidence": 0.8,
                    }
                else:
                    code = next(iter(question["criteria"]))
                    answers[name] = {
                        "type": "choice",
                        "choice": code,
                        "probabilities": {
                            c: 0.8 if c == code else 0.02 for c in question["criteria"]
                        },
                        "confidence": 0.8,
                    }
            return answers
        if role == "suggestion_labels":
            labels = self.labels or ["acknowledge"] * packet["replies"]
            return {
                f"purpose_{index}": label_choice(label)
                for index, label in enumerate(labels[: packet["replies"]], 1)
            }
        if role == "reaction_assessment":
            code = self.assessment["emotion"]
            useful = 0.9 if self.assessment["suggestions_useful"] else 0.1
            return {
                "emotion": choice(code, self.probability),
                "suggestions_useful": {"type": "noul", "noul": useful},
            }
        if self.emotion is None:
            return {}
        return {
            "emotion": choice(self.emotion, self.probability),
            "suggestions_useful": {"type": "noul", "noul": self.useful},
            "sarcasm": {"type": "noul", "noul": self.sarcasm},
        }


def choice(code, probability):
    from ..domain.emotion import CODES

    rest = (1 - probability) / (len(CODES) - 1)
    return {
        "type": "choice",
        "choice": code,
        "probabilities": {c: probability if c == code else rest for c in CODES},
        "confidence": probability,
    }


def label_choice(purpose, probability=0.9):
    """One suggestion label in the provider's choice shape (ADR 49 Q19)."""
    from .guidance import LABEL_CRITERIA

    rest = (1 - probability) / (len(LABEL_CRITERIA) - 1)
    return {
        "type": "choice",
        "choice": purpose,
        "probabilities": {
            p: probability if p == purpose else rest for p in LABEL_CRITERIA
        },
        "confidence": probability,
    }
