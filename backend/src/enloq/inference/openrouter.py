from __future__ import annotations

import json
import math
import time
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from ..domain.briefing_contract import (
    BriefingImageOutput,
    BriefingOutput,
    attached_image_ids,
    covered_source_ids,
)
from ..domain.models import Model, Refused, Unavailable
from ..domain.suggestions import ResponseSuggestions
from .base import EngineResponse, ProviderRefused
from .guidance import instructions
from .streaming import (
    USAGE,
    BriefingPreview,
    PartsPreview,
    cost_nanos,
    read_stream,
    render,
)

# ADR 29: one exact Model Route for every role, with no selector or fallback.
# ADR 39 moved it to the owner's LiteLLM proxy, whose route of this name calls
# Xiaomi's own API; the module keeps its earlier name. The route name is also
# what the proxy sends back as `model`.
LOGICAL_MODEL = MODEL = "xiaomi_mimo/mimo-v2.6-flash"
PROVIDER = "xiaomi"
# The proxy keeps request and response bodies in its spend log unless a request
# asks it not to. Every adapter sends this; the proxy's pass-through routes do
# not honour it.
PRIVATE = {"x-litellm-enable-message-redaction": "true"}
# Text generation roles use hidden reasoning.
BACKGROUND_OUTPUT_TOKENS = 16384
# Attempt deadlines fit a cap-length response: 4,096 tokens take 105 seconds at
# the listed 39 tokens per second; 16,384 take 512 seconds at a 32-per-second
# floor, plus first-token latency.
BACKGROUND_SECONDS = 540
# The per-token rates listed when the owner adopted the route (2026-10-09), the
# same as Xiaomi's own. Finite checks refuse higher rates, so their recorded
# cost bounds stay valid.
ADOPTED_RATES = {"prompt": "0.00000014", "completion": "0.00000028"}
# ADR 53: calmed rewrites only, on Meta's Muse Spark
# 1.3, which read sarcasm by its meaning where MiMo did not. The proxy's route
# of this name pins Meta, with low reasoning hidden. Its rates come from
# OpenRouter's public listing, as the proxy records none for it.
MUSE = "openrouter/meta/muse-spark-1.3-contributor"
MUSE_LISTED = "meta/muse-spark-1.3-contributor"
MUSE_PROVIDER = "meta"
MUSE_ADOPTED_RATES = {"prompt": "0.0000001", "completion": "0.0000002"}
LISTING = "https://openrouter.ai/api/v1"


class ImageContext(Model):
    message_id: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=2000)


class Part(Model):
    kind: Literal["paragraph", "bullet", "numbered"]
    text: str = Field(min_length=1, max_length=8000)

    @field_validator("text")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Empty part")
        return value


# The provider returns ordered parts (ADR 29 structure without line breaks
# inside a JSON string), rendered into the plain text that is stored and shown.
# Offline authored engines may still return the earlier single text.
class IncomingRewrite(Model):
    parts: list[Part] | None = Field(default=None, max_length=80)
    text: str = Field(default="", max_length=65536)
    image_context: list[ImageContext] = Field(max_length=4)

    @model_validator(mode="after")
    def rendered(self):
        if self.parts is None:
            if "text" not in self.model_fields_set:
                raise ValueError("Missing rewrite parts")
            return self
        if "text" in self.model_fields_set:
            raise ValueError("Return parts or text, not both")
        self.text = render(self.parts)
        if len(self.text) > 65536:
            raise ValueError("Rewrite exceeds bound")
        return self


# The owner's 2026-10-09 decision: one uniform 150-character ceiling per
# generated takeaway, shared by the provider schema and local validation, so
# Latin and Cyrillic text always fits ADR 21's 300-byte entry bound.
TAKEAWAY_CHARACTERS = 150


