"""H4/ADR 29: bounded output per role, precise format failures and preserved facts."""

import json
import time
from types import SimpleNamespace

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.inference.base import EngineResponse
from enloq.inference.openrouter import (
    BACKGROUND_OUTPUT_TOKENS,
    MODEL,
    TAKEAWAY_CHARACTERS,
    OpenRouterEngine,
    output_schema,
)
from enloq.inference.work import InvalidOutput, validate_output

from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_memory import KEY, conversation, fold, update
from backend.tests.test_memory_takeaways import retained
from backend.tests.test_rebuild import fixture

# Memory as before ADR 56's Off default: these chats learn without switching
# on; test_memory_default covers the default as shipped.
pytestmark = pytest.mark.usefixtures("remember_on")


def memory_packet(*, generated=0, owners=0, remaining=40, sources=True):
    """A memory update's packet: the person's list as authored, and an empty
    Account Memory (ADR 55)."""
    return {
        "person": {
            "memory": person_list(generated, owners),
            "limits": limits(remaining),
        },
        "account": {"memory": [], "limits": limits(40)},
        "messages": [{"id": 1, "text": "Authored fact.", "outgoing": False}]
        if sources
        else [],
    }


def limits(remaining):
    return {"generated_entries": remaining, "generated_bytes": 3000}


def person_list(generated, owners):
    return [
        {
            "id": f"generated-{index}",
            "source": "model",
            "text": "Authored retained fact.",
            "citations": [80 + index],
        }
        for index in range(generated)
    ] + [
        {
            "id": f"owner-{index}",
            "source": "owner",
            "text": "Owner note.",
            "citations": [],
        }
        for index in range(owners)
    ]


def addition(text):
    return update({"add": [{"text": text, "citations": [1], "based_on": []}]})


@pytest.fixture
async def adapter(tmp_path):
    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-authored-placeholder")
    requests = []
    outcome = SimpleNamespace(output=None, finish_reason="stop", completion_tokens=25)

    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == f"{ENDPOINT}/chat/completions"
        payload = json.loads(request.content)
        requests.append(payload)
        fields = payload["response_format"]["json_schema"]["schema"]["properties"]
        if "person" in fields:
            output = update()
        elif "image_context" in fields:
            output = {
                "text": "Authored rewrite.",
                "image_context": [],
                "emotion": "unknown",
            }
        else:
            output = {"text": "Authored rewrite."}
        if outcome.output is not None:
            output = outcome.output
        choice = {"finish_reason": outcome.finish_reason}
        response = {
            "model": MODEL,
            "provider": "Xiaomi",
            "id": f"authored-memory-format-receipt-{len(requests)}",
            "usage": {"cost": 0.000001, "completion_tokens": outcome.completion_tokens},
            "choices": [choice],
        }
        if payload.get("stream"):
            choice["delta"] = {"content": json.dumps(output)}
            return httpx.Response(
                200, content=f"data: {json.dumps(response)}\n\ndata: [DONE]\n\n"
            )
        choice["message"] = {"content": json.dumps(output)}
        return httpx.Response(200, json=response)

    engine = OpenRouterEngine(
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
            "prompt": "0.00000014",
            "completion": "0.00000028",
            "verified_at": time.time(),
            "context_length": 1048576,
        },
    )
    yield engine, requests, outcome
    await engine.client.aclose()


# Xiaomi's own thinking switch; its reasoning text is never read or retained.
HIDDEN = {"type": "enabled"}


@pytest.mark.parametrize(
    "role,stream,expected_limit,expected_thinking",
    [
        ("chat_memory", False, BACKGROUND_OUTPUT_TOKENS, HIDDEN),
        ("incoming_rewrite", False, BACKGROUND_OUTPUT_TOKENS, HIDDEN),
        ("incoming_rewrite", True, BACKGROUND_OUTPUT_TOKENS, HIDDEN),
    ],
)
async def test_each_role_has_its_reasoning_and_cap_and_every_receipt_records_it(
    adapter, role, stream, expected_limit, expected_thinking
):
    engine, requests, _ = adapter
    packet = (
        memory_packet()
        if role == "chat_memory"
        else {
            "messages": [{"id": 1, "text": "Authored source."}],
            "draft": {"text": "Own words."},
            "tone": "neutral",
            "images": [],
        }
    )
    shown = []
    response = (
        await engine.generate_stream(role, packet, shown.append)
        if stream
        else await engine.generate(role, packet)
    )
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == expected_limit
    assert requests[0]["extra_body"] == {"thinking": expected_thinking}
    assert "reasoning" not in requests[0]
    assert "tool_choice" not in requests[0]
    assert requests[0]["user"] == "authored-provider-pseudonym"
    assert response.usage["output_token_limit"] == expected_limit
    assert response.actual_nanos == 1000
    assert response.usage["finish_reason"] == "stop"
    assert requests[0]["model"] == MODEL
    # The proxy route names its provider; no OpenRouter routing is sent.
    assert "provider" not in requests[0]
    if stream:
        assert shown == ["Authored rewrite."]
    # The deadline fits a cap-length response: outgoing at the listed 39 tokens
    # per second, hidden background reasoning at a 32-per-second floor.
    floor = 39 if role == "outgoing_rewrite" else 32
    assert engine.attempt_seconds(role) >= expected_limit / floor


