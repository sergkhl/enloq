"""The citation check measurement, run wholly offline with authored engines."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from enloq.inference.base import EngineResponse
from enloq.inference.fake import FakeDecider, FakeEngine
from enloq.storage.accounts import AccountRegistry

from backend.tests.test_group_briefing_check import settings_at

spec = importlib.util.spec_from_file_location(
    "citation_check_script",
    Path(__file__).parents[2] / "scripts/check-citations.py",
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

GROUP = {
    "name": "garden",
    "title": "Authored garden",
    "language": "en",
    "speakers": ["Authored Dana", "Authored Eli"],
    "days": [
        {
            "days_ago": 3,
            "messages": [
                {"id": 1, "speaker": 0, "minute": 0, "text": "Shed key is with Eli."},
                {
                    "id": 2,
                    "speaker": 1,
                    "minute": 3,
                    "voice": "I will water on Friday.",
                },
            ],
        },
        {
            "days_ago": 1,
            "messages": [
                {
                    "id": 3,
                    "speaker": 0,
                    "minute": 0,
                    "text": "Watering moved to Sunday.",
                },
                {
                    "id": 4,
                    "speaker": 1,
                    "minute": 2,
                    "text": "Yes, Sunday.",
                    "reply_to": 3,
                },
                {"id": 5, "speaker": 0, "minute": 5, "image": "A rota on a board."},
            ],
        },
    ],
}
CLAIMS = [
    {
        "group": "garden",
        "kind": "topic",
        "language": "en",
        "title": "Watering",
        "text": "Watering moved to Sunday, and Eli agreed.",
        "citations": {3: True, 4: True, 1: False},
        "supported": True,
        "trap": "same_subject",
    },
    {
        "group": "garden",
        "kind": "statement",
        "language": "en",
        "title": None,
        "text": "Eli will water on Friday.",
        "citations": {2: True},
        "supported": False,
        "trap": "contradicted",
    },
]


def engines(supports):
    def decider(settings, registry, bounds):
        engine = FakeDecider()
        engine.name = "authored-decider"
        engine.rates = {"prompt": "0.00000002", "completion": "0"}
        engine.citation_support = supports
        engine.client = SimpleNamespace(aclose=_closed)
        original = engine.generate

        async def generate(role, packet):
            bounds.take("decision")
            answers = await original(role, packet)
            return EngineResponse(answers, usage={"cost": 0.000001}, actual_nanos=1000)

        async def prepare(packet):
            return 0

        engine.generate, engine.prepare = generate, prepare
        return engine

    def route(settings, registry, bounds):
        engine = FakeEngine()
        engine.rates = {"prompt": "0.00000014", "completion": "0.00000028"}
        engine.calls = []
        engine.client = SimpleNamespace(aclose=_closed)

        async def prepare(packet):
            return 0

        engine.prepare = prepare
        return engine

    def judge(settings, registry, bounds):
        async def ask(system, content):
            bounds.take("judge")
            cited = [m["id"] for m in json.loads(content)["cited_messages"]]
            return {"citations": {str(i): True for i in cited}, "claim": True}, 0.1

        return SimpleNamespace(ask=ask, client=SimpleNamespace(aclose=_closed))

    return dict(decider_factory=decider, route_factory=route, judge_factory=judge)


async def _closed():
    return None


async def test_measures_authored_claims_and_a_live_briefing(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.fixtures, "GROUPS", [GROUP], raising=False)
    monkeypatch.setattr(runner.fixtures, "CLAIMS", CLAIMS, raising=False)
    monkeypatch.setattr(runner.fixtures, "BRIEFED", [("garden", 1)], raising=False)
    monkeypatch.setattr(runner.fixtures, "problems", lambda: [], raising=False)
    (tmp_path / ".local").mkdir()
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    registry.register(37, "authored.session", directory="authored-account")
    registry.set_processing(37, True)
    report = await runner.observe(settings, root=tmp_path, **engines({1: 0.2}))
    assert "stopped" not in report and report["within_bounds"]
    summary = report["summary"]
    assert summary["checked_claims"] == 2 and summary["citation_pairs"] == 4
    decision = summary["decision"]["citations"]
    assert decision["unsupported_removed"] == 1 and decision["supported_kept"] == 3
    # The claim the labels call unsupported was kept: one wrong claim answer.
    assert summary["decision"]["claims"]["unsupported_kept"] == 1
    assert summary["judge"]["citations"]["unsupported_kept"] == 1
    topic = next(c for c in report["claims"] if c["kind"] == "topic")
    assert topic["decision"]["cited_ids"] == [1, 3, 4]
    [briefing] = report["briefings"]
    assert briefing["final"]["topics"] and briefing["check"]["topics"]
    assert summary["live"]["briefings"] == 1
    assert summary["live"]["checked_topics"] == summary["live"]["topics"] > 0
    assert report["telegram_calls"] == 0
    saved = next((tmp_path / ".local").glob("citation-check-*/report.json"))
    assert json.loads(saved.read_text())["summary"] == json.loads(json.dumps(summary))