class MemoryAddition(Model):
    text: str = Field(
        min_length=1,
        max_length=TAKEAWAY_CHARACTERS,
        description=(
            "One nonblank takeaway of at most 150 characters, and never more than "
            "300 UTF-8 bytes. Keep the source language and write concisely."
        ),
    )
    citations: list[int] = Field(min_length=1, max_length=5)
    # Existing generated entries may be shortened/merged without pretending
    # that an expired original was supplied as new evidence.
    based_on: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("text", mode="before")
    @classmethod
    def bounded_text(cls, value):
        if not isinstance(value, str):
            return value
        if not value.strip():
            raise PydanticCustomError("memory_text_empty", "Takeaway is blank")
        if len(value.encode("utf-8")) > 300:
            raise PydanticCustomError(
                "memory_text_bytes", "Takeaway exceeds byte limit"
            )
        if len(value) > TAKEAWAY_CHARACTERS:
            raise PydanticCustomError(
                "memory_text_length", "Takeaway exceeds character limit"
            )
        return value


class MemoryFlag(Model):
    id: str = Field(min_length=1, max_length=64)
    citations: list[int] = Field(min_length=1, max_length=5)


class MemoryChanges(Model):
    """Changes to one memory only: new cited notes, superseded model entries
    to remove and owner entries the cited messages suggest are possibly
    outdated."""

    add: list[MemoryAddition] = Field(max_length=40)
    remove: list[str] = Field(max_length=40)
    outdated: list[MemoryFlag] = Field(max_length=10)


class MemoryUpdate(Model):
    """ADR 55: one chat's update changes its Person Memory (what the other
    person cares about) and the Account Memory (what the owner cares about)."""

    person: MemoryChanges
    account: MemoryChanges


def memory_schema(packet=None):
    """The strict provider schema of a memory update: each memory's list gets
    its own definitions, so its IDs and citations are bounded separately."""
    base = MemoryChanges.model_json_schema()
    definitions = base.pop("$defs")
    schema = {
        "type": "object",
        "properties": {},
        "required": ["person", "account"],
        "additionalProperties": False,
        "$defs": {},
    }
    sources = {m["id"] for m in packet["messages"]} if packet is not None else set()
    for side in ("person", "account"):
        prefix = side.title()
        changes = json.loads(
            json.dumps(base)
            .replace("#/$defs/MemoryAddition", f"#/$defs/{prefix}Addition")
            .replace("#/$defs/MemoryFlag", f"#/$defs/{prefix}Flag")
        )
        addition = json.loads(json.dumps(definitions["MemoryAddition"]))
        flag = json.loads(json.dumps(definitions["MemoryFlag"]))
        # The strict provider schema declares every property required. Local
        # validation still accepts older authored fixtures without based_on.
        addition["required"] = list(addition["properties"])
        if packet is not None:
            listed = packet[side]
            generated = [e for e in listed["memory"] if e["source"] == "model"]
            owners = [e["id"] for e in listed["memory"] if e["source"] == "owner"]
            known = [e["id"] for e in generated]
            changes["properties"]["add"]["maxItems"] = min(
                40, listed.get("limits", {}).get("generated_entries", 40)
            )
            for field in (
                addition["properties"]["based_on"],
                changes["properties"]["remove"],
            ):
                field["maxItems"] = min(field["maxItems"], len(known))
                if known:
                    field["items"]["enum"] = known
                else:
                    field["maxItems"] = 0
            # A person note may carry its merged notes' citations; an account
            # note cites this chat's supplied messages, and its merged notes'
            # citations from other chats are carried by the service.
            citations = set(sources)
            if side == "person":
                citations |= {i for e in generated for i in e["citations"]}
            if citations:
                addition["properties"]["citations"]["items"]["enum"] = sorted(citations)
            else:
                changes["properties"]["add"]["maxItems"] = 0
            if owners and sources:
                changes["properties"]["outdated"]["maxItems"] = min(10, len(owners))
                flag["properties"]["id"]["enum"] = owners
                flag["properties"]["citations"]["items"]["enum"] = sorted(sources)
            else:
                changes["properties"]["outdated"]["maxItems"] = 0
        schema["$defs"][f"{prefix}Addition"] = addition
        schema["$defs"][f"{prefix}Flag"] = flag
        schema["properties"][side] = changes
    return schema


