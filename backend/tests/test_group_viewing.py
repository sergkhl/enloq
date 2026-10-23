"""Native group unseen/viewing over authored caches, without native receipts."""

import pytest
from enloq.domain.models import (
    Chat,
    Conflict,
    Message,
    Reaction,
    ReactionActor,
    ReactionChip,
    ReactionSnapshot,
    Refused,
)
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import RETENTION, WINDOW
from enloq.storage.viewing import ViewedContent, ViewedSources

from backend.tests.test_rebuild import fixture

KEY = "1/chat:42"


def populate(app, peer="chat:42"):
    store = app.store
    key = f"{store.account_id}/{peer}"
    store.put_chat(
        Chat(
            account_id=store.account_id,
            peer=peer,
            title="Authored group",
            kind="group",
        )
    )
    store.groups.observe(key, access="available", reason="authored_native_evidence")
    return key


def message(app, identity=1, *, peer="chat:42", **kwargs):
    return Message(
        account_id=app.store.account_id,
        peer=peer,
        message_id=identity,
        revision=kwargs.pop("revision", 1),
        date=kwargs.pop("date", app.store.clock()),
        text=kwargs.pop("text", "Authored native original"),
        author_peer=kwargs.pop("author_peer", "user:7"),
        **kwargs,
    )


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    populate(app)
    return app, now


def rows(app, key=KEY):
    return {
        row["message_id"]: row
        for row in app.store.rows("SELECT * FROM source_views WHERE chat_key=?", (key,))
    }


def captured(app, key=KEY, **kwargs):
    return app.store.viewing.ready_sources(key, **kwargs)


def identities(page):
    return [int(content["identity"]) for content in page["content"]]


def acknowledge(app, page, key=KEY):
    return app.store.viewing.acknowledge(
        key, ViewedSources.model_validate({"content": page["content"]})
    )


def ready(app, now, count=1, *, key=KEY):
    peer = key.split("/", 1)[1]
    for identity in range(1, count + 1):
        app.sync.ingest(message(app, identity, peer=peer))
    now[0] += 30
    app.unseen.advance()


def assert_local_only(app):
    assert not app.engine.calls
    assert not app.transport.reads and not app.transport.exact_reads
    assert not app.transport.deliveries
    assert not app.notifications.rows()


@pytest.mark.parametrize("group_ai", [False, True])
def test_quiet_native_readiness_ignores_group_ai_private_admission_and_alerts(
    rig, tmp_path, group_ai
):
    app, now = rig
    app.store.groups.set_processing_enabled(KEY, group_ai)
    registry = AccountRegistry(tmp_path)
    registry.set_processing_threshold(10_000)
    registry.set_budget(0, app.store.clock)
    registry.set_budget(0, app.store.clock, family="group_briefing")
    app.account_processing_enabled = lambda: False

    def unexpected(*args, **kwargs):
        raise AssertionError("Group readiness must not inspect AI or private state")

    app.active = app.stages = app.notifications.unavailable = unexpected
    app.admit_ai = app.admit_group_runtime = unexpected
    app.sync.ingest(message(app, 1, text="Short"), live=True)
    now[0] += 20
    app.sync.ingest(message(app, 2, media="image", attachment_id="authored-image"))
    now[0] += 29
    app.unseen.advance()
    assert captured(app)["content"] == []
    assert app.previews.opening(KEY) is None
    now[0] += 1
    app.unseen.advance()
    assert identities(captured(app)) == [1, 2]
    assert app.previews.opening_view(KEY) == (1, [1, 2])
    preview = app.previews.recent("group")[0]
    assert preview.unseen and preview.opening_source == 1
    app.notifications.advance()
    assert not app.group_processing(KEY).effective
    assert_local_only(app)
    assert app.store.chat(KEY).read_through == 0


