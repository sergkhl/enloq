import json

import pytest
from enloq.inference.base import EngineResponse
from enloq.inference.work import FLAT_UNKNOWN

from backend.tests.test_rebuild import fixture, image_bytes, message

# The incoming rewrite's output contract and receipts; calming (ADR 53),
# which decides first, is covered by test_calming.
pytestmark = pytest.mark.usefixtures("calming_off")


async def test_cached_images_plus_new_images_reuse_descriptions(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    for i in range(1, 5):
        app.transport.media["user:42", i] = image_bytes()
        app.sync.ingest(message(app, i, text=f"Option {i}", media="image"))
    now[0] += 30
    await app.process_due()
    await app.process_due()  # The Emotion Classification that follows it.
    prior = app.store.rows("SELECT * FROM derivatives ORDER BY source")
    app.transport.media["user:42", 5] = image_bytes()
    app.sync.ingest(message(app, 5, text="One more option", media="image"))
    now[0] += 30
    await app.process_due()
    packet = app.engine.calls[-1][1]
    assert [i["message_id"] for i in packet["images"]] == [5]
    assert all(m.get("image_context") for m in packet["messages"][:4])
    assert app.conversation("1/user:42")["blocks"][0]["state"] == "ready"
    assert app.store.rows("SELECT * FROM derivatives ORDER BY source")[:4] == prior
    await app.process_due()  # The Emotion Classification that follows it.
    # A genuinely failed changed group retains Retry and its cached meanings.
    generate = app.engine.generate

    async def failed(role, packet):
        response = await generate(role, packet)
        return {"wrong": "shape"} if role == "incoming_rewrite" else response

    app.engine.generate = failed
    app.sync.ingest(message(app, 6, text="A further authored point"))
    now[0] += 30
    await app.process_due()
    assert app.conversation("1/user:42")["blocks"][0]["state"] == "failed"
    app.engine.generate = generate
    app.rewriting.retry("1/user:42/1")
    await app.process_due()
    assert not app.engine.calls[-1][1]["images"]
    assert app.conversation("1/user:42")["blocks"][0]["state"] == "ready"


@pytest.mark.parametrize(
    "content,finish,reason,actual",
    [
        ('{"text":', "stop", "invalid_json", 123),
        ({"wrong": "shape"}, "stop", "schema", None),
        (
            {"text": "partial", "image_context": []},
            "length",
            "incomplete_response",
            123,
        ),
        ({"text": "request", "image_context": []}, "stop", "image_references", 123),
        (
            {
                "text": "request",
                "image_context": [{"message_id": 1, "text": "image"}] * 2,
            },
            "stop",
            "image_references",
            123,
        ),
        (
            {"text": "", "image_context": [{"message_id": 1, "text": "image"}]},
            "stop",
            "omitted_target_text",
            123,
        ),
        (
            {"parts": [], "image_context": [{"message_id": 1, "text": "image"}]},
            "stop",
            "omitted_target_text",
            123,
        ),
    ],
)
async def test_failure_codes_receipts_and_unknown_cost(
    tmp_path, content, finish, reason, actual
):
    app = fixture(tmp_path)
    app.transport.media["user:42", 1] = image_bytes()
    app.sync.ingest(message(app, media="image", date=app.store.clock() - 31))
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0")

    class Engine:
        name, model, provider = "muse", "synthetic", "meta"

        async def generate(self, role, packet):
            return EngineResponse(content, {"finish_reason": finish}, actual)

    app.engine = Engine()
    outcomes = []
    app.rewriting.work.on_format = lambda role, ok: outcomes.append(ok)
    await app.process_due()
    receipt = app.store.rows("SELECT * FROM receipts")[0]
    assert json.loads(receipt["usage"])["format_failure"] == reason
    assert reason in receipt["error"]
    reservation = app.store.rows("SELECT * FROM reservations")[0]
    assert reservation["actual"] == actual
    assert reservation["status"] == ("unknown" if actual is None else "receipted")
    assert reservation["reserved"] == FLAT_UNKNOWN
    assert outcomes == [False]
    assert app.conversation("1/user:42")["blocks"][0]["rendered"] is None
    await app.process_due()
    assert len(app.store.rows("SELECT * FROM receipts")) == 1