class TraitNote(Model):
    """One free-form trait (ADR 55 Q12 C): a tendency cited from at least
    three of the subject's own messages on two days, with a confidence."""

    text: str = Field(
        min_length=1,
        max_length=TAKEAWAY_CHARACTERS,
        description="One tendency, not a verdict, in at most 150 characters.",
    )
    citations: list[int] = Field(min_length=3, max_length=8)
    confidence: float = Field(ge=0, le=1)

    @field_validator("text", mode="before")
    @classmethod
    def bounded_text(cls, value):
        if isinstance(value, str) and (
            not value.strip() or len(value.encode("utf-8")) > 300
        ):
            raise PydanticCustomError("trait_text", "Trait note out of bounds")
        return value


class TraitNotes(Model):
    """Changes only: new notes, and model notes to remove."""

    add: list[TraitNote] = Field(max_length=10)
    remove: list[str] = Field(max_length=10)


SCHEMAS = {
    "incoming_rewrite": IncomingRewrite,
    "chat_memory": MemoryUpdate,
    "trait_notes": TraitNotes,
    "response_suggestions": ResponseSuggestions,
    "group_briefing_image": BriefingImageOutput,
    "group_briefing_chunk": BriefingOutput,
    "group_briefing": BriefingOutput,
}
STREAMED_ROLES = frozenset({"incoming_rewrite", "group_briefing"})


def has_target_text(packet):
    """Prepared target originals/transcripts require visible wording, not support."""
    return any(
        m["text"].strip() or m.get("transcript", {}).get("text", "").strip()
        for m in packet["messages"]
    )


def output_schema(role, packet=None):
    if role not in SCHEMAS:
        raise Refused("Unsupported rewrite role")
    schema = SCHEMAS[role].model_json_schema()
    if role == "incoming_rewrite":
        # Parts first, so rendered text streams before hidden metadata. Emotion
        # is a separate decision (ADR 30), never part of this output.
        fields = schema["properties"]
        schema["properties"] = {
            "parts": {
                "type": "array",
                "items": {"$ref": "#/$defs/Part"},
                "maxItems": 80,
            },
            "image_context": fields["image_context"],
        }
        schema["required"] = list(schema["properties"])
    if role == "response_suggestions":
        # Text first, so each option is labelled after it is written: an
        # option that says yes is accept even when it also asks (ADR 49 Q18).
        option = schema["$defs"]["SuggestionOption"]
        option["properties"] = {
            "text": option["properties"]["text"],
            "purpose": option["properties"]["purpose"],
        }
        option["required"] = ["text", "purpose"]
    if role == "chat_memory":
        return memory_schema(packet)
    if role == "trait_notes" and packet is not None:
        note = schema["$defs"]["TraitNote"]
        own = sorted(
            m["id"] for m in packet["messages"] if m["from"] == packet["writer"]
        )
        if own:
            note["properties"]["citations"]["items"]["enum"] = own
        else:
            schema["properties"]["add"]["maxItems"] = 0
        schema["properties"]["add"]["maxItems"] = min(
            schema["properties"]["add"]["maxItems"],
            packet.get("limits", {}).get("generated", 10),
        )
        known = [n["id"] for n in packet["notes"] if n["source"] == "model"]
        if known:
            schema["properties"]["remove"]["items"]["enum"] = known
            schema["properties"]["remove"]["maxItems"] = len(known)
        else:
            schema["properties"]["remove"]["maxItems"] = 0
    if role == "incoming_rewrite" and packet is not None:
        if has_target_text(packet):
            schema["properties"]["parts"]["minItems"] = 1
        identities = [i["message_id"] for i in packet.get("images", [])]
        schema["properties"]["image_context"].update(
            minItems=len(identities), maxItems=len(identities)
        )
        if identities:
            schema["$defs"]["ImageContext"]["properties"]["message_id"]["enum"] = (
                identities
            )
    if role == "group_briefing_image" and packet is not None:
        identities = sorted(attached_image_ids(packet))
        schema["properties"]["image_context"].update(
            minItems=len(identities), maxItems=len(identities)
        )
        if identities:
            schema["$defs"]["BriefingImageContext"]["properties"]["message_id"][
                "enum"
            ] = identities
    if role in {"group_briefing_chunk", "group_briefing"} and packet is not None:
        identities = sorted(covered_source_ids(packet))
        citations = schema["$defs"]["BriefingTopic"]["properties"]["citations"]
        citations.update(maxItems=len(identities), uniqueItems=True)
        participants = schema["$defs"]["BriefingTopic"]["properties"][
            "participant_source_ids"
        ]
        participants.update(maxItems=len(identities), uniqueItems=True)
        if identities:
            citations["items"]["enum"] = identities
            participants["items"]["enum"] = identities
        else:
            schema["properties"]["topics"]["maxItems"] = 0
        if packet.get("contract_version") == 2:
            schema["properties"]["contract_version"] = {"type": "integer", "const": 2}
            schema["required"] = ["contract_version", "outcome", "topics"]
            topic = schema["$defs"]["BriefingTopic"]
            topic["required"] = ["title", "text", "citations", "participant_source_ids"]
            topic["properties"]["title"] = {
                "type": "string",
                "minLength": 1,
                "maxLength": 240,
            }
            participants["minItems"] = 1
    return schema


