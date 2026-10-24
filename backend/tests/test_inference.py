import json
import sys
import time

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.domain.models import Refused, Unavailable
from enloq.inference.decider import MODEL as DECIDER
from enloq.inference.decider import DeciderEngine
from enloq.inference.openrouter import (
    MODEL,
    OpenRouterEngine,
    checked_endpoint,
    proxy_endpoint,
    proxy_key,
)
from enloq.inference.runner import bounded_process

from backend.tests.proxy_helpers import ELSEWHERE, ENDPOINT
from backend.tests.test_route_refusals import exact_route, model_info

USER = "authored-provider-pseudonym"


def proxy_settings(tmp_path, endpoint):
    private_write(tmp_path / "endpoint", endpoint)
    private_write(tmp_path / "key", "sk-synthetic-placeholder")
    return Settings(
        mode="personal",
        data=tmp_path,
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://proxy.example.test/v1",
        "https://proxy.example.test",
        "https://proxy.example.test/v2",
        "https://proxy.example.test/v1/chat",
        "https://owner:secret@proxy.example.test/v1",
        "https://owner@proxy.example.test/v1",
        "https://proxy.example.test/v1?key=secret",
        "https://proxy.example.test/v1#top",
        "https://proxy.example.test:99999/v1",
        "https:///v1",
    ],
    ids=[
        "http",
        "no-path",
        "v2",
        "below-v1",
        "credentials",
        "user",
        "query",
        "fragment",
        "port",
        "no-host",
    ],
)
def test_only_an_https_v1_proxy_url_without_credentials_is_accepted(tmp_path, endpoint):
    settings = proxy_settings(tmp_path, endpoint)
    for read in (proxy_endpoint, proxy_key):
        with pytest.raises(Refused, match="ending in /v1"):
            read(settings)
    for engine in (OpenRouterEngine, DeciderEngine):
        with pytest.raises(Refused, match="ending in /v1"):
            engine(settings, user=USER)


async def test_requests_go_to_the_configured_endpoint_on_another_host(tmp_path):
    assert checked_endpoint(f" {ENDPOINT}/\n") == ENDPOINT
    settings = proxy_settings(tmp_path, ELSEWHERE + "/")
    assert proxy_endpoint(settings) == ELSEWHERE
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.method == "GET":
            return httpx.Response(200, json=model_info(exact_route()))
        if request.url.path.endswith("/decisions"):
            return httpx.Response(
                200,
                json={"model": DECIDER, "provider": "Perplexity", "answers": {}},
            )
        return httpx.Response(
            200,
            json={
                "model": MODEL,
                "choices": [{"message": {"content": '{"text":"Authored"}'}}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mimo = OpenRouterEngine(settings, user=USER, client=client)
    assert (await mimo.verify())["endpoint"] == ELSEWHERE
    await mimo.generate(
        "response_suggestions", {"messages": [{"id": 91}], "text": "authored"}
    )
    decider = DeciderEngine(settings, user=USER, client=client)
    await decider.generate("emotion_classification", {"state": {"authored": True}})
    assert seen == [
        f"{ELSEWHERE}/model/info",
        f"{ELSEWHERE}/chat/completions",
        "https://models.example.test/litellm/openrouter/api/alpha/decisions",
    ]
    await client.aclose()


async def test_exact_route_endpoint_binding_and_receipt(tmp_path):
    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-synthetic-placeholder")
    settings = Settings(
        mode="personal",
        data=tmp_path,
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )
    seen = []

    def handler(request):
        assert request.headers["x-litellm-enable-message-redaction"] == "true"
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": MODEL,
                "provider": "Xiaomi",
                "id": "synthetic-receipt",
                "usage": {"cost": 0.0000012, "prompt_tokens": 5},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"text":"An authored rewrite"}',
                            "reasoning": "never retained",
                        },
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = OpenRouterEngine(
        settings,
        user="authored-provider-pseudonym",
        client=client,
        rates={
            "prompt": "0.00000014",
            "completion": "0.00000028",
            "verified_at": time.time(),
            "context_length": 1048576,
        },
    )
    assert await engine.prepare({}) == 151_388_160
    result = await engine.generate(
        "response_suggestions",
        {
            "messages": [{"id": 91}, {"id": 85}],
            "text": "authored",
            "tone": "friendly",
            "images": [
                {"message_id": 91, "data_url": "data:image/png;base64,YXV0aG9yZWQ="},
                {"message_id": 85, "data_url": "data:image/png;base64,ZmFrZQ=="},
            ],
        },
    )
    assert result.actual_nanos == 1200 and "reasoning" not in str(result)
    assert result.usage["finish_reason"] == "stop"
    assert seen[0]["max_tokens"] == 16384
    assert seen[0]["model"] == MODEL
    # The proxy route names the provider; OpenRouter routing fields are gone.
    assert "provider" not in seen[0] and "reasoning" not in seen[0]
    assert "tool_choice" not in seen[0]
    assert seen[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert seen[0]["user"] == "authored-provider-pseudonym"
    parts = seen[0]["messages"][1]["content"]
    assert json.loads(parts[1]["text"])["native_message_id"] == 91
    assert parts[2]["image_url"]["url"].endswith("YXV0aG9yZWQ=")
    assert json.loads(parts[3]["text"])["native_message_id"] == 85
    assert parts[4]["image_url"]["url"].endswith("ZmFrZQ==")
    private_write(tmp_path / "endpoint", "https://unrelated.invalid/api")
    with pytest.raises(Refused):
        OpenRouterEngine(settings, user="authored-provider-pseudonym", client=client)
    private_write(tmp_path / "endpoint", ENDPOINT)
    # An OpenRouter key is not a proxy caller key.
    private_write(tmp_path / "key", "sk-or-synthetic-placeholder")
    with pytest.raises(Refused):
        OpenRouterEngine(settings, user="authored-provider-pseudonym", client=client)
    private_write(tmp_path / "key", "sk-synthetic-placeholder")
    for user in (None, "", "short"):
        with pytest.raises(Refused):
            OpenRouterEngine(settings, user=user, client=client)
    await client.aclose()


async def test_reply_without_usage_cost_takes_the_proxy_header_cost(tmp_path):
    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-synthetic-placeholder")
    settings = Settings(
        mode="personal",
        data=tmp_path,
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )

    def handler(request):
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0.0000021"},
            json={
                "model": MODEL,
                "id": "synthetic-receipt",
                "usage": {"prompt_tokens": 5, "completion_tokens": 7},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"text":"An authored rewrite"}'},
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = OpenRouterEngine(
        settings, user="authored-provider-pseudonym", client=client
    )
    result = await engine.generate(
        "response_suggestions", {"messages": [{"id": 91}], "text": "authored"}
    )
    assert result.usage["cost"] == 0.0000021 and result.actual_nanos == 2100
    await client.aclose()


async def test_bounded_subprocess_cancellation_and_output_cap(tmp_path):
    with pytest.raises(Unavailable):
        await bounded_process(
            [sys.executable, "-c", "print('x'*10000)"],
            stdin=b"",
            cwd=tmp_path,
            env={},
            stdout_limit=100,
        )
    with pytest.raises(TimeoutError):
        await bounded_process(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=b"",
            cwd=tmp_path,
            env={},
            timeout=0.05,
        )
