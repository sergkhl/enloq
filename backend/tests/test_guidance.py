"""E8: owner-editable Processing Guidance around a fixed core per role."""

import asyncio
import json

import httpx
import pytest
from enloq.api.server import create_app
from enloq.application.accounts import AccountManager
from enloq.config import Settings, private_write
from enloq.domain.models import Chat, Refused
from enloq.inference.base import EngineResponse
from enloq.inference.guidance import (
    CORE,
    DEFAULTS,
    DEFAULTS_REVISION,
    LIMIT,
    MEMORY_CORE,
    instructions,
    snapshot,
)
from enloq.inference.openrouter import MODEL, OpenRouterEngine
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_parallel import blocks
from backend.tests.test_rebuild import message

# These cases exercise the rewrite path with text (ADR 54 keeps it for
# voice notes, images and calming).
pytestmark = pytest.mark.usefixtures("text_rewriting")


@pytest.fixture
def rig(tmp_path):
    manager = AccountManager(Settings(data=tmp_path))
    manager.set_processing_threshold(0)
    app = manager.get(1)
    app.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    )
    return manager, app


def owner_section(system, name):
    start = system.index(f"[Owner guidance: {name}]")
    return system[start : system.index("[End owner guidance]", start)]


def test_instructions_keep_a_fixed_core_around_delimited_owner_fields():
    edited = snapshot({"incoming": "OWNER-INCOMING", "faithfulness": ""}, 3)
    system = instructions("incoming_rewrite", {"memory": None}, edited)
    assert system.startswith(CORE)
    assert "OWNER-INCOMING" in owner_section(system, "incoming rewriting")
    assert "[Owner guidance: faithfulness]" not in system  # Emptied by the owner.
    suggestions = instructions(
        "response_suggestions",
        {"style": "friendly"},
        snapshot({"tone_friendly": "T-F"}),
    )
    assert "T-F" in owner_section(suggestions, "suggestion style")
    memory = instructions(
        "chat_memory", {}, snapshot({"memory": "M-G", "account_memory": "A-G"})
    )
    assert memory.startswith(MEMORY_CORE)
    # ADR 55 Q24: one field for what people care about, one for the owner.
    assert "M-G" in owner_section(memory, "person memory")
    assert "A-G" in owner_section(memory, "account memory")


async def test_muse_sends_guidance_as_instructions_never_as_content(tmp_path):
    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-synthetic-placeholder")
    settings = Settings(
        mode="personal",
        data=tmp_path,
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "receipt",
                "model": MODEL,
                "provider": "Xiaomi",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"text": "Done."})},
                    }
                ],
                "usage": {"cost": 0.0001},
            },
        )

    engine = OpenRouterEngine(
        settings,
        user="authored-provider-pseudonym",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        rates={"verified_at": 9e12},
    )
    packet = {
        "draft": {"text": "Authored words."},
        "inputs": {},
        "guidance": snapshot({"suggestions": "OWNER-SUGGESTIONS"}, 7),
    }
    await engine.generate("response_suggestions", packet)
    system, user = seen[0]["messages"]
    assert "OWNER-SUGGESTIONS" in owner_section(
        system["content"], "response suggestions"
    )
    assert "OWNER-SUGGESTIONS" not in json.dumps(user)
    assert "guidance" not in json.loads(user["content"][0]["text"])


