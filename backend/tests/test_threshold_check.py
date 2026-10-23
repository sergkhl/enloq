"""Drive the CT5 finite qualification helper without a live provider or Telegram."""

import asyncio
import importlib.util
import json
import stat
import time
from pathlib import Path

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.domain.models import Refused
from enloq.inference.guidance import DEFAULTS_REVISION, instructions, snapshot
from enloq.inference.openrouter import (
    ADOPTED_RATES,
    BACKGROUND_OUTPUT_TOKENS,
    MODEL,
    output_schema,
)
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry
from enloq.storage.lock import OwnerLock

from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_route_refusals import exact_route, model_info, priced

spec = importlib.util.spec_from_file_location(
    "threshold_check", Path(__file__).parents[2] / "scripts/check-threshold.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def settings_at(tmp_path):
    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-authored-placeholder")
    return Settings(
        mode="personal",
        data=tmp_path / "accounts",
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )


def owner_state(registry):
    return {
        "metadata": registry.rows(
            "SELECT key,value FROM metadata WHERE key!='budget_day' ORDER BY key"
        ),
        "guidance": registry.guidance(),
        "preferences": registry.preferences(),
        "format": registry.format_failures(),
        "threshold": registry.processing_threshold(),
        "budget_changes": registry.rows("SELECT * FROM budget_changes"),
    }


async def run(
    tmp_path,
    *,
    rates=None,
    missing_route=False,
    budget=None,
    threshold=None,
    prior_spent=0,
    unknown_at=None,
    malformed_at=None,
    refused_at=None,
    incomplete_at=None,
    hanging_at=None,
    deadline_at=None,
    cost=0.00001,
    upstream=None,
    **bounds,
):
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    registry.provider_user()
    registry.set_concurrency(3)
    registry.set_guidance({"faithfulness": "Keep each authored conditional request."})
    for _ in range(3):
        registry.record_format("incoming_rewrite", False)
        registry.record_format("chat_memory", False)
    if budget is not None:
        registry.set_budget(budget)
    if threshold is not None:
        registry.set_processing_threshold(threshold)
    if prior_spent:
        day = registry.budget(timezone=settings.timezone)["day"]
        with registry.transaction() as db:
            db.execute(
                "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created) VALUES "
                "(9,'existing-owner-receipt','existing','incoming_rewrite','existing',"
                "?,'mimo',?,?, 'receipted',?)",
                ("day:" + day, FLAT_UNKNOWN, prior_spent, time.time()),
            )
    before = owner_state(registry)
    calls, listings, engines = [], [], []

    async def handler(request):
        assert request.headers["authorization"] == "Bearer sk-authored-placeholder"
        if request.method == "GET":
            assert str(request.url) == f"{ENDPOINT}/model/info"
            listings.append(request)
            route = exact_route(**priced(rates))
            return httpx.Response(
                200, json=model_info(*([] if missing_route else [route]))
            )
        assert request.method == "POST"
        assert str(request.url) == f"{ENDPOINT}/chat/completions"
        payload = json.loads(request.content)
        calls.append(payload)
        number = len(calls)
        if number == refused_at:
            return httpx.Response(429)
        schema = payload["response_format"]["json_schema"]["schema"]
        parts = schema["properties"]["parts"]
        image_field = schema["properties"]["image_context"]
        identities = (
            schema["$defs"]["ImageContext"]["properties"]["message_id"]["enum"]
            if image_field["minItems"]
            else []
        )
        content = json.dumps(
            {
                "parts": (
                    [{"kind": "paragraph", "text": "Authored incoming result."}]
                    if parts.get("minItems") and number != malformed_at
                    else []
                ),
                "image_context": [
                    {"message_id": identity, "text": "Authored sample bill."}
                    for identity in identities
                ],
            },
            ensure_ascii=False,
        )
        usage = {} if number == unknown_at else {"cost": cost}
        if upstream is not None:
            usage["cost_details"] = {"upstream_inference_cost": upstream}
        events = []
        for index, part in enumerate([content[:60], content[60:]]):
            event = {
                "id": f"authored-threshold-receipt-{number}",
                "model": MODEL,
                "provider": "Xiaomi",
                "usage": usage if index else {},
                "choices": [
                    {
                        "delta": {"content": part},
                        "finish_reason": "stop" if index else None,
                    }
                ],
            }
            events.append("data: " + json.dumps(event) + "\n\n")
            await asyncio.sleep(0)
            if number == incomplete_at:
                break
        if number == hanging_at:

            class HangingStream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield events[0].encode()
                    await asyncio.Event().wait()

            return httpx.Response(200, stream=HangingStream())
        if number == deadline_at:
            # Advance just this fixture's elapsed clock, without real waiting.
            engines[0].started -= runner.BOUNDS["seconds"]
        return httpx.Response(
            200,
            content="".join(events)
            + ("" if number == incomplete_at else "data: [DONE]\n\n"),
        )

    def engine_factory(settings, registry, initial, bounds):
        engine = runner.ObservedThreshold(
            settings,
            registry,
            initial,
            bounds,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        engines.append(engine)
        return engine

    report = await runner.observe(
        settings, root=tmp_path, engine_factory=engine_factory, **bounds
    )
    private_report = next((tmp_path / ".local").glob("threshold-check-*/report.json"))
    assert json.loads(private_report.read_text()) == report
    assert stat.S_IMODE(private_report.stat().st_mode) == 0o600
    assert stat.S_IMODE(private_report.parent.stat().st_mode) == 0o700
    assert engines[0].client.is_closed
    return report, calls, listings, before, owner_state(registry), registry


@pytest.mark.parametrize("threshold", [None, 0, 10_000])
async def test_six_first_streamed_attempts_preserve_owner_settings_and_format_counts(
    tmp_path, threshold, capsys
):
    report, calls, listings, before, after, registry = await run(
        tmp_path, threshold=threshold
    )
    assert report["format_verified"] and report["within_bounds"]
    assert "stopped" not in report and not report["unverified_cases"]
    assert report["requests"] == len(calls) == 6
    assert len(listings) == 1
    assert report["bounds"] == runner.BOUNDS
    assert report["counted_test_nanos"] == 60_000
    assert report["telegram_calls"] == 0
    assert report["defaults_revision"] == DEFAULTS_REVISION
    assert before == after
    assert registry.paused_roles() == {"incoming_rewrite", "chat_memory"}
    assert report["saved_processing_threshold"] == (
        200 if threshold is None else threshold
    )
    assert [r["name"] for r in report["results"]] == [c["name"] for c in runner.CASES]
    assert [r["original_characters"] for r in report["results"][:2]] == [199, 200]
    assert all(r["outcome"][0]["state"] == "done" for r in report["results"])
    assert all(len(r["calls"]) == 1 for r in report["results"])
    assert report["format"] == [{"role": "incoming_rewrite", "ok": True}] * 6
    observed = [r["calls"][0] for r in report["results"]]
    assert [c["parts_min_items"] for c in observed] == [1, 1, 1, 1, 1, 0]
    assert [c["attached_images"] for c in observed] == [0, 0, 0, 0, 1, 1]
    transcript = observed[3]["packet"]["messages"][0]
    assert not transcript["text"] and transcript["transcript"]["text"]
    assert observed[5]["packet"]["context"][0]["text"]
    assert report["results"][5]["rendered"] == [{"rendered": "", "state": "ready"}]
    assert report["results"][5]["image_context"] == ["Authored sample bill."]
    for payload, observation in zip(calls, observed):
        assert payload["stream"] and payload["stream_options"]["include_usage"]
        assert payload["model"] == MODEL
        # The proxy route names its provider; no OpenRouter routing is sent.
        assert "provider" not in payload and "reasoning" not in payload
        assert payload["extra_body"] == {"thinking": {"type": "enabled"}}
        assert payload["max_tokens"] == BACKGROUND_OUTPUT_TOKENS
        assert observation["request_bound_nanos"] >= FLAT_UNKNOWN
        assert (
            observation["request_bound_nanos"] >= observation["conservative_cost_nanos"]
        )
        assert observation["input_token_bound"] >= observation["prepared_request_bytes"]
        assert observation["raw"]
        assert "key" not in observation and "guidance" not in observation["packet"]
        assert all(
            c["type"] in {"text", "image_url"}
            for c in payload["messages"][1]["content"]
        )
    assert all(c["first_text_seconds"] >= 0 for c in observed[:5])
    terminal = capsys.readouterr().out
    for private_text in (
        "Authored incoming result.",
        "Привет!",
        "authored:",
        "sk-authored-placeholder",
        registry.provider_user(),
    ):
        assert private_text not in terminal


@pytest.mark.parametrize("unknown_at", [1, 2])
async def test_unknown_outcome_counts_and_stops_without_another_case(
    tmp_path, unknown_at
):
    report, calls, _, before, after, registry = await run(
        tmp_path, unknown_at=unknown_at
    )
    assert report["requests"] == len(calls) == unknown_at
    assert report["counted_test_nanos"] == FLAT_UNKNOWN + (unknown_at - 1) * 10_000
    assert report["stopped"].startswith("Unknown provider outcome")
    assert not report["format_verified"] and report["within_bounds"]
    assert before == after and registry.budget()["unknown"] == 1
    assert runner.CASES[unknown_at - 1]["name"] in report["unverified_cases"]
    assert report["results"][-1]["reservations"] == [
        {"status": "unknown", "actual": None}
    ]


async def test_interrupted_stream_keeps_first_private_partial_and_unknown_accounting(
    tmp_path,
):
    report, calls, *_ = await run(tmp_path, incomplete_at=1)
    assert len(calls) == report["requests"] == 1
    assert report["counted_test_nanos"] == FLAT_UNKNOWN
    assert report["stopped"].startswith("Unknown provider outcome")
    result = report["results"][0]
    assert result["outcome"][0]["state"] == "failed"
    assert result["calls"][0]["raw"].startswith('{"parts":')
    assert result["rendered"] == [{"rendered": None, "state": "failed"}]
    assert not report["format_verified"] and len(report["unverified_cases"]) == 6


async def test_deadline_cancellation_retains_partial_second_outcome_and_spending(
    tmp_path,
):
    report, calls, _, before, after, registry = await run(
        tmp_path, hanging_at=2, seconds=1
    )
    assert len(calls) == report["requests"] == 2
    assert report["stopped"].startswith("Deadline reached")
    assert report["counted_test_nanos"] == FLAT_UNKNOWN + 10_000
    assert registry.budget()["unknown"] == 1 and before == after
    partial = report["results"][1]
    assert partial["calls"][0]["raw"].startswith('{"parts":')
    assert partial["outcome"][0]["state"] == "failed"
    assert partial["rendered"] == [{"rendered": None, "state": "unknown"}]
    assert len(report["unverified_cases"]) == 5
    assert not report["format_verified"]


@pytest.mark.parametrize("budget", [0, FLAT_UNKNOWN - 1])
async def test_shared_day_allowance_must_cover_the_unknown_bound_before_dispatch(
    tmp_path, budget
):
    report, calls, listings, before, after, registry = await run(
        tmp_path, budget=budget
    )
    assert listings and not calls and report["requests"] == 0
    assert report["stopped"].startswith("Remaining Daily Budget")
    assert report["counted_test_nanos"] == 0 and before == after
    assert not registry.rows("SELECT * FROM model_reservations")
    assert len(report["unverified_cases"]) == 6


async def test_shared_day_remaining_stops_after_one_known_receipt(tmp_path):
    report, calls, _, before, after, registry = await run(tmp_path, budget=FLAT_UNKNOWN)
    assert len(calls) == 1 and report["counted_test_nanos"] == 10_000
    assert report["stopped"].startswith("Remaining Daily Budget")
    assert report["final_budget"]["spent"] == 10_000
    assert registry.budget()["unknown"] == 0 and before == after
    assert len(report["unverified_cases"]) == 5 and report["within_bounds"]


async def test_finite_remaining_must_cover_the_next_unknown_bound(tmp_path):
    report, calls, *_ = await run(tmp_path, counted_nanos=FLAT_UNKNOWN + 9_999)
    assert len(calls) == report["requests"] == 1
    assert report["stopped"].startswith("Remaining finite allowance")
    assert report["counted_test_nanos"] == 10_000
    assert len(report["unverified_cases"]) == 5 and report["within_bounds"]


async def test_normal_older_work_quarter_reserve_is_preserved(tmp_path):
    report, calls, _, before, after, registry = await run(
        tmp_path, budget=200_000_000, prior_spent=150_000_000
    )
    assert not calls and report["counted_test_nanos"] == 0
    assert report["stopped"].startswith("Case did not dispatch")
    assert before == after and registry.budget()["spent"] == 150_000_000
    assert not report["results"][0]["reservations"]


@pytest.mark.parametrize("kind", ["raised", "missing", "nonfinite"])
async def test_route_or_rate_changes_stop_before_any_model_call(tmp_path, kind):
    rates = (
        {"prompt": "0.00000015", "completion": "0.00000028"}
        if kind == "raised"
        else {"prompt": "NaN", "completion": "0.00000028"}
        if kind == "nonfinite"
        else None
    )
    report, calls, listings, before, after, registry = await run(
        tmp_path, rates=rates, missing_route=kind == "missing"
    )
    assert len(listings) == 1 and not calls and report["requests"] == 0
    assert report["stopped"]
    assert before == after and not registry.rows("SELECT * FROM model_reservations")
    assert not report["format_verified"]


@pytest.mark.parametrize("kind", ["malformed", "refused"])
async def test_bad_first_outcomes_are_retained_without_sampling_the_case_again(
    tmp_path, kind
):
    report, calls, _, before, after, _ = await run(
        tmp_path, **{"malformed_at" if kind == "malformed" else "refused_at": 1}
    )
    assert len(calls) == report["requests"] == 6
    assert before == after and not report["format_verified"]
    first = report["results"][0]
    assert first["outcome"][0]["state"] == (
        "failed" if kind == "malformed" else "refused"
    )
    assert len(first["calls"]) == 1
    assert report["counted_test_nanos"] == (60_000 if kind == "malformed" else 50_000)
    if kind == "malformed":
        assert first["calls"][0]["raw"] == '{"parts": [], "image_context": []}'
        assert "omitted_target_text" in first["receipts"][0]["usage"]
        assert report["format"][0] == {"role": "incoming_rewrite", "ok": False}
    else:
        assert first["reservations"] == [{"status": "refused", "actual": 0}]


async def test_upstream_inference_cost_counts_when_charged_cost_is_zero(tmp_path):
    report, calls, *_ = await run(tmp_path, cost=0, upstream=0.00002)
    assert len(calls) == 6 and report["counted_test_nanos"] == 120_000
    assert report["format_verified"]


async def test_unexpected_receipt_above_prepared_bound_stops_the_pass(tmp_path):
    report, calls, *_ = await run(tmp_path, cost=0.012)
    assert len(calls) == report["requests"] == 1
    assert report["stopped"].startswith("Receipt exceeded the conservative")
    assert report["counted_test_nanos"] == 12_000_000
    assert not report["format_verified"] and report["within_bounds"]


async def test_lower_request_bound_records_partial_unverified_cases(tmp_path):
    report, calls, *_ = await run(tmp_path, requests=2)
    assert len(calls) == report["requests"] == report["bounds"]["requests"] == 2
    assert report["stopped"].startswith("Finite request or time bound")
    assert report["unverified_cases"] == [c["name"] for c in runner.CASES[2:]]
    assert not report["format_verified"] and report["within_bounds"]


async def test_deadline_guard_stops_between_first_attempts(tmp_path):
    report, calls, *_ = await run(tmp_path, deadline_at=1)
    assert len(calls) == 1
    assert report["stopped"].startswith("Finite request or time bound")
    assert (
        not report["format_verified"]
        and report["elapsed_seconds"] >= runner.BOUNDS["seconds"]
    )


@pytest.mark.parametrize(
    "bounds",
    [
        {"requests": 7},
        {"seconds": 601},
        {"counted_nanos": 50_000_001},
        {"requests": 0},
        {"seconds": True},
    ],
)
async def test_recorded_bounds_cannot_be_raised_or_disabled(tmp_path, bounds):
    with pytest.raises(ValueError, match="only lower"):
        await runner.observe(Settings(), root=tmp_path, **bounds)


async def test_exclusive_service_owner_lock_prevents_even_route_verification(tmp_path):
    def no_engine(*args):
        pytest.fail("A concurrent owner must prevent constructing the engine")

    with OwnerLock(tmp_path / ".local/service.owner.lock"):
        with pytest.raises(Refused, match="already has an owner"):
            await runner.observe(Settings(), root=tmp_path, engine_factory=no_engine)
    assert not list((tmp_path / ".local").glob("threshold-check-*"))


@pytest.mark.parametrize("ceiling", ["run", "day"])
async def test_conservative_image_input_bound_larger_than_flat_must_fit(
    tmp_path, ceiling
):
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    packet = {
        "messages": [{"id": 2, "text": "", "media": "image"}],
        "context": [{"id": 1, "text": "Authored support."}],
        "images": [
            {"message_id": 2, "data_url": "data:image/png;base64," + "a" * 100_000}
        ],
    }
    schema = output_schema(runner.ROLE, packet)
    system = instructions(runner.ROLE, packet, snapshot())
    bound = runner.conservative_request_bound(system, packet, schema, ADOPTED_RATES)
    assert bound["request_bound_nanos"] > FLAT_UNKNOWN
    assert bound["prepared_request_bytes"] > 100_000
    assert (
        bound["input_token_bound"]
        >= bound["prepared_request_bytes"] + runner.IMAGE_TOKENS
    )
    assert bound["output_token_limit"] == BACKGROUND_OUTPUT_TOKENS
    assert schema["properties"]["parts"].get("minItems", 0) == 0
    bounds = dict(runner.BOUNDS)
    if ceiling == "run":
        bounds["counted_nanos"] = bound["request_bound_nanos"] - 1
    else:
        registry.set_budget(bound["request_bound_nanos"] - 1)

    async def listing_only(request):
        assert request.method == "GET", "No provider dispatch before its bound fits"
        assert str(request.url) == f"{ENDPOINT}/model/info"
        return httpx.Response(200, json=model_info(exact_route()))

    engine = runner.ObservedThreshold(
        settings,
        registry,
        registry.budget(),
        bounds,
        client=httpx.AsyncClient(transport=httpx.MockTransport(listing_only)),
    )
    engine.current = {"calls": []}
    try:
        with pytest.raises(Refused, match="cannot cover"):
            await engine.prepare(packet)
        assert engine.requests == 0 and not engine.reservations()
        assert engine.current["request_bound_nanos"] == bound["request_bound_nanos"]
    finally:
        await engine.client.aclose()


async def test_run_accounting_includes_receipts_across_dispatch_days(tmp_path):
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    day = registry.budget(timezone=settings.timezone)["day"]
    registry.set_budget(FLAT_UNKNOWN + 5999)
    with registry.transaction() as db:
        for identity, bucket, actual in [
            ("first", "day:2000-01-01", 5000),
            ("second", "day:" + day, 6000),
        ]:
            db.execute(
                "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created) VALUES "
                "(1,?,?, 'incoming_rewrite',?,?,'mimo',?,?,'receipted',?)",
                (
                    identity,
                    identity,
                    identity,
                    bucket,
                    FLAT_UNKNOWN,
                    actual,
                    time.time(),
                ),
            )

    async def no_network(request):
        pytest.fail("Accounting guards never need provider access")

    engine = runner.ObservedThreshold(
        settings,
        registry,
        # Prior-day initial spend cannot be subtracted from current-day spend.
        {"spent": 99_000_000},
        dict(runner.BOUNDS),
        client=httpx.AsyncClient(transport=httpx.MockTransport(no_network)),
    )
    engine.attempts = ["first", "second"]
    try:
        assert engine.counted() == 11_000
        assert registry.budget()["spent"] == 6000
        with pytest.raises(Refused, match="Daily Budget cannot cover"):
            engine.admit()
    finally:
        await engine.client.aclose()


async def test_pending_old_day_reservation_never_credits_new_day_allowance(tmp_path):
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    registry.set_budget(FLAT_UNKNOWN - 1)
    with registry.transaction() as db:
        db.execute(
            "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created) VALUES "
            "(1,'reserved','reserved','incoming_rewrite','reserved',"
            "'day:2000-01-01','mimo',?,NULL,'unknown',?)",
            (FLAT_UNKNOWN, time.time()),
        )

    async def no_network(request):
        pytest.fail("Accounting guards never need provider access")

    engine = runner.ObservedThreshold(
        settings,
        registry,
        registry.budget(),
        dict(runner.BOUNDS),
        client=httpx.AsyncClient(transport=httpx.MockTransport(no_network)),
    )
    engine.attempts = ["reserved"]
    engine.current = {"attempt": "reserved"}
    try:
        assert registry.budget()["spent"] == 0 and engine.counted() == FLAT_UNKNOWN
        with pytest.raises(Refused, match="Daily Budget cannot cover"):
            engine.guard(FLAT_UNKNOWN, reserved=True)
    finally:
        await engine.client.aclose()
