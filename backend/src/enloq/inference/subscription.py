"""Ask this group's writer on the owner's ChatGPT subscription (ADR 59 Q5).

The proxy's `chatgpt/gpt-6.1-sol` route reaches the subscription through
LiteLLM's Codex backend at the route's default medium effort. Its chat route
drops `response_format`, so the answer is asked for as one JSON object in the
reply and validated strictly afterwards. It calls tools in the chat route's
format (ADR 60). Spend records as $0, so no budget limits it; the owner chose
no other limit.
"""

import json
import re
import time

import httpx

from ..domain.group_questions import (
    AROUND,
    LINKED,
    NEIGHBOURS,
    QUESTION_ROLE,
    REPLIES,
    SEARCH,
    SEARCHED,
)
from ..domain.models import Refused, Unavailable
from .base import EngineResponse, ProviderRefused
from .guidance import QUESTION_CORE, QUESTION_TOOLS
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


def _tool(name, description, properties):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


MESSAGE_ID = {"message_id": {"type": "integer", "description": "A message's id"}}
# ADR 60 Q1: the writer's tools, over this group's searchable originals only.
TOOLS = [
    _tool(
        SEARCH,
        f"Search this group's saved messages by meaning, in any language. Returns "
        f"up to {SEARCHED} messages, best match first.",
        {"query": {"type": "string", "description": "What to look for"}},
    ),
    _tool(
        AROUND,
        f"The messages just before and after one message, oldest first: up to "
        f"{NEIGHBOURS} on each side, with the message itself.",
        MESSAGE_ID,
    ),
    _tool(
        LINKED,
        f"One message, the message it replies to (null when that one is not "
        f"available) and up to {REPLIES} replies to it.",
        MESSAGE_ID,
    ),
]


def conversation(packet):
    """The chat messages for one writer request: instructions, the question,
    then each round of tool calls with their results (ADR 60)."""
    tools = bool(packet.get("tools"))
    content = {k: packet[k] for k in ("question", "today", "messages") if k in packet}
    messages = [
        {
            "role": "system",
            "content": QUESTION_CORE + (QUESTION_TOOLS if tools else "") + JSON_REPLY,
        },
        {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
    ]
    for exchange in packet.get("rounds", ()):
        calls = exchange["calls"]
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(
                                call["arguments"], ensure_ascii=False
                            ),
                        },
                    }
                    for call in calls
                ],
            }
        )
        messages += [
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(
                    exchange["results"][call["id"]], ensure_ascii=False
                ),
            }
            for call in calls
        ]
    return messages


class SubscriptionEngine:
    name, model, provider = "chatgpt", MODEL, "openai"
    # ADR 60: this route calls tools.
    tools = True

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
        body = {"model": MODEL, "user": self.user, "messages": conversation(packet)}
        if packet.get("tools"):
            # The last allowed request must answer (ADR 60 Q2).
            body.update(
                tools=TOOLS, tool_choice="none" if packet.get("final") else "auto"
            )
        try:
            response = await self.client.post(
                f"{self.endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}", **PRIVATE},
                json=body,
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
        usage = {k: v for k, v in (data.get("usage") or {}).items() if k in USAGE}
        usage.update(
            cost=0, finish_reason=choice.get("finish_reason"), receipt_id=data.get("id")
        )
        if choice["message"].get("tool_calls"):
            # Validated with the answer contract (ADR 60 Q2).
            content = {"tool_calls": choice["message"]["tool_calls"]}
        else:
            # A reply without one JSON object fails validation as invalid JSON.
            found = OBJECT.search(choice["message"].get("content") or "")
            content = found.group(0) if found else ""
        return EngineResponse(content=content, usage=usage, actual_nanos=0)
