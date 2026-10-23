"""Native peerless deletion after content expiry uses retained identity only."""

import json
import uuid

import pytest
from enloq.domain.models import Chat
from enloq.storage.store import RETENTION

from backend.tests.test_deletion_consequences import KEY, PEER, pending, rig
from backend.tests.test_rebuild import message

OTHER, OTHER_PEER = "1/user:43", "user:43"


def citation(app, identity, key=KEY, sources=(1,), origin="model"):
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO memory_entries (id,chat_key,text,citations,source,outdated,created) VALUES (?,?,?,?,?,NULL,?)",
            (
                identity,
                key,
                "Authored retained note",
                json.dumps(sources),
                origin,
                app.store.clock(),
            ),
        )


def expired_source(app, now, identity=1, *, peer=PEER):
    if peer != PEER:
        app.store.put_chat(
            Chat(account_id=1, peer=peer, title="Authored other", kind="private")
        )
    app.sync.ingest(
        message(app, identity, date=now[0] - RETENTION + 5).model_copy(
            update={"peer": peer}
        ),
        target=False,
    )
    app.sync.ingest(
        message(app, 100, outgoing=True).model_copy(update={"peer": peer}),
        target=False,
    )


def expire(app, now):
    now[0] += 6
    app.store.expire()


def no_external_work_or_recreation(app, sources):
    for key, identity in sources:
        assert not app.store.rows(
            "SELECT 1 FROM messages WHERE chat_key=? AND message_id=?",
            (key, identity),
        )
        assert not app.store.rows(
            "SELECT 1 FROM message_versions WHERE source=?", (f"{key}/{identity}",)
        )
    assert not app.engine.calls and not app.decider.calls
    assert not app.transport.exact_reads and not app.transport.deletion_calls
    assert not app.transport.deliveries and not app.transport.reads


async def test_first_peerless_native_observation_after_expiry_forgets_generated_citations(
    tmp_path,
):
    app, now = rig(tmp_path)
    expired_source(app, now)
    citation(app, "generated-a")
    citation(app, "generated-b", sources=(1, 100))
    citation(app, "unrelated", sources=(100,))
    citation(app, "owner", origin="owner")
    expire(app, now)
    assert not pending(app) and app.store.message(KEY, 1) is None
    before = app.store.rows("SELECT * FROM messages")
    app.sync.deleted(None, [1])
    assert {entry["id"] for entry in app.memory.entries(KEY)} == {
        "unrelated",
        "owner",
    }
    state = app.memory.state(KEY)
    app.sync.deleted(None, [1])
    assert app.memory.state(KEY) == state
    assert app.store.rows("SELECT * FROM messages") == before and not pending(app)
    no_external_work_or_recreation(app, [(KEY, 1)])


async def test_one_peerless_update_resolves_each_unique_expired_identity_independently(
    tmp_path,
):
    app, now = rig(tmp_path)
    expired_source(app, now, 1)
    expired_source(app, now, 5, peer=OTHER_PEER)
    citation(app, "first", sources=(1,))
    citation(app, "second", OTHER, sources=(5,))
    citation(app, "owner-first", origin="owner")
    citation(app, "owner-second", OTHER, sources=(5,), origin="owner")
    expire(app, now)
    app.sync.deleted(None, [1, 5])
    assert {entry["id"] for entry in app.memory.entries(KEY)} == {"owner-first"}
    assert {entry["id"] for entry in app.memory.entries(OTHER)} == {"owner-second"}
    assert not pending(app)
    no_external_work_or_recreation(app, [(KEY, 1), (OTHER, 5)])