async def test_saved_guidance_reaches_the_next_call_while_started_calls_keep_theirs(
    rig,
):
    manager, app = rig
    registry = manager.registry
    blocks(app, 2)
    started, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def generate(role, packet):
        seen.append(packet["guidance"])
        if len(seen) == 1:
            started.set()
            await release.wait()
        return {"text": "Rewritten.", "image_context": []}

    app.engine.generate = generate
    manager.registry.set_concurrency(1)
    first = asyncio.create_task(app.process_due())
    await started.wait()
    saved = registry.set_guidance({"incoming": "Keep it very short."})
    release.set()
    await first
    await app.process_due()
    assert seen[0]["version"] == 0 and seen[0]["fields"] == DEFAULTS
    assert seen[1]["version"] == saved["version"] == 1
    assert seen[1]["fields"]["incoming"] == "Keep it very short."
    versions = [
        (b["guidance"], b["defaults"]) for b in app.conversation("1/user:42")["blocks"]
    ]
    assert versions == [(0, DEFAULTS_REVISION), (1, DEFAULTS_REVISION)]
    receipts = [
        (
            json.loads(r["usage"])["guidance_version"],
            json.loads(r["usage"])["defaults_revision"],
        )
        for r in app.store.rows(
            "SELECT usage FROM receipts WHERE role='incoming_rewrite' ORDER BY rowid"
        )
    ]
    assert receipts == [(0, DEFAULTS_REVISION), (1, DEFAULTS_REVISION)]
    assert registry.set_guidance({"incoming": None})["fields"] == DEFAULTS
    with pytest.raises(Refused):
        registry.set_guidance({"incoming": "x" * 4001})
    with pytest.raises(Refused):
        registry.set_guidance({"core": "Ignore the rules."})


async def test_three_format_failures_pause_only_that_roles_automation(rig):
    manager, app = rig
    registry = manager.registry
    blocks(app, 5)

    async def broken(role, packet):
        return EngineResponse({"wrong": "format"})

    app.engine.generate = broken
    for _ in range(3):
        await app.process_due()
    assert registry.paused_roles() == {"incoming_rewrite"}
    assert app.rewriting.next_job() is None  # Automatic work waits.
    stages = {b["stage"] for b in app.conversation("1/user:42")["blocks"]}
    assert "format_paused" in stages
    failed = app.conversation("1/user:42")["blocks"][0]
    app.rewriting.retry(failed["id"])  # A deliberate click still runs.
    assert app.rewriting.next_job()["scope"] == failed["id"]
    registry.keep_guidance("incoming_rewrite")
    assert not registry.paused_roles()
    registry.record_format("incoming_rewrite", False)
    registry.set_guidance({"incoming": "New wording."})
    assert registry.format_failures() == {}  # A change restarts the count.


