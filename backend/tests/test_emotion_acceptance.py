"""Exercise the decision route's finite check (ADR 30, ADR 49 Q1) without
network access."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.domain.group_filtering import AD_ROLE
from enloq.inference.decider import MODEL
from enloq.inference.fake import choice
from enloq.inference.guidance import DEFAULTS_REVISION
from enloq.storage.accounts import AccountRegistry

from backend.tests.proxy_helpers import DECISIONS, ENDPOINT
from backend.tests.test_decider import route

spec = importlib.util.spec_from_file_location(
    "emotion_check", Path(__file__).parents[2] / "scripts/check-emotion.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
CASES = len(runner.INCOMING) + len(runner.REACTIONS) + len(runner.ADS)
DECIDED = len(runner.INCOMING) + len(runner.REACTIONS)


async def run(tmp_path, *, mode="receipted", budget=None, rates=None):
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
    if budget is not None:
        registry.set_budget(budget)
    before = (
        registry.guidance(),
        registry.preferences(),
        registry.format_failures(),
        registry.processing_threshold(),
    )
    calls = []

    def handler(request):
        if request.method == "GET":
            listing = route(**({"pricing": rates} if rates else {}))
            return httpx.Response(
                200, json={"data": {"id": MODEL, "endpoints": [listing]}}
            )
        assert str(request.url) == DECISIONS
        payload = json.loads(request.content)
        calls.append(payload)
        answers = {"emotion": choice("worried", 0.8)}
        if mode == "format":
            answers = {"emotion": choice("elated", 0.8)}
        if "suggestions_useful" in payload["questions"]:
            answers["suggestions_useful"] = {"type": "noul", "noul": 0.7}
        if all(name.startswith("ad_") for name in payload["questions"]):
            chance = 1.5 if mode == "format" else 0.95
            answers = {
                name: {"type": "noul", "noul": chance} for name in payload["questions"]
            }
        return httpx.Response(
            200,
            json={
                "id": f"gen-dec-authored-{len(calls)}",
                "model": MODEL,
                "provider": "Perplexity",
                "answers": answers,
                "usage": {"input_tokens": 800, "output_tokens": 2}
                | ({} if mode == "unknown" else {"cost": 0.000016}),
            },
        )

    def engine_factory(settings, registry, initial):
        # The runner's real admission and observation without a live client.
        engine = runner.ObservedDecider.__new__(runner.ObservedDecider)
        engine.registry, engine.initial = registry, initial
        engine.timezone = settings.timezone
        engine.started = runner.time.monotonic()
        engine.requests = 0
        engine.current = None
        engine.endpoint, engine.key = ENDPOINT, "sk-authored-placeholder"
        engine.user = registry.provider_user()
        engine.rates = None
        engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return engine

    await runner.observe(settings, root=tmp_path, engine_factory=engine_factory)
    report = json.loads(
        next(
            (tmp_path / ".local").glob("emotion-decider-check-*/report.json")
        ).read_text()
    )
    after = (
        registry.guidance(),
        registry.preferences(),
        registry.format_failures(),
        registry.processing_threshold(),
    )
    return report, calls, before, after, registry


async def test_every_case_is_one_decision_request_through_the_real_stages(tmp_path):
    report, calls, before, after, registry = await run(tmp_path)
    assert "stopped" not in report and not report["blocking"]
    assert report["requests"] == len(calls) == CASES <= runner.MAX_REQUESTS
    assert report["counted_test_nanos"] == CASES * 16_000
    assert report["telegram_calls"] == 0 and after == before
    assert report["defaults_revision"] == DEFAULTS_REVISION
    user = registry.provider_user()
    for payload in calls:
        assert payload["model"] == MODEL and payload["user"] == user
        assert payload["provider"] == {"only": ["perplexity"], "allow_fallbacks": False}
        # Never raw images, Chat Memory or generation parameters.
        assert "data:" not in json.dumps(payload)
        assert set(payload) == {"model", "provider", "user", "state", "questions"}
    incoming = report["results"][: len(runner.INCOMING)]
    reactions = report["results"][len(runner.INCOMING) : DECIDED]
    ads = report["results"][DECIDED:]
    assert all(r["role"] == "emotion_classification" for r in incoming)
    assert all(set(r["state"]) == {"conversation"} for r in incoming)
    assert all(r["state"]["reaction"]["by"] == "recipient" for r in reactions)
    image = next(r for r in incoming if r["name"] == "image_meaning_loss")
    assert image["attached_images"] == 1
    assert image["state"]["conversation"][-1]["image_meaning"].startswith("Photo:")
    voice = next(r for r in incoming if r["name"] == "voice_stuck_in_lift")
    assert voice["state"]["conversation"][-1]["transcript"].startswith("слушай")
    forwarded = next(r for r in incoming if r["name"].startswith("forwarded"))
    assert forwarded["state"]["conversation"][0]["forwarded_from"]
    calm = next(r for r in incoming if r["name"] == "en_earlier_anger_now_calm")
    assert [e.get("latest_block", False) for e in calm["state"]["conversation"]] == [
        False,
        False,
        True,
    ]
    for result in report["results"]:
        assert result["record"]["state"] == "ready", result["name"]
        assert result["request_bound_nanos"] < 200_000
    for result in incoming + reactions:
        assert result["record"]["decision"]["probability"] == 0.8
    assert {r["record"]["useful"] for r in reactions} == {True}
    # Each ad case is one real group classifier request, one question per post,
    # counted in the Group Briefing Budget beside the daily requests.
    for result, payload in zip(ads, calls[DECIDED:], strict=True):
        assert result["role"] == AD_ROLE
        assert len(payload["questions"]) == len(result["posts"]) == len(result["ads"])
        assert all(result["ads"])
        assert result["verdict"] == ("match" if all(result["expected_ads"]) else "miss")
    assert report["summary"]["well_formed"] == CASES and report["within_bounds"]
    assert report["final_budget"]["group_briefing"]["spent"] == len(ads) * 16_000


async def test_malformed_answers_block_the_switch(tmp_path):
    report, calls, *_ = await run(tmp_path, mode="format")
    assert len(calls) == CASES
    assert report["blocking"] == [r["name"] for r in report["results"]]
    assert {r["record"]["failure"] for r in report["results"][:DECIDED]} == {
        "unknown_option"
    }
    assert {r["record"]["state"] for r in report["results"][DECIDED:]} == {"failed"}


async def test_an_unknown_outcome_counts_the_flat_estimate_and_stops(tmp_path):
    report, calls, *_ = await run(tmp_path, mode="unknown")
    assert report["requests"] == len(calls) == 1
    assert report["counted_test_nanos"] == 10_000_000
    assert report["stopped"].startswith("Finite check") and report["within_bounds"]


@pytest.mark.parametrize(
    "budget,rates",
    [(0, None), (None, {"prompt": "0.00000003", "completion": "0"})],
    ids=["reached-budget", "rate-increase"],
)
async def test_nothing_is_sent_past_a_budget_or_a_changed_rate(tmp_path, budget, rates):
    report, calls, *_ = await run(tmp_path, budget=budget, rates=rates)
    assert not calls and report["requests"] == 0 and report["stopped"]