def test_verified_native_author_overrides_outgoing_and_rejects_unknown_authorship(rig):
    app, now = rig
    sources = (
        (1, {"author_peer": "user:1", "outgoing": False}),
        (2, {"author_peer": "user:7", "outgoing": True}),
        (3, {"author_peer": "channel:7", "outgoing": True}),
        (4, {"author_peer": "channel:1", "outgoing": False}),
        (5, {"author_peer": None}),
        (6, {"author_peer": "user:0"}),
        (7, {"author_peer": "user:007"}),
        (8, {"service": True}),
        (9, {"topic_id": 3}),
    )
    for identity, fields in sources:
        app.sync.ingest(message(app, identity, **fields))
    assert set(rows(app)) == {2, 3, 4}
    assert app.store.viewing.original(app.store.message(KEY, 1)) is None
    now[0] += 30
    app.unseen.advance()
    assert identities(captured(app)) == [2, 3, 4]
    assert app.previews.opening_view(KEY) == (2, [2, 3, 4])
    assert acknowledge(app, captured(app)) == {"viewed": 3}
    assert_local_only(app)


def test_initial_native_read_baseline_stays_frozen_across_restart_and_paging(
    rig, tmp_path
):
    app, now = rig
    for identity in (1, 3):
        app.sync.ingest(message(app, identity), arrival=False, intake="initial")
    assert all(row["candidate"] is None for row in rows(app).values())
    now[0] += 31
    app.unseen.advance()
    assert captured(app)["content"] == []
    app.store.viewing.baseline(KEY, 2, 5)
    assert rows(app)[1]["viewed"] and rows(app)[3]["candidate"] == 1
    app.unseen.advance()
    assert identities(captured(app)) == [3]
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"read_through": 9}))
    app.store.viewing.baseline(KEY, 9, 9)
    app.store.close()
    app = fixture(tmp_path, clock=lambda: now[0])
    for identity in (2, 4, 6):
        app.sync.ingest(message(app, identity), arrival=False, intake="initial")
    assert rows(app)[2]["viewed"]
    assert all(rows(app)[identity]["candidate"] == 1 for identity in (3, 4, 6))
    now[0] += 30
    app.unseen.advance()
    assert identities(captured(app)) == [3, 4, 6]
    assert app.store.chat(KEY).read_through == 9
    assert_local_only(app)


def test_initial_window_manual_history_and_late_native_reads_do_not_create_dots(rig):
    app, now = rig
    app.store.viewing.baseline(KEY, 1, 3)
    app.sync.ingest(message(app, 1), arrival=False, intake="initial")
    app.sync.ingest(message(app, 2), arrival=False, intake="history")
    app.sync.ingest(
        message(app, 3, date=now[0] - WINDOW - 1), arrival=False, intake="initial"
    )
    assert set(rows(app)) == {1, 2}
    now[0] += 31
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"read_through": 99}))
    app.store.viewing.baseline(KEY, 99, 99)
    app.unseen.advance()
    assert captured(app)["content"] == []
    assert app.previews.opening_view(KEY) == (None, [])
    assert_local_only(app)


def test_focus_pages_include_offscreen_posts_and_share_durable_viewing(rig, tmp_path):
    app, now = rig
    ready(app, now, 205)
    assert app.conversation(KEY)["messages"][0]["message_id"] == 106
    pages = []
    after = 0
    while after is not None:
        page = captured(app, after=after)
        assert len(page["content"]) <= 100
        assert all(content["kind"] == "original" for content in page["content"])
        pages.append(page)
        after = page["next_cursor"]
    assert [page["next_cursor"] for page in pages] == [100, 200, None]
    assert [identity for page in pages for identity in identities(page)] == list(
        range(1, 206)
    )
    assert app.previews.opening(KEY) == 1
    assert all(row["viewed"] is None for row in rows(app).values())
    for page in pages:
        assert acknowledge(app, page) == {"viewed": len(page["content"])}
        assert acknowledge(app, page) == {"viewed": 0}
    app.store.close()
    app = fixture(tmp_path, clock=lambda: now[0])
    assert captured(app) == {"content": [], "next_cursor": None}
    assert app.previews.opening_view(KEY) == (None, [])
    assert app.store.chat(KEY).read_through == 0
    assert_local_only(app)


