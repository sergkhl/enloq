"""Exercise the ADR 29 finite check helper's real paths without network access."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.inference.guidance import DEFAULTS_REVISION
from enloq.inference.openrouter import MODEL
from enloq.storage.accounts import AccountRegistry

from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_route_refusals import exact_route, model_info, priced

spec = importlib.util.spec_from_file_location(
    "route_check", Path(__file__).parents[2] / "scripts/check-route.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def output(schema):
    properties = schema["properties"]
    if "image_context" in properties:
        image = schema["$defs"]["ImageContext"]["properties"]["message_id"]
        identities = (
            image.get("enum", []) if properties["image_context"]["minItems"] else []
        )
        return {
            "parts": [{"kind": "paragraph", "text": "Authored structured rewrite."}],
            "image_context": [
                {"message_id": identity, "text": "Authored bill lines."}
                for identity in identities
            ],
        }
    if "person" in properties:
        return {
            "person": {"add": [], "remove": [], "outdated": []},
            "account": {"add": [], "remove": [], "outdated": []},
        }
    if "options" in properties:
        return {"options": [{"purpose": "support", "text": "That sounds stressful."}]}
    return {"text": "Authored outgoing rewrite."}


async def run(tmp_path, check, *, rates=None, budget=None, unknown=False):
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
    for _ in range(3):
        registry.record_format("chat_memory", False)
    if budget is not None:
        registry.set_budget(budget)
    before = (
        registry.guidance(),
        registry.preferences(),
        registry.format_failures(),
        registry.processing_threshold(),
    )
    calls = []

    async def handler(request):
        assert request.headers["authorization"] == "Bearer sk-authored-placeholder"
        if request.method == "GET":
            assert str(request.url) == f"{ENDPOINT}/model/info"
            route = exact_route(**priced(rates))
            return httpx.Response(200, json=model_info(route))
        payload = json.loads(request.content)
        calls.append(payload)
        schema = payload["response_format"]["json_schema"]["schema"]
        content = json.dumps(output(schema), ensure_ascii=False)
        body = {
            "id": f"authored-receipt-{len(calls)}",
            "model": MODEL,
            "provider": "Xiaomi",
            "usage": {} if unknown else {"cost": 0.00001},
            "choices": [{"finish_reason": "stop"}],
        }
        if not payload.get("stream"):
            body["choices"][0]["message"] = {"content": content}
            return httpx.Response(200, json=body)
        events = []
        for index, part in enumerate([content[:12], content[12:]]):
            event = json.loads(json.dumps(body))
            event["choices"][0] = {
                "delta": {"content": part},
                "finish_reason": "stop" if index else None,
            }
            events.append("data: " + json.dumps(event) + "\n\n")
            await asyncio.sleep(0)
        return httpx.Response(200, content="".join(events) + "data: [DONE]\n\n")

    def engine_factory(settings, registry, initial, bounds):
        engine = runner.ObservedRoute.__new__(runner.ObservedRoute)
        engine.registry, engine.initial, engine.bounds = registry, initial, bounds
        engine.started = runner.time.monotonic()
        engine.requests = 0
        engine.current = None
        engine.timezone = settings.timezone
        engine.endpoint, engine.key = ENDPOINT, "sk-authored-placeholder"
        engine.user = registry.provider_user()
        engine.rates = None
        engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return engine

    await runner.observe(check, settings, root=tmp_path, engine_factory=engine_factory)
    report = json.loads(
        next(
            (tmp_path / ".local").glob(f"route-check-{check}-*/report.json")
        ).read_text()
    )
    after = (
        registry.guidance(),
        registry.preferences(),
        registry.format_failures(),
        registry.processing_threshold(),
    )
    return report, calls, before, after, registry


@pytest.mark.parametrize(
    "check,roles",
    [
        (
            1,
            ["incoming_rewrite"] * 5 + ["chat_memory", "response_suggestions"],
        ),
        (2, ["incoming_rewrite"] * 8),
        (3, ["incoming_rewrite"] * 13),
    ],
)
async def test_each_check_makes_one_first_attempt_per_case_within_bounds(
    tmp_path, check, roles
):
    report, calls, before, after, registry = await run(tmp_path, check)
    assert "stopped" not in report
    assert report["requests"] == len(calls) == len(roles)
    assert len(calls) <= report["bounds"]["requests"]
    assert [call["role"] for r in report["results"] for call in r["calls"]] == roles
    assert report["counted_test_nanos"] == len(roles) * 10_000
    assert report["telegram_calls"] == 0
    assert report["defaults_revision"] == DEFAULTS_REVISION
    assert {
        call["defaults_revision"] for r in report["results"] for call in r["calls"]
    } == {DEFAULTS_REVISION}
    # Owner settings and the running route's format counts stay untouched.
    assert after == before and registry.paused_roles() == {"chat_memory"}
    assert {(f["role"], f["ok"]) for f in report["format"]} <= {
        (role, True) for role in roles
    }
    for result in report["results"]:
        assert result["outcome"][0]["state"] == "done", result["name"]
    user = registry.provider_user()
    for payload, role in zip(calls, roles):
        assert payload["user"] == user and "tool_choice" not in payload
        assert payload["model"] == MODEL
        assert "provider" not in payload and "reasoning" not in payload
        assert payload["extra_body"]["thinking"] == {"type": "enabled"}
    streamed = [c for r in report["results"] for c in r["calls"] if "stream" in c]
    assert not streamed  # Packets and timings only; no raw request bodies.
    timed = [
        call
        for result in report["results"]
        for call in result["calls"]
        if call["role"] in ("incoming_rewrite", "outgoing_rewrite")
    ]
    assert all(call["first_text_seconds"] >= 0 for call in timed)
    voiced = [
        m.get("transcript", {}).get("text")
        for result in report["results"]
        for call in result["calls"]
        for m in call["packet"].get("messages", [])
        if m.get("transcript")
    ]
    assert voiced and all(voiced)
    if check == 1:
        image = report["results"][3]["calls"][0]
        assert image["attached_images"] == 1


async def test_unknown_outcomes_stop_at_the_counted_spending_bound(tmp_path):
    report, calls, *_ = await run(tmp_path, 2, unknown=True)
    # $0.03 allows two flat unknown estimates before a third would exceed it.
    assert report["requests"] == len(calls) == 3
    assert report["stopped"].startswith("Finite check")


async def test_rate_increase_refuses_before_any_request(tmp_path):
    rates = {"prompt": "0.00000015", "completion": "0.00000028"}
    report, calls, *_ = await run(tmp_path, 1, rates=rates)
    assert not calls and report["requests"] == 0
    assert "Rate increase" in report["stopped"]


async def test_reached_shared_budget_makes_no_request(tmp_path):
    report, calls, *_ = await run(tmp_path, 1, budget=0)
    assert not calls and report["stopped"]


async def test_a_continuation_selects_cases_and_can_only_lower_bounds(tmp_path):
    with pytest.raises(ValueError):
        await runner.observe(2, Settings(), root=tmp_path, requests=13)
    with pytest.raises(ValueError):
        await runner.observe(1, Settings(), root=tmp_path, cases=["single_point"])
    selected = ["parallel_items", "rambling_transcript"]
    original = runner.observe

    async def observe(check, settings, **options):
        return await original(
            check,
            settings,
            cases=selected,
            requests=2,
            note="Authored note.",
            **options,
        )

    runner.observe = observe
    try:
        report, calls, *_ = await run(tmp_path, 2)
    finally:
        runner.observe = original
    assert [r["name"] for r in report["results"]] == selected
    assert report["bounds"]["requests"] == 2 and len(calls) == 2
    assert report["selected_cases"] == selected and report["note"] == "Authored note."
    assert all(call["raw"] for r in report["results"] for call in r["calls"])
