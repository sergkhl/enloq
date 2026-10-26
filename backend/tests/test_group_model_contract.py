"""GB4 roles, editable guidance and final-only streams; mocked HTTP only."""

import json
import time

import httpx
import pytest
from enloq.api.server import create_app
from enloq.config import Settings, private_write
from enloq.domain.briefing_contract import GROUP_ROLES, NO_SUBSTANCE
from enloq.domain.models import Refused
from enloq.inference import guidance
from enloq.inference.base import (
    EngineResponse,
    InvalidOutput,
    ProviderRefused,
)
from enloq.inference.fake import FakeEngine
from enloq.inference.openrouter import (
    BACKGROUND_OUTPUT_TOKENS,
    LOGICAL_MODEL,
    MODEL,
    PROVIDER,
    OpenRouterEngine,
    output_schema,
)
from enloq.inference.streaming import BriefingPreview
from enloq.inference.work import (
    BACKGROUND,
    FORMAT_PAUSED,
    ROLES,
    ROUTES,
    validate_output,
)
from enloq.storage.accounts import AccountRegistry
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_group_budgets import accounting, queue


def packet():
    return {
        "messages": [
            {
                "id": 11,
                "text": "Alice confirmed Friday.",
                "author": "Alice",
                "author_peer": "user:7",
                "author_signature": None,
                "outgoing": False,
            }
        ],
        "context": [{"id": 99, "text": "An older discussion."}],
        "covered_ids": [11],
        "chunks": [],
    }


def answer(role):
    return (
        {"image_context": [{"message_id": 11, "text": "Authored chart meaning."}]}
        if role == "group_briefing_image"
        else {
            "outcome": "briefing",
            "topics": [{"text": "Alice confirmed Friday.", "citations": [11]}],
        }
    )


def engine(tmp_path, handler):
    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-authored-placeholder")
    return OpenRouterEngine(
        Settings(
            mode="personal",
            data=tmp_path,
            refs={
                "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
                "MUSE_API_KEY_REF": str(tmp_path / "key"),
            },
        ),
        user="authored-provider-pseudonym",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        rates={
            "verified_at": time.time(),
            "context_length": 1048576,
            "prompt": "0.00000014",
            "completion": "0.00000028",
        },
    )


def test_group_roles_use_exact_mimo_route_without_private_format_pauses():
    assert GROUP_ROLES <= ROLES & BACKGROUND
    assert {ROUTES[role] for role in GROUP_ROLES} == {f"{LOGICAL_MODEL}@{PROVIDER}"}
    assert FORMAT_PAUSED == {"incoming_rewrite", "chat_memory"}
    assert GROUP_ROLES.isdisjoint(FORMAT_PAUSED)


@pytest.mark.parametrize("role", sorted(GROUP_ROLES))
def test_group_guidance_keeps_a_separate_core_and_owner_field(role):
    fields = {
        name: "PRIVATE-OWNER-SENTINEL"
        for name in guidance.FIELDS
        if name != "group_briefing"
    }
    fields["group_briefing"] = "GROUP-OWNER-SENTINEL"
    system = guidance.instructions(role, packet(), guidance.snapshot(fields, 7))
    assert system.startswith(guidance.GROUP_CORE)
    assert "GROUP-OWNER-SENTINEL" in system
    assert "PRIVATE-OWNER-SENTINEL" not in system
    assert guidance.ROLES[role] == ("group_briefing",)
    empty = guidance.instructions(
        role, packet(), guidance.snapshot({"group_briefing": ""})
    )
    assert empty.startswith(guidance.GROUP_CORE)
    assert "[Owner guidance:" not in empty


def test_group_reuse_identity_ignores_unrelated_private_fields_and_global_owner_version(
    monkeypatch,
):
    original = guidance.briefing_fingerprint()
    private = {
        name: "Changed private guidance."
        for name in guidance.FIELDS
        if name != "group_briefing"
    }
    assert guidance.briefing_fingerprint(guidance.snapshot(private, 200)) == original
    assert guidance.briefing_fingerprint(guidance.snapshot(version=999)) == original
    assert (
        guidance.briefing_fingerprint(
            guidance.snapshot({"group_briefing": "Owner group wording."})
        )
        != original
    )
    assert guidance.briefing_fingerprint(route="another/model@provider") != original
    assert guidance.briefing_fingerprint(role="group_briefing_chunk") != original
    # Hide Bots decides whether verified bots are read at all. Its default
    # leaves earlier work reusable; turning it off is a rule change.
    assert guidance.briefing_fingerprint(hide_bots=True) == original
    assert guidance.briefing_fingerprint(hide_bots=False) != original
    revision = {
        **guidance.snapshot(),
        "defaults_revision": guidance.DEFAULTS_REVISION + 1,
    }
    assert guidance.briefing_fingerprint(revision) != original
    monkeypatch.setattr(
        guidance, "GROUP_CORE", guidance.GROUP_CORE + "A changed fixed rule."
    )
    assert guidance.briefing_fingerprint() != original


