"""P1: native outgoing read state and exact-source reactions (ADR 26).

Authored native objects only. Reaction and read updates are metadata: they
never create source edits, history versions, rewrite work or repeated media
preparation.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.application.reactions import PALETTE
from enloq.config import Settings
from enloq.domain.models import (
    Chat,
    Reaction,
    ReactionActor,
    ReactionChip,
    ReactionSnapshot,
    Refused,
)
from enloq.transport.telethon import (
    PersonalTransport,
    app_config_value,
    reaction_snapshot,
)
from fastapi.testclient import TestClient
from telethon.tl import types

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, message

NOW = 2_000_000_000.0
PEER = "user:42"
KEY = "1/user:42"


def native_reactions(results, recent=None, *, minimal=False):
    return types.MessageReactions(
        results=[
            types.ReactionCount(
                reaction=types.ReactionEmoji(emoticon=emoji),
                count=count,
                chosen_order=order,
            )
            for emoji, count, order in results
        ],
        min=minimal or None,
        recent_reactions=None
        if recent is None
        else [
            types.MessagePeerReaction(
                peer_id=types.PeerUser(user),
                date=datetime.fromtimestamp(date, UTC),
                reaction=types.ReactionEmoji(emoticon=emoji),
                my=user == 1 or None,
            )
            for user, emoji, date in recent
        ],
    )


def snapshot(*, own=(), peer=(), observed=NOW, complete=True, minimal=False):
    """An authored snapshot: own emoji, and the peer's (emoji, date) pairs."""
    counts, orders = {}, {}
    for order, emoji in enumerate(own):
        counts[emoji] = counts.get(emoji, 0) + 1
        orders[emoji] = order
    for emoji, _ in peer:
        counts[emoji] = counts.get(emoji, 0) + 1
    actors = [ReactionActor(actor="own", reaction=Reaction(emoji=e)) for e in own]
    actors += [
        ReactionActor(actor="peer", reaction=Reaction(emoji=e), date=d) for e, d in peer
    ]
    return ReactionSnapshot(
        chips=[
            ReactionChip(
                reaction=Reaction(emoji=e),
                count=count,
                mine=None if minimal else e in orders,
                order=None if minimal else orders.get(e),
            )
            for e, count in counts.items()
        ],
        actors=actors if complete else None,
        complete=complete,
        minimal=minimal,
        observed=observed,
    )


def events(app):
    return [
        (r["message_id"], r["emoji"], r["added"], r["baseline"], r["removed"])
        for r in app.store.rows(
            "SELECT * FROM reaction_events ORDER BY message_id,emoji,added"
        )
    ]


def test_adapter_attributes_own_and_correspondent_reactions_with_native_time():
    native = native_reactions(
        [("👍", 2, 1), ("❤️", 1, None)],
        [(1, "👍", NOW - 50), (42, "👍", NOW - 20), (42, "❤️", NOW - 10)],
    )
    value = reaction_snapshot(native, 1, PEER, NOW)
    assert value.complete and not value.minimal and value.observed == NOW
    assert [(c.reaction.emoji, c.count, c.mine) for c in value.chips] == [
        ("👍", 2, True),
        ("❤️", 1, False),
    ]
    assert [(a.actor, a.reaction.emoji, a.date) for a in value.actors] == [
        ("own", "👍", NOW - 50),
        ("peer", "👍", NOW - 20),
        ("peer", "❤️", NOW - 10),
    ]
    assert value.own() == [Reaction(emoji="👍")]
    assert not value.peer_lacks(Reaction(emoji="👍"))
    assert value.peer_lacks(Reaction(emoji="👎"))


def test_adapter_keeps_partial_and_minimal_evidence_explicit():
    # Telegram's min snapshot omits the owner's choices: never guessed.
    minimal = reaction_snapshot(
        native_reactions([("👍", 1, None)], minimal=True), 1, PEER, NOW
    )
    assert minimal.minimal and minimal.chips[0].mine is None
    assert minimal.own() is None and not minimal.complete
    assert not minimal.peer_lacks(Reaction(emoji="👍"))
    # Attribution covering only one of two counted reactions is partial.
    partial = reaction_snapshot(
        native_reactions([("👍", 2, None)], [(42, "👍", NOW)]), 1, PEER, NOW
    )
    assert not partial.complete and not partial.peer_lacks(Reaction(emoji="👍"))
    # An unknown third party never becomes the owner or the correspondent.
    other = reaction_snapshot(
        native_reactions([("👍", 1, None)], [(77, "👍", NOW)]), 1, PEER, NOW
    )
    assert other.actors == [] and not other.complete
    # A full message without reactions has none.
    empty = reaction_snapshot(None, 1, PEER, NOW)
    assert empty.complete and not empty.chips and empty.peer_lacks(Reaction(emoji="👍"))
    # Two participants: a single counted reaction chosen by the owner is not
    # also the correspondent's, even without attribution.
    counted = reaction_snapshot(native_reactions([("👍", 1, 3)]), 1, PEER, NOW)
    assert counted.actors is None and counted.peer_lacks(Reaction(emoji="👍"))


