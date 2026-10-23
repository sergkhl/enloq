import asyncio
import itertools
import json
import time

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.inference.guidance import instructions, snapshot
from enloq.inference.openrouter import MODEL, OpenRouterEngine
from enloq.inference.streaming import TextPreview
from enloq.storage.accounts import AccountRegistry

from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_emotion import KEY
from backend.tests.test_rebuild import fixture, message

# ADR 30's decision after the streamed rewrite; calming (ADR 53), which decides
# first, is covered by test_calming.
pytestmark = pytest.mark.usefixtures("calming_off")


@pytest.mark.parametrize(
    "order", list(itertools.permutations(("text", "image_context", "emotion")))
)
@pytest.mark.parametrize("escaped", [True, False])
def test_all_field_orders_and_character_boundaries_hide_metadata(order, escaped):
    text = 'Привет 😀 "quote" \\ line\nnext'
    values = {
        "text": text,
        "image_context": [{"message_id": 1, "text": "hidden"}],
        "emotion": "worried",
    }
    parser, shown = TextPreview(), []
    for char in json.dumps({k: values[k] for k in order}, ensure_ascii=escaped):
        value = parser.feed(char)
        if value:
            assert text.startswith(value)
            assert not any(0xD800 <= ord(c) <= 0xDFFF for c in value)
            shown.append(value)
    assert shown[-1] == text and len(set(shown)) > 2


def frame(fragment, finish=None):
    body = {
        "id": "authored-stream",
        "model": MODEL,
        "provider": "Xiaomi",
        "choices": [{"delta": {"content": fragment}, "finish_reason": finish}],
    }
    if finish:
        body["usage"] = {"cost": 0.00001}
    return ("data: " + json.dumps(body) + "\n\n").encode()


@pytest.mark.parametrize("outcome", ["good", "emotion", "primary", "edit", "interrupt"])
async def test_real_adapter_streams_then_emotion_is_classified_after_publication(
    tmp_path, outcome
):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, date=app.store.clock() - 31))
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0")
    held, release = asyncio.Event(), asyncio.Event()
    shown = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for part in ['{"text":"First', " visible", " prefix", ' and final text"']:
                yield frame(part)
                await asyncio.sleep(0)
                shown.append(app.conversation(KEY)["blocks"][0]["stream"]["text"])
            held.set()
            await release.wait()
            # ADR 30: emotion is no longer part of the rewrite's output.
            tail = ',"image_context":[]' + (
                ',"emotion":"worried"' if outcome == "emotion" else ""
            )
            yield frame(
                tail + (',"unknown":true}' if outcome == "primary" else "}"), "stop"
            )
            yield b"data: [DONE]\n\n"

    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-authored-placeholder")
    settings = Settings(
        mode="personal",
        data=tmp_path,
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=Stream())

    app.engine = OpenRouterEngine(
        settings,
        user="authored-provider-pseudonym",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        rates={
            "verified_at": time.time(),
            "prompt": "0.0000001",
            "completion": "0.0000002",
            "context_length": 1048576,
        },
    )
    registry = AccountRegistry(tmp_path)
    app.rewriting.work.on_format = registry.record_format
    task = asyncio.create_task(app.process_due())
    await asyncio.wait_for(held.wait(), 3)
    assert len(set(shown)) == 4
    assert app.conversation(KEY)["blocks"][0]["state"] == "working"
    assert app.emotion.view(KEY).code is None
    assert not app.store.rows("SELECT * FROM emotion_state")
    if outcome == "edit":
        app.sync.ingest(message(app, revision=2, text="Changed source"))
    if outcome == "interrupt":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        release.set()
        await task
    view = app.emotion.view(KEY)
    if outcome == "good":
        # Readiness never waits for the classification that follows it.
        assert app.conversation(KEY)["blocks"][0]["state"] == "ready"
        assert (view.code, view.reason) == (None, "pending")
        assert registry.format_failures().get("incoming_rewrite", 0) == 0
        app.decider.emotion = "worried"
        await app.process_due()
        view = app.emotion.view(KEY)
        assert (view.code, view.reason) == ("worried", "available")
    else:
        assert view.code is None
    if outcome in ("emotion", "primary"):
        # An emotion field is now outside the strict incoming contract.
        assert app.conversation(KEY)["blocks"][0]["state"] == "failed"
        assert registry.format_failures()["incoming_rewrite"] == 1
    assert len(requests) == 1 and requests[0]["stream"] is True
    assert app.store.rows("SELECT actual FROM reservations")[0]["actual"] == (
        None if outcome == "interrupt" else 10000
    )
    await app.engine.client.aclose()


def test_extraction_guidance_is_global_editable_and_decision_only(tmp_path):
    from enloq.inference.guidance import decision_questions

    registry = AccountRegistry(tmp_path)
    saved = registry.set_guidance({"emotion": "AUTHORED-EXTRACTION"})
    version = snapshot(saved["fields"], saved["version"])
    # ADR 30: the field instructs the decision questions, never a rewrite.
    for role in ("emotion_classification", "reaction_assessment"):
        question = decision_questions(role, version)["emotion"]
        assert "AUTHORED-EXTRACTION" in question["instructions"]
    for role in ("incoming_rewrite", "chat_memory", "outgoing_rewrite"):
        assert "AUTHORED-EXTRACTION" not in instructions(role, {}, version)
    assert (
        registry.set_guidance({"emotion": None})["fields"]["emotion"]
        != "AUTHORED-EXTRACTION"
    )