def test_saved_group_guidance_is_durable_bounded_and_reset_independently(tmp_path):
    registry = AccountRegistry(tmp_path)
    prior = registry.set_guidance(
        {"incoming": "Private owner wording.", "suggestions": "Private suggestions."}
    )
    custom = "Group owner wording." * 100
    saved = registry.set_guidance({"group_briefing": custom})
    retained = AccountRegistry(tmp_path).guidance()
    assert retained == saved
    assert saved["defaults_revision"] == guidance.DEFAULTS_REVISION == 20
    assert {
        name: text for name, text in saved["fields"].items() if name != "group_briefing"
    } == {
        name: text for name, text in prior["fields"].items() if name != "group_briefing"
    }
    registry.set_guidance({"group_briefing": "x" * 4000})
    with pytest.raises(Refused):
        registry.set_guidance({"group_briefing": "x" * 4001})
    reset = registry.set_guidance({"group_briefing": None})
    assert reset["fields"]["group_briefing"] == guidance.DEFAULTS["group_briefing"]
    assert reset["fields"]["incoming"] == "Private owner wording."


def test_group_guidance_uses_existing_authenticated_settings_api(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/settings").status_code == 401
        trust_browser(client, tmp_path / "app")
        before = client.get("/api/settings").json()["guidance"]
        assert before["fields"]["group_briefing"] == guidance.DEFAULTS["group_briefing"]
        saved = client.post(
            "/api/settings/guidance",
            headers=origin,
            json={"fields": {"group_briefing": "Concise group topics."}},
        )
        assert saved.status_code == 200
        assert (
            saved.json()["guidance"]["fields"]["incoming"]
            == before["fields"]["incoming"]
        )
        assert (
            client.post(
                "/api/settings/guidance",
                headers=origin,
                json={"fields": {"group_briefing": "x" * 4001}},
            ).status_code
            == 400
        )
        reset = client.post(
            "/api/settings/guidance",
            headers=origin,
            json={"fields": {"group_briefing": None}},
        )
        assert (
            reset.json()["guidance"]["fields"]["group_briefing"]
            == before["defaults"]["group_briefing"]
        )


@pytest.mark.parametrize("role", sorted(GROUP_ROLES))
async def test_group_requests_use_exact_route_hidden_reasoning_and_separate_structured_contract(
    tmp_path, role
):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "authored-group-receipt",
                "model": MODEL,
                "provider": "Xiaomi",
                "usage": {
                    "cost": 0,
                    "cost_details": {"upstream_inference_cost": 0.000002},
                },
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(answer(role)),
                            "reasoning": "HIDDEN-REASONING",
                        },
                    }
                ],
            },
        )

    adapter = engine(tmp_path, handler)
    source = packet()
    source["guidance"] = guidance.snapshot(
        {"group_briefing": "GROUP-OWNER-SENTINEL", "faithfulness": "PRIVATE-SENTINEL"}
    )
    if role == "group_briefing_image":
        source["images"] = [
            {"message_id": 11, "data_url": "data:image/png;base64,YXV0aG9yZWQ="}
        ]
    try:
        response = await adapter.generate(role, source)
    finally:
        await adapter.client.aclose()
    validated = validate_output(role, response, source)
    assert len(seen) == 1
    request = seen[0]
    assert request["model"] == MODEL
    # The proxy route names its provider; no OpenRouter routing is sent.
    assert "provider" not in request and "reasoning" not in request
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert request["max_tokens"] == BACKGROUND_OUTPUT_TOKENS
    assert "stream" not in request
    system, user = request["messages"]
    assert "GROUP-OWNER-SENTINEL" in system["content"]
    assert "PRIVATE-SENTINEL" not in system["content"]
    content = json.loads(user["content"][0]["text"])
    assert "guidance" not in content and "memory" not in content
    assert content["messages"][0]["author_peer"] == "user:7"
    assert "GROUP-OWNER-SENTINEL" not in json.dumps(content)
    properties = request["response_format"]["json_schema"]["schema"]["properties"]
    assert set(properties) == (
        {"image_context"}
        if role == "group_briefing_image"
        else {"contract_version", "outcome", "topics"}
    )
    assert response.actual_nanos == 2000
    assert "HIDDEN-REASONING" not in str(response)
    assert validated is not None


