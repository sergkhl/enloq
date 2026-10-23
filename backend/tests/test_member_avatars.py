"""TL5: group members' photos beside their messages (ADR 51 Q13).

Intake records each group author's current photo; the conversation carries
it with their messages, and the avatar route serves it like any other.
Authored entities and fake transports only."""

from datetime import UTC, datetime

import pytest
from enloq.domain.models import Refused
from enloq.storage.store import RETENTION
from telethon import types

from backend.tests.test_avatars import picture
from backend.tests.test_group_intake import group, native, rig, source


def member(identity, *, photo=None, min_=False):
    return types.User(
        id=identity,
        first_name="Authored",
        last_name=f"Member {identity}",
        access_hash=identity * 10,
        min=min_,
        photo=types.UserProfilePhoto(photo_id=photo, dc_id=2)
        if photo
        else types.UserProfilePhotoEmpty(),
    )


def test_a_later_sighting_replaces_an_earlier_one_and_expires(tmp_path):
    app, now = rig(tmp_path)
    store = app.store
    store.observe_authors([("user:7", "5001", now[0] - 60)])
    store.observe_authors([("user:7", "5000", now[0] - 120)])
    assert store.author_photo("user:7") == "5001"
    store.observe_authors([("user:7", None, now[0])])
    assert store.author_photo("user:7") is None
    store.observe_authors([("user:8", "6001", now[0] - RETENTION - 1)])
    assert store.author_photo("user:8") is None
    assert store.author_photo("user:9") is None


async def test_intake_records_full_group_authors_photos_only(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now, "channel:42")
    app.transport.client.entities["user:7"] = member(7, photo=5001)
    page = [
        native("channel:42", 1, now[0], sender=7),
        native("channel:42", 2, now[0], sender=8),
    ]
    entities = {"user:7": member(7, photo=5001), "user:8": member(8, min_=True)}
    for message, sender in zip(page, entities.values(), strict=True):
        message._sender = sender
    await app.transport.normalize_page(page, entities=entities)
    assert app.store.author_photo("user:7") == "5001"
    # A minimal entity is no evidence of a photo.
    assert not app.store.rows("SELECT 1 FROM group_authors WHERE peer='user:8'")
    # A private chat's sender is never a group author.
    private = types.Message(
        id=3,
        peer_id=types.PeerUser(9),
        date=datetime.fromtimestamp(now[0], UTC),
        message="Authored private note",
        from_id=types.PeerUser(9),
    )
    private._sender = member(9, photo=7001)
    await app.transport.normalize_page([private], entities={"user:9": member(9)})
    assert app.store.author_photo("user:9") is None
    assert key


def test_group_messages_carry_their_authors_photo(tmp_path):
    app, now = rig(tmp_path)
    key = group(app, now, "channel:42")
    app.sync.ingest(source(app, now, "channel:42", 1), live=True)
    app.store.observe_authors([("user:7", "5001", now[0])])
    [annotated] = app.store.history.annotate(app.store.messages(key))
    assert annotated["author_photo_id"] == "5001"


async def test_a_members_photo_is_served_only_while_they_are_a_known_author(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.transport.avatars = {("user:7", "5001"): picture("red")}
    app.transport.avatar_reads = []

    async def download(peer, photo_id, path, *, max_bytes):
        app.transport.avatar_reads.append((peer, photo_id))
        path.write_bytes(app.transport.avatars[peer, photo_id])

    app.transport.download_avatar = download
    with pytest.raises(Refused, match="Current profile photo unavailable"):
        await app.avatars.get("user:7", "5001")
    app.store.observe_authors([("user:7", "5001", now[0])])
    data, mime = await app.avatars.get("user:7", "5001")
    assert mime == "image/png" and data
    # A newer photo makes the old one stale.
    app.store.observe_authors([("user:7", "5002", now[0] + 1)])
    with pytest.raises(Refused, match="Current profile photo unavailable"):
        await app.avatars.get("user:7", "5001")
    assert app.transport.avatar_reads == [("user:7", "5001")]


async def test_a_members_profile_opens_only_for_a_recent_author(tmp_path):
    # ADR 51 Q14: a member's avatar or name opens their profile.
    app, now = rig(tmp_path)
    key = group(app, now, "channel:42")
    app.transport.profile = None

    async def profile(peer):
        assert peer == "user:7"
        return {"first_name": "Alice", "username": "alice_authored", "bio": "Hi."}

    app.transport.profile = profile
    with pytest.raises(Refused, match="isn't among the group's recent authors"):
        await app.profiles.read_member(key, "user:7")
    app.sync.ingest(source(app, now, "channel:42", 1), live=True)
    app.store.observe_authors([("user:7", "5001", now[0])])
    assert await app.profiles.read_member(key, "user:7") == {
        "name": "Alice",
        "username": "alice_authored",
        "phone": None,
        "bio": "Hi.",
        "photo_id": "5001",
    }
    for member in ("channel:42", "user:0", "user:7/x"):
        with pytest.raises(Refused):
            await app.profiles.read_member(key, member)


def test_an_anonymous_admins_post_carries_the_groups_own_photo(tmp_path):
    # ADR 51 Q13: an anonymous admin posts as the group itself.
    app, now = rig(tmp_path)
    key = group(app, now, "channel:42")
    chat = app.store.chat(key)
    app.store.put_chat(chat.model_copy(update={"photo_id": "777"}))
    assert app.store.author_photo("channel:42") == "777"
    assert app.store.author_photo("channel:99") is None