def test_adapter_keeps_custom_and_paid_reactions_for_display():
    native = types.MessageReactions(
        results=[
            types.ReactionCount(
                reaction=types.ReactionCustomEmoji(document_id=5_000_000_001), count=1
            ),
            types.ReactionCount(reaction=types.ReactionPaid(), count=3),
            types.ReactionCount(reaction=types.ReactionEmpty(), count=1),
        ]
    )
    value = reaction_snapshot(native, 1, PEER, NOW)
    assert [c.reaction.model_dump(exclude_defaults=True) for c in value.chips] == [
        {"custom": "5000000001"},
        {"paid": True},
    ]


async def test_normalized_pages_carry_reactions_but_service_messages_do_not():
    transport = object.__new__(PersonalTransport)
    transport.account_id = 1
    date = datetime.fromtimestamp(NOW, UTC)
    values = [
        types.Message(
            id=5,
            peer_id=types.PeerUser(42),
            date=date,
            message="Authored",
            out=True,
            reactions=native_reactions([("👍", 1, None)], [(42, "👍", NOW)]),
        ),
        types.MessageService(
            id=6,
            peer_id=types.PeerUser(42),
            date=date,
            action=types.MessageActionHistoryClear(),
        ),
    ]
    first, service = await transport.normalize_page(values, observed=NOW - 5)
    assert first.reactions.observed == NOW - 5
    assert first.reactions.actors[0].actor == "peer"
    assert service.reactions is None
    # Reaction metadata never enters stored content, tokens or digests.
    assert "reactions" not in first.model_dump()
    assert "reactions" not in first.model_dump_json()


def test_reaction_metadata_never_edits_sources_or_repeats_preparation(tmp_path):
    app = fixture(tmp_path, clock=lambda: NOW)
    app.sync.ingest(message(app, 1, date=NOW - 60))
    stored = app.store.message(KEY, 1)
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES (?,?,?)",
            (stored.key, stored.revision, json.dumps({"transcript": {"text": "x"}})),
        )
        db.execute(
            "UPDATE blocks SET state='ready',rendered='Done',rendered_revision=revision"
        )
        db.execute("UPDATE jobs SET state='done'")
    before = (
        app.store.rows("SELECT * FROM blocks"),
        app.store.rows("SELECT * FROM jobs"),
        app.store.rows("SELECT * FROM derivatives"),
    )
    for observed in (NOW - 30, NOW - 20, NOW - 10):
        app.sync.ingest(
            stored.model_copy(
                update={
                    "reactions": snapshot(peer=[("👍", NOW - 40)], observed=observed)
                }
            )
        )
    assert app.store.message(KEY, 1) == stored
    assert (
        app.store.rows("SELECT * FROM blocks"),
        app.store.rows("SELECT * FROM jobs"),
        app.store.rows("SELECT * FROM derivatives"),
    ) == before
    assert not app.store.rows("SELECT * FROM message_versions")
    assert app.store.reactions.view(stored.key)["chips"] == [
        {
            "reaction": {"emoji": "👍", "custom": None, "paid": False},
            "count": 1,
            "mine": False,
            "peer": True,
        }
    ]
    # Repeated identical observations change nothing a browser would refresh for.
    _, sequence = app.store.changes()
    app.sync.reactions(PEER, 1, snapshot(peer=[("👍", NOW - 40)], observed=NOW))
    assert app.store.changes(sequence)[0] == []
    assert len(events(app)) == 1


