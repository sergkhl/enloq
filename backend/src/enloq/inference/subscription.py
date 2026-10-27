"""Ask this group's writer on the owner's ChatGPT subscription (ADR 59 Q5).

The proxy's `chatgpt/gpt-6.1-sol` route reaches the subscription through
LiteLLM's Codex backend at the route's default medium effort. Its chat route
drops `response_format`, so the answer is asked for as one JSON object in the
reply and validated strictly afterwards. Spend records as $0, so no budget
limits it; the owner chose no other limit.
"""

import json
import re
import time

import httpx

from ..domain.group_questions import QUESTION_ROLE
from ..domain.models import Refused, Unavailable
from .base import EngineResponse, ProviderRefused
from .guidance import QUESTION_CORE
from .openrouter import PRIVATE, proxy_endpoint, proxy_key, retry_after

MODEL = "chatgpt/gpt-6.1-sol"
SECONDS = 120
JSON_REPLY = (
    ' Reply with only a JSON object: {"statements": [{"text": "...", '
    '"citations": [message ids]}]}, with no statements when the messages do not '
    "address the question."
)
OBJECT = re.compile(r"\{.*\}", re.S)
USAGE = {"prompt_tokens", "completion_tokens", "total_tokens"}


class SubscriptionEngine:
    name, model, provider = "chatgpt", MODEL, "openai"

    def __init__(self, settings, *, user, client=None):
        if not isinstance(user, str) or not 16 <= len(user) <= 128:
            raise Refused("A private provider user pseudonym is required")
        self.endpoint, self.key = proxy_endpoint(settings), proxy_key(settings)
        self.user, self.rates = user, None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(SECONDS, connect=30),
            follow_redirects=False,
            trust_env=False,
        )

    def attempt_seconds(self, role):
        return SECONDS

    async def prepare(self, packet):
        """The proxy still serves the exact route, checked at most daily."""
        if self.rates is None or time.time() - self.rates["verified_at"] > 86400:
            response = await self.client.get(
                f"{self.endpoint}/model/info",
                headers={"Authorization": f"Bearer {self.key}"},
            )
            if response.status_code != 200:
                raise ProviderRefused(
                    f"Model proxy HTTP {response.status_code}",
                    retry_after=retry_after(response.headers.get("retry-after")),
                )
            routes = [
                r for r in response.json()["data"] if r.get("model_name") == MODEL
            ]
            if len(routes) != 1:
                raise ProviderRefused("Exact gpt-6.1-sol route unavailable")
            self.rates = {"verified_at": time.time(), "prompt": "0", "completion": "0"}
        return 0

    async def generate(self, role, packet):
        if role != QUESTION_ROLE:
            raise Refused("Unsupported subscription role")
        content = {
            k: v for k, v in packet.items() if k not in ("retrieved_ids", "guidance")
        }
        try:
            response = await self.client.post(
                f"{self.endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}", **PRIVATE},
                json={
                    "model": MODEL,
                    "user": self.user,
                    "messages": [
                        {"role": "system", "content": QUESTION_CORE + JSON_REPLY},
                        {
                            "role": "user",
                            "content": json.dumps(content, ensure_ascii=False),
                        },
                    ],
                },
            )
        except httpx.ConnectError:
            raise ProviderRefused("Provider connection refused") from None
        if response.status_code in (401, 402, 403, 429):
            raise ProviderRefused(
                f"Model provider HTTP {response.status_code}",
                retry_after=retry_after(response.headers.get("retry-after")),
            )
        if response.status_code != 200:
            raise Unavailable(f"Model provider HTTP {response.status_code}")
        data = response.json()
        choice = data["choices"][0]
        found = OBJECT.search(choice["message"].get("content") or "")
        usage = {k: v for k, v in (data.get("usage") or {}).items() if k in USAGE}
        usage.update(
            cost=0, finish_reason=choice.get("finish_reason"), receipt_id=data.get("id")
        )
        # A reply without one JSON object fails validation as invalid JSON.
        return EngineResponse(
            content=found.group(0) if found else "", usage=usage, actual_nanos=0
        )