def checked_endpoint(value):
    """A LiteLLM proxy's OpenAI-compatible base URL: https, ending in /v1, with
    no user info, query or fragment. Its pass-through routes sit beside /v1."""
    endpoint = value.strip().rstrip("/")
    try:
        parts = urlsplit(endpoint)
        valid = (
            parts.scheme == "https"
            and bool(parts.hostname)
            and parts.port != 0  # Reading the port refuses an unreadable one.
            and "@" not in parts.netloc
            and not {"?", "#"} & set(endpoint)
            and not any(c.isspace() for c in endpoint)
            and parts.path.endswith("/v1")
        )
    except ValueError:
        valid = False
    if not valid:
        raise Refused("Model endpoint is not an https LiteLLM proxy URL ending in /v1")
    return endpoint


def proxy_endpoint(settings):
    """The configured proxy endpoint (ADR 39), which `./scripts/setup proxy-key`
    saves beside the caller key."""
    if settings.mode == "offline":
        raise Refused("Offline mode cannot construct a live model adapter")
    return checked_endpoint(settings.secret("MUSE_ENDPOINT_REF"))


def proxy_key(settings):
    """The proxy caller key, bound to the proxy endpoint (ADR 39). Every live
    adapter uses it: MiMo, the Decider's pass-through and speech-to-text."""
    if settings.mode == "offline":
        raise Refused("Offline mode cannot construct a live model adapter")
    proxy_endpoint(settings)
    key = settings.secret("MUSE_API_KEY_REF")
    if not key.startswith("sk-") or key.startswith("sk-or-"):
        raise Refused("Model endpoint and credential binding is not the LiteLLM proxy")
    return key


async def proxy_route(client, endpoint, key, model, name):
    """The proxy's own record of one route, with its required parameters."""
    response = await client.get(
        f"{endpoint}/model/info", headers={"Authorization": f"Bearer {key}"}
    )
    if response.status_code != 200:
        raise ProviderRefused(
            f"Model proxy HTTP {response.status_code}",
            retry_after=retry_after(response.headers.get("retry-after")),
        )
    routes = [r for r in response.json()["data"] if r.get("model_name") == model]
    if len(routes) != 1:
        raise ProviderRefused(f"Exact {name} route unavailable")
    route = routes[0]["model_info"]
    if route.get("mode") != "chat" or not (
        route.get("supports_response_schema") and route.get("supports_vision")
    ):
        raise Unavailable("Exact route lacks required output parameters")
    return route


async def route_rates(client, endpoint, key):
    """The proxy's own record of the route: what it calls and charges per token."""
    route = await proxy_route(client, endpoint, key, MODEL, "MiMo / Xiaomi")
    rates = {
        k: str(Decimal(str(route[f"{side}_cost_per_token"])))
        for k, side in (("prompt", "input"), ("completion", "output"))
    }
    if any(not Decimal(v).is_finite() or Decimal(v) < 0 for v in rates.values()):
        raise Unavailable("Provider rates cannot be bounded")
    return {
        **rates,
        "verified_at": time.time(),
        "context_length": route["max_input_tokens"],
        "model": LOGICAL_MODEL,
        "provider": PROVIDER,
        "endpoint": endpoint,
    }