def test_ledger_baseline_triggers_and_actors(tmp_path):
    now = [NOW]
    app = fixture(tmp_path, clock=lambda: now[0])
    for identity in (1, 2, 3, 4):
        app.sync.ingest(message(app, identity, date=NOW - 100, outgoing=identity > 2))
    introduced = float(
        app.store.rows("SELECT value FROM metadata WHERE key='reactions_introduced'")[
            0
        ]["value"]
    )
    assert introduced == NOW
    now[0] += 60
    # Existing at introduction: display-only baseline.
    app.sync.reactions(PEER, 1, snapshot(peer=[("👍", NOW - 30)], observed=now[0]))
    # A later correspondent trigger on the owner's outgoing message.
    app.sync.reactions(PEER, 3, snapshot(peer=[("🤝", NOW + 30)], observed=now[0]))
    # The owner's own trigger and a correspondent non-trigger stay display-only.
    app.sync.reactions(PEER, 4, snapshot(own=["👍"], observed=now[0]))
    app.sync.reactions(PEER, 2, snapshot(peer=[("❤️", NOW + 40)], observed=now[0]))
    assert events(app) == [
        (1, "👍", NOW - 30, 1, None),
        (3, "🤝", NOW + 30, 0, None),
    ]
    # Unknown native addition time cannot establish an event.
    app.sync.reactions(
        PEER,
        2,
        ReactionSnapshot(
            chips=[ReactionChip(reaction=Reaction(emoji="👎"), count=1, mine=False)],
            observed=now[0] + 1,
        ),
    )
    assert len(events(app)) == 2


def test_replacement_removal_and_partial_snapshots(tmp_path):
    now = [NOW]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app, 1, date=NOW - 100, outgoing=True))
    app.sync.reactions(PEER, 1, snapshot(peer=[("👍", NOW + 10)], observed=NOW + 10))
    # Partial and minimal snapshots never prove the correspondent removed it.
    app.sync.reactions(
        PEER,
        1,
        snapshot(
            own=["👍"],
            peer=[("👍", NOW + 10)],
            complete=False,
            observed=NOW + 20,
            minimal=True,
        ),
    )
    app.sync.reactions(
        PEER,
        1,
        ReactionSnapshot(
            chips=[ReactionChip(reaction=Reaction(emoji="👍"), count=1)],
            minimal=True,
            observed=NOW + 21,
        ),
    )
    assert [e[4] for e in events(app)] == [None]
    # A replacement removes the earlier reaction and adds the new one.
    now[0] = NOW + 30
    app.sync.reactions(PEER, 1, snapshot(peer=[("👎", NOW + 25)], observed=NOW + 30))
    assert events(app) == [
        (1, "👍", NOW + 10, 0, NOW + 30),
        (1, "👎", NOW + 25, 0, None),
    ]
    # A complete snapshot without it proves the removal.
    now[0] = NOW + 40
    app.sync.reactions(PEER, 1, snapshot(observed=NOW + 40))
    assert events(app)[-1] == (1, "👎", NOW + 25, 0, NOW + 40)
    assert app.store.reactions.view(f"{KEY}/1") is None


def test_older_observations_never_replace_newer_ones(tmp_path):
    app = fixture(tmp_path, clock=lambda: NOW)
    app.sync.ingest(message(app, 1, date=NOW - 100))
    assert app.sync.reactions(
        PEER, 1, snapshot(peer=[("👍", NOW + 1)], observed=NOW + 5)
    )
    # A history page started before that update arrived is older evidence.
    assert not app.sync.reactions(PEER, 1, snapshot(observed=NOW + 2))
    assert app.store.reactions.view(f"{KEY}/1")["chips"][0]["count"] == 1
    assert [e[4] for e in events(app)] == [None]
    # Uncached or deleted targets are never admitted.
    assert not app.sync.reactions(PEER, 9, snapshot(peer=[("👍", NOW)]))
    app.sync.deleted(PEER, [1])
    assert not app.sync.reactions(
        PEER, 1, snapshot(peer=[("👎", NOW)], observed=NOW + 9)
    )


def test_outgoing_read_cursor_only_moves_forward(tmp_path):
    app = fixture(tmp_path, clock=lambda: NOW)
    assert app.store.chat(KEY).read_outbox == 0
    assert app.sync.read_outbox(PEER, 7)
    assert not app.sync.read_outbox(PEER, 5)
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"read_outbox": 3}))
    assert app.store.chat(KEY).read_outbox == 7
    assert not app.sync.read_outbox("user:99", 9)  # Not an existing chat.
    assert app.conversation(KEY)["chat"]["read_outbox"] == 7


