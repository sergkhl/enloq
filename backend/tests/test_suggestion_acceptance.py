"""Exercise the finite suggestion runner's real paths without network or live storage."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.inference.openrouter import MODEL
from enloq.storage.accounts import AccountRegistry

from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_route_refusals import exact_route, model_info

spec = importlib.util.spec_from_file_location(
    "suggestion_acceptance", Path(__file__).parents[2] / "scripts/check-suggestions.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize(
    "mode", ["receipted", "unknown", "zero_budget", "switch_off", "format"]
)
async def test_finite_pass_uses_shared_accounting_and_stops_at_bounds(tmp_path, mode):
    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-authored-placeholder")
    settings = Settings(
        mode="personal",
        data=tmp_path / "accounts",
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )
    registry = AccountRegistry(settings.data)
    if mode == "zero_budget":
        registry.set_budget(0)
    if mode == "switch_off":
        registry.set_suggestions_enabled(False)
    before = (
        registry.preferences(),
        registry.guidance(),
        registry.reading_view(),
        registry.processing_threshold(),
    )
    calls = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer sk-authored-placeholder"
        if request.method == "GET":
            assert str(request.url) == f"{ENDPOINT}/model/info"
            route = exact_route(input_cost_per_token=1e-07, output_cost_per_token=2e-07)
            return httpx.Response(200, json=model_info(route))
        payload = json.loads(request.content)
        calls.append(payload)
        schema = payload["response_format"]["json_schema"]["schema"]
        assert set(schema["properties"]) == {"options"}
        options = (
            [{"purpose": "advice", "text": "Do this."}]
            if mode == "format"
            else [{"purpose": "support", "text": f"Authored option {len(calls)}"}]
        )
        body = {
            "id": "synthetic-receipt",
            "model": MODEL,
            "provider": "xiaomi",
            "choices": [
                {
                    "message": {"content": json.dumps({"options": options})},
                    "finish_reason": "stop",
                }
            ],
            "usage": {} if mode == "unknown" else {"cost": 0.00001},
        }
        return httpx.Response(200, json=body)

    def engine_factory(settings, registry, initial):
        # The runner's real admission/observation without a live client.
        engine = runner.ObservedRoute.__new__(runner.ObservedRoute)
        engine.registry, engine.initial = registry, initial
        engine.started = runner.time.monotonic()
        engine.requests = 0
        engine.current = None
        engine.timezone = settings.timezone
        engine.endpoint, engine.key = ENDPOINT, "sk-authored-placeholder"
        engine.user = "authored-provider-pseudonym"
        engine.rates = None
        engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return engine

    await runner.observe(settings, root=tmp_path, engine_factory=engine_factory)
    report = json.loads(
        next(
            (tmp_path / ".local").glob("suggestions-first-pass-*/report.json")
        ).read_text()
    )
    expected = {
        "receipted": 8,
        "format": 8,
        "unknown": 5,  # Each unknown outcome counts the flat $0.01.
        "zero_budget": 0,
        "switch_off": 0,
    }[mode]
    assert report["requests"] == len(calls) == expected
    assert report["counted_test_nanos"] == expected * (
        10_000_000 if mode == "unknown" else 10_000
    )
    assert (
        registry.preferences(),
        registry.guidance(),
        registry.reading_view(),
        registry.processing_threshold(),
    ) == before
    assert registry.suggestions_enabled() == (mode != "switch_off")
    assert report["telegram_calls"] == 0
    if mode in ("zero_budget", "switch_off"):
        assert report["stopped"]
    for result in report["results"][:expected]:
        packet = result["packet"]
        assert set(packet) == {
            "messages",
            "context",
            "direct_reply",
            "recipient_emotion",
            "style",
        }
        assert packet["recipient_emotion"] == result["emotion"]
        assert packet["messages"][-1]["text"] == result["input"]
        assert [m["text"] for m in packet["context"]] == [
            t for _, t in result["earlier"]
        ]
        state = result["record"]["state"]
        if mode == "format":
            assert (state, result["record"]["failure"]) == ("failed", "format")
            usage = json.loads(result["receipts"][0]["usage"])
            assert usage["format_failure"] == "schema"
        else:
            # A response without a cost receipt still publishes; it counts the
            # flat unknown amount, which is what stops the pass early.
            assert state == "ready" and result["view"]["state"] == "ready"
            assert result["record"]["options"][0]["purpose"] == "support"
