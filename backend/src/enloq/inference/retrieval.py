"""OpenRouter's embeddings and rerank routers for Ask this group (ADR 59 Q3).

Both go through the proxy's pass-throughs, which forward OpenRouter's own
format with the proxy's key, so each request can pin one provider with no
fallback and require zero data retention. Each route is verified against the
public listing before dispatch, like the decision model's.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx

from ..domain.group_questions import EMBED_ROLE, RERANK_ROLE, normalized
from ..domain.models import Refused, Unavailable
from .base import EngineResponse, InvalidOutput, ProviderRefused
from .openrouter import PRIVATE, proxy_endpoint, proxy_key, retry_after
from .streaming import cost_nanos

LISTING = "https://openrouter.ai/api/v1"
SECONDS = 60
MAX_INPUTS = 64
MAX_INPUT_BYTES = 120_000
USAGE = {"prompt_tokens", "total_tokens", "search_units", "cost", "cost_details"}


@dataclass(frozen=True)
class Route:
    """One exact model and provider, at no more than its adopted rate per
    input token. ``tag`` is the listing's endpoint tag for that provider."""

    model: str
    provider: str
    tag: str
    rate: str
    dimensions: int | None = None
    # Prepended to a question, never to a message, for models trained so.
    query_instruction: str | None = None


# ADR 59 Q3: the zero-retention routes the application may use. Which
# embedder and whether a reranker are decided by measurement; until then the
# application embeds with Qwen3 Embedding 8B and does not rerank.
QWEN_QUERY = (
    "Instruct: Given a question about a group chat, retrieve the messages "
    "that answer it\nQuery: "
)
EMBEDDING = Route(
    "qwen/qwen3-embedding-8b",
    "nebius",
    "nebius",
    "0.00000001",
    dimensions=1024,
    query_instruction=QWEN_QUERY,
)
RERANK = None


async def listed(client, route):
    """The route's public listing: exactly one up endpoint of its provider,
    at or below its adopted rate."""
    response = await client.get(f"{LISTING}/models/{route.model}/endpoints")
    if response.status_code != 200:
        raise ProviderRefused(
            f"Model listing HTTP {response.status_code}",
            retry_after=retry_after(response.headers.get("retry-after")),
        )
    data = response.json()["data"]
    endpoints = [
        e
        for e in data.get("endpoints", [])
        if e.get("tag") == route.tag and e.get("status") == 0
    ]
    if data.get("id") != route.model or len(endpoints) != 1:
        raise ProviderRefused(f"Exact {route.model} route unavailable")
    price = Decimal(str(endpoints[0]["pricing"]["prompt"]))
    if not price.is_finite() or price < 0 or price > Decimal(route.rate):
        raise ProviderRefused("Route rates exceed the adopted rates")
    return {"verified_at": time.time(), "prompt": str(price), "tag": route.tag}


class PassThrough:
    """Shared request handling for both routers."""

    path = ""

    def __init__(self, settings, route, *, user, client=None):
        if not isinstance(user, str) or not 16 <= len(user) <= 128:
            raise Refused("A private provider user pseudonym is required")
        self.endpoint, self.key = proxy_endpoint(settings), proxy_key(settings)
        self.route, self.user, self.rates = route, user, None
        self.model, self.provider = f"openrouter/{route.model}", route.provider
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(SECONDS, connect=30),
            follow_redirects=False,
            trust_env=False,
        )

    def attempt_seconds(self, role):
        return SECONDS

    async def prepare(self, packet):
        if self.rates is None or time.time() - self.rates["verified_at"] > 86400:
            self.rates = await listed(self.client, self.route)
        return 0

    async def post(self, body):
        body = {
            **body,
            "model": self.route.model,
            "user": self.user,
            "provider": {
                "only": [self.route.provider],
                "allow_fallbacks": False,
                "zdr": True,
            },
        }
        try:
            response = await self.client.post(
                self.endpoint.removesuffix("/v1") + self.path,
                headers={"Authorization": f"Bearer {self.key}", **PRIVATE},
                json=body,
            )
        except httpx.ConnectError:
            raise ProviderRefused("Provider connection refused") from None
        if response.status_code in (401, 402, 403, 429):
            # Refused at the proxy or OpenRouter before any model work.
            raise ProviderRefused(
                f"Model provider HTTP {response.status_code}",
                retry_after=retry_after(response.headers.get("retry-after")),
            )
        if response.status_code != 200:
            raise Unavailable(f"Model provider HTTP {response.status_code}")
        data = response.json()
        usage = {k: v for k, v in (data.get("usage") or {}).items() if k in USAGE}
        usage["receipt_id"] = data.get("id")
        result = EngineResponse(
            content=data, usage=usage, actual_nanos=cost_nanos(usage)
        )
        if str(data.get("provider", "")).split()[0].lower() != self.route.provider:
            failure = Unavailable("Provider returned an unqualified route")
            failure.receipt = result  # The provider worked: its cost counts.
            raise failure
        return result


class EmbeddingEngine(PassThrough):
    name = "embeddings"
    path = "/openrouter/api/v1/embeddings"

    async def generate(self, role, packet):
        if role != EMBED_ROLE:
            raise Refused("Unsupported embedding role")
        texts = packet["texts"]
        if (
            not 1 <= len(texts) <= MAX_INPUTS
            or sum(len(t.encode()) for t in texts) > MAX_INPUT_BYTES
        ):
            raise Refused("Embedding input exceeds its bounded request")
        body = {"input": texts}
        if self.route.dimensions:
            body["dimensions"] = self.route.dimensions
        return await self.post(body)


class RerankEngine(PassThrough):
    name = "rerank"
    path = "/openrouter/api/v1/rerank"

    async def generate(self, role, packet):
        if role != RERANK_ROLE:
            raise Refused("Unsupported rerank role")
        documents = packet["documents"]
        if (
            not 1 <= len(documents) <= MAX_INPUTS
            or sum(len(d.encode()) for d in documents) > MAX_INPUT_BYTES
        ):
            raise Refused("Rerank input exceeds its bounded request")
        return await self.post(
            {"query": packet["query"], "documents": documents, "top_n": len(documents)}
        )


def validate(role, response, packet):
    """Vectors in input order, or every document's relevance; anything else
    is a fixed content-free format failure."""
    data = response.content
    try:
        if role == EMBED_ROLE:
            rows = sorted(data["data"], key=lambda row: row["index"])
            if [row["index"] for row in rows] != list(range(len(packet["texts"]))):
                raise InvalidOutput("embedding_count")
            vectors = [row["embedding"] for row in rows]
            size = len(vectors[0])
            if (
                not size
                or any(len(v) != size for v in vectors)
                or any(
                    isinstance(x, bool)
                    or not isinstance(x, (int, float))
                    or not math.isfinite(x)
                    for v in vectors
                    for x in v
                )
            ):
                raise InvalidOutput("embedding_vector")
            return [normalized(v) for v in vectors]
        if role == RERANK_ROLE:
            results = data["results"]
            indexes = [r["index"] for r in results]
            if sorted(indexes) != list(range(len(packet["documents"]))):
                raise InvalidOutput("rerank_results")
            scores = {r["index"]: r["relevance_score"] for r in results}
            if any(
                isinstance(s, bool)
                or not isinstance(s, (int, float))
                or not math.isfinite(s)
                for s in scores.values()
            ):
                raise InvalidOutput("rerank_scores")
            return [scores[i] for i in range(len(packet["documents"]))]
    except InvalidOutput:
        raise
    except Exception:
        raise InvalidOutput("retrieval_schema") from None
    raise InvalidOutput("retrieval_role")