def live_transport(tmp_path):
    from enloq.storage.store import Store

    store = Store(tmp_path / "db.sqlite3", account_id=1, clock=lambda: NOW)
    store.put_chat(Chat(account_id=1, peer=PEER, title="Authored", kind="private"))
    transport = object.__new__(PersonalTransport)
    transport.store, transport.account_id = store, 1
    transport.verified = True
    received = []
    transport.sync = SimpleNamespace(
        reactions=lambda peer, identity, value: received.append(
            ("reactions", peer, identity, value)
        ),
        read_outbox=lambda peer, through: received.append(("read", peer, through)),
        ingest=lambda message, **options: received.append(("ingest", message)),
        deleted=lambda peer, ids: received.append(("deleted", tuple(ids))),
    )
    return transport, received


async def test_live_and_replayed_metadata_updates(tmp_path):
    transport, received = live_transport(tmp_path)
    reaction = types.UpdateMessageReactions(
        peer=types.PeerUser(42),
        msg_id=5,
        reactions=native_reactions([("👌", 1, None)], [(42, "👌", NOW)]),
    )
    read = types.UpdateReadHistoryOutbox(
        peer=types.PeerUser(42), max_id=5, pts=1, pts_count=1
    )
    await transport._reactions(reaction)
    await transport._read_outbox(read)
    assert [r[0] for r in received] == ["reactions", "read"]
    assert received[0][3].actors[0].date == NOW and received[1] == ("read", PEER, 5)
    # Group and channel peers are outside the private-DM scope.
    await transport._read_outbox(
        types.UpdateReadHistoryOutbox(
            peer=types.PeerChannel(9), max_id=5, pts=1, pts_count=1
        )
    )
    transport.verified = False
    await transport._reactions(reaction)
    assert len(received) == 2


async def test_discovery_records_outgoing_cursor_and_unread_reaction_count(tmp_path):
    transport, _ = live_transport(tmp_path)
    user = types.User(id=42, first_name="Authored", access_hash=1)
    dialog = SimpleNamespace(
        entity=user,
        name="Authored",
        archived=False,
        date=datetime.fromtimestamp(NOW, UTC),
        dialog=types.Dialog(
            peer=types.PeerUser(42),
            top_message=9,
            read_inbox_max_id=8,
            read_outbox_max_id=6,
            unread_count=0,
            unread_mentions_count=0,
            unread_reactions_count=2,
            unread_poll_votes_count=0,
            notify_settings=types.PeerNotifySettings(),
        ),
    )
    transport._discovered(dialog)
    assert transport.store.chat(KEY).read_outbox == 6
    rows = transport.store.rows(
        "SELECT value FROM cursors WHERE key='unread_reactions:1/user:42'"
    )
    assert json.loads(rows[0]["value"]) == {"count": 2, "read": 0}


async def test_unread_reaction_catch_up_reads_once_per_changed_count(tmp_path):
    app = fixture(tmp_path, clock=lambda: NOW)
    app.sync.ingest(message(app, 1, date=NOW - 100, outgoing=True))
    app.transport.reaction_state[PEER, 1] = {"own": [], "peer": {"👍": NOW + 5}}
    app.transport.reaction_state[PEER, 2] = {"own": [], "peer": {"👍": NOW + 5}}
    app.transport.unread[PEER] = [1, 2]
    await app.sync.reaction_catch_up(KEY)
    assert app.transport.unread_reads == []  # Telegram reported no count yet.
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES (?,?)",
            ("unread_reactions:" + KEY, json.dumps({"count": 1, "read": 0})),
        )
    await app.sync.reaction_catch_up(KEY)
    await app.sync.reaction_catch_up(KEY)
    assert app.transport.unread_reads == [PEER]
    # Only the cached message gains evidence; nothing new is cached.
    assert [e[0] for e in events(app)] == [1]
    assert app.store.message(KEY, 2) is None


