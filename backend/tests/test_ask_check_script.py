"""The Ask this group measurement, run wholly offline with authored engines."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from enloq.inference.fake import FakeDecider, FakeEmbedder, FakeEngine
from enloq.storage.accounts import AccountRegistry

from backend.tests.test_citation_check_script import GROUP, _closed
from backend.tests.test_group_briefing_check import settings_at

spec = importlib.util.spec_from_file_location(
    "ask_check_script", Path(__file__).parents[2] / "scripts/check-ask.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

QUESTIONS = [
    {
        "group": "garden",
        "language": "en",
        "question": "When is the watering now?",
        "relevant": [3, 4],
        "answer": "On Sunday.",
        "answerable": True,
        "kind": "change",
    },
    {
        "group": "garden",
        "language": "ru",
        "question": "Сколько стоит новый шланг?",
        "relevant": [],
        "answer": None,
        "answerable": False,
        "kind": "open",
    },
]


class Authored:
    """What the measurement asks of an engine, around an authored one."""

    def watch(self, bounds):
        self.bounds, self.seconds, self.nanos = bounds, [], 0
        return self

    async def prepare(self, packet):
        return 0

    async def generate(self, role, packet):
        self.bounds.take(self.kind)
        self.seconds.append(0.1)
        return await super().generate(role, packet)


class Embedder(Authored, FakeEmbedder):
    kind = "embed"

    def __init__(self, settings, route, *, user):
        super().__init__()
        self.route, self.client = route, SimpleNamespace(aclose=_closed)


class Reranker(Authored):
    kind = "rerank"

    def __init__(self, settings, route, *, user):
        self.route, self.calls = route, []
        self.name, self.model, self.provider = "offline", route.model, "local"
        self.client = SimpleNamespace(aclose=_closed)

    async def generate(self, role, packet):
        self.bounds.take(self.kind)
        self.seconds.append(0.1)
        count = len(packet["documents"])
        return {
            "results": [
                {"index": i, "relevance_score": (count - i) / count}
                for i in range(count)
            ]
        }


class Writer(Authored, FakeEngine):
    kind = "write"

    def __init__(self, settings, *, user):
        super().__init__()
        self.rates = {"prompt": "0.00000014", "completion": "0.00000028"}


class Decider(Authored, FakeDecider):
    kind = "check"

    def __init__(self, settings, *, user):
        super().__init__()
        self.rates = {"prompt": "0.00000002", "completion": "0"}


class Judge:
    def __init__(self, settings, registry, bounds):
        self.bounds, self.client = bounds, SimpleNamespace(aclose=_closed)

    async def ask(self, system, content, *, kind="judge"):
        self.bounds.take(kind)
        if kind == "sol":
            return {"statements": []}, 0.2
        return {"A": {"correct": True, "unsupported": []}, "B": {"correct": False}}, 0.3


async def test_measures_retrieval_writers_and_the_check_offline(tmp_path, monkeypatch):
    for name, value in (
        ("GROUPS", [GROUP]),
        ("QUESTIONS", QUESTIONS),
        ("problems", lambda: []),
    ):
        monkeypatch.setattr(runner.fixtures, name, value, raising=False)
    for name, value in (
        ("ObservedEmbedder", Embedder),
        ("ObservedReranker", Reranker),
        ("ObservedWriter", Writer),
        ("ObservedDecider", Decider),
        ("Judge", Judge),
    ):
        monkeypatch.setattr(runner, name, value)
    (tmp_path / ".local").mkdir()
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    registry.register(37, "authored.session", directory="authored-account")
    registry.set_processing(37, True)
    report = await runner.observe(settings, root=tmp_path)
    assert "stopped" not in report and report["within_bounds"]
    table = report["retrieval"]
    assert set(table) >= {"keywords", "fused:qwen/qwen3-embedding-8b"}
    assert "reranked:voyageai/rerank-3" in table
    assert report["best_embedder"] in {r.model for r in runner.EMBEDDERS}
    assert len(report["answers"]) == 2
    first = report["answers"][0]
    assert (
        first["mimo"]["outcome"] == "answer" and first["sol"]["outcome"] == "not_found"
    )
    summary = report["summary"]
    assert summary["questions"] == 2
    # The authored writer answers both, the unanswerable one included.
    assert summary["check_on_statements"]["statements"] == 2
    # Voyage is never adopted, whatever it scores.
    assert report["reranker"] in {None, "qwen/qwen3-reranker-8b"}
    saved = next((tmp_path / ".local").glob("ask-check-*/report.json"))
    assert json.loads(saved.read_text())["summary"]["questions"] == 2


async def test_an_answers_only_run_uses_the_adopted_retrieval(tmp_path, monkeypatch):
    for name, value in (
        ("GROUPS", [GROUP]),
        ("QUESTIONS", QUESTIONS),
        ("problems", lambda: []),
    ):
        monkeypatch.setattr(runner.fixtures, name, value, raising=False)
    for name, value in (
        ("ObservedEmbedder", Embedder),
        ("ObservedReranker", Reranker),
        ("ObservedWriter", Writer),
        ("ObservedDecider", Decider),
        ("Judge", Judge),
    ):
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setattr(
        runner,
        "ANSWERS_ONLY",
        dict(
            embedder="perplexity/pplx-embed-v1-4b",
            reranker="qwen/qwen3-reranker-8b",
            answerable=1,
            unanswerable=0,
        ),
    )
    (tmp_path / ".local").mkdir()
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    registry.register(37, "authored.session", directory="authored-account")
    registry.set_processing(37, True)
    report = await runner.observe(settings, root=tmp_path)
    assert "stopped" not in report and "retrieval" not in report
    assert report["reranker"] == "qwen/qwen3-reranker-8b"
    assert [a["question"] for a in report["answers"]] == [QUESTIONS[0]["question"]]
    assert report["requests"]["rerank"] == 1


class Subscription(Authored, FakeEngine):
    kind = "write"

    def __init__(self, settings, *, user):
        super().__init__()
        self.model = "chatgpt/gpt-6.1-sol"


async def test_the_final_run_compares_candidates_and_answers_everything(
    tmp_path, monkeypatch
):
    for name, value in (
        ("GROUPS", [GROUP]),
        ("QUESTIONS", QUESTIONS),
        ("problems", lambda: []),
    ):
        monkeypatch.setattr(runner.fixtures, name, value, raising=False)
    for name, value in (
        ("ObservedEmbedder", Embedder),
        ("ObservedReranker", Reranker),
        ("ObservedSubscription", Subscription),
        ("ObservedDecider", Decider),
        ("Judge", Judge),
        ("FINAL", [True]),
    ):
        monkeypatch.setattr(runner, name, value)
    (tmp_path / ".local").mkdir()
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    registry.register(37, "authored.session", directory="authored-account")
    registry.set_processing(37, True)
    report = await runner.observe(settings, root=tmp_path)
    assert "stopped" not in report
    assert set(report["retrieval"]) == {
        "candidates:dense",
        "candidates:fused",
        "reranked:dense",
        "reranked:fused",
    }
    assert report["candidates"] in {"dense", "fused"}
    assert len(report["answers"]) == 2
    # One answerable question reranked twice, the other once.
    assert report["requests"]["rerank"] == 3
    assert all("judge" in a for a in report["answers"])


class Graders(Judge):
    """Grades whatever answers it is given: A right, the others wrong."""

    async def ask(self, system, content, *, kind="judge"):
        self.bounds.take(kind)
        letters = json.loads(content)["answers"]
        return {
            letter: {"correct": letter == "A", "unsupported": []} for letter in letters
        }, 0.3


async def test_the_tools_run_answers_every_question_three_ways(tmp_path, monkeypatch):
    for name, value in (
        ("GROUPS", [GROUP]),
        ("QUESTIONS", QUESTIONS),
        ("problems", lambda: []),
    ):
        monkeypatch.setattr(runner.fixtures, name, value, raising=False)
    for name, value in (
        ("ObservedEmbedder", Embedder),
        ("ObservedReranker", Reranker),
        ("ObservedSubscription", Subscription),
        ("ObservedDecider", Decider),
        ("Judge", Graders),
        ("TOOLS_RUN", [None]),
    ):
        monkeypatch.setattr(runner, name, value)
    (tmp_path / ".local").mkdir()
    settings = settings_at(tmp_path)
    registry = AccountRegistry(settings.data)
    registry.register(37, "authored.session", directory="authored-account")
    registry.set_processing(37, True)
    report = await runner.observe(settings, root=tmp_path)
    assert "stopped" not in report and report["within_bounds"]
    first, second = report["answers"]
    assert set(first["retrievals"]) == {"fixed", "tools", "both"}
    # The authored writer searches only when it has read nothing yet.
    tools, both = first["retrievals"]["tools"], first["retrievals"]["both"]
    assert [c["name"] for c in tools["calls"]] == ["search_messages"]
    assert tools["requests"]["write"] == 2 and tools["retrieved"] == []
    assert both["calls"] == [] and both["requests"]["write"] == 1
    assert set(tools["read"]) == set(tools["calls"][0]["read"])
    # Each question's order is shuffled for the one judge request.
    assert sorted(first["judge"]["order"]) == ["both", "fixed", "tools"]
    summary = report["summary"]
    assert summary["graded"] == 2
    for retrieval in ("fixed", "tools", "both"):
        assert summary[retrieval]["correct_when_answerable"]["of"] == 1
    assert summary["tools"]["questions_with_tool_calls"] == 2
    assert set(summary["paired_with_fixed"]) == {"tools", "both"}
    assert summary["decision"]["outcome"] in {"adopt", "setting", "drop"}
    assert report["requests"]["judge"] == 2


def test_paired_answers_and_the_exact_mcnemar_p_value():
    from scripts.measuring import paired

    assert paired([True, True, False, False], [True, False, True, True]) == {
        "both": 1,
        "only_first": 1,
        "only_second": 2,
        "neither": 0,
        "p_value": 1.0,
    }
    # Six disagreements, all one way: twice 0.5 to the sixth.
    assert paired([False] * 6, [True] * 6)["p_value"] == pytest.approx(
        0.03125, abs=1e-4
    )
    assert paired([True], [True])["p_value"] == 1.0
