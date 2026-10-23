"""Exercise the bounded live-check harness with a provider-free adapter first."""

import importlib.util
from pathlib import Path

import pytest
from enloq.config import Settings
from enloq.inference.base import EngineResponse
from enloq.inference.fake import FakeDecider, FakeEngine
from enloq.inference.openrouter import output_schema

# Memory as before ADR 56's Off default: these chats learn without switching
# on; test_memory_default covers the default as shipped.
pytestmark = pytest.mark.usefixtures("remember_on")


def test_memory_provider_schema_has_no_optional_object_properties():
    schema = output_schema("chat_memory")
    for value in [schema, *schema["$defs"].values()]:
        assert set(value["required"]) == set(value["properties"])
        assert value["additionalProperties"] is False


def test_provider_contract_cannot_invent_retained_ids_or_use_owner_entries():
    packet = {
        "person": {"memory": []},
        "account": {"memory": []},
        "messages": [{"id": 1}, {"id": 2}],
    }
    schema = output_schema("chat_memory", packet)
    fields = schema["$defs"]["PersonAddition"]["properties"]
    assert fields["based_on"]["maxItems"] == 0
    assert fields["citations"]["items"]["enum"] == [1, 2]
    assert schema["properties"]["person"]["properties"]["remove"]["maxItems"] == 0
    packet["person"]["memory"] = [
        {"id": "retained", "source": "model", "citations": [80]},
        {"id": "mine", "source": "owner", "citations": []},
    ]
    # ADR 55: an account note cites only this chat's supplied messages.
    packet["account"]["memory"] = [
        {"id": "theirs", "source": "model", "citations": [81]},
    ]
    schema = output_schema("chat_memory", packet)
    fields = schema["$defs"]["PersonAddition"]["properties"]
    assert fields["based_on"]["items"]["enum"] == ["retained"]
    assert fields["citations"]["items"]["enum"] == [1, 2, 80]
    assert schema["$defs"]["PersonFlag"]["properties"]["id"]["enum"] == ["mine"]
    account = schema["$defs"]["AccountAddition"]["properties"]
    assert account["based_on"]["items"]["enum"] == ["theirs"]
    assert account["citations"]["items"]["enum"] == [1, 2]
    assert schema["properties"]["account"]["properties"]["outdated"]["maxItems"] == 0


spec = importlib.util.spec_from_file_location(
    "memory_check", Path(__file__).parents[2] / "scripts/check-memory.py"
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
RATES = {"prompt": "0", "completion": "0", "context_length": 1000}


class Route(FakeEngine):
    """Authored memory, suggestions and notes in the provider's shapes."""

    def __init__(self, settings, registry, bounds, wrong=False):
        super().__init__()
        self.bounds, self.rates, self.client, self.wrong = bounds, RATES, self, wrong

    async def prepare(self, packet):
        return 0

    async def aclose(self):
        pass

    async def generate(self, role, packet):
        self.bounds.take(gate.ROLES[role])
        if role == "trait_notes":
            # A pattern across days, in the subject's script; wrong, it is
            # English with its IDs in the text.
            own = [m["id"] for m in packet["messages"] if m["from"] == packet["writer"]]
            cited = [own[0], own[len(own) // 2], own[-1]]
            text = (
                f"Writes at length ({cited[0]}, {cited[1]})."
                if self.wrong
                else "Пишет длинно и тепло."
                if packet["script"] == "Cyrillic"
                else "Writes at length."
            )
            return EngineResponse(
                {
                    "add": [{"text": text, "citations": cited, "confidence": 0.7}],
                    "remove": [],
                }
            )
        if role == "response_suggestions":
            hidden = [o["text"] for o in packet["memory"]["owner"] if not o["may_say"]]
            text = hidden[0] if self.wrong and hidden else "Sounds good, tell me more!"
            return EngineResponse({"options": [{"purpose": "support", "text": text}]})
        return EngineResponse(await super().generate(role, packet))


class Decider(FakeDecider):
    def __init__(self, settings, registry, bounds):
        super().__init__()
        self.bounds, self.rates, self.client = bounds, RATES, self

    async def prepare(self, packet):
        return 0

    async def aclose(self):
        pass

    async def generate(self, role, packet):
        self.bounds.take("decision")
        return await super().generate(role, packet)


class Judge:
    def __init__(self, settings, registry, bounds):
        self.bounds, self.client, self.asked = bounds, self, []

    async def aclose(self):
        pass

    async def ask(self, system, payload):
        self.bounds.take("judge")
        self.asked.append(payload)
        if system == gate.JUDGE_NOTE:
            mine = payload["list"] == "the owner"
            return {"about": "owner" if mine else "person", "supported": True}
        if system == gate.JUDGE_TRAIT:
            return {"right_person": True, "supported": True}
        if system == gate.JUDGE_LEAK:
            revealed = any(
                note in option
                for note in payload["hidden_notes"]
                for option in payload["options"]
            )
            return {"revealed": revealed}
        return {"stale": False}


async def check(tmp_path, monkeypatch, wrong=False):
    (tmp_path / ".local").mkdir(mode=0o700)
    settings = Settings(mode="personal", data=tmp_path / "data")
    monkeypatch.setattr(gate, "within_adopted_rates", lambda rates: True)
    monkeypatch.setattr(gate, "decider_rates_adopted", lambda rates: True)
    return await gate.observe(
        settings,
        root=tmp_path,
        route_factory=lambda *args: Route(*args, wrong=wrong),
        decider_factory=Decider,
        judge_factory=Judge,
    )


async def test_the_gate_runs_every_group_within_its_bounds(tmp_path, monkeypatch):
    report = await check(tmp_path, monkeypatch)
    assert [v["name"] for v in report["cares"]] == [c["name"] for c in gate.CARES]
    assert [v["name"] for v in report["suggestions"]] == [
        c["name"] for c in gate.SUGGESTIONS
    ]
    assert report["within_bounds"] and not report.get("stopped")
    # The authored engine notes every message: the small-talk chat is a miss.
    assert report["misses"] == {
        "cares:small_talk_ru": [
            "invented:Привет",
            "invented:Ок, спасибо",
            "invented:Привет!",
        ]
    }
    marathon = report["cares"][0]
    assert marathon["person"] and marathon["account"]
    katya = next(v for v in report["traits"] if v["name"] == "katya_ru")
    assert katya["notes"] == ["Пишет длинно и тепло."]
    assert katya["verdicts"][0]["answer"] == {"right_person": True, "supported": True}
    assert len(report["decisions"]) == 5
    assert report["traits_summary"]["shown"] > 0
    assert not report["passed"]
    written = list((tmp_path / ".local").glob("memory-check-*/results.md"))
    assert written and "## Traits" in written[0].read_text()


async def test_a_revealed_owner_note_is_a_miss(tmp_path, monkeypatch):
    report = await check(tmp_path, monkeypatch, wrong=True)
    assert report["misses"]["suggestions:leak_en"] == ["revealed"]
    assert report["misses"]["suggestions:leak_ru"] == ["revealed"]
    assert "suggestions:said_here_en" not in report["misses"]
    # A note in the wrong script is a miss; IDs never reach a note's text.
    assert report["misses"]["traits:katya_ru"] == ["note_language"]
    assert "traits:dan_en" not in report["misses"]
