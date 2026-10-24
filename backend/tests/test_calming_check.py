"""Exercise ADR 53's finite calming check on its real paths without network
access: authored provider answers, a fake Telegram transport."""

import asyncio
import importlib.util
import json
import re
import stat
from pathlib import Path

import httpx
from enloq.config import Settings, private_write
from enloq.inference.decider import MODEL as DECIDER
from enloq.inference.fake import choice
from enloq.inference.openrouter import LISTING, MUSE, MUSE_LISTED
from enloq.storage.accounts import AccountRegistry

from backend.tests.proxy_helpers import DECISIONS, ENDPOINT
from backend.tests.test_decider import route
from backend.tests.test_route_refusals import exact_route, model_info

spec = importlib.util.spec_from_file_location(
    "calming_check", Path(__file__).parents[2] / "scripts/check-calming.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
HOSTILE = [c for c in runner.CASES if c["calm"]]


def originals(case):
    return " ".join(s.get("text") or s.get("voice", "") for s in case["sources"])


def which(text):
    """The authored case whose originals the request carries."""
    return next(
        c
        for c in runner.CASES
        if originals(c)[:40] in text
        or json.dumps(originals(c)[:40], ensure_ascii=False)[1:-1] in text
    )


async def run(
    tmp_path,
    *,
    sloppy=None,
    calm_everything=False,
    broken=None,
    literal=None,
    refused=None,
    refusals=1,
):
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
    before = (registry.guidance(), registry.processing_threshold())
    decisions, rewrites, refusing = [], [], []

    def decide(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"data": {"id": DECIDER, "endpoints": [route()]}}
            )
        assert str(request.url) == DECISIONS
        payload = json.loads(request.content)
        decisions.append(payload)
        case = which(json.dumps(payload["state"], ensure_ascii=False))
        code = "angry" if case["calm"] or calm_everything else "positive"
        return httpx.Response(
            200,
            json={
                "id": f"gen-dec-authored-{len(decisions)}",
                "model": DECIDER,
                "provider": "Perplexity",
                "answers": {
                    "emotion": choice(code, 0.8),
                    "suggestions_useful": {"type": "noul", "noul": 0.1},
                    "hostility": {
                        "type": "noul",
                        "noul": float(case["calm"] or calm_everything),
                    },
                },
                "usage": {"input_tokens": 900, "output_tokens": 2, "cost": 0.000018},
            },
        )

    async def rewrite(request):
        """Both routes: MiMo's and, for calmed text, Muse's (ADR 53)."""
        if request.method == "GET":
            if str(request.url) == f"{ENDPOINT}/model/info":
                return httpx.Response(
                    200,
                    json=model_info(
                        exact_route(), exact_route(MUSE, input_cost_per_token=None)
                    ),
                )
            assert str(request.url) == f"{LISTING}/models/{MUSE_LISTED}/endpoints"
            endpoint = {
                "tag": "meta",
                "status": 0,
                "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
            }
            return httpx.Response(
                200, json={"data": {"id": MUSE_LISTED, "endpoints": [endpoint]}}
            )
        payload = json.loads(request.content)
        rewrites.append(payload)
        system = payload["messages"][0]["content"]
        content = payload["messages"][1]["content"][0]["text"]
        case = which(content)
        text = originals(case)
        # Muse's shared upstream pool refusing, as in the ninth run.
        if case["name"] == refused and len(refusing) < refusals:
            refusing.append(payload)
            return httpx.Response(
                429, headers={"retry-after": "0"}, json={"error": "Authored"}
            )
        if case["name"] == broken:
            body = "{not json"
            event = {
                "id": f"authored-calming-{len(rewrites)}",
                "model": payload["model"],
                "usage": {"cost": 0.0001},
                "choices": [{"delta": {"content": body}, "finish_reason": "stop"}],
            }
            return httpx.Response(
                200, content="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
            )
        if "[Owner guidance: calming]" in system:
            assert payload["model"] == MUSE and "extra_body" not in payload
            # An authored calm rendering: no shouting, no insult, same facts.
            for stem in case["insults"]:
                if case["name"] != sloppy:
                    text = re.sub(re.escape(stem), "", text, flags=re.IGNORECASE)
            feeling = (
                "Он расстроен. " if case["language"] == "ru" else "They are upset. "
            )
            text = feeling + text.lower()
        body = json.dumps(
            {"parts": [{"kind": "paragraph", "text": text}], "image_context": []},
            ensure_ascii=False,
        )
        event = {
            "id": f"authored-calming-{len(rewrites)}",
            "model": payload["model"],
            "usage": {"cost": 0.0001},
            "choices": [{"delta": {"content": body}, "finish_reason": "stop"}],
        }
        await asyncio.sleep(0)
        return httpx.Response(
            200, content="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
        )

    judged = []

    def judge(request):
        payload = json.loads(request.content)
        assert str(request.url) == f"{ENDPOINT}/chat/completions"
        assert payload["model"] == runner.JUDGE_MODEL
        asked = json.loads(payload["messages"][1]["content"])
        judged.append(asked)
        flagged = (
            literal and which(" ".join(asked["original_messages"]))["name"] == literal
        )
        answer = {"literal": bool(flagged), "reason": "Authored verdict."}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(answer)}}]}
        )

    def judge_factory(settings, registry, bounds):
        return runner.Judge(
            settings,
            registry,
            bounds,
            client=httpx.AsyncClient(transport=httpx.MockTransport(judge)),
        )

    def calmer_factory(settings, registry, bounds):
        return runner.ObservedMuse(
            settings,
            registry,
            bounds,
            client=httpx.AsyncClient(transport=httpx.MockTransport(rewrite)),
        )

    def route_factory(settings, registry, bounds):
        return runner.ObservedRoute(
            settings,
            registry,
            bounds,
            client=httpx.AsyncClient(transport=httpx.MockTransport(rewrite)),
        )

    def decider_factory(settings, registry, bounds):
        return runner.ObservedDecider(
            settings,
            registry,
            bounds,
            client=httpx.AsyncClient(transport=httpx.MockTransport(decide)),
        )

    report = await runner.observe(
        settings,
        root=tmp_path,
        route_factory=route_factory,
        decider_factory=decider_factory,
        judge_factory=judge_factory,
        calmer_factory=calmer_factory,
    )
    folder = next((tmp_path / ".local").glob("calming-check-*"))
    assert json.loads((folder / "report.json").read_text()) == report
    for path in (folder / "report.json", folder / "pairs.md"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    after = (registry.guidance(), registry.processing_threshold())
    return report, decisions, rewrites, before, after, folder


async def test_every_case_compares_its_originals_with_the_calming_chat(tmp_path):
    report, decisions, rewrites, before, after, folder = await run(tmp_path)
    assert report["passed"] and report["within_bounds"], report["misses"]
    assert "stopped" not in report and not report["unverified"]
    # One calmed rewrite per hostile case, on Muse; text that isn't calmed
    # stays combined (ADR 54). The judge reads each calmed text once.
    assert report["requests"] == {
        "rewrite": len(HOSTILE),
        "decision": len(runner.CASES),
        "judge": len(HOSTILE),
    }
    assert len(rewrites) == report["requests"]["rewrite"] <= runner.MAX_REWRITES
    assert len(decisions) == len(runner.CASES) <= runner.MAX_DECISIONS
    assert all(r["model"] == MUSE for r in rewrites)
    assert all('"calm"' not in r["messages"][1]["content"][0]["text"] for r in rewrites)
    results = {r["name"]: r for r in report["results"]}
    for name, result in results.items():
        assert "today" not in result, name
        assert result["calming"]["calmed"] == result["calm"], name
        assert result["calming"]["code"] == ("angry" if result["calm"] else "positive")
    # Text is held and decided from its originals, at any length; a voice note
    # is decided first.
    assert results["insult_short_en"]["calming"]["path"] == "held"
    assert results["rant_refund_en"]["calming"]["path"] == "held"
    assert results["voice_rent_ru"]["calming"]["path"] == "decided_first"
    assert results["neutral_ru"]["calming"]["path"] == "held"
    # Not calmed, it shows its originals.
    neutral = next(c for c in runner.CASES if c["name"] == "neutral_ru")
    assert results["neutral_ru"]["calming"]["shown"] == originals(neutral)
    assert report["summary"]["calmed"] == len(HOSTILE)
    assert after == before and report["telegram_calls"] == 0
    pairs = (folder / "pairs.md").read_text()
    assert pairs.count("\n## ") == len(runner.CASES)
    assert "Passed: True." in pairs and "Today's rewrite" not in pairs


async def test_a_kept_insult_or_a_calmed_neutral_message_is_a_miss(tmp_path):
    report, *_ = await run(tmp_path, sloppy="insult_short_en")
    assert not report["passed"]
    assert report["misses"] == {"insult_short_en": ["insult:useless"]}


async def test_a_calmed_warm_message_is_a_miss(tmp_path):
    report, *_ = await run(tmp_path, calm_everything=True)
    assert not report["passed"]
    assert set(report["misses"]) == {
        case["name"] for case in runner.CASES if not case["calm"]
    }
    assert all(m == ["non_hostile_softened"] for m in report["misses"].values())


def test_the_rules_match_authored_literals_threats_and_shouting():
    assert runner.keeps("Refund of $240 by Friday the 14th", "240")
    assert runner.keeps("18500 рублей", "18 500") and runner.keeps("в 10.00", "10:00")
    assert not runner.keeps("2400", "240") and not runner.keeps("48213", "4821")
    assert runner.stated("A complaint with the bank", [["complain"], ["bank"]])
    assert not runner.stated("the current state", [["rent"], ["withh"]])
    assert not runner.found("because of your car", "because of you", whole=True)
    assert runner.shouted("WHERE IS THE DELIVERY", []) == ["WHERE", "DELIVERY"]
    assert runner.shouted("Invoice INV-2207 is wrong", ["INV-2207"]) == []
    assert runner.in_language("Он злится из-за возврата 18 500 рублей.", "ru")
    assert not runner.in_language("They are angry about the refund.", "ru")


async def test_a_failed_calmed_rewrite_shows_the_originals_and_is_a_miss(tmp_path):
    report, *_ = await run(tmp_path, broken="insult_short_en")
    assert not report["passed"] and "stopped" not in report
    result = next(r for r in report["results"] if r["name"] == "insult_short_en")
    assert result["calming"]["state"] == "failed" and not result["calming"]["calmed"]
    assert result["misses"] == ["insult:useless"]


async def test_sarcasm_read_literally_is_a_miss(tmp_path):
    report, *_ = await run(tmp_path, literal="sarcasm_ru")
    assert not report["passed"]
    assert report["misses"] == {"sarcasm_ru": ["sarcasm_literal"]}
    result = next(r for r in report["results"] if r["name"] == "sarcasm_ru")
    assert result["calming"]["judge_reason"] == "Authored verdict."


async def test_a_refused_calmed_rewrite_is_retried_as_the_app_retries_it(tmp_path):
    report, decisions, rewrites, *_ = await run(tmp_path, refused="insult_short_ru")
    assert report["passed"], report["misses"]
    result = next(r for r in report["results"] if r["name"] == "insult_short_ru")
    assert result["calming"]["refusals"] == 1 and result["calming"]["calmed"]
    # The refused request counts, and the retry made no second decision.
    assert report["requests"]["rewrite"] == len(HOSTILE) + 1
    assert len(decisions) == len(runner.CASES)


async def test_a_voice_note_refused_twice_is_retried_and_decided_again(tmp_path):
    report, decisions, *_ = await run(tmp_path, refused="voice_rent_en", refusals=2)
    assert report["passed"], report["misses"]
    result = next(r for r in report["results"] if r["name"] == "voice_rent_en")
    assert result["calming"]["refusals"] == 2 and result["calming"]["calmed"]
    assert len(decisions) == len(runner.CASES) + 2


async def test_a_rewrite_still_refused_leaves_its_case_unverified(tmp_path):
    report, *_ = await run(tmp_path, refused="insult_short_ru", refusals=3)
    assert not report["passed"] and not report["misses"]
    assert report["unverified"] == ["insult_short_ru"]
    result = next(r for r in report["results"] if r["name"] == "insult_short_ru")
    assert result["calming"]["refusals"] == runner.RETRIES
    assert result["calming"]["state"] == "queued"