@pytest.mark.parametrize("evidence", ["operation", "block", "pending", "outdated"])
async def test_unique_retained_dependency_identity_cleans_up_without_native_receipt(
    tmp_path, evidence
):
    app, now = rig(tmp_path)
    expired_source(app, now)
    citation(app, "owner", sources=(), origin="owner")
    actual = app.sync.changed
    if evidence == "operation":
        app.store.deletion_operations.prepare(
            KEY,
            uuid.uuid4().hex,
            [{"message_id": 1, "revision": 1}],
            browser="authored-browser",
        )
    elif evidence == "block":
        app.sync.ingest(message(app, 2, date=now[0] - RETENTION + 40), target=False)
    elif evidence == "pending":
        app.sync.changed = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Authored saved hook interruption")
        )
        with pytest.raises(RuntimeError):
            app.sync.deleted(PEER, [1])
    else:
        with app.store.transaction() as db:
            db.execute(
                "UPDATE memory_entries SET outdated=? WHERE id='owner'",
                (json.dumps({"citations": [1]}),),
            )
    expire(app, now)
    operations = app.store.rows("SELECT * FROM deletion_operations")
    observed = []

    def tracked(source, **kwargs):
        observed.append(source)
        actual(source, **kwargs)

    app.sync.changed = tracked
    app.sync.deleted(None, [1])
    assert len(observed) == 1
    assert observed[0].key == f"{KEY}/1" and observed[0].deleted
    assert observed[0].date == 0 and observed[0].text == "" and not observed[0].media
    assert {entry["id"] for entry in app.memory.entries(KEY)} == {"owner"}
    assert app.memory.entries(KEY)[0]["outdated"] is None
    assert app.store.rows("SELECT * FROM deletion_operations") == operations
    assert not pending(app)
    if evidence == "block":
        assert json.loads(
            app.store.rows("SELECT sources FROM blocks")[0]["sources"]
        ) == [2]
    no_external_work_or_recreation(app, [(KEY, 1)])


@pytest.mark.parametrize(
    "other_evidence", ["citation", "operation", "block", "pending"]
)
async def test_colliding_retained_private_identities_are_not_guessed(
    tmp_path, other_evidence
):
    app, now = rig(tmp_path)
    expired_source(app, now)
    expired_source(app, now, peer=OTHER_PEER)
    citation(app, "first")
    if other_evidence == "citation":
        citation(app, "other", OTHER)
    elif other_evidence == "operation":
        app.store.deletion_operations.prepare(
            OTHER,
            uuid.uuid4().hex,
            [{"message_id": 1, "revision": 1}],
            browser="authored-browser",
        )
    elif other_evidence == "block":
        app.sync.ingest(
            message(app, 2, date=now[0] - RETENTION + 40).model_copy(
                update={"peer": OTHER_PEER}
            ),
            target=False,
        )
    else:
        actual = app.sync.changed
        app.sync.changed = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Authored other-identity hook interruption")
        )
        with pytest.raises(RuntimeError):
            app.sync.deleted(OTHER_PEER, [1])
        app.sync.changed = actual
    expire(app, now)
    entries = app.store.rows("SELECT * FROM memory_entries")
    markers = pending(app)
    app.sync.deleted(None, [1])
    assert app.store.rows("SELECT * FROM memory_entries") == entries
    assert pending(app) == markers
    no_external_work_or_recreation(app, [(KEY, 1), (OTHER, 1)])


async def test_unique_cached_source_and_another_retained_identity_remain_ambiguous(
    tmp_path,
):
    app, now = rig(tmp_path)
    expired_source(app, now)
    citation(app, "expired")
    app.store.put_chat(
        Chat(account_id=1, peer=OTHER_PEER, title="Authored other", kind="private")
    )
    app.sync.ingest(message(app).model_copy(update={"peer": OTHER_PEER}), target=False)
    expire(app, now)
    before = app.store.rows("SELECT * FROM messages")
    app.sync.deleted(None, [1])
    assert app.store.rows("SELECT * FROM messages") == before
    assert {entry["id"] for entry in app.memory.entries(KEY)} == {"expired"}
    assert not pending(app) and not app.engine.calls


async def test_ambiguous_cached_sources_are_not_disambiguated_by_a_single_citation(
    tmp_path,
):
    app, _ = rig(tmp_path)
    app.store.put_chat(
        Chat(account_id=1, peer=OTHER_PEER, title="Authored other", kind="private")
    )
    app.sync.ingest(message(app), target=False)
    app.sync.ingest(message(app).model_copy(update={"peer": OTHER_PEER}), target=False)
    citation(app, "one-citation")
    before = app.store.rows("SELECT * FROM messages")
    app.sync.deleted(None, [1])
    assert app.store.rows("SELECT * FROM messages") == before
    assert {entry["id"] for entry in app.memory.entries(KEY)} == {"one-citation"}
    assert not pending(app) and not app.engine.calls


