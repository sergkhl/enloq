"""Exercise the GB10 helper through real pipeline/HTTP parsing, wholly offline."""

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
from enloq.inference.guidance import DEFAULTS_REVISION
from enloq.inference.openrouter import BACKGROUND_OUTPUT_TOKENS, LOGICAL_MODEL, MODEL
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry
from enloq.storage.lock import OwnerLock

from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_route_refusals import exact_route, model_info, priced

spec = importlib.util.spec_from_file_location(
    "group_briefing_check",
    Path(__file__).parents[2] / "scripts/check-group-briefings.py",
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


def receipt(
    registry, settings, *, identity, family, actual, status="receipted", account_id=9
):
    day = registry.budget(timezone=settings.timezone, family=family)["day"]
    with registry.transaction() as db:
        db.execute(
            "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created,family) "
            "VALUES (?,?,?,?,'existing',?,'mimo',?,?,?,?,?)",
            (
                account_id,
                identity,
                identity,
                "group_briefing" if family == "group_briefing" else "incoming_rewrite",
                "day:" + day,
                FLAT_UNKNOWN,
                actual,
                status,
                time.time(),
                family,
            ),
        )


async def run(
    tmp_path,
    *,
    account=True,
    account_on=True,
    group_budget=None,
    daily_budget=None,
    threshold=None,
    prior_group_spent=0,
    prior_daily_spent=0,
    rates=None,
    missing_route=False,
    unknown_at=None,
    malformed_at=None,
    bad_citation_at=None,
    refused_at=None,
    incomplete_final=False,
    hanging_final=False,
    deadline_at=None,
    rollover_at=None,
    cost=0.00001,
    upstream=None,
    **bounds,
):
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    if account:
        registry.register(37, "authored.session", directory="authored-account")
        registry.set_processing(37, account_on)
    registry.provider_user()
    registry.set_concurrency(3)
    registry.set_guidance(
        {"group_briefing": "Preserve conditional plans and speaker attribution."}
    )
    for _ in range(3):
        registry.record_format("incoming_rewrite", False)
        registry.record_format("chat_memory", False)
    if group_budget is not None:
        registry.set_budget(group_budget, family="group_briefing")
    if daily_budget is not None:
        registry.set_budget(daily_budget)
    if threshold is not None:
        registry.set_processing_threshold(threshold)
    if prior_group_spent:
        receipt(
            registry,
            settings,
            identity="owner-group",
            family="group_briefing",
            actual=prior_group_spent,
        )
    if prior_daily_spent:
        receipt(
            registry,
            settings,
            identity="owner-daily",
            family="daily",
            actual=prior_daily_spent,
        )
    before = runner.protected_state(registry)
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
        packet = json.loads(payload["messages"][1]["content"][0]["text"])
        schema = payload["response_format"]["json_schema"]["schema"]
        if "image_context" in schema["properties"]:
            identities = schema["$defs"]["BriefingImageContext"]["properties"][
                "message_id"
            ]["enum"]
            output = {
                "image_context": [
                    {
                        "message_id": identity,
                        "text": "The authored timetable proposes Tuesday, corrects it to Wednesday, and says the room is unconfirmed.",
                    }
                    for identity in identities
                ]
            }
        elif packet["chat_key"].endswith("channel:881"):
            output = {"contract_version": 2, "outcome": "no_substance", "topics": []}
        else:
            output = {
                "contract_version": 2,
                "outcome": "briefing",
                "topics": [
                    {
                        "title": "Conditional workshop plans",
                        "text": "The authored workshop plans remain conditional on the hall booking and label checks.",
                        "citations": packet["covered_ids"],
                        "participant_source_ids": [packet["covered_ids"][0]],
                    }
                ],
            }
        if number == bad_citation_at:
            if "image_context" in output:
                output["image_context"][0]["message_id"] = 999
            else:
                output["topics"][0]["citations"] = [999]
        content = (
            "{" if number == malformed_at else json.dumps(output, ensure_ascii=False)
        )
        usage = {} if number == unknown_at else {"cost": cost}
        if upstream is not None:
            usage["cost_details"] = {"upstream_inference_cost": upstream}
        if number == deadline_at:
            engines[0].started -= runner.BOUNDS["seconds"]
        if number == rollover_at:
            from datetime import date, timedelta

            day = registry.budget(timezone=settings.timezone, family=runner.FAMILY)[
                "day"
            ]
            with registry.transaction() as db:
                db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('budget_day',?)",
                    ((date.fromisoformat(day) + timedelta(days=1)).isoformat(),),
                )
        if not payload.get("stream"):
            return httpx.Response(
                200,
                json={
                    "id": f"authored-group-{number}",
                    "model": MODEL,
                    "provider": "Xiaomi",
                    "usage": usage,
                    "choices": [
                        {"message": {"content": content}, "finish_reason": "stop"}
                    ],
                },
            )
        events = []
        for index, part in enumerate([content[:100], content[100:]]):
            events.append(
                "data: "
                + json.dumps(
                    {
                        "id": f"authored-group-{number}",
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
                )
                + "\n\n"
            )
            if incomplete_final:
                break
        if hanging_final:

            class HangingStream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield events[0].encode()
                    await asyncio.Event().wait()

            return httpx.Response(200, stream=HangingStream())
        return httpx.Response(
            200,
            content="".join(events) + ("" if incomplete_final else "data: [DONE]\n\n"),
        )

    def engine_factory(settings, registry, initial, bounds, *, account_id):
        engine = runner.ObservedBriefings(
            settings,
            registry,
            initial,
            bounds,
            account_id=account_id,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        engines.append(engine)
        return engine

    report = await runner.observe(
        settings, root=tmp_path, engine_factory=engine_factory, **bounds
    )
    private_report = next(
        (tmp_path / ".local").glob("group-briefing-check-*/report.json")
    )
    assert json.loads(private_report.read_text()) == report
    assert stat.S_IMODE(private_report.stat().st_mode) == 0o600
    assert stat.S_IMODE(private_report.parent.stat().st_mode) == 0o700
    assert not list((tmp_path / ".local").glob("group-briefing-fixture-*"))
    assert not (settings.data / "authored-account").exists()
    assert not (settings.data / "authored.session").exists()
    assert engines[0].client.is_closed
    assert runner.protected_state(registry) == before
    with OwnerLock(tmp_path / ".local/service.owner.lock"):
        pass
    return report, calls, listings, registry


def observations(report):
    return [
        call
        for result in report["results"]
        for stage in result["stages"]
        for call in stage["calls"]
    ]


@pytest.mark.parametrize("threshold", [None, 0, 10_000])
async def test_real_image_chunk_streamed_final_and_no_substance_path(
    tmp_path, threshold, capsys
):
    report, calls, listings, registry = await run(tmp_path, threshold=threshold)
    assert report["format_source_verified"] and report["within_bounds"]
    assert not report["unverified_cases"] and "stopped" not in report
    assert report["defaults_revision"] == DEFAULTS_REVISION
    assert report["saved_processing_threshold"] == (
        200 if threshold is None else threshold
    )
    assert len(listings) == 1 and 4 <= len(calls) <= 6
    assert report["requests"] == len(calls)
    assert report["generative_requests"] == len(calls)
    assert report["classifier_requests"] == 0
    assert report["counted_test_nanos"] == 10_000 * len(calls)
    assert report["budget_family"] == "group_briefing"
    assert (
        report["telegram_calls"]
        == report["excluded_role_jobs"]
        == report["audio_files"]
        == 0
    )
    assert report["protected_owner_state_unchanged"]
    assert report["semantic_review"] == "pending manual review"
    assert all(
        stage["source_valid"]
        for result in report["results"]
        for stage in result["stages"]
    )
    assert all(
        result["classified_non_ad_ids"] == result["intended_ids"]
        for result in report["results"]
    )
    assert registry.paused_roles() == {"incoming_rewrite", "chat_memory"}
    observed = observations(report)
    assert {c["role"] for c in observed} == runner.GROUP_ROLES
    assert (
        observed[0]["role"] == "group_briefing_image"
        and observed[0]["attached_images"] == 1
    )
    assert any(c["role"] == "group_briefing_chunk" for c in observed)
    long, short = report["results"]
    assert long["final"]["chunk_ids"] and len(long["final"]["covered_ids"]) == len(
        runner.CASES[0]["sources"]
    )
    assert short["final"]["output"] == {
        "contract_version": 2,
        "outcome": "no_substance",
        "topics": [],
    }
    assert long["final"]["output"]["contract_version"] == 2
    assert all(
        topic["title"] and topic["participant_source_ids"]
        for topic in long["final"]["output"]["topics"]
    )
    assert long["source_valid"] and short["source_valid"]
    assert long["original_characters"] > 10_000 and short["original_characters"] < 200
    source_messages = [m for c in observed for m in c["packet"].get("messages", [])]
    assert len({m["speaker"]["peer"] for m in source_messages}) == 3
    assert any(m.get("transcript", {}).get("text") for m in source_messages)
    assert any(
        m.get("forwarded_from") == "Authored hall notice" for m in source_messages
    )
    context = [m for c in observed for m in c["packet"].get("context", [])]
    assert any(m["id"] == 1 for m in context)
    assert all(m["id"] != 999 for m in context)
    assert all(1 not in c["packet"]["covered_ids"] for c in observed)
    for payload, observation in zip(calls, observed):
        assert payload["model"] == MODEL
        # The proxy route names its provider; no OpenRouter routing is sent.
        assert "provider" not in payload and "reasoning" not in payload
        assert payload["extra_body"] == {"thinking": {"type": "enabled"}}
        assert payload["max_tokens"] == BACKGROUND_OUTPUT_TOKENS
        assert payload.get("stream", False) == (observation["role"] == "group_briefing")
        assert observation["request_bound_nanos"] >= max(
            FLAT_UNKNOWN, observation["conservative_cost_nanos"]
        )
        assert (
            observation["input_token_bound"]
            >= observation["prepared_request_bytes"]
            >= len(json.dumps(payload, ensure_ascii=False).encode())
        )
        assert observation["raw"] and "guidance" not in observation["packet"]
        if observation["role"] == "group_briefing" and observation["packet"][
            "chat_key"
        ].endswith("channel:880"):
            assert (
                observation["preview_updates"] > 0
                and observation["first_text_seconds"] >= 0
            )
    rows = registry.rows(
        "SELECT account_id,family,role,status,actual FROM model_reservations"
    )
    assert len(rows) == len(calls)
    assert all(
        r["account_id"] == 37
        and r["family"] == "group_briefing"
        and r["role"] in runner.GROUP_ROLES
        and r["status"] == "receipted"
        for r in rows
    )
    shared_receipts = registry.rows("SELECT family,model,provider FROM model_receipts")
    assert len(shared_receipts) == len(rows)
    assert all(
        r == {"family": "group_briefing", "model": LOGICAL_MODEL, "provider": "xiaomi"}
        for r in shared_receipts
    )
    terminal = capsys.readouterr().out
    assert json.loads(terminal)["requests"] == len(calls)
    assert (
        "Authored" not in terminal
        and "sk-authored-placeholder" not in terminal
        and "user:101" not in terminal
    )


@pytest.mark.parametrize("unknown_at", [1, 2, 4])
async def test_unknown_first_receipt_stops_every_further_stage_and_keeps_ledger(
    tmp_path, unknown_at
):
    report, calls, _, registry = await run(tmp_path, unknown_at=unknown_at)
    assert len(calls) == report["requests"] == unknown_at
    assert report["counted_test_nanos"] == FLAT_UNKNOWN + (unknown_at - 1) * 10_000
    assert report["stopped"].startswith("Unknown provider outcome")
    assert not report["format_source_verified"] and len(report["unverified_cases"]) == 2
    assert registry.budget(family=runner.FAMILY)["unknown"] == 1
    stage = report["results"][0]["stages"][-1]
    assert stage["source_valid"] and stage["format_valid"] is None
    assert stage["calls"][0]["raw"]
    assert stage["reservations"][0]["status"] == "unknown"


@pytest.mark.parametrize("failure", ["malformed_at", "bad_citation_at"])
@pytest.mark.parametrize("number", [1, 2, 4])
async def test_bad_first_output_keeps_receipt_and_source_proof_without_resampling(
    tmp_path, failure, number
):
    report, calls, _, registry = await run(tmp_path, **{failure: number})
    assert len(calls) == number and report["counted_test_nanos"] == number * 10_000
    assert report["stopped"].startswith("First stage did not complete")
    assert not report["format_source_verified"]
    stage = report["results"][0]["stages"][-1]
    assert stage["format_valid"] is False and stage["source_valid"]
    assert stage["outcome"][0]["state"] == "failed"
    assert stage["calls"][0]["raw"]
    assert all(
        r["status"] == "receipted"
        for r in registry.rows("SELECT status FROM model_reservations")
    )
    assert json.loads(stage["receipts"][0]["usage"])["format_failure"]


@pytest.mark.parametrize("number", [1, 2, 4])
async def test_zero_work_refusal_stops_without_automatic_route_retry(tmp_path, number):
    report, calls, _, registry = await run(tmp_path, refused_at=number)
    assert len(calls) == number
    assert report["stopped"].startswith("First provider attempt refused")
    assert report["counted_test_nanos"] == (number - 1) * 10_000
    assert registry.rows(
        "SELECT status,actual FROM model_reservations WHERE status='refused'"
    ) == [{"status": "refused", "actual": 0}]
    assert report["results"][0]["stages"][-1]["format_valid"] is None


async def test_interrupted_stream_preserves_first_partial_and_unknown_cost(tmp_path):
    report, calls, *_ = await run(tmp_path, incomplete_final=True)
    assert len(calls) == 4 and report["counted_test_nanos"] == FLAT_UNKNOWN + 30_000
    assert report["stopped"].startswith("Unknown provider outcome")
    stage = report["results"][0]["stages"][-1]
    assert (
        stage["role"] == "group_briefing" and stage["outcome"][0]["state"] == "failed"
    )
    assert stage["calls"][0]["raw"].startswith('{"contract_version": 2,')
    assert not report["format_source_verified"]


async def test_deadline_cancels_stream_cleans_fixture_and_retains_unknown_attempt(
    tmp_path,
):
    report, calls, _, registry = await run(tmp_path, hanging_final=True, seconds=2)
    assert len(calls) == 4 and report["counted_test_nanos"] == FLAT_UNKNOWN + 30_000
    assert report["stopped"].startswith("Deadline reached")
    assert registry.budget(family=runner.FAMILY)["unknown"] == 1
    assert report["results"][0]["stages"][-1]["calls"][0]["raw"].startswith(
        '{"contract_version": 2,'
    )
    assert report["telegram_calls"] == report["excluded_role_jobs"] == 0
    assert not report["format_source_verified"]


@pytest.mark.parametrize("group_budget", [0, FLAT_UNKNOWN - 1])
async def test_group_budget_must_cover_unknown_allowance_before_any_dispatch(
    tmp_path, group_budget
):
    report, calls, listings, registry = await run(tmp_path, group_budget=group_budget)
    assert listings and not calls
    assert report["stopped"].startswith("Remaining Group Briefing Budget")
    assert report["counted_test_nanos"] == 0
    assert not registry.rows("SELECT id FROM model_reservations")


@pytest.mark.parametrize("pool", ["run", "day"])
async def test_actual_prepared_image_bound_can_refuse_after_unknown_admission(
    tmp_path, pool
):
    # The complete authored image request costs more than the flat unknown
    # estimate. Admission passes, but the exact prepared cap must stop HTTP.
    kwargs = (
        {"counted_nanos": FLAT_UNKNOWN}
        if pool == "run"
        else {"group_budget": FLAT_UNKNOWN}
    )
    report, calls, _, registry = await run(tmp_path, **kwargs)
    assert not calls and report["requests"] == 0
    stage = report["results"][0]["stages"][-1]
    assert stage["stage"] == "image" and stage["request_bound_nanos"] > FLAT_UNKNOWN
    assert not stage["reservations"] and not registry.rows(
        "SELECT id FROM model_reservations"
    )
    assert report["stopped"].startswith(
        "Remaining finite" if pool == "run" else "Remaining Group Briefing Budget"
    )


async def test_shared_group_spending_from_other_account_restricts_this_pass(tmp_path):
    report, calls, _, registry = await run(
        tmp_path, group_budget=20_000_000, prior_group_spent=9_000_000
    )
    assert not calls and report["counted_test_nanos"] == 0
    assert report["stopped"].startswith("Remaining Group Briefing Budget")
    assert registry.budget(family=runner.FAMILY)["spent"] == 9_000_000


async def test_separate_daily_ceiling_and_old_quarter_reserve_do_not_gate_group_work(
    tmp_path,
):
    report, calls, _, registry = await run(
        tmp_path,
        group_budget=200_000_000,
        prior_group_spent=150_000_000,
        daily_budget=0,
        prior_daily_spent=70_000_000,
    )
    assert report["format_source_verified"] and len(calls) == 5
    assert report["final_budget"]["spent"] == 150_050_000
    assert registry.budget()["spent"] == 70_000_000
    assert registry.budget()["limit"] == 0


@pytest.mark.parametrize(
    "charged,upstream", [(0.00001, 0.00002), (0.00002, 0.00001), (0, 0)]
)
async def test_larger_known_receipt_counted_in_both_shared_and_local_ledgers(
    tmp_path, charged, upstream
):
    report, calls, _, registry = await run(tmp_path, cost=charged, upstream=upstream)
    actual = round(max(charged, upstream) * 1_000_000_000)
    assert report["format_source_verified"]
    assert (
        report["counted_test_nanos"]
        == report["final_budget"]["spent"]
        == actual * len(calls)
    )
    assert all(
        r["actual"] == actual
        for r in registry.rows("SELECT actual FROM model_reservations")
    )
    assert all(
        r["actual"] == actual
        for result in report["results"]
        for stage in result["stages"]
        for r in stage["reservations"]
    )


async def test_run_allowance_remains_across_immutable_dispatch_days(tmp_path):
    report, calls, _, registry = await run(tmp_path, rollover_at=1)
    rows = registry.rows(
        "SELECT bucket,actual FROM model_reservations ORDER BY created"
    )
    assert report["format_source_verified"] and len(calls) == 5
    assert len({r["bucket"] for r in rows}) == 2
    assert rows[0]["bucket"] != rows[1]["bucket"]
    assert report["counted_test_nanos"] == 50_000
    assert report["final_budget"]["spent"] == 40_000
    assert report["initial_budget"]["day"] != report["final_budget"]["day"]


async def test_new_day_cannot_replenish_the_finite_run_cap(tmp_path):
    report, calls, *_ = await run(
        tmp_path, rollover_at=1, cost=0.003, counted_nanos=12_000_000
    )
    assert len(calls) == 1 and report["counted_test_nanos"] == 3_000_000
    assert report["final_budget"]["spent"] == 0
    assert report["stopped"].startswith("Remaining finite allowance")


@pytest.mark.parametrize("limit", [1, 4, 5])
async def test_request_limit_records_first_outcomes_and_unfinished_cases(
    tmp_path, limit
):
    report, calls, *_ = await run(tmp_path, requests=limit)
    assert len(calls) == report["requests"] == limit
    if limit == 5:
        assert report["format_source_verified"] and not report["unverified_cases"]
    else:
        assert report["stopped"].startswith("Finite request bound")
        assert not report["format_source_verified"]
        assert len(report["unverified_cases"]) == (2 if limit == 1 else 1)


async def test_elapsed_time_stops_before_next_stage(tmp_path):
    report, calls, *_ = await run(tmp_path, deadline_at=1)
    assert len(calls) == 1 and report["stopped"].startswith("Finite time bound")
    assert report["counted_test_nanos"] == 10_000
    assert not report["within_bounds"]


async def test_receipt_exceeding_prepared_bound_stops_every_remaining_stage(tmp_path):
    report, calls, *_ = await run(tmp_path, cost=0.012)
    assert len(calls) == 1 and report["counted_test_nanos"] == 12_000_000
    assert report["stopped"].startswith("Receipt exceeded")
    assert report["within_bounds"] and not report["format_source_verified"]


@pytest.mark.parametrize("kind", ["missing", "raised", "nonfinite"])
async def test_fresh_exact_route_and_adopted_rates_gate_every_model_dispatch(
    tmp_path, kind
):
    kwargs = (
        {"missing_route": True}
        if kind == "missing"
        else {
            "rates": {
                "prompt": "0.00000015" if kind == "raised" else "NaN",
                "completion": "0.00000028",
            }
        }
    )
    report, calls, listings, registry = await run(tmp_path, **kwargs)
    assert listings and not calls and report["requests"] == 0
    assert report["stopped"] and report["unverified_cases"] == [
        c["name"] for c in runner.CASES
    ]
    assert not registry.rows("SELECT id FROM model_reservations")


@pytest.mark.parametrize("kwargs", [{"account": False}, {"account_on": False}])
async def test_no_eligible_account_records_unverified_without_opening_any_session(
    tmp_path, kwargs
):
    report, calls, listings, registry = await run(tmp_path, **kwargs)
    assert not calls and not listings
    assert report["stopped"].startswith("No authorized AI-enabled account")
    assert not registry.rows("SELECT id FROM model_reservations")
    assert len(report["unverified_cases"]) == 2


@pytest.mark.parametrize("key", ["requests", "seconds", "counted_nanos"])
@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "1", "raise"])
async def test_only_positive_integer_lower_bounds_are_accepted_before_lock(
    tmp_path, key, invalid
):
    value = runner.BOUNDS[key] + 1 if invalid == "raise" else invalid
    with pytest.raises(ValueError, match="only lower"):
        await runner.observe(settings_at(tmp_path), root=tmp_path, **{key: value})
    assert not (tmp_path / ".local").exists()


