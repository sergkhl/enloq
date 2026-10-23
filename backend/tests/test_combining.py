"""ADR 54: incoming text stays combined, not rewritten, unless calming
rewrites it; images, voice notes and video circles are still rewritten.
Authored sources and fake providers."""

import pytest
from enloq.application.conversations import Conversations
from enloq.domain.models import Chat
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import Store
from enloq.transport.fake import FakeTransport

from backend.tests.test_rebuild import image_bytes, message

KEY = "1/user:42"
LONG = (
    "The plumber can come on Thursday between ten and twelve, or on Friday "
    "after four. Tell me which suits you and I will confirm it with him, and "
    "please leave the key with the neighbour if nobody is home on the day."
)


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    registry = AccountRegistry(tmp_path)  # Text rewriting off, as shipped.
    store = Store(
        tmp_path / "rewriting.sqlite3",
        account_id=1,
        clock=lambda: now[0],
        shared_path=registry.path,
    )
    store.put_chat(
        Chat(
            account_id=1,
            peer="user:42",
            title="Authored",
            kind="private",
            ai_eligible=True,
        )
    )
    app = Conversations(store, FakeTransport())
    app.decider.emotion = "neutral"
    return app, now, registry


def rewrites(app):
    return [packet for role, packet in app.engine.calls if role == "incoming_rewrite"]


def block(app, identity=1):
    return next(b for b in app.stages(KEY) if identity in b["sources"])


async def settle(app, rounds=6):
    for _ in range(rounds):
        await app.process_due()


@pytest.mark.usefixtures("remember_on")
async def test_text_stays_combined_and_chat_memory_learns_from_its_decision(rig):
    app, now, registry = rig
    app.sync.ingest(message(app, 1, text=LONG), live=True)
    now[0] += 30
    await settle(app)
    assert not rewrites(app)
    assert block(app)["state"] == "original" and not block(app)["calmed"]
    assert app.emotion.view(KEY).code == "neutral"
    # The decision read it, so Chat Memory may fold it.
    analyzed = app.store.rows("SELECT key FROM analyzed WHERE chat_key=?", (KEY,))
    assert [row["key"] for row in analyzed] == [f"{KEY}/1"]


async def test_images_are_still_rewritten(rig):
    app, now, _ = rig
    app.transport.media["user:42", 1] = image_bytes()
    app.sync.ingest(message(app, 1, text="Is this the right one?", media="image"))
    now[0] += 30
    await settle(app)
    assert len(rewrites(app)) == 1 and block(app)["state"] == "ready"


async def test_text_rewriting_on_rewrites_text_from_the_threshold(rig):
    app, now, registry = rig
    registry.set_text_rewriting(True)
    app.sync.ingest(message(app, 1, text=LONG), live=True)
    app.sync.ingest(
        message(app, 2, text="Thanks!", outgoing=True, date=now[0] + 1), live=True
    )
    app.sync.ingest(message(app, 3, text="Short.", date=now[0] + 2), live=True)
    now[0] += 32
    await settle(app)
    # The long block is rewritten; the short one waits for the threshold.
    assert [p["messages"][0]["id"] for p in rewrites(app)] == [1]
    assert block(app, 3)["state"] == "original"


async def test_rewrite_earlier_messages_offers_only_media(rig):
    app, now, registry = rig
    old = now[0] - 3600
    app.sync.ingest(message(app, 1, text=LONG, date=old), target=False)
    app.sync.ingest(
        message(app, 2, text="Reply.", outgoing=True, date=old + 1), target=False
    )
    app.transport.media["user:42", 3] = image_bytes()
    app.sync.ingest(
        message(app, 3, text="And this?", media="image", date=old + 2), target=False
    )
    assert [b["sources"] for b in map(dict, app.rewriting.older(KEY))] == ["[3]"]
    registry.set_text_rewriting(True)
    assert len(app.rewriting.older(KEY)) == 2


async def test_a_retried_text_block_that_is_not_hostile_ends_combined(rig):
    app, now, _ = rig
    app.decider.emotion = "angry"

    async def unavailable(role, packet):
        raise ConnectionError("Authored outage")

    app.engine.generate = unavailable
    app.sync.ingest(message(app, 1, text="You're useless. Answer me."), live=True)
    now[0] += 30
    await settle(app)
    # The calmed rewrite failed; a Retry decides again.
    assert block(app)["state"] == "failed"
    del app.engine.generate
    app.decider.emotion = "neutral"
    app.rewriting.retry(block(app)["id"])
    await settle(app)
    assert block(app)["state"] == "original" and not rewrites(app)