def test_stale_tokens_are_atomic_and_cannot_swallow_later_sources_or_edits(rig):
    app, now = rig
    ready(app, now, 2)
    previous = captured(app)
    app.sync.ingest(message(app, 2, text="Authored changed content"))
    app.sync.ingest(message(app, 3))
    with pytest.raises(Conflict):
        acknowledge(app, previous)
    assert all(row["viewed"] is None for row in rows(app).values())
    current = captured(app)
    assert identities(current) == [1, 2]
    assert current["content"][0] == previous["content"][0]
    assert current["content"][1] != previous["content"][1]
    assert acknowledge(app, current) == {"viewed": 2}
    assert rows(app)[3]["viewed"] is None
    now[0] += 30
    app.unseen.advance()
    assert identities(captured(app)) == [3]
    assert app.previews.opening(KEY) == 3
    assert acknowledge(app, captured(app)) == {"viewed": 1}
    app.sync.ingest(message(app, 2, text="Authored edit after viewing"))
    now[0] += 30
    app.unseen.advance()
    assert captured(app)["content"] == []
    assert_local_only(app)


def test_group_original_ack_waits_for_quiet_native_readiness(rig):
    app, now = rig
    app.sync.ingest(message(app))
    token = app.store.viewing.original(app.store.message(KEY, 1))
    body = ViewedSources(content=[token])
    with pytest.raises(Conflict):
        app.store.viewing.acknowledge(KEY, body)
    assert rows(app)[1]["viewed"] is None
    now[0] += 30
    app.unseen.advance()
    assert app.store.viewing.acknowledge(KEY, body) == {"viewed": 1}
    assert app.store.viewing.acknowledge(KEY, body) == {"viewed": 0}


@pytest.mark.parametrize("loss", ["lost", "migrated"])
def test_cached_lost_or_migrated_peer_can_settle_and_view_without_native_access(
    rig, loss
):
    app, now = rig
    app.sync.ingest(message(app))
    app.store.groups.observe(
        KEY,
        access="lost",
        reason="authored_loss",
        migrated_to="channel:42" if loss == "migrated" else None,
    )
    populate(app, "channel:42")
    now[0] += 30
    app.unseen.advance()
    assert identities(captured(app)) == [1]
    assert app.previews.opening_view(KEY) == (1, [1])
    assert captured(app, "1/channel:42")["content"] == []
    assert not app.store.groups.get("1/channel:42").processing_enabled
    page = captured(app)
    assert acknowledge(app, page) == {"viewed": 1}
    now[0] += RETENTION + 1
    assert captured(app)["content"] == []
    assert app.previews.opening(KEY) is None
    with pytest.raises(Conflict):
        acknowledge(app, page)
    assert_local_only(app)


def test_exact_cached_source_proof_is_required_for_readiness_and_acknowledgement(rig):
    app, now = rig
    app.sync.ingest(message(app))
    original = app.store.message(KEY, 1)
    with app.store.transaction() as db:
        db.execute(
            "UPDATE group_sources SET revision=revision+1 WHERE source=?",
            (original.key,),
        )
    now[0] += 30
    app.unseen.advance()
    assert rows(app)[1]["ready"] is None
    with app.store.transaction() as db:
        db.execute(
            "UPDATE group_sources SET revision=? WHERE source=?",
            (original.revision, original.key),
        )
    app.unseen.advance()
    page = captured(app)
    assert identities(page) == [1]
    app.store.groups.withdraw_source(original.key)
    assert captured(app)["content"] == []
    assert app.previews.opening_view(KEY) == (None, [])
    with pytest.raises(Conflict):
        acknowledge(app, page)
    assert rows(app)[1]["viewed"] is None
    assert_local_only(app)


@pytest.mark.parametrize(
    "change,error",
    [
        ({"peer": "channel:42"}, Refused),
        ({"revision": 2}, Conflict),
        ({"date": 1.0}, Conflict),
    ],
)
def test_body_identity_drift_hides_dot_and_cannot_acknowledge_another_source(
    rig, change, error
):
    app, now = rig
    ready(app, now, 2)
    previous = captured(app)
    source = app.store.message(KEY, 2)
    with app.store.transaction() as db:
        db.execute(
            "UPDATE messages SET body=? WHERE key=?",
            (
                source.model_copy(update=change).model_dump_json(),
                source.key,
            ),
        )
    assert identities(captured(app)) == [1]
    assert app.previews.opening_view(KEY) == (1, [1])
    with pytest.raises(error):
        acknowledge(app, previous)
    assert all(row["viewed"] is None for row in rows(app).values())
    assert_local_only(app)