async def test_exclusive_owner_lock_refuses_before_opening_provider_or_fixture(
    tmp_path,
):
    settings = settings_at(tmp_path)
    with OwnerLock(tmp_path / ".local/service.owner.lock"):
        with pytest.raises(Refused, match="already has an owner"):
            await runner.observe(settings, root=tmp_path)
    assert not (settings.data / "accounts.sqlite3").exists()
    assert not list((tmp_path / ".local").glob("group-briefing-check-*"))


async def guard_engine(tmp_path):
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    registry.register(37, "authored.session")
    engine = runner.ObservedBriefings(
        settings,
        registry,
        registry.budget(family=runner.FAMILY),
        runner.BOUNDS,
        account_id=37,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP permitted"))
        ),
    )
    return settings, registry, engine


@pytest.mark.parametrize(
    "role,streamed",
    [
        ("incoming_rewrite", True),
        ("group_briefing_image", True),
        ("group_briefing_chunk", True),
        ("group_briefing", False),
    ],
)
async def test_only_group_roles_with_final_only_streaming_can_dispatch(
    tmp_path, role, streamed
):
    _, _, engine = await guard_engine(tmp_path)
    engine.current = {"id": "authored", "role": role, "calls": []}
    try:
        with pytest.raises(Refused, match="only dispatches real group roles"):
            await engine.structured(
                "authored",
                {},
                {},
                role=role,
                on_text=(lambda _: None) if streamed else None,
            )
        assert engine.requests == 0
    finally:
        await engine.client.aclose()


