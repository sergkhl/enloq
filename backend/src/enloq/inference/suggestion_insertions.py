"""Read an exact completed draft as its insertion, before validating options.

Manual writers sometimes echo the draft around their addition. Only an exact
copy on both sides of the captured caret can be removed; changed wording is
never used to replace authored text. Ordinary fragments stay as supplied.
"""

from ..domain.suggestions import OPTIONS, split_draft
from .base import InvalidOutput


def _word(character):
    return character.isalnum() or character == "_"


def _addition(text, draft, before, after):
    candidate = text.strip()
    if candidate == draft.strip():
        raise InvalidOutput("invalid_insertion")
    # The app supplies separating spaces, so the writer may omit boundary
    # whitespace. Every other authored character must match exactly.
    prefix, suffix = before.rstrip(), after.lstrip()
    if not candidate.startswith(prefix) or not candidate.endswith(suffix):
        return text
    end = len(candidate) - len(suffix)
    if end < len(prefix):
        return text
    addition = candidate[len(prefix) : end]
    # A shared initial letter is not evidence of an echoed draft: draft "I"
    # and the genuine fragment "In addition" must not become "n addition".
    if addition and (
        (prefix and _word(prefix[-1]) and _word(addition[0]))
        or (suffix and _word(suffix[0]) and _word(addition[-1]))
    ):
        return text
    if not addition.strip():
        raise InvalidOutput("invalid_insertion")
    return addition.strip()


def normalize_insertions(content, packet):
    draft = packet.get("draft")
    if (
        not packet.get("manual")
        or not isinstance(draft, str)
        or not draft.strip()
        or not isinstance(content, dict)
        or not isinstance(content.get("options"), list)
        or len(content["options"]) > OPTIONS
    ):
        return content
    before, after = split_draft(draft, packet.get("cursor"))
    return {
        **content,
        "options": [
            {**option, "text": _addition(option["text"], draft, before, after)}
            if isinstance(option, dict) and isinstance(option.get("text"), str)
            else option
            for option in content["options"]
        ],
    }