async def test_preparation_ceiling_covers_the_memory_output_cap(adapter):
    engine, requests, _ = adapter
    # 1,048,576 prompt tokens at $0.00000014, plus 16,384 output at $0.00000028.
    assert await engine.prepare({}) == 151_388_160
    assert not requests


@pytest.mark.parametrize("text", ["я" * 150, "🙂" * 75], ids=["cyrillic", "emoji"])
def test_memory_preserves_complete_multilingual_takeaways_at_the_byte_limit(text):
    assert len(text.encode()) == 300
    result = validate_output(
        "chat_memory", EngineResponse(addition(text)), memory_packet()
    )
    assert result.person.add[0].text == text


@pytest.mark.parametrize("text", ["я" * 151, "🙂" * 76], ids=["cyrillic", "emoji"])
def test_memory_reports_byte_overflow_without_truncating_or_echoing_text(text):
    with pytest.raises(InvalidOutput) as raised:
        validate_output("chat_memory", EngineResponse(addition(text)), memory_packet())
    assert str(raised.value) == "memory_text_bytes"
    assert text not in str(raised.value)


@pytest.mark.parametrize(
    "text,valid",
    [("a" * 150, True), ("я" * 150, True), ("a" * 151, False)],
    ids=["latin-at-limit", "cyrillic-at-limit", "latin-over"],
)
def test_takeaways_share_one_150_character_ceiling_with_the_provider(text, valid):
    schema = output_schema("chat_memory", memory_packet())
    field = schema["$defs"]["PersonAddition"]["properties"]["text"]
    assert field["maxLength"] == TAKEAWAY_CHARACTERS == 150
    response = EngineResponse(addition(text))
    if valid:
        assert validate_output("chat_memory", response, memory_packet())
    else:
        with pytest.raises(InvalidOutput) as raised:
            validate_output("chat_memory", response, memory_packet())
        assert str(raised.value) == "memory_text_length"
        assert text not in str(raised.value)


@pytest.mark.parametrize("text", ["", " \t\n"], ids=["empty", "whitespace"])
def test_blank_memory_text_has_a_fixed_content_free_diagnostic(text):
    with pytest.raises(InvalidOutput) as raised:
        validate_output("chat_memory", EngineResponse(addition(text)), memory_packet())
    assert str(raised.value) == "memory_text_empty"


@pytest.mark.parametrize("value", [None, {"private-authored-marker": "not text"}, 123])
def test_wrong_memory_text_types_remain_generic_schema_errors(value):
    with pytest.raises(InvalidOutput) as raised:
        validate_output("chat_memory", EngineResponse(addition(value)), memory_packet())
    assert str(raised.value) == "schema"


@pytest.mark.parametrize(
    "generated,owners,remaining,sources,expected",
    [
        (2, 3, 37, True, (2, 37, 3)),
        (0, 2, 38, True, (0, 38, 2)),
        (2, 3, 37, False, (2, 37, 0)),
        (0, 3, 37, False, (0, 0, 0)),
        (0, 40, 0, True, (0, 0, 10)),
    ],
)
def test_memory_arrays_respect_packet_provenance_and_remaining_entry_allowance(
    generated, owners, remaining, sources, expected
):
    packet = memory_packet(
        generated=generated, owners=owners, remaining=remaining, sources=sources
    )
    schema = output_schema("chat_memory", packet)
    fields = schema["properties"]["person"]["properties"]
    based_on = schema["$defs"]["PersonAddition"]["properties"]["based_on"]
    assert (
        fields["remove"]["maxItems"],
        fields["add"]["maxItems"],
        fields["outdated"]["maxItems"],
    ) == expected
    assert based_on["maxItems"] == expected[0]
    if generated:
        known = [
            entry["id"]
            for entry in packet["person"]["memory"]
            if entry["source"] == "model"
        ]
        assert fields["remove"]["items"]["enum"] == known
        assert based_on["items"]["enum"] == known


