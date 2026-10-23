"""Emotion Classification on its own exact Model Route (ADR 30).

The decision model writes no text. It reads the supplied ``state`` and answers
named questions with probabilities, through OpenRouter's Decisions endpoint and
Perplexity only. There is no selector, fallback or automatic resampling. Since
ADR 39 the call goes through the owner's LiteLLM proxy, which forwards the body
unchanged with its own OpenRouter key and returns OpenRouter's answer as sent.
Since ADR 49 Q1 the route is Decider V1.2: OpenRouter withdrew V1.1's endpoint.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from decimal import Decimal

import httpx

from ..domain import traits
from ..domain.emotion import CODES, FLOOR, Classification, EmotionDecision
from ..domain.group_filtering import FILTER_ROLES, REPEATED_INPUT, checked_packet
from ..domain.group_filtering import questions as filter_questions
from ..domain.models import Refused, Unavailable
from ..domain.reactions import AssessmentResult
from .base import EngineResponse, InvalidOutput, ProviderRefused
from .guidance import LABEL_CRITERIA, decision_questions
from .openrouter import PRIVATE, proxy_endpoint, proxy_key, retry_after
from .streaming import cost_nanos

_LOG = logging.getLogger("enloq.decider")
LOGICAL_MODEL = "openrouter/perplexity/pplx-decider-v1.2"
MODEL = "perplexity/pplx-decider-v1.2"
# The checkpoint the owner adopted (ADR 30 Q4, ADR 49 Q1): no automatic later
# checkpoint.
CHECKPOINT = MODEL + "-20261016"
PROVIDER = "perplexity"
# OpenRouter's public listing still names the route's rates and checkpoint.
LISTING = "https://openrouter.ai/api/v1"
# The listing's rates when the owner adopted the route (2026-10-09, and V1.2's
# on 2026-10-18): $0.02 per million input tokens and free output. A higher
# listed rate is unavailable.
ADOPTED_RATES = {"prompt": "0.00000002", "completion": "0"}
# ADR 55 Q22 adds the starter list of traits, scored for the owner's eyes.
ROLES = (
    frozenset(
        {
            "emotion_classification",
            "reaction_assessment",
            "suggestion_labels",
            "trait_scales",
        }
    )
    | FILTER_ROLES
)
# An implementation default, not an owner decision: suggestions_useful is yes
# at or above this probability.
USEFUL = 0.5
# Calls took 0.72-1.59 seconds in the suitability check.
SECONDS = 60
CAPTURE = 1024 * 1024
USAGE = {"input_tokens", "output_tokens", "cost", "cost_details", "is_byok"}


def decisions_url(endpoint):
    """OpenRouter's Decisions endpoint, on the proxy's pass-through beside /v1."""
    return endpoint.removesuffix("/v1") + "/openrouter/api/alpha/decisions"


def within_adopted_rates(rates):
    return all(Decimal(rates[k]) <= Decimal(v) for k, v in ADOPTED_RATES.items())


def exact(model):
    """The adopted model, or its adopted dated checkpoint name."""
    return model in (MODEL, CHECKPOINT)


def provider_of(route):
    """An endpoint tag names its provider, optionally with a variant: xiaomi/fp8."""
    return str(route.get("tag", "")).split("/", 1)[0]


def checkpoint(route):
    """The dated checkpoint an endpoint names, when it names one."""
    name = str(route.get("name", "")).rsplit("|", 1)[-1].strip()
    return name if re.fullmatch(re.escape(MODEL) + r"-\d{8}", name) else None


async def route_rates(client):
    response = await client.get(f"{LISTING}/models/{MODEL}/endpoints")
    response.raise_for_status()
    data = response.json()["data"]
    routes = [
        r
        for r in data["endpoints"]
        if provider_of(r) == PROVIDER
        and r.get("model_id") == MODEL
        and r.get("status") == 0
    ]
    if data["id"] != MODEL or len(routes) != 1:
        raise ProviderRefused(
            "Exact Decider / Perplexity route unavailable",
            retry_after=retry_after(response.headers.get("retry-after")),
        )
    route = routes[0]
    rates = {k: str(Decimal(route["pricing"][k])) for k in ("prompt", "completion")}
    if any(not Decimal(v).is_finite() or Decimal(v) < 0 for v in rates.values()):
        raise Unavailable("Provider rates cannot be bounded")
    if not within_adopted_rates(rates):
        # No work is sent at an unadopted price: emotion stays unavailable.
        raise ProviderRefused("Exact Decider route rates exceed the adopted rates")
    if checkpoint(route) not in (None, CHECKPOINT):
        raise ProviderRefused("Exact Decider route serves another checkpoint")
    return {
        **rates,
        "verified_at": time.time(),
        "context_length": route["context_length"],
        "model": LOGICAL_MODEL,
        "provider": PROVIDER,
        "endpoint": route.get("tag"),
        "checkpoint": checkpoint(route),
    }


def probability(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def choice(answers, name):
    answer = answers.get(name)
    if not isinstance(answer, dict):
        raise InvalidOutput("missing_answer")
    if answer.get("type") != "choice":
        raise InvalidOutput("invalid_answer")
    selected = answer.get("choice")
    if selected not in CODES:
        raise InvalidOutput("unknown_option")
    probabilities = answer.get("probabilities")
    if (
        not isinstance(probabilities, dict)
        or selected not in probabilities
        or any(k not in CODES or not probability(v) for k, v in probabilities.items())
    ):
        raise InvalidOutput("invalid_probabilities")
    confidence = answer.get("confidence")
    if confidence is not None and not probability(confidence):
        raise InvalidOutput("invalid_answer")
    chance = float(probabilities[selected])
    return EmotionDecision(
        code=selected if chance >= FLOOR else "unknown",
        choice=selected,
        probability=chance,
        confidence=confidence,
        probabilities={k: float(v) for k, v in probabilities.items()},
    )


def purpose(answers, name):
    """One suggestion's label (ADR 49 Q19): a supplied purpose with finite
    probabilities that include it."""
    answer = answers.get(name)
    if not isinstance(answer, dict):
        raise InvalidOutput("missing_answer")
    if answer.get("type") != "choice":
        raise InvalidOutput("invalid_answer")
    selected = answer.get("choice")
    if selected not in LABEL_CRITERIA:
        raise InvalidOutput("unknown_option")
    probabilities = answer.get("probabilities")
    if (
        not isinstance(probabilities, dict)
        or selected not in probabilities
        or any(
            k not in LABEL_CRITERIA or not probability(v)
            for k, v in probabilities.items()
        )
    ):
        raise InvalidOutput("invalid_probabilities")
    return selected


def noul(answers, name):
    answer = answers.get(name)
    if not isinstance(answer, dict):
        raise InvalidOutput("missing_answer")
    if answer.get("type") != "noul" or not probability(answer.get("noul")):
        raise InvalidOutput("invalid_answer")
    return float(answer["noul"])


def validate(role, answers, packet=None):
    """Answers by question name, a supplied option and finite probabilities;
    anything else is a fixed content-free format failure."""
    if role in FILTER_ROLES:
        expected = set(filter_questions(role, packet or {}))
        if not isinstance(answers, dict) or set(answers) != expected:
            raise InvalidOutput("invalid_answer")
        return {name: noul(answers, name) for name in expected}
    if role == "trait_scales":
        return traits.validate(answers, (packet or {}).get("subject"))
    if role == "suggestion_labels":
        names = list(decision_questions(role, packet=packet))
        if not isinstance(answers, dict) or set(answers) != set(names):
            raise InvalidOutput("invalid_answer")
        return [purpose(answers, name) for name in names]
    expected = set(decision_questions(role))
    if not isinstance(answers, dict):
        raise InvalidOutput("missing_answer")
    if set(answers) - expected:
        raise InvalidOutput("invalid_answer")
    decision = choice(answers, "emotion")
    useful = noul(answers, "suggestions_useful")
    if role == "emotion_classification":
        # ADR 49 Q2: the incoming request answers usefulness too, and ADR 53
        # whether it is sarcastic: only a calmed rewrite is told, so a missing
        # answer means no hint.
        return Classification(
            decision=decision,
            suggestions_useful=useful >= USEFUL,
            usefulness=useful,
            sarcasm=noul(answers, "sarcasm") if "sarcasm" in answers else None,
        )
    return AssessmentResult(
        emotion=decision.code,
        suggestions_useful=useful >= USEFUL,
        decision=decision,
        usefulness=useful,
    )


class DeciderEngine:
    name, model, provider = "decider", LOGICAL_MODEL, PROVIDER

    def __init__(self, settings, *, user, client=None, rates=None):
        key = proxy_key(settings)
        # The same private app-level pseudonym as MiMo requests (ADR 30 Q4).
        if not isinstance(user, str) or not 16 <= len(user) <= 128:
            raise Refused("A private provider user pseudonym is required")
        self.endpoint = proxy_endpoint(settings)
        self.key, self.user, self.rates = key, user, rates
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(SECONDS, connect=30),
            follow_redirects=False,
            trust_env=False,
        )
        # ADR 49 Q14: told whether the route answered, and how it failed; the
        # account manager keeps one state for every account.
        self.on_route = lambda available, reason=None: None

    def report(self, available, reason=None):
        try:
            self.on_route(available, reason)
        except Exception:
            # The route's state is advisory; it never changes this call.
            _LOG.warning("decision_state_unavailable", exc_info=True)

    def attempt_seconds(self, role):
        return SECONDS

    async def prepare(self, packet):
        if packet.get("filter_role") in FILTER_ROLES:
            checked_packet(packet)
        if self.rates is None or time.time() - self.rates["verified_at"] > 86400:
            try:
                self.rates = await route_rates(self.client)
            except (ProviderRefused, Unavailable):
                self.report(False, "listing")
                raise
        context = self.rates["context_length"]
        if not isinstance(context, int) or not 1 <= context <= 1048576:
            raise Unavailable("Model context cost bound changed")
        if packet.get("filter_role") in FILTER_ROLES:
            filter_questions(packet["filter_role"], packet)
            bound = packet.get("input_bound")
            if type(bound) is not int or not 1 <= bound <= min(context, REPEATED_INPUT):
                raise ProviderRefused(
                    "Group classifier repeated input exceeds route context"
                )
        # Informational, like MiMo's: ADR 15 counts receipts plus a flat estimate.
        return math.ceil(Decimal(self.rates["prompt"]) * context * 1_000_000_000)

    async def generate(self, role, packet):
        if role not in ROLES:
            raise Refused("Unsupported decision role")
        if role in FILTER_ROLES:
            checked_packet(packet)
        payload = {
            "model": MODEL,
            "provider": {"only": [PROVIDER], "allow_fallbacks": False},
            "user": self.user,
            "state": packet["state"],
            # The guidance version was fixed when this attempt was dispatched.
            "questions": filter_questions(role, packet)
            if role in FILTER_ROLES
            else traits.questions(packet["subject"])
            if role == "trait_scales"
            else decision_questions(role, packet.get("guidance"), packet),
        }
        try:
            request = self.client.stream(
                "POST",
                decisions_url(self.endpoint),
                headers={"Authorization": f"Bearer {self.key}", **PRIVATE},
                json=payload,
            )
            response = await request.__aenter__()
        except httpx.ConnectError:
            self.report(False, "connection")
            raise ProviderRefused("Provider connection refused") from None
        try:
            if response.status_code in (401, 403):
                # Refused at the proxy or OpenRouter before any model work, as
                # when the proxy's key lacks the model (ADR 49 Q14): a no-work
                # refusal that counts nothing.
                self.report(False, "refused")
                raise ProviderRefused(f"Model provider HTTP {response.status_code}")
            if response.status_code in (402, 429):
                if response.status_code == 402:
                    self.report(False, "refused")
                raise ProviderRefused(
                    f"Model provider HTTP {response.status_code}",
                    retry_after=retry_after(response.headers.get("retry-after")),
                )
            if response.status_code != 200:
                self.report(False, "http")
                raise Unavailable(
                    f"Model provider HTTP {response.status_code}; first attempt retained"
                )
            chunks, total = [], 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > CAPTURE:
                    raise Unavailable("Model response exceeded capture limit")
                chunks.append(chunk)
        finally:
            await request.__aexit__(None, None, None)
        data = json.loads(b"".join(chunks))
        usage = {k: v for k, v in (data.get("usage") or {}).items() if k in USAGE}
        usage["receipt_id"] = data.get("id")
        usage["checkpoint"] = data.get("model")
        result = EngineResponse(
            content=data.get("answers"), usage=usage, actual_nanos=cost_nanos(usage)
        )
        if not exact(data.get("model")) or (
            str(data.get("provider", "")).lower() != PROVIDER
        ):
            self.report(False, "route")
            failure = Unavailable("Provider returned an unqualified model or route")
            failure.receipt = result  # The provider worked: its cost counts.
            raise failure
        self.report(True)
        return result