@pytest.mark.parametrize("escaped", [False, True])
@pytest.mark.parametrize("order", ["text_first", "citations_first"])
def test_final_topic_preview_at_every_character_boundary_hides_citations_and_other_json(
    escaped, order
):
    texts = ['Алиса: пятница 😀 "quoted" \\ path', "Bob: uncertain name (?)"]
    topics = [{"text": text, "citations": [11]} for text in texts]
    if order == "citations_first":
        topics = [dict(reversed(list(topic.items()))) for topic in topics]
    raw = json.dumps(
        {
            "outcome": "briefing",
            "image_context": [{"text": "HIDDEN-MEANING"}],
            "topics": topics,
            "text": "HIDDEN-ROOT-TEXT",
        },
        ensure_ascii=escaped,
    )
    expected = "\n\n".join(texts)
    preview, seen = BriefingPreview(), []
    for char in raw:
        value = preview.feed(char)
        if value is not None:
            assert expected.startswith(value)
            assert not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
            seen.append(value)
    assert seen[-1] == expected
    assert any(value != expected for value in seen)
    assert "HIDDEN" not in "".join(seen)


def test_no_substance_and_unrelated_root_text_have_no_provisional_projection():
    for output in (
        {"outcome": "no_substance", "topics": []},
        {"text": "HIDDEN-ROOT-TEXT"},
    ):
        preview = BriefingPreview()
        assert all(preview.feed(char) is None for char in json.dumps(output))
    with pytest.raises(ValueError):
        BriefingPreview().feed(
            '{"outcome":"briefing","topics":[{"text":"bad\\ud800\\u0061'
        )


@pytest.mark.parametrize("citations_first", [False, True])
def test_first_usable_topic_prefix_arrives_before_the_topic_closes(citations_first):
    preview = BriefingPreview()
    prefix = '{"outcome":"briefing","topics":[{'
    if citations_first:
        prefix += '"citations":[11],'
    prefix += '"text":"Alice con'
    assert preview.feed(prefix) == "Alice con"
    suffix = 'firmed"' + ("" if citations_first else ',"citations":[11]') + "}]}"
    assert preview.feed(suffix) == "Alice confirmed"


def stream_body(output):
    frames = []
    raw = json.dumps(output, ensure_ascii=True)
    for offset in range(0, len(raw), 5):
        value = {
            "id": "authored-group-stream",
            "model": MODEL,
            "provider": "Xiaomi",
            "choices": [
                {
                    "delta": {
                        "content": raw[offset : offset + 5],
                        "reasoning": "HIDDEN-REASONING",
                    }
                }
            ],
        }
        frames.append("data: " + json.dumps(value) + "\n\n")
    frames.append(
        "data: "
        + json.dumps(
            {
                "model": MODEL,
                "provider": "Xiaomi",
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"cost": 0.000003},
            }
        )
        + "\n\n"
    )
    frames.append("data: [DONE]\n\n")
    return "".join(frames)


async def test_only_final_briefing_streams_topic_prefixes_and_still_requires_final_validation(
    tmp_path,
):
    seen, requests = [], []
    output = answer("group_briefing")
    output["topics"][0]["text"] = "Алиса: пятница 😀."

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=stream_body(output))

    adapter = engine(tmp_path, handler)
    try:
        response = await adapter.generate_stream(
            "group_briefing", packet(), seen.append
        )
        for role in ("group_briefing_image", "group_briefing_chunk"):
            with pytest.raises(Refused):
                await adapter.generate_stream(role, packet(), seen.append)
            with pytest.raises(Refused):
                await adapter.structured(
                    "Authored",
                    packet(),
                    output_schema(role),
                    role=role,
                    on_text=seen.append,
                )
    finally:
        await adapter.client.aclose()
    assert len(requests) == 1 and requests[0]["stream"] is True
    assert seen[-1] == output["topics"][0]["text"]
    assert any(value != seen[-1] for value in seen)
    assert response.actual_nanos == 3000
    assert validate_output("group_briefing", response, packet()).text == seen[-1]
    assert "HIDDEN-REASONING" not in str(response)
    output["topics"][0]["citations"] = [99]
    with pytest.raises(InvalidOutput, match="^briefing_citations$"):
        validate_output("group_briefing", EngineResponse(output), packet())