async def test_guard_credits_only_its_fresh_undispatched_reservation(tmp_path):
    settings, registry, engine = await guard_engine(tmp_path)
    registry.set_budget(FLAT_UNKNOWN, family=runner.FAMILY)
    receipt(
        registry,
        settings,
        identity="owned",
        family=runner.FAMILY,
        actual=None,
        status="unknown",
        account_id=37,
    )
    engine.attempts = ["owned"]
    engine.current = {"id": "owned", "role": "group_briefing", "calls": []}
    try:
        engine.guard(FLAT_UNKNOWN, reserved=True)
        engine.current["calls"].append({"role": "group_briefing"})
        with pytest.raises(Refused, match="Unknown provider outcome"):
            engine.guard(FLAT_UNKNOWN, reserved=True)
    finally:
        await engine.client.aclose()


async def test_other_unknown_cannot_receive_the_current_reservation_credit(tmp_path):
    settings, registry, engine = await guard_engine(tmp_path)
    receipt(
        registry,
        settings,
        identity="older",
        family=runner.FAMILY,
        actual=None,
        status="unknown",
        account_id=37,
    )
    engine.attempts = ["older"]
    engine.current = {"id": "fresh", "role": "group_briefing", "calls": []}
    try:
        with pytest.raises(Refused, match="Unknown provider outcome"):
            engine.guard(FLAT_UNKNOWN, reserved=True)
    finally:
        await engine.client.aclose()


