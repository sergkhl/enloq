"""Generation-only Response Suggestions typography (ADR 25).

The character map changes prose typography, never source values. Literal
recognition is deliberately conservative: token syntax and bounded supplied
source evidence protect values whose spelling may be meaningful. No text is
read from a store, the network or guidance, and this module is not a validator.
"""

import re

from ..domain.suggestions import OPTION_CHARACTERS, OPTIONS

REPLACEMENTS = {
    "—": "-",
    "–": "-",
    "“": '"',
    "”": '"',
    "«": '"',
    "»": '"',
    "‘": "'",
    "’": "'",
}
_QUOTES = {"“": "”", "«": "»", "‘": "’", '"': '"', "'": "'"}
_TOKEN = re.compile(r"\S+")
_CODE = re.compile(r"`[^`\n]*`")
_ADDRESS = re.compile(
    r"(?:\b[a-zA-Z][a-zA-Z0-9+.-]*://|\bwww\.|"
    r"(?<!\w)(?:~/|\.{1,2}/|/)|\b[a-zA-Z]:[\\/]|"
    r"\b[\w.\-–—‘’“”«»]+[/\\])[^\s`]+"
)
_NUMBER = re.compile(
    r"(?<!\w)(?:[+\-−–—]\s*)?\d[\d.,]*"
    r"(?:\s*[-−–—/:]\s*(?:[+\-−–—]\s*)?\d[\d.,]*)*"
)
_WORD_DASH = re.compile(r"(?<=\w)[—–](?=\w)")
_WORD_APOSTROPHE = re.compile(r"[^\W\d_]+[‘’][^\W\d_]+", re.UNICODE)
_SPACED_VALUE = re.compile(r"(?<!\S)\S+(?:\s+[—–‘’“”«»]\s+\S+)+")
_VALUE_PAIR = re.compile(r"(?<!\S)(?=(\S+\s+[—–‘’“”«»]\s+\S+))")
_NAME_PART = re.compile(r"[^\W\d_][\w‘’]*")
_VALUE_MARKER = re.compile(
    r"\b(?:file name|название файла|имя файла|"
    r"code|name|identifier|key|value|filename|path|range|title|id|"
    r"код|имя|название|идентификатор|ключ|значение|файл|путь|диапазон)\b",
    re.IGNORECASE,
)
_DIRECTIVE = re.compile(
    r"\b(?:use|enter|copy|open|repeat|resend|send|write|read|check|"
    r"используй|используйте|введи|введите|копируй|скопируй|скопируйте|"
    r"открой|откройте|повтори|повторите|пришли|пришлите|отправь|отправьте)"
    r"\s+(?:the\s+)?$",
    re.IGNORECASE,
)
_FILESYSTEM_PREFIX = re.compile(r"(?:/|~/|\.{1,2}/|[a-zA-Z]:[\\/])")
_CONTRACTIONS = frozenset({"s", "t", "re", "ve", "ll", "d", "m"})
_COPULAS = frozenset(
    {
        "is",
        "was",
        "=",
        "called",
        "named",
        "is called",
        "is named",
        "это",
        "равен",
        "называется",
    }
)


def _word_character(character):
    return character.isalnum() or character == "_"


def _token_value(token):
    token = token.strip(".,;:!?()[]{}")
    while len(token) > 1 and _QUOTES.get(token[0]) == token[-1]:
        token = token[1:-1]
    return token


def _marked_value(prefix, *, complete):
    markers = list(_VALUE_MARKER.finditer(prefix))
    if not markers:
        return False
    tail = prefix[markers[-1].end() :].strip().lower()
    # A bare label is evidence only for a complete value at the line's end.
    # "My code failed — it is frustrating" supplies ordinary prose after the
    # pair, whereas "use code abc — def" can supply the complete exact value.
    directive = _DIRECTIVE.search(prefix[: markers[-1].start()])
    return tail in _COPULAS or (not tail and (complete or directive))


def _ends_value(text, end):
    tail = text[end:].partition("\n")[0]
    return not tail.strip(" \t\r.,;:!?)]}“”«»‘’\"'")


def _run_values(run):
    """Keep each overlapping pair as well as a complete exact literal run."""
    yield _token_value(run)
    for match in _VALUE_PAIR.finditer(run):
        yield _token_value(match[1])


def _occurrences(text, literal):
    """Exact whole fragments; a short value must not protect a longer word."""
    start = 0
    while (start := text.find(literal, start)) != -1:
        end = start + len(literal)
        if not (
            start and _word_character(literal[0]) and _word_character(text[start - 1])
        ) and not (
            end < len(text)
            and _word_character(literal[-1])
            and _word_character(text[end])
        ):
            yield start, end
        start += 1


def _quoted_values(text):
    """Quoted copied content is a value; outer prose framing still maps.

    A preceding capitalized name, as in Studio «North Wind», can be part of
    the actual name. Protect that complete source name when copied, rather
    than silently choosing whether its quote characters are decorative.
    """
    for opening, closing in _QUOTES.items():
        start = 0
        while (start := text.find(opening, start)) != -1:
            end = text.find(closing, start + 1, start + OPTION_CHARACTERS + 2)
            if end == -1:
                start += 1
                continue
            inner = text[start + 1 : end]
            if inner and "\n" not in inner:
                yield inner
                window = max(0, start - OPTION_CHARACTERS)
                prefix = re.search(r"([^\W\d_]\w*)\s+$", text[window:start])
                if prefix and prefix[1][0].isupper():
                    yield text[window + prefix.start() : end + 1]
            start = end + 1