def test_equal_native_ids_do_not_cross_accounts_or_typed_group_peers(tmp_path):
    now = [2_000_000_000.0]
    first = fixture(tmp_path, clock=lambda: now[0])
    second = fixture(tmp_path, account=2, clock=lambda: now[0])
    populate(first)
    populate(first, "channel:42")
    populate(second)
    for app, peer in ((first, "chat:42"), (first, "channel:42"), (second, "chat:42")):
        app.sync.ingest(message(app, peer=peer))
    now[0] += 30
    first.unseen.advance()
    second.unseen.advance()
    page = captured(first)
    assert page != captured(first, "1/channel:42")
    assert page != captured(second, "2/chat:42")
    with pytest.raises(Conflict):
        acknowledge(first, page, "1/channel:42")
    with pytest.raises(Conflict):
        acknowledge(second, page, "2/chat:42")
    with pytest.raises(Refused):
        captured(second, KEY)
    assert acknowledge(first, page) == {"viewed": 1}
    assert identities(captured(first, "1/channel:42")) == [1]
    assert identities(captured(second, "2/chat:42")) == [1]
    assert_local_only(first)
    assert_local_only(second)


@pytest.mark.parametrize("kind", ["rewrite", "reaction"])
def test_groups_refuse_non_native_viewing_tokens_atomically(rig, kind):
    app, now = rig
    ready(app, now)
    content = [ViewedContent.model_validate(captured(app)["content"][0])]
    content.append(ViewedContent(kind=kind, identity="authored-id", token="a" * 64))
    with pytest.raises(Refused, match="native originals"):
        app.store.viewing.acknowledge(KEY, ViewedSources(content=content))
    assert rows(app)[1]["viewed"] is None
    assert_local_only(app)


def test_reaction_only_changes_and_briefing_projection_do_not_create_dots(rig):
    app, now = rig
    ready(app, now)
    assert acknowledge(app, captured(app)) == {"viewed": 1}
    reaction = Reaction(emoji="👍")
    observed = ReactionSnapshot(
        chips=[ReactionChip(reaction=reaction, count=1, mine=False)],
        actors=[ReactionActor(actor="peer", reaction=reaction, date=now[0])],
        complete=True,
        observed=now[0],
    )
    app.sync.ingest(
        app.store.message(KEY, 1).model_copy(update={"reactions": observed})
    )
    app.briefings.view = lambda key: [{"day": "2033-05-18", "text": "Authored final"}]
    now[0] += 30
    app.unseen.advance()
    assert not app.store.rows("SELECT * FROM reaction_events WHERE chat_key=?", (KEY,))
    assert captured(app)["content"] == []
    assert app.previews.opening_view(KEY) == (None, [])
    assert_local_only(app)


def test_pre_gb9_cached_authorship_upgrade_keeps_native_baseline_and_viewed_ids(
    rig, tmp_path
):
    app, now = rig
    app.store.viewing.baseline(KEY, 0, 3)
    app.sync.ingest(message(app, 1, author_peer="user:1", outgoing=False))
    app.sync.ingest(message(app, 2, author_peer="user:7", outgoing=True))
    app.sync.ingest(message(app, 3, author_peer="channel:7", outgoing=True))
    now[0] += 30
    app.unseen.advance()
    page = captured(app)
    assert acknowledge(app, {"content": [page["content"][1]]}) == {"viewed": 1}
    # GB1-GB8 used out=False for candidates and omitted another participant's
    # out=True originals. Preserve the native baseline when adopting authors.
    with app.store.transaction() as db:
        db.execute("DELETE FROM source_views WHERE source=?", (f"{KEY}/2",))
        db.execute(
            "INSERT INTO source_views VALUES (?,?,?,'arrival',1,?,NULL,?)",
            (f"{KEY}/1", KEY, 1, now[0], now[0]),
        )
    app.store.close()
    app = fixture(tmp_path, clock=lambda: now[0])
    assert set(rows(app)) == {2, 3}
    assert rows(app)[2]["candidate"] == 1 and rows(app)[3]["viewed"]
    app.store.viewing.baseline(KEY, 99, 99)
    now[0] += 30
    app.unseen.advance()
    assert identities(captured(app)) == [2]
    assert_local_only(app)
