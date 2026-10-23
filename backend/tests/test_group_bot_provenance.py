"""Native bot provenance uses authored Telethon objects and no live sessions."""

import json
from types import SimpleNamespace

import pytest
from enloq.domain.models import Refused, Unavailable
from enloq.transport.group_authors import bot_flag
from telethon import functions, types

from backend.tests.test_group_intake import group, native, rig, source


@pytest.mark.parametrize(
    "entity,peer,expected",
    [
        (types.User(id=7, bot=True), "user:7", True),
        (types.User(id=7), "user:7", False),
        (types.User(id=7, bot=True, min=True), "user:7", None),
        (types.User(id=7, bot=True, deleted=True), "user:7", None),
        (types.UserEmpty(id=7), "user:7", None),
        (types.User(id=8, bot=True), "user:7", None),
        (None, "user:7", None),
        (types.User(id=7, bot=True), "channel:7", None),
    ],
)
def test_only_full_exact_native_user_proves_bot_identity(entity, peer, expected):
    assert bot_flag(entity, peer) is expected


async def test_intake_uses_supplied_provenance_not_botlike_names(tmp_path):
    app, now = rig(tmp_path)
    group(app, now)
    [value] = await app.transport.normalize_page(
        [native("channel:42", 1, now[0])],
        entities={
            "user:7": types.User(id=7, first_name="Helpful human bot", bot=False)
        },
    )
    assert value.sender_is_bot is False
    [unknown] = await app.transport.normalize_page([native("channel:42", 2, now[0])])
    assert unknown.sender_is_bot is None
    assert app.transport.client.calls == app.transport.client.lookups == []


def test_enrichment_preserves_revisions_derivatives_and_deletion(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    value = source(app, now)
    app.sync.ingest(value, live=True)
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES (?,?,?)",
            (
                value.key,
                value.revision,
                json.dumps(dict(image_context="Authored cached meaning")),
            ),
        )
    before = app.store.message(key, 1)
    assert app.store.group_filters.native_bot(key, "user:7", True) == 1
    after = app.store.message(key, 1)
    assert after.revision == before.revision and after.sender_is_bot is True
    assert app.store.rows("SELECT body FROM derivatives")[0]["body"] == json.dumps(
        dict(image_context="Authored cached meaning")
    )
    # An unchanged replay without full User metadata cannot erase proof.
    app.sync.ingest(value, live=True)
    assert app.store.message(key, 1).sender_is_bot is True
    app.sync.ingest(value.model_copy(update=dict(deleted=True, revision=2)), live=True)
    assert app.store.message(key, 1).deleted
    assert app.store.message(key, 1).sender_is_bot is True


def test_new_native_proof_fills_all_retained_same_author_rows_only(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    for identity in (1, 2):
        app.sync.ingest(source(app, now, identity=identity), live=True)
    app.sync.ingest(
        source(app, now, identity=3).model_copy(update={"author_peer": "user:8"}),
        live=True,
    )
    app.sync.ingest(source(app, now, identity=4, sender_is_bot=True), live=True)
    assert [app.store.message(key, i).sender_is_bot for i in range(1, 5)] == [
        True,
        True,
        None,
        True,
    ]
    assert [app.store.message(key, i).revision for i in range(1, 5)] == [1, 1, 1, 1]


def cached_session(missing=()):
    def get(peer):
        if peer.user_id in missing:
            raise ValueError("No authored local input user")
        return types.InputPeerUser(peer.user_id, 100)

    return SimpleNamespace(get_input_entity=get)


async def test_exact_cached_author_profile_lookup_has_one_guarded_request(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now), live=True)
    app.transport.client.session = cached_session()

    async def response(request):
        assert isinstance(request, functions.users.GetUsersRequest)
        assert [user.user_id for user in request.id] == [7]
        return [types.User(id=7, first_name="Authored", bot=True)]

    app.transport.client.response = response
    assert await app.transport.refresh_group_authors("channel:42", ["user:7"]) == 1
    assert app.store.message(key, 1).sender_is_bot is True
    assert len(app.transport.client.calls) == 1
    assert app.transport.client.lookups == []


async def test_missing_cached_input_never_resolves_or_fetches(tmp_path):
    app, now = rig(tmp_path)
    group(app, now)
    app.sync.ingest(source(app, now), live=True)
    app.transport.client.session = cached_session(missing={7})
    assert await app.transport.refresh_group_authors("channel:42", ["user:7"]) == 0
    assert app.transport.client.calls == app.transport.client.lookups == []


@pytest.mark.parametrize(
    "answer",
    [
        [types.User(id=8, bot=True)],
        [types.User(id=7, bot=True), types.User(id=7, bot=True)],
    ],
)
async def test_wrong_or_duplicate_response_never_updates_any_provenance(
    tmp_path, answer
):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now), live=True)
    app.transport.client.session = cached_session()

    async def response(request):
        return answer

    app.transport.client.response = response
    with pytest.raises(Unavailable, match="identity mismatch"):
        await app.transport.refresh_group_authors("channel:42", ["user:7"])
    assert app.store.message(key, 1).sender_is_bot is None


async def test_native_wire_guard_rechecks_access_and_bounds(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now), live=True)
    app.transport.client.session = cached_session()

    async def revoke(request):
        app.store.groups.observe(key, access="lost", reason="authored revocation")

    app.transport.client.before_wire = revoke
    with pytest.raises(Refused):
        await app.transport.refresh_group_authors("channel:42", ["user:7"])
    assert app.transport.client.calls == []
    for authors in (
        ["user:8"],
        ["user:7", "user:7"],
        [f"user:{i}" for i in range(1, 34)],
    ):
        with pytest.raises(Refused):
            await app.transport.refresh_group_authors("channel:42", authors)


async def test_deleted_only_retained_author_uses_remaining_enrichment_allowance(
    tmp_path,
):
    app, now = rig(tmp_path)
    key = group(app, now)
    app.sync.ingest(source(app, now, identity=1), live=True)
    old = app.store.message(key, 1)
    now[0] += 10 * 86400
    # Seed an observed retained tombstone with its original native author.
    with app.store.transaction() as db:
        db.execute(
            "UPDATE messages SET deleted=1,body=? WHERE key=?",
            (old.model_copy(update={"deleted": True}).model_dump_json(), old.key),
        )
    app.sync.ingest(
        source(app, now, identity=2).model_copy(update={"author_peer": "user:8"}),
        live=True,
    )
    app.transport.client.session = cached_session()

    async def response(request):
        assert [user.user_id for user in request.id] == [8, 7]
        return [types.User(id=8), types.User(id=7, bot=True)]

    app.transport.client.response = response
    assert await app.group_filtering.enrich(key, [2], limit=2) == 2
    assert app.store.message(key, 1).deleted and app.store.message(key, 1).sender_is_bot
    assert not app.store.message(key, 2).sender_is_bot
