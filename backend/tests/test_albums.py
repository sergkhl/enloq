"""TL6: Telegram albums, drawn as one grid (ADR 51 Q12). Intake records each
photo's or video's album; the conversation carries it with the message.
Authored native messages and fake transports only."""

from datetime import UTC, datetime

from enloq.domain.models import Chat
from enloq.storage.store import RETENTION
from telethon import types

from backend.tests.test_group_intake import rig


def photo(identity, now, *, grouped=None, peer=101):
    return types.Message(
        id=identity,
        peer_id=types.PeerUser(peer),
        date=datetime.fromtimestamp(now, UTC),
        message="",
        from_id=types.PeerUser(peer),
        grouped_id=grouped,
        media=types.MessageMediaPhoto(
            photo=types.Photo(
                identity,
                2,
                b"ref",
                datetime.fromtimestamp(now, UTC),
                [types.PhotoSize("y", 640, 480, 1000)],
                2,
            )
        ),
    )


async def test_intake_records_albums_and_serves_them_with_their_messages(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.store.put_chat(
        Chat(account_id=1, peer="user:101", title="Authored album", kind="private")
    )
    page = [
        photo(1, now[0], grouped=777),
        photo(2, now[0], grouped=777),
        photo(3, now[0]),
    ]
    page.append(
        types.Message(
            id=4,
            peer_id=types.PeerUser(101),
            date=datetime.fromtimestamp(now[0], UTC),
            message="A note in no album",
            grouped_id=777,
        )
    )
    messages = await app.transport.normalize_page(page)
    for message in messages:
        app.sync.ingest(message, target=False)
    assert [app.store.album(m.key) for m in messages] == ["777", "777", None, None]
    annotated = app.store.history.annotate(messages)
    assert [value["album"] for value in annotated] == ["777", "777", None, None]


def test_albums_past_the_content_window_are_forgotten(tmp_path):
    app, now = rig(tmp_path)
    app.store.observe_albums([("1/user:101/1", "5", now[0] - RETENTION - 1)])
    app.store.observe_albums([("1/user:101/2", "6", now[0])])
    assert app.store.album("1/user:101/1") is None
    assert app.store.album("1/user:101/2") == "6"