@pytest.mark.parametrize(
    "evidence", ["owner", "unknown", "channel", "service", "string-id"]
)
async def test_untrusted_or_non_private_retained_cues_do_not_admit_peerless_source(
    tmp_path, evidence
):
    app, now = rig(tmp_path)
    key = KEY
    if evidence in ("channel", "service"):
        peer, kind = (
            ("channel:777", "broadcast")
            if evidence == "channel"
            else ("user:777", "service")
        )
        chat = Chat(account_id=1, peer=peer, title="Authored excluded", kind=kind)
        app.store.put_chat(chat)
        key = chat.key
    elif evidence == "unknown":
        key = "1/user:777"
    if evidence == "owner":
        expired_source(app, now)
        expire(app, now)
    citation(
        app,
        "retained",
        key,
        sources=("1",) if evidence == "string-id" else (1,),
        origin="owner" if evidence == "owner" else "model",
    )
    chats = app.store.rows("SELECT * FROM chats")
    entries = app.store.rows("SELECT * FROM memory_entries")
    app.sync.deleted(None, [1, 999])
    assert app.store.rows("SELECT * FROM chats") == chats
    assert app.store.rows("SELECT * FROM memory_entries") == entries
    assert not pending(app)
    no_external_work_or_recreation(app, [(key, 1)])


@pytest.mark.parametrize("cue", ["channel", "service"])
async def test_cached_channel_or_service_source_is_not_used_for_peerless_cleanup(
    tmp_path, cue
):
    app, _ = rig(tmp_path)
    if cue == "channel":
        chat = Chat(
            account_id=1,
            peer="channel:777",
            title="Authored channel",
            kind="broadcast",
        )
        app.store.put_chat(chat)
        original = message(app).model_copy(update={"peer": chat.peer})
        key = chat.key
    else:
        original, key = message(app, service=True), KEY
    app.sync.ingest(original, target=False)
    citation(app, "excluded", key)
    before = app.store.rows("SELECT * FROM messages")
    app.sync.deleted(None, [1])
    assert app.store.rows("SELECT * FROM messages") == before
    assert {entry["id"] for entry in app.memory.entries(key)} == {"excluded"}
    assert not pending(app) and not app.engine.calls


@pytest.mark.parametrize("peer", [None, PEER])
@pytest.mark.parametrize("all_failed", [False, True])
async def test_expired_native_batch_records_all_members_and_preserves_first_error(
    tmp_path, peer, all_failed
):
    app, now = rig(tmp_path)
    expired_source(app, now, 1)
    expired_source(app, now, 2)
    citation(app, "first", sources=(1,))
    citation(app, "second", sources=(2,))
    citation(app, "owner", sources=(1, 2), origin="owner")
    expire(app, now)
    actual = app.sync.changed
    first = RuntimeError("Authored first-source local blocker")

    def first_blocked(message, **kwargs):
        if message.message_id == 1:
            raise first
        if all_failed:
            raise RuntimeError("Authored second-source local blocker")
        return actual(message, **kwargs)

    app.sync.changed = first_blocked
    with pytest.raises(RuntimeError) as observed:
        app.sync.deleted(peer, [1, 2])
    assert observed.value is first
    app.sync.recover_deletions()
    assert {row["key"] for row in pending(app)} == {
        f"deletion-effects:{KEY}/{identity}"
        for identity in ((1, 2) if all_failed else (1,))
    }
    assert {entry["id"] for entry in app.memory.entries(KEY)} == (
        {"first", "second", "owner"} if all_failed else {"first", "owner"}
    )
    app.sync.changed = actual
    app.sync.recover_deletions()
    assert not pending(app)
    assert {entry["id"] for entry in app.memory.entries(KEY)} == {"owner"}
    no_external_work_or_recreation(app, [(KEY, 1), (KEY, 2)])


@pytest.mark.parametrize("peer", [None, PEER])
async def test_batch_identity_resolution_precedes_shared_citation_removal(
    tmp_path, peer
):
    app, now = rig(tmp_path)
    expired_source(app, now, 1)
    expired_source(app, now, 2)
    citation(app, "shared", sources=(1, 2))
    citation(app, "owner", sources=(1, 2), origin="owner")
    expire(app, now)
    actual, observed = app.sync.changed, []

    def tracked(message, **kwargs):
        observed.append(message.key)
        actual(message, **kwargs)

    app.sync.changed = tracked
    app.sync.deleted(peer, [1, 2])
    assert observed == [f"{KEY}/1", f"{KEY}/2"]
    assert {entry["id"] for entry in app.memory.entries(KEY)} == {"owner"}
    assert not pending(app)
    no_external_work_or_recreation(app, [(KEY, 1), (KEY, 2)])