@pytest.mark.parametrize(
    "failure,expected_code",
    [
        ("length", "incomplete_response"),
        ("bytes", "memory_text_bytes"),
        ("blank", "memory_text_empty"),
        ("wrong_type", "schema"),
    ],
)
async def test_failed_memory_attempt_keeps_previous_facts_and_receipt_until_explicit_retry(
    tmp_path, adapter, failure, expected_code
):
    engine, requests, outcome = adapter
    now = [2_000_000_000.0]
    app = fixture(tmp_path / "store", clock=lambda: now[0])
    app.engine = engine
    conversation(app, now)
    app.memory.add(KEY, 0, "Keep this owner-authored fact.")
    retained(app, "prior", "Keep this prior generated fact.", [80])
    before = app.memory.entries(KEY)
    app.memory.record(KEY, [f"{KEY}/1"])
    marker = "AUTHORED-FAILED-BODY"
    output = {
        "length": marker,
        "bytes": marker + "я" * 151,
        "blank": " \t\n",
        "wrong_type": {marker: "Must never reach operational records."},
    }[failure]
    outcome.output = addition(output)
    outcome.finish_reason = "length" if failure == "length" else "stop"
    outcome.completion_tokens = BACKGROUND_OUTPUT_TOKENS if failure == "length" else 200
    app.memory.schedule(KEY)
    try:
        original = await fold(app)
        assert app.memory.entries(KEY) == before
        assert [row["message_id"] for row in app.memory.candidates(KEY)] == [1]
        assert app.memory.state(KEY)["error"]
        first_receipt = app.store.rows(
            "SELECT * FROM receipts WHERE id=?", (original["id"],)
        )[0]
        usage = json.loads(first_receipt["usage"])
        assert usage["format_failure"] == expected_code
        assert usage["output_token_limit"] == BACKGROUND_OUTPUT_TOKENS
        assert usage["completion_tokens"] == outcome.completion_tokens
        assert usage["finish_reason"] == outcome.finish_reason
        assert app.store.rows(
            "SELECT state FROM jobs WHERE id=?", (original["id"],)
        ) == [{"state": "failed"}]
        assert app.store.rows("SELECT status,actual FROM reservations") == [
            {"status": "receipted", "actual": 1000}
        ]
        for _ in range(2):
            now[0] += 3600
            assert not app.memory.schedule(KEY)
            await app.process_due()
        assert len(requests) == 1
        operational = {
            table: app.store.rows(f"SELECT * FROM {table}")
            for table in ("jobs", "receipts", "reservations")
        }
        assert marker not in json.dumps(operational)

        outcome.output = update()
        outcome.finish_reason = "stop"
        outcome.completion_tokens = 25
        app.memory.retry(KEY)
        retried = await fold(app)
        assert retried["id"] != original["id"]
        assert len(requests) == 2
        assert app.memory.entries(KEY) == before
        assert not app.memory.candidates(KEY) and not app.memory.state(KEY)["error"]
        assert app.store.rows(
            "SELECT * FROM receipts WHERE id=?", (original["id"],)
        ) == [first_receipt]
        assert app.store.rows(
            "SELECT state FROM jobs WHERE id=?", (original["id"],)
        ) == [{"state": "failed"}]
        assert app.store.rows("SELECT status,actual FROM reservations") == [
            {"status": "receipted", "actual": 1000},
            {"status": "receipted", "actual": 1000},
        ]
    finally:
        app.store.close()


@pytest.mark.parametrize("extra_byte", [False, True], ids=["at-limit", "one-over"])
async def test_large_consolidation_respects_total_allowance_owner_text_and_provenance(
    tmp_path, adapter, extra_byte
):
    engine, requests, outcome = adapter
    now = [2_000_000_000.0]
    app = fixture(tmp_path / "store", clock=lambda: now[0])
    app.engine = engine
    conversation(app, now)
    owner_text = "Owner-authored condition: " + "x" * 49
    assert len(owner_text.encode()) == 75
    owner = app.memory.add(KEY, 0, owner_text)["entries"][0]["id"]
    old_ids = [f"{index:012x}" for index in range(39)]
    for index, identity in enumerate(old_ids):
        retained(app, identity, f"Prior authored fact {index}.", [40 + index])
    before = app.memory.entries(KEY)
    app.memory.record(KEY, [f"{KEY}/1"])
    generated = [
        {
            "text": f"Fact{index:02d} " + "я" * 34,
            "citations": [40 + index],
            "based_on": [identity],
        }
        for index, identity in enumerate(old_ids)
    ]
    if extra_byte:
        generated[0]["text"] += "!"
    assert sum(len(entry["text"].encode()) for entry in generated) == 2925 + extra_byte
    outcome.output = update({"add": generated, "remove": old_ids})
    app.memory.schedule(KEY)
    try:
        job = await fold(app)
        assert len(requests) == 1
        assert requests[0]["max_tokens"] == BACKGROUND_OUTPUT_TOKENS
        assert len(app.memory.entries(KEY)) == 40
        kept_owner = next(
            entry for entry in app.memory.entries(KEY) if entry["id"] == owner
        )
        assert kept_owner == next(entry for entry in before if entry["id"] == owner)
        if extra_byte:
            assert app.memory.entries(KEY) == before
            assert "allowance" in app.memory.state(KEY)["error"]
            assert app.memory.candidates(KEY) and not app.memory.schedule(KEY)
        else:
            result = [
                entry for entry in app.memory.entries(KEY) if entry["source"] == "model"
            ]
            assert [entry["text"] for entry in result] == [
                entry["text"] for entry in generated
            ]
            assert [entry["citations"] for entry in result] == [
                [40 + index] for index in range(39)
            ]
            assert not app.memory.state(KEY)["error"] and not app.memory.candidates(KEY)
            assert (
                sum(len(entry["text"].encode()) for entry in app.memory.entries(KEY))
                == 3000
            )
        assert app.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],)) == [
            {"state": "failed" if extra_byte else "done"}
        ]
    finally:
        app.store.close()