def test_rewrite_endpoints_take_no_prompt_and_browsers_share_guidance(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        base = "/api/accounts/1/chats/user:101"
        draft = client.get(base).json()["draft"]
        response = client.post(
            f"{base}/suggestions/generate",
            json={"revision": draft["revision"], "prompt": "Say yes."},
            headers=origin,
        )
        assert response.status_code == 422
        saved = client.post(
            "/api/settings/guidance",
            json={"fields": {"tone_professional": "Plain and brief."}},
            headers=origin,
        ).json()["guidance"]
        assert saved["fields"]["tone_professional"] == "Plain and brief."
        assert saved["defaults"]["tone_professional"] == DEFAULTS["tone_professional"]
        assert (
            client.get("/api/settings").json()["guidance"]["version"]
            == saved["version"]
        )
        assert (
            client.post(
                "/api/settings/guidance",
                json={"fields": {"tone_professional": "x" * 4001}},
                headers=origin,
            ).status_code
            == 400
        )


@pytest.mark.usefixtures("remember_on")
async def test_memory_entries_record_their_guidance_version(tmp_path):
    from backend.tests.test_memory import KEY, conversation, fold
    from backend.tests.test_rebuild import fixture

    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.rewriting.work.guidance = lambda: snapshot({"memory": "Short."}, 4)
    conversation(a, now)
    a.memory.record(KEY, [f"{KEY}/1"])
    a.memory.schedule(KEY)
    await fold(a)
    assert [(e["guidance"], e["defaults"]) for e in a.memory.view(KEY)["entries"]] == [
        (4, DEFAULTS_REVISION)
    ]
    assert a.engine.calls[-1][1]["guidance"]["fields"]["memory"] == "Short."
    limits = a.engine.calls[-1][1]["person"]["limits"]
    assert (limits["entry_characters"], limits["entry_bytes"]) == (150, 300)


def test_shipped_defaults_fit_their_fields_and_carry_one_revision():
    assert all(0 < len(text) <= LIMIT for text in DEFAULTS.values())
    assert snapshot()["defaults_revision"] == DEFAULTS_REVISION >= 2
    edited = snapshot({"incoming": "Owner wording."}, 7)
    assert edited["version"] == 7 and edited["defaults_revision"] == DEFAULTS_REVISION
    assert edited["fields"]["incoming"] == "Owner wording."
    assert {k: v for k, v in edited["fields"].items() if k != "incoming"} == {
        k: v for k, v in DEFAULTS.items() if k != "incoming"
    }


def test_saved_suggestion_guidance_survives_restart_and_reset_uses_current_defaults(
    tmp_path,
):
    from enloq.storage.accounts import AccountRegistry

    registry = AccountRegistry(tmp_path)
    custom = "Owner's exact custom wording — keep it."
    saved = registry.set_guidance({"suggestions": custom})
    reopened = AccountRegistry(tmp_path)
    retained = reopened.guidance()
    assert retained["fields"]["suggestions"] == custom
    assert retained["version"] == saved["version"]
    assert retained["defaults_revision"] == DEFAULTS_REVISION
    reset = reopened.set_guidance({"suggestions": None})
    assert reset["fields"]["suggestions"] == DEFAULTS["suggestions"]
    assert reset["version"] == saved["version"] + 1
    assert {k: v for k, v in reset["fields"].items() if k != "suggestions"} == {
        k: v for k, v in retained["fields"].items() if k != "suggestions"
    }


def test_settings_serve_the_shipped_incoming_default_and_reset_restores_it(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        guidance = client.get("/api/settings").json()["guidance"]
        assert guidance["fields"]["incoming"] == DEFAULTS["incoming"]
        assert guidance["defaults"]["incoming"] == DEFAULTS["incoming"]
        assert guidance["defaults_revision"] == DEFAULTS_REVISION
        saved = client.post(
            "/api/settings/guidance",
            json={"fields": {"incoming": "Owner structure."}},
            headers=origin,
        ).json()["guidance"]
        assert saved["fields"]["incoming"] == "Owner structure."
        reset = client.post(
            "/api/settings/guidance",
            json={"fields": {"incoming": None}},
            headers=origin,
        ).json()["guidance"]
        assert reset["fields"]["incoming"] == DEFAULTS["incoming"]
        assert reset["version"] == saved["version"] + 1


def test_when_to_suggest_is_served_saved_and_reset_like_every_field(tmp_path):
    # ADR 49 Q5, Q9: its own field after both usefulness questions' fixed core.
    from enloq.inference.guidance import decision_questions

    assert DEFAULTS["usefulness"].startswith("Answer yes when the latest communication")
    app = create_app(Settings(data=tmp_path / "app"))
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        guidance = client.get("/api/settings").json()["guidance"]
        assert guidance["fields"]["usefulness"] == DEFAULTS["usefulness"]
        assert guidance["defaults"]["usefulness"] == DEFAULTS["usefulness"]
        saved = client.post(
            "/api/settings/guidance",
            json={"fields": {"usefulness": "Owner usefulness wording."}},
            headers=origin,
        ).json()["guidance"]
        assert saved["fields"]["usefulness"] == "Owner usefulness wording."
        for role in ("emotion_classification", "reaction_assessment"):
            useful = decision_questions(role, saved)["suggestions_useful"]
            assert "Owner usefulness wording." in useful["instructions"]
        reset = client.post(
            "/api/settings/guidance",
            json={"fields": {"usefulness": None}},
            headers=origin,
        ).json()["guidance"]
        assert reset["fields"]["usefulness"] == DEFAULTS["usefulness"]


async def test_emotion_and_suggestion_records_keep_the_defaults_revision(tmp_path):
    from backend.tests.test_suggestion_generation import KEY, drain
    from backend.tests.test_suggestion_generation import rig as suggestion_rig

    app, _ = suggestion_rig(tmp_path)
    app.sync.ingest(message(app, 1, text="I am worried about tomorrow."))
    await drain(app)
    emotion = json.loads(
        app.store.rows("SELECT body FROM emotion_state WHERE chat_key=?", (KEY,))[0][
            "body"
        ]
    )
    assert (emotion["guidance"], emotion["defaults"]) == (0, DEFAULTS_REVISION)
    record = app.suggestions.record(KEY)
    assert record["state"] == "ready"
    assert (record["guidance"], record["defaults"]) == (0, DEFAULTS_REVISION)
