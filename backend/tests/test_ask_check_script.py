"""The Ask this group measurement, run wholly offline with authored engines."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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
        self.bounds, self.seconds = bounds, []
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
            return {"outcome": "not_found", "statements": []}, 0.2
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