@pytest.mark.parametrize("role", sorted(GROUP_ROLES))
@pytest.mark.parametrize("actual", [123, None])
async def test_group_format_failures_keep_receipts_family_and_no_private_pause_or_replay(
    tmp_path, role, actual
):
    rig = accounting(tmp_path)
    seen = []
    rig.owners[0].on_format = lambda *values: seen.append(values)

    class Broken:
        name, model, provider = "mimo", LOGICAL_MODEL, PROVIDER
        calls = 0

        async def generate(self, role, source):
            self.calls += 1
            return EngineResponse(
                {"wrong": "Authored private marker."}, actual_nanos=actual
            )

    adapter = Broken()
    job = queue(rig.stores[0], "authored-failed-group", role)
    try:
        with pytest.raises(Refused, match="briefing_schema"):
            await rig.owners[0].run(adapter, job, packet(), lambda: True)
        receipt = rig.stores[0].rows("SELECT * FROM receipts")[0]
        usage = json.loads(receipt["usage"])
        assert usage["format_failure"] == "briefing_schema"
        assert usage["defaults_revision"] == guidance.DEFAULTS_REVISION
        assert receipt["family"] == "group_briefing"
        assert "Authored private marker" not in receipt["error"]
        assert rig.stores[0].rows("SELECT status,actual,family FROM reservations") == [
            {
                "status": "receipted" if actual is not None else "unknown",
                "actual": actual,
                "family": "group_briefing",
            }
        ]
        assert rig.stores[0].rows("SELECT state FROM jobs") == [{"state": "failed"}]
        with pytest.raises(Refused, match="Attempt already dispatched"):
            await rig.owners[0].run(adapter, job, packet(), lambda: True)
        assert adapter.calls == 1 and seen == []
    finally:
        for store in rig.stores:
            store.close()


@pytest.mark.parametrize("role", sorted(GROUP_ROLES))
async def test_group_exact_route_refusal_records_no_dispatch(tmp_path, role):
    rig = accounting(tmp_path)

    class Missing:
        name, model, provider = "mimo", LOGICAL_MODEL, PROVIDER

        async def prepare(self, source):
            raise ProviderRefused("Exact MiMo / Xiaomi route unavailable")

        async def generate(self, *args):
            pytest.fail("A route refusal cannot reach generation")

    try:
        job = queue(rig.stores[0], "authored-route-refusal", role)
        with pytest.raises(ProviderRefused):
            await rig.owners[0].run(Missing(), job, packet(), lambda: True)
        assert rig.stores[0].rows("SELECT state FROM jobs") == [{"state": "refused"}]
        assert not rig.stores[0].rows("SELECT * FROM reservations")
        assert not rig.stores[0].rows("SELECT * FROM receipts")
    finally:
        for store in rig.stores:
            store.close()


@pytest.mark.parametrize("role", ["group_briefing_image", "group_briefing_chunk"])
async def test_hidden_stages_cannot_publish_even_with_a_modelwork_callback(
    tmp_path, role
):
    rig = accounting(tmp_path)
    shown = []

    class Hidden:
        name, model, provider = "offline", "authored", "local"

        async def generate_stream(self, *args):
            pytest.fail("Only the final briefing may stream")

        async def generate(self, value, source):
            return EngineResponse(answer(value), actual_nanos=0)

    source = packet()
    source["images"] = [{"message_id": 11}]
    try:
        job = queue(rig.stores[0], "authored-hidden", role)
        await rig.owners[0].run(
            Hidden(), job, source, lambda: True, on_text=shown.append
        )
        assert shown == []
    finally:
        for store in rig.stores:
            store.close()


async def test_authored_fake_has_all_group_contracts_without_private_role_output():
    adapter = FakeEngine()
    source = packet()
    source["images"] = [{"message_id": 11}]
    for role in sorted(GROUP_ROLES):
        result = validate_output(
            role, EngineResponse(await adapter.generate(role, source)), source
        )
        assert result is not None
    no_substance = {**packet(), "messages": [{"id": 11, "text": ""}]}
    result = validate_output(
        "group_briefing",
        EngineResponse(await adapter.generate("group_briefing", no_substance)),
        no_substance,
    )
    assert result.text == NO_SUBSTANCE