def _literal_values(packet, generated):
    """Only the already bounded suggestion target/context, never Chat Memory
    or owner guidance. Incoming and reaction suggestions share this packet.
    """
    values = set()
    quoted = {value for text in generated for value in _quoted_values(text)}

    def keep(value):
        if (
            isinstance(value, str)
            and 1 < len(value) <= OPTION_CHARACTERS
            and any(character in REPLACEMENTS for character in value)
        ):
            values.add(value)

    sources = [*packet.get("messages", []), *packet.get("context", [])]
    if packet.get("manual") and isinstance(packet.get("draft"), str):
        sources.append({"text": packet["draft"]})
    if isinstance(packet.get("direct_reply"), dict):
        sources.append(packet["direct_reply"])
    for source in sources:
        if not isinstance(source, dict):
            continue
        # These fields already identify exact names/values, including spaces.
        for field in ("forwarded_from", "forward_signature"):
            keep(source.get(field))
        for link in source.get("links") or []:
            if isinstance(link, dict):
                keep(link.get("url"))
        for field in ("media_info", "placeholder"):
            info = source.get(field)
            if isinstance(info, dict):
                for name in ("file_name", "title", "site", "performer", "question"):
                    keep(info.get(name))
                for option in info.get("options") or []:
                    keep(option)
        transcript = source.get("transcript")
        texts = [source.get("text"), source.get("image_context")]
        if isinstance(transcript, dict):
            texts.append(transcript.get("text"))
        for text in texts:
            if not isinstance(text, str):
                continue
            for match in _TOKEN.finditer(text):
                token = _token_value(match[0])
                # Internal typography can be an unknown identifier or a name.
                # Ordinary contraction suffixes are punctuation apostrophes;
                # explicit quotes/labels below still protect exact values.
                if any(character in REPLACEMENTS for character in token):
                    contraction = _WORD_APOSTROPHE.fullmatch(token)
                    prefix = text[max(0, match.start() - 60) : match.start()]
                    if (
                        not contraction
                        or re.split("[‘’]", token)[-1].lower() not in _CONTRACTIONS
                        or _marked_value(
                            prefix, complete=_ends_value(text, match.end())
                        )
                    ):
                        keep(token)
            for value in _quoted_values(text):
                keep(value)
            # The output can quote an uncertain value that the source supplied
            # without quote framing. Preserve the copied inner spelling.
            for value in quoted:
                if any(_occurrences(text, value)):
                    keep(value)
            # Labelled values can contain spaces or otherwise ambiguous
            # typography. Do not interpret the label or rewrite its value.
            for line in text.splitlines():
                label, colon, value = line.partition(":")
                if (
                    colon
                    and label.strip()
                    and "/" not in label
                    and not value.startswith(("//", "\\"))
                ):
                    keep(_token_value(value.strip()))
            for match in _SPACED_VALUE.finditer(text):
                prefix = text[max(0, match.start() - 60) : match.start()]
                parts = [
                    _token_value(part)
                    for part in re.split(r"\s+[—–‘’“”«»]\s+", match[0])
                ]
                named = all(
                    _NAME_PART.fullmatch(part) and part[0].isupper() for part in parts
                )
                path = bool(
                    _ADDRESS.fullmatch(parts[0])
                    and (
                        re.search(r"[/\\]|\.\w{1,10}$", parts[-1])
                        or (
                            _FILESYSTEM_PREFIX.match(parts[0])
                            and _DIRECTIVE.search(prefix)
                        )
                    )
                )
                if (
                    named
                    or path
                    or _marked_value(prefix, complete=_ends_value(text, match.end()))
                ):
                    for value in _run_values(match[0]):
                        keep(value)
    return values


def _normalize(text, literals):
    protected = [False] * len(text)

    def protect(start, end):
        protected[start:end] = [True] * (end - start)

    for pattern in (_CODE, _ADDRESS, _NUMBER, _WORD_DASH):
        for match in pattern.finditer(text):
            start, end = match.span()
            if pattern is _ADDRESS and start:
                # A curly/angled quote around an address is prose framing;
                # those same characters inside the address remain literal.
                closing = _QUOTES.get(text[start - 1])
                tail = end
                while tail > start and text[tail - 1] in ".,;:!?)":
                    tail -= 1
                if closing and tail > start and text[tail - 1] == closing:
                    end = tail - 1
            protect(start, end)
    for match in _WORD_APOSTROPHE.finditer(text):
        left, right = re.split("[‘’]", match[0])
        if left[0].isupper() and right.lower() not in _CONTRACTIONS:
            protect(*match.span())  # Names such as O’Neil, even with sparse source.
    for literal in literals:
        for start, end in _occurrences(text, literal):
            protect(start, end)
    return "".join(
        character if protected[index] else REPLACEMENTS.get(character, character)
        for index, character in enumerate(text)
    )


def normalize_suggestions(content, packet):
    """Copy parsed new options before their existing final schema validation.

    Invalid shapes and lengths pass through unchanged to that validator; this
    does not coerce, truncate, filter or retry an option. The output work is
    bounded by the existing three-option/280-character contract. Cached domain
    models and authored drafts never call this function.
    """
    if (
        not isinstance(content, dict)
        or not isinstance(content.get("options"), list)
        or len(content["options"]) > OPTIONS
    ):
        return content
    generated = [
        option["text"]
        for option in content["options"]
        if isinstance(option, dict)
        and isinstance(option.get("text"), str)
        and len(option["text"]) <= OPTION_CHARACTERS
    ]
    literals = _literal_values(packet, generated)
    options = []
    for option in content["options"]:
        if (
            isinstance(option, dict)
            and isinstance(option.get("text"), str)
            and len(option["text"]) <= OPTION_CHARACTERS
        ):
            option = {**option, "text": _normalize(option["text"], literals)}
        options.append(option)
    return {**content, "options": options}
