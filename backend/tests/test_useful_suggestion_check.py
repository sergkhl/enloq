"""Exercise the ADR 49 Q13 finite check's real paths without network access."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.inference.decider import MODEL as DECIDER
from enloq.inference.fake import choice, label_choice
from enloq.inference.openrouter import MODEL as MIMO
from enloq.storage.accounts import AccountRegistry

from backend.tests.proxy_helpers import DECISIONS, ENDPOINT
from backend.tests.test_decider import route
from backend.tests.test_route_refusals import exact_route, model_info

spec = importlib.util.spec_from_file_location(
    "useful_suggestion_check",
    Path(__file__).parents[2] / "scripts/check-useful-suggestions.py",
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
CASES = len(runner.CASES)


async def run(tmp_path, *, mode="receipted", threshold=None):
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
    if threshold is not None:
        registry.set_processing_threshold(threshold)
    before = (registry.guidance(), registry.processing_threshold())
    decisions, suggestions = [], []

    def decide(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"data": {"id": DECIDER, "endpoints": [route()]}}
            )
        assert str(request.url) == DECISIONS
        payload = json.loads(request.content)
        decisions.append(payload)
        if "replies" in payload["state"]:
            # ADR 49 Q19: each reply labelled by what it says.
            answers = {
                f"purpose_{reply['id']}": label_choice(
                    "accept" if reply["text"].startswith("Yes") else "decline"
                )
                for reply in payload["state"]["replies"]
            }
            return httpx.Response(
                200,
                json={
                    "id": f"gen-dec-authored-{len(decisions)}",
                    "model": DECIDER,
                    "provider": "Perplexity",
                    "answers": answers,
                    "usage": {
                        "input_tokens": 900,
                        "output_tokens": 2,
                        "cost": 0.000018,
                    },
                },
            )
        latest = " ".join(
            entry.get("text", "")
            for entry in payload["state"]["conversation"]
            if entry.get("latest_block")
        )
        # Authored answers: closing words need no reply, and only the
        # goodnight's smile is emotional among them (ADR 56's either signal).
        quiet = any(w in latest for w in ("Thanks", "ок", "Good night", "7 works"))
        code = (
            "elated"
            if mode == "format"
            else "positive"
            if "Good night" in latest
            else "neutral"
            if quiet
            else "worried"
        )
        answers = {
            "emotion": choice(code, 0.8),
            "suggestions_useful": {"type": "noul", "noul": 0.1 if quiet else 0.9},
            "hostility": {"type": "noul", "noul": 0.0},
        }
        return httpx.Response(
            200,
            json={
                "id": f"gen-dec-authored-{len(decisions)}",
                "model": DECIDER,
                "provider": "Perplexity",
                "answers": answers,
                "usage": {"input_tokens": 900, "output_tokens": 2, "cost": 0.000018},
            },
        )

    def suggest(request):
        if request.method == "GET":
            assert str(request.url) == f"{ENDPOINT}/model/info"
            exact = exact_route(input_cost_per_token=1e-07, output_cost_per_token=2e-07)
            return httpx.Response(200, json=model_info(exact))
        payload = json.loads(request.content)
        suggestions.append(payload)
        options = [
            {"purpose": "accept", "text": f"Yes, authored {len(suggestions)}."},
            {"purpose": "decline", "text": f"Sorry, not this time {len(suggestions)}."},
        ]
        if mode == "one_sided":
            options = options[:1]
        if mode == "invalid_options":
            options[0]["text"] = ""
        return httpx.Response(
            200,
            json={
                "id": "synthetic-receipt",
                "model": MIMO,
                "provider": "xiaomi",
                "choices": [
                    {
                        "message": {"content": json.dumps({"options": options})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {} if mode == "unknown" else {"cost": 0.0002},
            },
        )

    def decider_factory(settings, registry, bounds):
        engine = runner.ObservedDecider.__new__(runner.ObservedDecider)
        engine.bounds, engine.current = bounds, None
        engine.endpoint, engine.key = ENDPOINT, "sk-authored-placeholder"
        engine.user = registry.provider_user()
        engine.rates = None
        engine.on_route = lambda available, reason=None: None
        engine.client = httpx.AsyncClient(transport=httpx.MockTransport(decide))
        return engine

    def route_factory(settings, registry, bounds):
        engine = runner.ObservedRoute.__new__(runner.ObservedRoute)
        engine.bounds, engine.current = bounds, None
        engine.endpoint, engine.key = ENDPOINT, "sk-authored-placeholder"
        engine.user = registry.provider_user()
        engine.rates = None
        engine.client = httpx.AsyncClient(transport=httpx.MockTransport(suggest))
        return engine

    await runner.observe(
        settings,
        root=tmp_path,
        decider_factory=decider_factory,
        route_factory=route_factory,
    )
    report = json.loads(
        next(
            (tmp_path / ".local").glob("useful-suggestions-check-*/report.json")
        ).read_text()
    )
    after = (registry.guidance(), registry.processing_threshold())
    return report, decisions, suggestions, before, after


async def test_each_case_decides_once_and_emotional_or_useful_blocks_get_one_set(
    tmp_path,
):
    report, decisions, suggestions, before, after = await run(tmp_path)
    assert "stopped" not in report and not report["blocking"]
    assert report["requests"] == {
        "decision": len(decisions),
        "suggestion": len(suggestions),
    }
    incoming = [d for d in decisions if "emotion" in d["questions"]]
    labels = [d for d in decisions if "replies" in d["state"]]
    assert len(incoming) == CASES and len(decisions) <= runner.MAX_DECISIONS
    quiet = {"en_thanks", "ru_ok", "en_goodnight", "en_answer_closes"}
    # The goodnight is emotional, so it gets a set however little it asks.
    silent = quiet - {"en_goodnight"}
    assert len(suggestions) == CASES - len(silent) <= runner.MAX_SUGGESTIONS
    # One label request per written set (ADR 49 Q19).
    assert len(labels) == len(suggestions)
    assert len(decisions) == len(incoming) + len(labels)
    assert (
        report["counted_test_nanos"]
        == len(decisions) * 18_000 + len(suggestions) * 200_000
    )
    assert after == before and report["telegram_calls"] == 0 and report["within_bounds"]
    paths = {r["name"]: r["path"] for r in report["results"]}
    # The default threshold holds short blocks; long ones are rewritten.
    assert paths["en_neutral_question"] == "held"
    assert paths["en_long_worried_request"] == "rewritten"
    assert paths["mixed_long_invitation"] == "rewritten"
    for payload in incoming:
        assert set(payload["questions"]) == {
            "emotion",
            "suggestions_useful",
            "hostility",
            "sarcasm",
        }
        assert "data:" not in json.dumps(payload)
    for payload in labels:
        assert set(payload["questions"]) == {"purpose_1", "purpose_2"}
    for result in report["results"]:
        assert result["decision"]["state"] == "ready", result["name"]
        assert result["useful_verdict"] == (
            "match"
            if result["expected_useful"] == (result["name"] not in quiet)
            else "miss"
        )
        if result["name"] in silent:
            assert result["suggestions"] == "not_qualifying" and not result.get(
                "options"
            )
        else:
            assert result["suggestions"] == "ready"
            assert {o["purpose"] for o in result["options"]} == {"accept", "decline"}
            assert not result["answers_missing"]


async def test_a_low_threshold_rewrites_every_case_with_a_stand_in(tmp_path):
    report, decisions, *_ = await run(tmp_path, threshold=0)
    assert {r["path"] for r in report["results"]} == {"rewritten"}
    assert len([d for d in decisions if "emotion" in d["questions"]]) == CASES


@pytest.mark.parametrize("mode", ["format", "invalid_options"])
async def test_malformed_answers_or_option_sets_block_the_switch(tmp_path, mode):
    report, decisions, suggestions, *_ = await run(tmp_path, mode=mode)
    assert len([d for d in decisions if "emotion" in d["questions"]]) == CASES
    assert report["blocking"]
    if mode == "format":
        assert not suggestions
        assert report["blocking"] == [r["name"] for r in report["results"]]
    else:
        failed = [r for r in report["results"] if r.get("suggestions") == "failed"]
        assert failed and {r["suggestion_failure"] for r in failed} == {"format"}


async def test_one_sided_sets_pass_the_finite_check_without_resampling(tmp_path):
    report, decisions, suggestions, before, after = await run(
        tmp_path, mode="one_sided"
    )
    assert not report["blocking"] and "stopped" not in report
    ready = [r for r in report["results"] if r.get("suggestions") == "ready"]
    assert ready and len(ready) == len(suggestions)
    assert len([d for d in decisions if "replies" in d["state"]]) == len(ready)
    assert all([o["purpose"] for o in r["options"]] == ["accept"] for r in ready)
    assert before == after and report["within_bounds"] and report["telegram_calls"] == 0


async def test_an_unknown_outcome_counts_the_flat_estimate_and_stops_in_time(tmp_path):
    report, decisions, suggestions, *_ = await run(tmp_path, mode="unknown")
    # One flat unknown fits the $0.02 bound beside the receipts; the next start
    # would not.
    assert report["stopped"].startswith("Finite check")
    assert len(suggestions) == 1 and report["within_bounds"]
    assert report["counted_test_nanos"] <= runner.MAX_NANOS
