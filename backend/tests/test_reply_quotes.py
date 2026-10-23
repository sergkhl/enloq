"""ADR 41 Reply Quotes: reply header capture, the replied-to message's
resolution and replies to another chat's message, over authored sources."""

import asyncio
from datetime import UTC, datetime

import pytest
from enloq.api.server import create_app
from enloq.application.accounts import AccountManager
from enloq.application.context import incoming, projection
from enloq.config import Settings
from enloq.domain.models import Refused, ReplyElsewhere, reply_target
from fastapi.testclient import TestClient
from telethon.tl import types

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_group_briefing_views import KEY as GROUP
from backend.tests.test_group_briefing_views import authored, source
from backend.tests.test_rebuild import fixture, message
from backend.tests.test_transport import entityless_transport, native_message

KEY = "1/user:42"
ELSEWHERE = ReplyElsewhere(
    peer="channel:99", sender_peer="user:7", sender="Anna Example", date=1.5e9
)


async def test_native_reply_headers_keep_fragments_other_chats_and_stories():
    dialog = types.PeerUser(42)
    values = [native_message(i, dialog) for i in range(1, 7)]
    values[1].reply_to = types.MessageReplyHeader(
        reply_to_msg_id=1, quote=True, quote_text="Authored fragment"
    )
    values[2].reply_to = types.MessageReplyHeader(
        reply_to_msg_id=5,
        reply_to_peer_id=types.PeerChannel(99),
        reply_from=types.MessageFwdHeader(
            date=datetime(2026, 8, 30, 9, tzinfo=UTC), from_name=" Anna Example "
        ),
        quote_text="Quoted elsewhere",
    )
    values[3].reply_to = types.MessageReplyStoryHeader(peer=dialog, story_id=3)
    values[4].reply_to = types.MessageReplyHeader(
        reply_to_msg_id=8,
        reply_to_peer_id=types.PeerChat(55),
        reply_from=types.MessageFwdHeader(
            date=datetime(2026, 8, 31, tzinfo=UTC), from_id=types.PeerUser(1)
        ),
    )
    # A header naming this same chat is an ordinary reply.
    values[5].reply_to = types.MessageReplyHeader(
        reply_to_msg_id=1, reply_to_peer_id=dialog
    )
    actual = await entityless_transport().normalize_page(values)
    assert all(m.reply_read for m in actual)
    assert [
        (m.reply_to, m.reply_quote, m.reply_story, m.reply_status, m.reply_to_self)
        for m in actual
    ] == [
        (None, None, False, "none", False),
        (1, "Authored fragment", False, "available", False),
        (5, "Quoted elsewhere", False, "none", False),
        (None, None, True, "none", False),
        (8, None, False, "none", False),
        (1, None, False, "available", False),
    ]
    assert actual[2].reply_elsewhere == ReplyElsewhere(
        peer="channel:99",
        sender_peer=None,
        sender="Anna Example",
        date=datetime(2026, 8, 30, 9, tzinfo=UTC).timestamp(),
    )
    # The owner's own message elsewhere keeps its peer; no name is resolved.
    assert actual[4].reply_elsewhere.sender_peer == "user:1"
    assert actual[4].reply_elsewhere.sender is None
    assert [reply_target(m) for m in actual] == [None, 1, None, None, None, 1]


async def test_another_chat_sender_is_named_from_a_known_chat_only(tmp_path):
    app = fixture(tmp_path)
    transport = entityless_transport()
    transport.store = app.store
    native = native_message(2, types.PeerUser(42))
    native.reply_to = types.MessageReplyHeader(
        reply_to_msg_id=9,
        reply_to_peer_id=types.PeerChannel(99),
        reply_from=types.MessageFwdHeader(date=native.date, from_id=types.PeerUser(42)),
    )
    actual = await transport.normalize_page([native])
    assert actual[0].reply_elsewhere.sender == "Authored conversation"
    # No parent lookup ran for the other chat's number.
    assert actual[0].reply_status == "none"


def test_reply_details_are_learned_and_never_erased_without_new_work(tmp_path):
    app = fixture(tmp_path)
    legacy = message(app, 2, text="Legacy reply", reply_to=5)
    app.sync.ingest(legacy)
    before = {
        table: app.store.rows(f"SELECT * FROM {table}")
        for table in ("blocks", "jobs", "derivatives", "message_versions")
    }
    read = legacy.model_copy(
        update={"reply_elsewhere": ELSEWHERE, "reply_read": True, "text": "Ignored"}
    )
    app.store.learn_reply(read)
    learned = app.store.message(KEY, 2)
    assert learned.reply_elsewhere == ELSEWHERE and learned.reply_read
    assert learned.text == "Legacy reply" and learned.revision == legacy.revision
    assert reply_target(learned) is None
    assert {
        table: app.store.rows(f"SELECT * FROM {table}") for table in before
    } == before
    # A later capture without the details keeps them; a read never relearns.
    app.sync.ingest(legacy.model_copy(update={"revision": 2, "text": "Edited"}))
    edited = app.store.message(KEY, 2)
    assert edited.text == "Edited" and edited.reply_elsewhere == ELSEWHERE
    app.store.learn_reply(read.model_copy(update={"reply_elsewhere": None}))
    assert app.store.message(KEY, 2).reply_elsewhere == ELSEWHERE


