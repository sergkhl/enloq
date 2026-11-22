"""Sample LiteLLM proxy endpoints for authored model-route tests.

The service reads its proxy endpoint from configuration (ADR 39); tests write
one of these as sample data and expect requests at the URLs derived from it."""

import json

from enloq.inference.openrouter import SCHEMA_REPLY

ENDPOINT = "https://llm.globesoul.com/v1"
# The proxy's OpenRouter pass-through, beside /v1.
DECISIONS = "https://llm.globesoul.com/openrouter/api/alpha/decisions"
HOSTED = "https://llm.globesoul.com/openrouter/api/v1/audio/transcriptions"
# Another deployment's proxy, on its own host below a path prefix.
ELSEWHERE = "https://models.example.test/litellm/v1"


def request_schema(payload):
    """The JSON Schema a generation request asks for: Muse's strict
    `response_format`, or the schema ending Terra's instructions (ADR 29 Q15)."""
    if "response_format" in payload:
        return payload["response_format"]["json_schema"]["schema"]
    system = payload["messages"][0]["content"]
    return json.loads(system.rsplit(SCHEMA_REPLY, 1)[1])