def within_adopted_rates(rates, adopted=ADOPTED_RATES):
    return all(Decimal(rates[k]) <= Decimal(v) for k, v in adopted.items())


async def muse_rates(client, endpoint, key):
    """Muse's route on the proxy, and its rates from OpenRouter's public
    listing: exactly one Meta endpoint, up, within the adopted rates."""
    route = await proxy_route(client, endpoint, key, MUSE, "Muse / Meta")
    response = await client.get(f"{LISTING}/models/{MUSE_LISTED}/endpoints")
    if response.status_code != 200:
        raise ProviderRefused(
            f"Model listing HTTP {response.status_code}",
            retry_after=retry_after(response.headers.get("retry-after")),
        )
    data = response.json()["data"]
    endpoints = [
        e
        for e in data["endpoints"]
        if str(e.get("tag", "")).split("/", 1)[0] == MUSE_PROVIDER
        and e.get("status") == 0
    ]
    if data.get("id") != MUSE_LISTED or len(endpoints) != 1:
        raise ProviderRefused("Exact Muse / Meta route unavailable")
    rates = {
        k: str(Decimal(str(endpoints[0]["pricing"][k])))
        for k in ("prompt", "completion")
    }
    if any(not Decimal(v).is_finite() or Decimal(v) < 0 for v in rates.values()):
        raise Unavailable("Provider rates cannot be bounded")
    if not within_adopted_rates(rates, MUSE_ADOPTED_RATES):
        raise ProviderRefused("Exact Muse route rates exceed the adopted rates")
    return {
        **rates,
        "verified_at": time.time(),
        "context_length": route["max_input_tokens"],
        "model": MUSE,
        "provider": MUSE_PROVIDER,
        "endpoint": endpoint,
    }