@pytest.fixture
def served(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        conversation = app.state.accounts.get(1)
        conversation.store.put_chat(
            Chat(account_id=1, peer=PEER, title="Authored", kind="private")
        )
        conversation.sync.ingest(message(conversation, 1, outgoing=False), target=False)
        yield client, conversation


def test_reaction_choices_follow_the_native_limit(served):
    client, conversation = served
    url = "/api/accounts/1/chats/user:42/originals/1/reactions"
    options = client.get("/api/accounts/1/reactions/options").json()
    assert options["limit"] == 1 and "👍" in options["emoji"]
    first = client.post(url, json={"emoji": "👍", "selected": True}).json()
    assert first["reactions"]["chips"][0]["mine"] is True
    # One reaction for this account: a new choice replaces the earlier one.
    second = client.post(url, json={"emoji": "🔥", "selected": True}).json()
    assert [c["reaction"]["emoji"] for c in second["reactions"]["chips"]] == ["🔥"]
    assert conversation.transport.reaction_calls[-1][2] == (Reaction(emoji="🔥"),)
    removed = client.post(url, json={"emoji": "🔥", "selected": False}).json()
    assert removed["reactions"] is None
    # Choosing what is already chosen, or removing what is absent, sends nothing.
    calls = len(conversation.transport.reaction_calls)
    client.post(url, json={"emoji": "🔥", "selected": False})
    assert len(conversation.transport.reaction_calls) == calls
    # A reaction outside the account's palette is refused before Telegram.
    refused = client.post(url, json={"emoji": "🦄", "selected": True})
    assert refused.status_code == 400
    assert len(conversation.transport.reaction_calls) == calls
    # The palette is cached for a day, never guessed when unavailable.
    assert (
        json.loads(
            conversation.store.rows(
                "SELECT value FROM cursors WHERE key=?", (PALETTE,)
            )[0]["value"]
        )["limit"]
        == 1
    )


def test_reaction_choices_refuse_unsafe_targets(served):
    client, conversation = served
    base = "/api/accounts/1/chats/user:42/originals"
    assert (
        client.post(
            f"{base}/9/reactions", json={"emoji": "👍", "selected": True}
        ).status_code
        == 400
    )
    conversation.sync.deleted(PEER, [1])
    assert (
        client.post(
            f"{base}/1/reactions", json={"emoji": "👍", "selected": True}
        ).status_code
        == 400
    )
    conversation.sync.ingest(message(conversation, 2), target=False)
    conversation.transport.connected = False
    assert (
        client.post(
            f"{base}/2/reactions", json={"emoji": "👍", "selected": True}
        ).status_code
        == 400
    )
    conversation.transport.connected = True
    conversation.transport.reaction_failure = TimeoutError("authored")
    response = client.post(
        f"{base}/2/reactions", json={"emoji": "👍", "selected": True}
    )
    assert (
        response.status_code == 400 and "did not confirm" in response.json()["detail"]
    )
    assert conversation.store.reactions.view(f"{KEY}/2") is None


async def test_unknown_own_reactions_are_read_before_removal(tmp_path):
    app = fixture(tmp_path, clock=lambda: NOW)
    app.sync.ingest(message(app, 1, date=NOW - 100))
    # Telegram's min snapshot does not say whose reaction this is.
    app.sync.reactions(
        PEER,
        1,
        ReactionSnapshot(
            chips=[ReactionChip(reaction=Reaction(emoji="👍"), count=1)],
            minimal=True,
            observed=NOW,
        ),
    )
    app.transport.messages.append(message(app, 1, date=NOW - 100))
    with pytest.raises(Refused):
        # The authored exact read carries no reactions: still unknown.
        await app.reactions.choose(KEY, 1, "👍", False)
    assert app.transport.reaction_calls == []


def test_app_config_limit_parsing():
    config = types.JsonObject(
        value=[
            types.JsonObjectValue(
                key="reactions_user_max_default", value=types.JsonNumber(value=1.0)
            ),
            types.JsonObjectValue(
                key="reactions_user_max_premium", value=types.JsonNumber(value=3.0)
            ),
        ]
    )
    assert app_config_value(config, "reactions_user_max_premium") == 3.0
    assert app_config_value(config, "missing") is None


async def test_gap_catch_up_replays_read_cursor_and_carried_reactions(tmp_path):
    transport, received = live_transport(tmp_path)
    with transport.store.transaction() as db:
        db.execute(
            "INSERT INTO cursors VALUES ('global_difference',?)",
            (json.dumps({"pts": 4, "qts": 0, "date": 1000}),),
        )

    class Client:
        async def __call__(self, request):
            return types.updates.Difference(
                new_messages=[],
                new_encrypted_messages=[],
                other_updates=[
                    types.UpdateMessageReactions(
                        peer=types.PeerUser(42),
                        msg_id=3,
                        reactions=native_reactions(
                            [("👎", 1, None)], [(42, "👎", NOW)]
                        ),
                    ),
                    types.UpdateReadHistoryOutbox(
                        peer=types.PeerUser(42), max_id=3, pts=5, pts_count=1
                    ),
                ],
                chats=[],
                users=[],
                state=types.updates.State(
                    pts=6,
                    qts=0,
                    date=datetime.fromtimestamp(NOW, UTC),
                    seq=0,
                    unread_count=0,
                ),
            )

    transport.client = Client()
    await transport.catch_up()
    assert [r[0] for r in received] == ["reactions", "read"]
    assert received[0][3].observed == NOW and received[0][3].actors[0].date == NOW
    assert received[1] == ("read", PEER, 3)


async def test_send_reaction_returns_telegrams_resulting_snapshot(tmp_path):
    from enloq.domain.models import Unavailable

    transport, _ = live_transport(tmp_path)
    requests = []

    class Client:
        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            return types.InputPeerUser(peer.user_id, 0)

        async def __call__(self, request):
            requests.append(request)
            return types.Updates(
                updates=[
                    types.UpdateMessageReactions(
                        peer=types.PeerUser(42),
                        msg_id=5,
                        reactions=native_reactions([("👍", 1, 1)], [(1, "👍", NOW)]),
                    )
                ],
                users=[],
                chats=[],
                date=datetime.fromtimestamp(NOW, UTC),
                seq=0,
            )

    transport.client = Client()
    result = await transport.send_reaction(
        PEER, 5, [Reaction(emoji="👍"), Reaction(custom="77")]
    )
    sent = requests[0]
    assert [type(r).__name__ for r in sent.reaction] == [
        "ReactionEmoji",
        "ReactionCustomEmoji",
    ]
    assert sent.msg_id == 5 and not sent.big
    assert result.own() == [Reaction(emoji="👍")]
    # Removing every reaction sends an empty list without adding to recents.
    await transport.send_reaction(PEER, 5, [])
    assert requests[-1].reaction == [] and not requests[-1].add_to_recent
    transport.verified = False
    with pytest.raises(Unavailable):
        await transport.send_reaction(PEER, 5, [])


def test_reaction_evidence_expires_with_its_cached_message(tmp_path):
    from enloq.storage.store import RETENTION

    now = [NOW]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app, 1, date=NOW - 100, outgoing=True))
    app.sync.reactions(PEER, 1, snapshot(peer=[("👍", NOW + 1)], observed=NOW + 1))
    assert app.store.rows("SELECT * FROM message_reactions") and events(app)
    now[0] = NOW + RETENTION
    app.store.expire()
    assert not app.store.rows("SELECT * FROM message_reactions")
    assert not events(app)


