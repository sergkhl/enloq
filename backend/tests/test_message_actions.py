"""P3: manual repeat processing is removed as functionality (ADR 26).

Chat-level earlier processing, failure Retry and ordinary transcription remain.
Authored content and fake transports only."""

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import MediaInfo, Message, Refused
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"


class Speech:
    def __init__(self, *, fail=False):
        self.fail, self.calls = fail, 0

    async def transcribe(self, path):
        self.calls += 1
        if self.fail:
            raise TimeoutError("Authored transcription failure")
        return {"text": "Authored spoken words.", "language": "en"}


async def test_a_completed_rewrite_has_no_repeat_processing(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app, 1, text="Authored request"))
    now[0] += 31
    await app.process_due()
    await app.process_due()  # The Emotion Classification that follows it.
    block = app.store.rows("SELECT * FROM blocks")[0]
    assert block["state"] == "ready" and len(app.engine.calls) == 1
    with pytest.raises(Refused, match="already rewritten"):
        app.rewriting.retry(block["id"])
    assert not app.store.rows(
        "SELECT 1 FROM jobs WHERE state IN ('queued','claimed','running')"
    )
    # Failure metadata cannot regenerate retained completed text.
    with app.store.transaction() as db:
        db.execute("UPDATE blocks SET state='failed',error='Authored failure'")
    with pytest.raises(Refused, match="already rewritten"):
        app.rewriting.retry(block["id"])
    # A genuinely failed changed source keeps its deliberate Retry.
    generate = app.engine.generate

    async def failed(role, packet):
        response = await generate(role, packet)
        return {"wrong": "shape"} if role == "incoming_rewrite" else response

    app.engine.generate = failed
    app.sync.ingest(message(app, 1, revision=2, text="Changed authored request"))
    now[0] += 31
    await app.process_due()
    assert app.store.rows("SELECT state FROM blocks")[0]["state"] == "failed"
    app.engine.generate = generate
    app.rewriting.retry(block["id"])
    await app.process_due()
    assert len(app.engine.calls) == 3
    await app.process_due()  # The Emotion Classification that follows it.
    # First-time processing belongs to the separate chat-level action.
    app.sync.ingest(message(app, 2, outgoing=True))
    app.sync.ingest(message(app, 3), target=False)
    with pytest.raises(Refused, match="failed or interrupted"):
        app.rewriting.retry(f"{KEY}/3")
    assert app.rewriting.process_older(KEY) == 1
    await app.process_due()
    assert len(app.engine.calls) == 4


async def test_failed_transcription_retries_only_through_block_retry(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.media.transcriber = Speech(fail=True)
    app.transport.media["user:42", 1] = b"authored local audio"
    app.sync.ingest(
        Message(
            account_id=1,
            peer="user:42",
            message_id=1,
            revision=1,
            date=now[0],
            media="audio",
            media_info=MediaInfo(voice=True),
        )
    )
    now[0] += 31
    await app.process_due()
    block = app.store.rows("SELECT * FROM blocks")[0]
    assert block["state"] == "failed" and app.media.transcriber.calls == 1
    # Normal processing never repeats a failed transcription on its own.
    now[0] += 3600
    await app.process_due()
    assert app.media.transcriber.calls == 1
    app.media.transcriber = Speech()
    app.rewriting.retry(block["id"])
    await app.process_due()
    assert app.media.transcriber.calls == 1
    assert app.store.rows("SELECT state FROM blocks")[0]["state"] == "ready"
    # A completed transcript is never replaced by hand.
    with pytest.raises(Refused):
        app.rewriting.retry(block["id"])


@pytest.fixture
def served(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        yield app, client


def test_removed_actions_are_unavailable_through_the_public_api(served):
    app, client = served
    conversation = app.state.accounts.get(1)
    conversation.sync.ingest(
        Message(
            account_id=1,
            peer="user:101",
            message_id=9,
            revision=1,
            date=conversation.store.clock(),
            media="audio",
            media_info=MediaInfo(voice=True),
        ),
        target=False,
    )
    base = "/api/accounts/1/chats/user:101/originals/9"
    assert client.post(base + "/retranscribe", json={"revision": 1}).status_code in (
        404,
        405,
    )
    assert client.get(base + "/transcription").status_code in (404, 405)
    # The raw cached transcript stays readable.
    assert client.get(base + "/transcript").status_code == 200
    with conversation.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='ready',rendered='Done',rendered_revision=revision"
        )
    block = conversation.store.rows(
        "SELECT id,revision FROM blocks WHERE chat_key='1/user:101' LIMIT 1"
    )[0]
    anchor = block["id"]
    retry = client.post(
        f"/api/accounts/1/chats/user:101/blocks/{anchor.rsplit('/', 1)[1]}/retry",
        json={"revision": block["revision"]},
    )
    assert retry.status_code == 400 and "already rewritten" in retry.json()["detail"]