def retry_after(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= 3600 else None


def output_tokens(role):
    return BACKGROUND_OUTPUT_TOKENS


def thinking(role):
    """Xiaomi's own reasoning switch. Its reasoning text comes back beside the
    content and is never read or retained."""
    return {"type": "enabled"}


class OpenRouterEngine:
    name, model, provider = "mimo", LOGICAL_MODEL, PROVIDER
    route = MODEL

    def __init__(self, settings, *, user, client=None, rates=None):
        key = proxy_key(settings)
        # One private app-level pseudonym (ADR 29); never logged or derived.
        if not isinstance(user, str) or not 16 <= len(user) <= 128:
            raise Refused("A private provider user pseudonym is required")
        self.endpoint = proxy_endpoint(settings)
        self.key, self.user, self.rates = key, user, rates
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(BACKGROUND_SECONDS, connect=30),
            follow_redirects=False,
            trust_env=False,
        )

    def attempt_seconds(self, role):
        return BACKGROUND_SECONDS

    async def verify(self):
        return await route_rates(self.client, self.endpoint, self.key)

    def extra_body(self, role):
        """The request's own extra body, which replaces the proxy route's."""
        return {"thinking": thinking(role)}

    async def prepare(self, packet):
        if self.rates is None or time.time() - self.rates["verified_at"] > 86400:
            self.rates = await self.verify()
        # Route verification before dispatch. The returned whole-context ceiling
        # is informational; ADR 15 counts receipts plus a flat unknown estimate.
        context = self.rates["context_length"]
        if not isinstance(context, int) or not 1 <= context <= 1048576:
            raise Unavailable("Model context cost bound changed")
        value = (
            Decimal(self.rates["prompt"]) * context
            + Decimal(self.rates["completion"]) * BACKGROUND_OUTPUT_TOKENS
        )
        return math.ceil(value * 1_000_000_000)

    async def generate(self, role, packet):
        if role not in SCHEMAS:
            raise Refused("Unsupported rewrite role")
        # The guidance version was fixed when this attempt was dispatched.
        system = instructions(role, packet, packet.get("guidance"))
        return await self.structured(
            system, packet, output_schema(role, packet), role=role
        )

    async def generate_stream(self, role, packet, on_text):
        if role not in STREAMED_ROLES:
            raise Refused("This role has no rewrite stream")
        return await self.structured(
            instructions(role, packet, packet.get("guidance")),
            packet,
            output_schema(role, packet),
            role=role,
            on_text=on_text,
        )

    async def structured(self, system, packet, schema, *, role, on_text=None):
        """Use the same qualified transport for a caller-owned bounded schema."""
        if role not in SCHEMAS:
            raise Refused("Unsupported rewrite role")
        if on_text is not None and role not in STREAMED_ROLES:
            raise Refused("This role has no rewrite stream")
        max_tokens = output_tokens(role)
        images = packet.get("images", [])
        # Guidance belongs to the instructions, never the untrusted content;
        # so do the calming flag (ADR 53) and the idioms flag (ADR 56).
        plain = {
            k: v
            for k, v in packet.items()
            if k not in ("images", "guidance", "calm", "sarcastic", "idioms")
        }
        content = [{"type": "text", "text": json.dumps(plain, ensure_ascii=False)}]
        for image in images:
            content.append(
                {
                    "type": "text",
                    "text": json.dumps(
                        dict(attachment="image", native_message_id=image["message_id"])
                    ),
                }
            )
            content.append(
                {"type": "image_url", "image_url": {"url": image["data_url"]}}
            )
        # No tools are offered, so no tool_choice is sent: Xiaomi rejects "none".
        # The request's extra_body replaces the proxy route's own, which only
        # turns thinking off.
        payload = {
            "model": self.route,
            "user": self.user,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "enloq",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if extra := self.extra_body(role):
            payload["extra_body"] = extra
        if on_text is not None:
            payload.update(stream=True, stream_options={"include_usage": True})
        try:
            request = self.client.stream(
                "POST",
                f"{self.endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}", **PRIVATE},
                json=payload,
            )
            response = await request.__aenter__()
        except httpx.ConnectError:
            raise ProviderRefused("Provider connection refused") from None
        try:
            if response.status_code in (402, 429):
                # Rate or in-flight spend refusals happen before any provider work.
                raise ProviderRefused(
                    f"Model provider HTTP {response.status_code}",
                    retry_after=retry_after(response.headers.get("retry-after")),
                )
            if response.status_code != 200:
                raise Unavailable(
                    f"Model provider HTTP {response.status_code}; first attempt retained"
                )
            if on_text is not None:
                result = await read_stream(
                    response,
                    self.route,
                    on_text,
                    preview=BriefingPreview()
                    if role == "group_briefing"
                    else PartsPreview()
                    if role == "incoming_rewrite"
                    else None,
                )
                result.usage["output_token_limit"] = max_tokens
                return result
            # A streamed reply carries its cost in the final usage; this one has
            # it only in the proxy's header.
            charged = response.headers.get("x-litellm-response-cost")
            chunks, total = [], 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > 1024 * 1024:
                    raise Unavailable("Model response exceeded capture limit")
                chunks.append(chunk)
        finally:
            await request.__aexit__(None, None, None)
        data = json.loads(b"".join(chunks))
        if data.get("model") != self.route:
            raise Unavailable("Provider returned an unqualified model or route")
        usage = {k: v for k, v in data.get("usage", {}).items() if k in USAGE}
        if charged is not None and "cost" not in usage:
            try:
                usage["cost"] = float(charged)
            except ValueError:
                pass  # An unreadable cost stays unknown and keeps the flat estimate.
        usage["receipt_id"] = data.get("id")
        usage["finish_reason"] = data["choices"][0].get("finish_reason")
        usage["output_token_limit"] = max_tokens
        actual = cost_nanos(usage)
        # Never retain reasoning fields from the response.
        text = data["choices"][0]["message"]["content"]
        # Keep the original JSON for duplicate-key/framing validation.
        content = text if isinstance(text, str) else ""
        return EngineResponse(content=content, usage=usage, actual_nanos=actual)


class MuseEngine(OpenRouterEngine):
    """Calmed incoming rewrites only (ADR 53), on the
    proxy's Muse route, which pins Meta and hides low reasoning. The request
    sends no extra body, which would replace the route's."""

    name, model, provider = "muse", MUSE, MUSE_PROVIDER
    route = MUSE

    async def verify(self):
        return await muse_rates(self.client, self.endpoint, self.key)

    def extra_body(self, role):
        return None