def test_other_client_outgoing_messages_read_through_the_cursor(tmp_path):
    """Status belongs to every observed outgoing message, wherever it was sent:
    the payload carries the native cursor beside each message's direction."""
    app = fixture(tmp_path, clock=lambda: NOW)
    for identity in (5, 6):
        app.sync.ingest(message(app, identity, outgoing=True), live=True)
    app.sync.read_outbox(PEER, 5)
    value = app.conversation(KEY)
    statuses = {
        m["message_id"]: "read"
        if m["message_id"] <= value["chat"]["read_outbox"]
        else "sent"
        for m in value["messages"]
        if m["outgoing"]
    }
    assert statuses == {5: "read", 6: "sent"}
    # No pending submission stands in for a native message.
    assert value["operations"] == []


def test_a_live_removal_is_ordered_before_a_stale_read_without_earlier_evidence(
    tmp_path,
):
    """Review finding: a reaction missed while offline, removed live while a
    catch-up read was in flight, must not be restored by that older read."""
    app = fixture(tmp_path, clock=lambda: NOW)
    app.sync.ingest(message(app, 1, date=NOW - 100, outgoing=True))
    # The live update: no reactions remain (observed at NOW + 10).
    assert app.sync.reactions(PEER, 1, snapshot(observed=NOW + 10))
    # The catch-up read started earlier and still shows the removed reaction.
    assert not app.sync.reactions(
        PEER, 1, snapshot(peer=[("👍", NOW + 1)], observed=NOW + 5)
    )
    assert not events(app) and app.store.reactions.view(f"{KEY}/1") is None
    # Ordinary reads of messages without reactions still store no row.
    app.sync.ingest(
        message(app, 2, date=NOW - 50).model_copy(
            update={"reactions": snapshot(observed=NOW + 11)}
        )
    )
    assert not app.store.rows("SELECT 1 FROM message_reactions WHERE message_id=2")