async def test_wrong_accounting_family_refuses_without_borrowing(tmp_path):
    settings, registry, engine = await guard_engine(tmp_path)
    receipt(
        registry,
        settings,
        identity="wrong-family",
        family="daily",
        actual=0,
        account_id=37,
    )
    engine.attempts = ["wrong-family"]
    try:
        with pytest.raises(Refused, match="Unexpected accounting family"):
            engine.guard()
    finally:
        await engine.client.aclose()


async def test_unseeded_classifier_stage_cannot_borrow_closed_generative_allowance(
    tmp_path, monkeypatch
):
    populate = runner.populate

    def missing_non_ad_premises(app, case, index):
        key, day = populate(app, case, index)
        with app.store.transaction() as db:
            db.execute("DELETE FROM group_filter_decisions WHERE chat_key=?", (key,))
        return key, day

    monkeypatch.setattr(runner, "populate", missing_non_ad_premises)
    report, calls, listings, registry = await run(tmp_path)
    assert listings and not calls
    assert report["generative_requests"] == report["classifier_requests"] == 0
    assert report["counted_test_nanos"] == 0
    assert not report["format_source_verified"]
    assert report["stopped"].startswith("Unexpected")
    assert not report["results"][0]["stages"]
    assert not registry.rows("SELECT id FROM model_reservations")
