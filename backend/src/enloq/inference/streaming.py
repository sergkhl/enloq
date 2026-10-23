"""Bounded SSE decoding and a provisional root-level JSON text projection."""

import asyncio
import codecs
import json
import math
import re
from decimal import Decimal, InvalidOperation

from ..domain.briefing_contract import MAX_TEXT
from ..domain.models import Unavailable
from .base import EngineResponse

USAGE = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "cost_details",
    "is_byok",
    "prompt_tokens_details",
    "completion_tokens_details",
}


def nanos(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return math.ceil(number * 1_000_000_000)


def cost_nanos(usage):
    """The larger of the charged and the upstream inference cost. Xiaomi's
    receipts report a zero ``cost`` beside a nonzero upstream cost, so the Daily
    Budget never counts less than the provider's inference charge (ADR 29)."""
    details = usage.get("cost_details")
    upstream = (
        details.get("upstream_inference_cost") if isinstance(details, dict) else None
    )
    amounts = [n for n in (nanos(usage.get("cost")), nanos(upstream)) if n is not None]
    return max(amounts) if amounts else None


class StreamFailure(Unavailable):
    def __init__(self, receipt):
        super().__init__("Provider stream interrupted; use explicit Retry")
        self.receipt = receipt


class TextPreview:
    """Only the root `text` string, never image descriptions or JSON framing.

    Escapes and UTF-16 surrogate pairs are held until complete. The completed
    object still needs normal strict validation; this is only a display parser.
    """

    def __init__(self, field="text"):
        self.field = field
        self.buffer, self.text = "", ""
        self.cursor = None
        self.done = False

    def feed(self, fragment):
        self.buffer += fragment
        if self.cursor is None:
            decoder = json.JSONDecoder()
            data = self.buffer.lstrip()
            if not data.startswith("{"):
                return None
            index = len(self.buffer) - len(data) + 1
            try:
                while True:
                    while index < len(self.buffer) and self.buffer[index].isspace():
                        index += 1
                    key, index = decoder.raw_decode(self.buffer, index)
                    while index < len(self.buffer) and self.buffer[index].isspace():
                        index += 1
                    if not isinstance(key, str) or self.buffer[index] != ":":
                        return None
                    index += 1
                    while index < len(self.buffer) and self.buffer[index].isspace():
                        index += 1
                    if key == self.field:
                        if self.buffer[index] != '"':
                            return None
                        self.cursor = index + 1
                        break
                    _, index = decoder.raw_decode(self.buffer, index)
                    while index < len(self.buffer) and self.buffer[index].isspace():
                        index += 1
                    if self.buffer[index] != ",":
                        return None
                    index += 1
            except (ValueError, IndexError):
                return None
        value = []
        while not self.done and self.cursor < len(self.buffer):
            index = self.cursor
            char = self.buffer[index]
            if char == '"':
                self.done = True
                break
            if char == "\\":
                if index + 1 >= len(self.buffer):
                    break
                size = 6 if self.buffer[index + 1] == "u" else 2
                if index + size > len(self.buffer):
                    break
                char = json.loads('"' + self.buffer[index : index + size] + '"')
                if 0xD800 <= ord(char) <= 0xDBFF:
                    if index + 12 > len(self.buffer):
                        break
                    pair = self.buffer[index : index + 12]
                    char = json.loads('"' + pair + '"')
                    if len(char) != 1 or 0xD800 <= ord(char) <= 0xDFFF:
                        raise ValueError("Invalid Unicode pair")
                    size = 12
                self.cursor += size
            else:
                self.cursor += 1
            # A raw line break or tab inside the string is a common model
            # artifact with an exact meaning; other raw controls stay invalid.
            if (
                ord(char) < 32
                and self.buffer[index] != "\\"
                and char not in "\n\r\t"
                or 0xD800 <= ord(char) <= 0xDFFF
            ):
                raise ValueError("Invalid JSON string")
            value.append(char)
        self.text += "".join(value)
        if len(self.text) > 65536:
            raise ValueError("Rewrite exceeds display bound")
        return self.text


# ADR 29 structure without line breaks inside any JSON string: the model returns
# ordered parts and the app renders them as plain text.
KINDS = ("paragraph", "bullet", "numbered")
LIST_MARKER = re.compile(r"^\s*(?:[•\-*–]|\d+[.)])\s+")


def render(parts):
    """Plain text: a run of bullet parts becomes "• " lines, a run of numbered
    parts "1. " lines; a list follows its lead-in paragraph on the next line, and
    other runs are divided by a blank line."""
    out, previous, number = [], None, 0
    for part in parts:
        kind = part.get("kind") if isinstance(part, dict) else part.kind
        text = part.get("text") if isinstance(part, dict) else part.text
        if kind not in KINDS or not isinstance(text, str) or not text.strip():
            continue
        text = text.strip()
        if kind != "paragraph":
            text = LIST_MARKER.sub("", text, count=1)
        number = number + 1 if kind == "numbered" and previous == "numbered" else 1
        line = {"bullet": "• " + text, "numbered": f"{number}. {text}"}.get(kind, text)
        if previous is not None:
            joined = kind != "paragraph" and previous in (kind, "paragraph")
            out.append("\n" if joined else "\n\n")
        out.append(line)
        previous = kind
    return "".join(out)


PARTIAL_PART = re.compile(
    r'\{\s*"kind"\s*:\s*"(paragraph|bullet|numbered)"\s*,\s*"text"\s*:\s*"'
)


def root_array(buffer, key):
    """The index just inside a root-level array value named ``key``, or None
    while it has not arrived or the root has another shape."""
    decoder = json.JSONDecoder(strict=False)
    data = buffer.lstrip()
    if not data.startswith("{"):
        return None
    index = len(buffer) - len(data) + 1
    try:
        while True:
            while index < len(buffer) and buffer[index].isspace():
                index += 1
            name, index = decoder.raw_decode(buffer, index)
            while buffer[index].isspace():
                index += 1
            if not isinstance(name, str) or buffer[index] != ":":
                return None
            index += 1
            while buffer[index].isspace():
                index += 1
            if name == key:
                return index + 1 if buffer[index] == "[" else None
            _, index = decoder.raw_decode(buffer, index)
            while buffer[index].isspace():
                index += 1
            if buffer[index] != ",":
                return None
            index += 1
    except (ValueError, IndexError):
        return None


class PartsPreview:
    """The rendered incoming ``parts`` received so far, including the part still
    arriving, or a legacy root ``text``. Display only: validation stays strict."""

    def __init__(self):
        self.legacy = TextPreview()
        self.buffer = ""
        self.cursor = None
        self.parts = []

    def feed(self, fragment):
        legacy = self.legacy.feed(fragment)
        if self.legacy.cursor is not None:
            return legacy
        self.buffer += fragment
        if self.cursor is None:
            self.cursor = root_array(self.buffer, "parts")
            if self.cursor is None:
                return None
        decoder = json.JSONDecoder(strict=False)
        index, partial = self.cursor, None
        while index < len(self.buffer):
            char = self.buffer[index]
            if char.isspace() or char == ",":
                index += 1
                continue
            if char != "{":
                break  # The list ended, or this is not a part.
            try:
                part, index = decoder.raw_decode(self.buffer, index)
            except ValueError:
                match = PARTIAL_PART.match(self.buffer, index)
                if match:
                    reader = TextPreview()
                    reader.buffer, reader.cursor = self.buffer, match.end()
                    partial = {"kind": match.group(1), "text": reader.feed("")}
                break
            self.parts.append(part)
            self.cursor = index
        text = render([*self.parts, *([partial] if partial else [])])
        if len(text) > 65536:
            raise ValueError("Rewrite exceeds display bound")
        return text or None


class BriefingPreview:
    """Final briefing topic text only; citations and intermediate work stay hidden.

    This disposable display projection does not validate shape, citations or
    coverage. It never projects a no-substance message before final validation.
    """

    def __init__(self):
        self.buffer = ""
        self.cursor = None
        self.topics = []

    def feed(self, fragment):
        self.buffer += fragment
        if self.cursor is None:
            self.cursor = root_array(self.buffer, "topics")
            if self.cursor is None:
                return None
        decoder = json.JSONDecoder(strict=False)
        index, partial = self.cursor, None
        while index < len(self.buffer):
            char = self.buffer[index]
            if char.isspace() or char == ",":
                index += 1
                continue
            if char != "{":
                break
            try:
                topic, index = decoder.raw_decode(self.buffer, index)
            except ValueError:
                # Provider schemas permit either field order. Project the
                # current topic's root text as soon as it starts, even when a
                # complete citations array precedes the still-arriving string.
                title = TextPreview("title").feed(self.buffer[index:])
                text = TextPreview().feed(self.buffer[index:])
                partial = (
                    {"text": text, "title": title}
                    if title is not None or text is not None
                    else None
                )
                break
            self.topics.append(topic)
            self.cursor = index
        wording = []
        for topic in [*self.topics, *([partial] if partial else [])]:
            if not isinstance(topic, dict):
                continue
            parts = []
            for field in ("title", "text"):
                text = topic.get(field)
                if isinstance(text, str):
                    text.encode("utf-8")
                    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
                        raise ValueError("Invalid topic text")
                    if text.strip():
                        parts.append(text.strip())
            if parts:
                wording.append("\n".join(parts))
        text = "\n\n".join(wording)
        if len(text) > MAX_TEXT:
            raise ValueError("Briefing exceeds display bound")
        return text or None


# Content keeps the 1 MiB capture limit. Hidden reasoning arrives as its own
# deltas, each in a full event envelope, and is discarded, so the wire may carry
# many times the content: 16,384 output tokens fit well within this.
CONTENT_LIMIT = 1024 * 1024
WIRE_LIMIT = 16 * 1024 * 1024


async def read_stream(response, model, on_text, *, preview=None):
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    buffer, lines, raw = "", [], ""
    usage, route = {}, {}
    preview = preview or TextPreview()
    total, finished, shown = 0, False, None

    def receipt():
        return EngineResponse(raw, usage, cost_nanos(usage))

    def event(data):
        nonlocal raw, finished, shown
        if data == "[DONE]":
            finished = True
            return
        if finished:
            raise ValueError("Output after terminator")
        value = json.loads(data)
        if "model" in value:
            if value["model"] != model:
                raise ValueError("Unexpected model route")
            route["model"] = value["model"]
        if value.get("id"):
            if usage.get("receipt_id") not in (None, value["id"]):
                raise ValueError("Changed stream identity")
            usage["receipt_id"] = value["id"]
        usage.update(
            {k: v for k, v in (value.get("usage") or {}).items() if k in USAGE}
        )
        choices = value.get("choices") or []
        if choices:
            choice = choices[0]
            if choice.get("finish_reason"):
                usage["finish_reason"] = choice["finish_reason"]
            if value.get("error"):
                raise ValueError("Provider stream error")
            fragment = (choice.get("delta") or {}).get("content") or ""
            if not isinstance(fragment, str):
                raise ValueError("Invalid delta")
            raw += fragment
            if len(raw) > CONTENT_LIMIT:
                raise ValueError("Stream capture limit")
            text = preview.feed(fragment)
            if text is not None and text != shown and route:
                on_text(text)
                shown = text
        if value.get("error"):
            raise ValueError("Provider stream error")

    try:
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > WIRE_LIMIT:
                raise ValueError("Stream capture limit")
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.removesuffix("\r")
                if not line:
                    if lines:
                        event("\n".join(lines))
                        lines = []
                elif line.startswith("data:"):
                    lines.append(line[5:].removeprefix(" "))
        decoder.decode(b"", final=True)
        if not finished or not route or not usage.get("finish_reason"):
            raise ValueError("Incomplete stream")
        return receipt()
    except asyncio.CancelledError as error:
        error.receipt = receipt()
        raise
    except Exception:
        raise StreamFailure(receipt()) from None