async def test_private_resolution_reads_the_cache_first_then_telegram_once(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app, 4, text="Cached target"), target=False)
    app.sync.ingest(message(app, 10, reply_to=3, reply_read=True), target=False)
    app.sync.ingest(message(app, 11, reply_to=4, reply_read=True), target=False)
    # Captured before ADR 41: its number names another chat's message.
    app.sync.ingest(message(app, 12, reply_to=5), target=False)
    app.sync.ingest(message(app, 13, reply_to=6, reply_read=True), target=False)
    app.transport.messages = [
        message(app, 3, text="Uncached target"),
        message(app, 5, text="Same number in this chat"),
        message(app, 6, text="Old target", date=now[0] - 40 * 86400),
        message(app, 12, reply_to=5, reply_elsewhere=ELSEWHERE, reply_read=True),
    ]
    result = await app.replies.resolve(KEY, [10, 11, 12, 13])
    assert app.transport.exact_reads == [("user:42", (12, 3, 5, 6))]
    assert [t["message_id"] for t in result["targets"]] == [3, 4, 6]
    assert result["missing"] == [] and result["pending"] == []
    replies = {r["message_id"]: r for r in result["replies"]}
    assert replies[12]["reply_elsewhere"]["sender"] == "Anna Example"
    assert replies[12]["reply_read"] is True
    # Recent targets join the cache; the old one and the other chat's number
    # are not stored.
    assert app.store.message(KEY, 3).text == "Uncached target"
    assert app.store.message(KEY, 5) is None
    assert app.store.message(KEY, 6) is None
    again = await app.replies.resolve(KEY, [10, 11, 12])
    assert len(app.transport.exact_reads) == 1
    assert [t["message_id"] for t in again["targets"]] == [3, 4]
    # A target older than 30 days is read again on a later open.
    await app.replies.resolve(KEY, [13])
    assert app.transport.exact_reads[-1] == ("user:42", (6,))


async def test_failed_reads_retry_and_absent_targets_stay_missing(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app, 10, reply_to=3, reply_read=True), target=False)
    app.sync.ingest(message(app, 11, reply_to=7, reply_read=True), target=False)
    calls = []

    async def failing(peer, identities):
        calls.append(tuple(identities))
        raise ConnectionError("Authored offline failure")

    app.transport.exact_many = failing
    result = await app.replies.resolve(KEY, [10])
    assert result["pending"] == [3] and result["targets"] == []
    await app.replies.resolve(KEY, [10])
    assert calls == [(3,)]
    now[0] += 61

    async def answering(peer, identities):
        calls.append(tuple(identities))
        return [message(app, 3, text="Read after the failure")]

    app.transport.exact_many = answering
    result = await app.replies.resolve(KEY, [10, 11])
    assert calls[-1] == (3, 7)
    assert [t["message_id"] for t in result["targets"]] == [3]
    assert result["missing"] == [7] and result["pending"] == []
    await app.replies.resolve(KEY, [11])
    assert len(calls) == 2


async def test_concurrent_requests_share_one_read(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 10, reply_to=3, reply_read=True), target=False)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def exact(peer, identities):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return [message(app, 3, text="Shared target")]

    app.transport.exact_many = exact
    first = asyncio.create_task(app.replies.resolve(KEY, [10]))
    await entered.wait()
    second = asyncio.create_task(app.replies.resolve(KEY, [10]))
    release.set()
    values = await asyncio.gather(first, second)
    assert calls == 1
    assert all(v["targets"][0]["message_id"] == 3 for v in values)


async def test_requests_are_bounded(tmp_path):
    app = fixture(tmp_path)
    for identities in ([], [0], list(range(1, 102))):
        with pytest.raises(Refused):
            await app.replies.resolve(KEY, identities)


@pytest.fixture
def rig(tmp_path):
    value = authored(AccountManager(Settings(data=tmp_path)))
    yield value
    for runtime in value.manager.runtimes.values():
        runtime.assistant.store.close()


async def test_group_resolution_never_reads_telegram(rig):
    source(rig, 1, text="Cached group target")
    source(rig, 2, reply_to=1, reply_read=True)
    source(rig, 3, reply_to=40, reply_read=True)
    # A group reply captured before ADR 41 keeps its earlier reading.
    source(rig, 4, reply_to=41)
    result = await rig.app.replies.resolve(GROUP, [2, 3, 4])
    assert [t["message_id"] for t in result["targets"]] == [1]
    assert "group_filter" in result["targets"][0]
    assert result["missing"] == [40, 41] and result["pending"] == []
    assert not rig.app.transport.exact_reads


async def test_reply_elsewhere_no_longer_refuses_its_block(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, text="Earlier context"))
    app.sync.ingest(
        message(
            app,
            2,
            text="Answer to a message elsewhere",
            reply_to=5,
            reply_elsewhere=ELSEWHERE,
            reply_read=True,
        )
    )
    block = app.store.rows("SELECT * FROM blocks ORDER BY date")[-1]
    packet = await incoming(app, block)
    assert packet["direct_reply"] is None
    assert projection(app.store.message(KEY, 2))["reply_to"] is None
    assert not app.transport.exact_reads
    # The same number without the header details still names this chat's
    # message, whose absence refuses the block as before.
    app.sync.ingest(message(app, 3, text="Legacy reply", reply_to=5))
    block = app.store.rows("SELECT * FROM blocks ORDER BY date")[-1]
    with pytest.raises(Refused, match="Direct reply"):
        await incoming(app, block)


def test_replies_api_requires_trust_and_bounds_requests(tmp_path):
    app = create_app(Settings(data=tmp_path))
    base = "/api/accounts/1/chats/user:101/originals/replies"
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.post(base, json={"identities": [1]}).status_code == 403
        trust_browser(client, tmp_path)
        for identities in ([], [0], list(range(1, 102))):
            assert client.post(base, json={"identities": identities}).status_code == 422
        assert client.post(base, json={"identities": [999999]}).json() == {
            "replies": [],
            "targets": [],
            "missing": [],
            "pending": [],
        }
        assert not app.state.accounts.get(1).transport.exact_reads
