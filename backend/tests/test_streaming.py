import asyncio
import json

import httpx
import pytest
from enloq.inference.base import EngineResponse
from enloq.inference.openrouter import MODEL
from enloq.inference.streaming import StreamFailure, TextPreview, read_stream

from backend.tests.test_rebuild import fixture, message


@pytest.mark.parametrize("escaped", [True, False])
@pytest.mark.parametrize("first", ["text", "image_context"])
def test_preview_every_character_boundary_only_exposes_root_text(escaped, first):
    text = 'Привет 😀 "quoted" \\ line\n@username'
    parts = {
        "text": text,
        "image_context": [{"message_id": 1, "text": "hidden interpretation"}],
    }
    if first == "image_context":
        parts = dict(reversed(list(parts.items())))
    raw = json.dumps(parts, ensure_ascii=escaped)
    parser = TextPreview()
    seen = []
    for char in raw:
        value = parser.feed(char)
        if value is not None:
            seen.append(value)
            assert text.startswith(value)
            assert not any(0xD800 <= ord(c) <= 0xDFFF for c in value)
    assert seen[-1] == text
    assert any(value and value != text for value in seen)


class Bytes(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        for i in range(0, len(self.body), 3):
            yield self.body[i : i + 3]


def stream_body(*, done=True, model=MODEL, error=False):
    frames = [": comment\r\n\r\n"]
    for fragment, finish, usage in [
        (' {"image_context":[],"te', None, None),
        ('xt":"Привет ', None, None),
        ('😀"}', "stop", None),
        ("", "stop", {"cost": 0.000003, "completion_tokens": 9}),
    ]:
        value = dict(
            id="synthetic-stream",
            model=model,
            # The proxy may pass a provider name through; it is not checked.
            provider="Xiaomi",
            choices=[
                dict(
                    delta={"content": fragment, "reasoning": "never exposed"},
                    finish_reason=finish,
                )
            ],
        )
        if usage:
            value["usage"] = usage
            if error:
                value["error"] = {"message": "private provider detail"}
        frames.append("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n")
    if done:
        frames.append("data: [DONE]\r\n\r\n")
    return "".join(frames).encode()


async def test_stream_framing_unicode_usage_and_route_binding():
    seen = []
    response = await read_stream(
        httpx.Response(200, stream=Bytes(stream_body())), MODEL, seen.append
    )
    assert seen == ["Привет ", "Привет 😀"]
    assert response.actual_nanos == 3000
    assert response.usage["finish_reason"] == "stop"
    assert "reasoning" not in str(response)
    assert json.loads(response.content)["text"] == "Привет 😀"
    with pytest.raises(StreamFailure):
        await read_stream(
            httpx.Response(200, stream=Bytes(stream_body(model="xiaomi/wrong"))),
            MODEL,
            seen.append,
        )


@pytest.mark.parametrize("error", [False, True])
async def test_interrupted_stream_retains_receipted_cost(error):
    with pytest.raises(StreamFailure) as failed:
        await read_stream(
            httpx.Response(200, stream=Bytes(stream_body(done=False, error=error))),
            MODEL,
            lambda _: None,
        )
    assert failed.value.receipt.actual_nanos == 3000
    assert "private provider detail" not in str(failed.value)


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
@pytest.mark.parametrize(
    "outcome", ["success", "format", "empty", "source_change", "cancel"]
)
async def test_incoming_snapshots_reconnect_recovery_and_stale_output(
    tmp_path, outcome
):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, date=app.store.clock() - 31))
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0")
    started, finish = asyncio.Event(), asyncio.Event()

    class Engine:
        name, model, provider = "muse", "synthetic", "meta"
        calls = 0

        async def generate_stream(self, role, packet, on_text):
            self.calls += 1
            on_text("Visible partial")
            started.set()
            await finish.wait()
            on_text("Late text")
            return EngineResponse(
                {"wrong": "shape"}
                if outcome == "format"
                else (
                    {"parts": [], "image_context": []}
                    if outcome == "empty"
                    else {"text": "Validated text", "image_context": []}
                ),
                actual_nanos=7,
            )

    app.engine = Engine()
    work = asyncio.create_task(app.process_due())
    await started.wait()
    stream = app.conversation("1/user:42")["blocks"][0]["stream"]
    assert stream["text"] == "Visible partial"
    reopened = fixture(tmp_path)
    assert reopened.conversation("1/user:42")["blocks"][0]["stream"] == stream
    await app.process_due()
    assert app.engine.calls == 1
    if outcome == "source_change":
        app.sync.ingest(message(app, revision=2, text="New source"))
        assert app.conversation("1/user:42")["blocks"][0]["stream"] is None
    if outcome == "cancel":
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        app.store.recover()
    else:
        finish.set()
        await work
    block = app.conversation("1/user:42")["blocks"][0]
    assert block["stream"] is None
    assert block["rendered"] == ("Validated text" if outcome == "success" else None)
    assert not app.store.rows("SELECT * FROM rewrite_streams")
    assert app.store.rows("SELECT status FROM reservations")[0]["status"] == (
        "unknown" if outcome == "cancel" else "receipted"
    )
    if outcome == "empty":
        assert block["state"] == "failed"
        assert "(omitted_target_text)" in block["error"]
        receipt = app.store.rows("SELECT * FROM receipts")[0]
        assert json.loads(receipt["usage"])["format_failure"] == "omitted_target_text"
        assert app.store.rows("SELECT actual FROM reservations")[0]["actual"] == 7
        await app.process_due()
        assert app.engine.calls == 1


async def test_fast_partial_updates_reach_event_subscribers_before_completion(tmp_path):
    from types import SimpleNamespace

    from enloq.api.accounts import account_routes
    from enloq.storage.accounts import AccountRegistry

    app = fixture(tmp_path)
    app.sync.ingest(message(app, date=app.store.clock() - 31))
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0")

    class Engine:
        name, model, provider = "offline", "synthetic", "local"

        async def generate_stream(self, role, packet, on_text):
            for text in ["First", "First second", "First second third"]:
                on_text(text)
                await asyncio.sleep(0.04)
            return EngineResponse(
                {"text": "Final validated words", "image_context": []}, actual_nanos=0
            )

    app.engine = Engine()
    registry = AccountRegistry(tmp_path)
    # The stream carries only the member's own accounts (ADR 48).
    registry.register(1, "synthetic.session", directory=".")
    manager = SimpleNamespace(
        registry=registry,
        runtimes={1: SimpleNamespace(accepting=True, assistant=app)},
    )
    endpoint = next(
        r.endpoint
        for r in account_routes(
            manager, None, SimpleNamespace(active=lambda _: True)
        ).routes
        if r.path == "/api/events"
    )
    response = await endpoint(
        SimpleNamespace(state=SimpleNamespace(local_session="fixture", member="owner"))
    )
    events = response.body_iterator
    assert "refresh" in await anext(events)
    work = asyncio.create_task(app.process_due())
    seen = []
    try:
        async with asyncio.timeout(2):
            while True:
                event = await anext(events)
                assert "First" not in event  # Change notices still carry no text.
                block = app.conversation("1/user:42")["blocks"][0]
                if block["stream"]:
                    seen.append(block["stream"]["text"])
                if block["state"] == "ready":
                    break
        assert "First" in seen and "First second" in seen
        assert block["rendered"] == "Final validated words"
    finally:
        await work
        await events.aclose()
