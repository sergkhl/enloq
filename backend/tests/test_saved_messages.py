"""ADR 43 IB1: the Owner Account's own Saved Messages, posted by host commands.

The route takes no peer, answers only the container's loopback with the
bootstrap control credential, and posts one APK from the builds folder or one
note through the running account's client, without resending a failure. Two
host reads come back: the newest still image there, saved as
the only file in the data folder's inbox, and the newest pairing code the
service posted there while it still works. Since ADR 50 Q5 one Saved Build
message is kept: each new build replaces its file and caption in place, and
it is pinned once when first posted.
"""

import dataclasses
import json
import time
from contextlib import closing
from datetime import UTC, datetime

import pytest
from enloq.api.server import create_app
from enloq.config import Settings, private_read, private_write
from enloq.domain.models import MediaTooLarge, Refused
from enloq.saved import latest_image, note_lines, notes, pairing_code, post
from fastapi.testclient import TestClient
from telethon import errors, types

from backend.tests.test_native_catalog import RPC, owner

ORIGIN = "https://enloq.example.com"
APK = b"PK\x03\x04" + b"authored apk bytes" * 10
PNG = b"\x89PNG\r\n\x1a\n" + b"authored screenshot" * 4
TAKEN = datetime(2026, 10, 18, 10, 40, tzinfo=UTC)


def app_for(tmp_path, owner=1):
    settings = Settings(
        data=tmp_path / "offline",
        listen_host="0.0.0.0",
        public_origin=ORIGIN,
        owner_account=owner,
    )
    app = create_app(settings)
    return app, settings


def host(app, address="127.0.0.1"):
    # The host command reaches the service on the container's loopback.
    return closing(
        TestClient(
            app,
            base_url=ORIGIN,
            client=(address, 50123),
            headers={"Origin": ORIGIN},
        )
    )


def secret(settings):
    return private_read(settings.data / "bootstrap")


def build(settings, name="enloq-0.1.0-1a2b3c4.apk", content=APK):
    folder = settings.data.parent / "builds"
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    return path


def transport(app):
    return app.state.accounts.get(1).transport


def posts(app):
    return transport(app).saved_posts


def test_an_apk_goes_to_the_owner_accounts_own_saved_messages_once(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        path = build(settings)
        answer = client.post(
            "/api/auth/saved-messages",
            json={
                "secret": secret(settings),
                "file": path.name,
                "caption": "Enloq 0.1.0 · 1a2b3c4",
            },
        )
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"message_id": 5000, "replaced": False, "pinned": True}
        assert posts(app) == [
            {
                "id": 5000,
                "file": "enloq-0.1.0-1a2b3c4.apk",
                "bytes": APK,
                "caption": "Enloq 0.1.0 · 1a2b3c4",
                "text": "",
                "monospace": False,
            }
        ]
        # The service's copy goes once Telegram confirmed it, and the new
        # build message is pinned without a notification.
        assert not path.exists()
        assert transport(app).saved_pins == [5000]
        note = client.post(
            "/api/auth/saved-messages",
            json={"secret": secret(settings), "text": "A paired phone."},
        )
        assert note.status_code == 200
        assert note.json() == {"message_id": 5001, "replaced": False, "pinned": False}
        assert posts(app)[-1]["text"] == "A paired phone."
        assert transport(app).saved_pins == [5000]


def publish(client, settings, name, caption, content=APK):
    path = build(settings, name, content)
    answer = client.post(
        "/api/auth/saved-messages",
        json={"secret": secret(settings), "file": path.name, "caption": caption},
    )
    return answer, path


def test_each_new_build_replaces_the_pinned_build_message_in_place(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        first, _ = publish(client, settings, "enloq-0.1.0-1a2b3c4.apk", "First")
        second, path = publish(
            client, settings, "enloq-0.1.0-5d6e7f8.apk", "Second", APK + b"new"
        )
        assert first.json() == {"message_id": 5000, "replaced": False, "pinned": True}
        assert second.status_code == 200, second.text
        assert second.json() == {"message_id": 5000, "replaced": True, "pinned": True}
        # One message: the file and caption changed, nothing else was posted,
        # deleted or pinned again.
        assert [
            (post["id"], post["file"], post["bytes"], post["caption"])
            for post in posts(app)
        ] == [(5000, "enloq-0.1.0-5d6e7f8.apk", APK + b"new", "Second")]
        assert transport(app).saved_edits == [5000]
        assert transport(app).saved_pins == [5000]
        assert not path.exists()
        assert json.loads(private_read(settings.data / "saved-build.json")) == {
            "account": 1,
            "message_id": 5000,
            "pinned": True,
        }
        # A note in between is never edited.
        client.post(
            "/api/auth/saved-messages",
            json={"secret": secret(settings), "text": "A paired phone."},
        )
        third, _ = publish(client, settings, "enloq-0.1.0-9a8b7c6.apk", "Third")
        assert third.json()["message_id"] == 5000
        assert posts(app)[-1]["text"] == "A paired phone."


def test_a_build_message_deleted_in_telegram_is_posted_and_pinned_anew(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        publish(client, settings, "enloq-0.1.0-1a2b3c4.apk", "First")
        # The owner deletes the build message in Telegram.
        posts(app).clear()
        answer, _ = publish(client, settings, "enloq-0.1.0-5d6e7f8.apk", "Second")
        assert answer.json() == {"message_id": 5000, "replaced": False, "pinned": True}
        assert transport(app).saved_pins == [5000, 5000]
        assert transport(app).saved_edits == []


def test_a_failed_pin_is_tried_again_by_the_next_publish_only(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        transport(app).pin_failure = "Authored pin failure"
        first, path = publish(client, settings, "enloq-0.1.0-1a2b3c4.apk", "First")
        # The build is posted either way.
        assert first.json() == {"message_id": 5000, "replaced": False, "pinned": False}
        assert not path.exists()
        transport(app).pin_failure = None
        second, _ = publish(client, settings, "enloq-0.1.0-5d6e7f8.apk", "Second")
        assert second.json() == {"message_id": 5000, "replaced": True, "pinned": True}
        # Once pinned, a pin the owner removes stays removed.
        publish(client, settings, "enloq-0.1.0-9a8b7c6.apk", "Third")
        assert transport(app).saved_pins == [5000]


def test_a_failed_replacement_keeps_the_previous_build_and_the_copy(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        publish(client, settings, "enloq-0.1.0-1a2b3c4.apk", "First")
        calls = []

        async def failing(identity, **post):
            calls.append(identity)
            raise ConnectionError("authored upload failure")

        transport(app).edit_saved = failing
        answer, path = publish(client, settings, "enloq-0.1.0-5d6e7f8.apk", "Second")
        assert answer.status_code == 400
        assert answer.json()["detail"] == (
            "Telegram did not take the new build (ConnectionError); the previous one stays"
        )
        assert calls == [5000]
        assert path.exists()
        assert [post["caption"] for post in posts(app)] == ["First"]


def test_another_owner_accounts_build_message_is_never_edited(tmp_path):
    app, settings = app_for(tmp_path)
    private_write(
        settings.data / "saved-build.json",
        json.dumps({"account": 2, "message_id": 5000, "pinned": True}),
    )
    with host(app) as client:
        answer, _ = publish(client, settings, "enloq-0.1.0-1a2b3c4.apk", "First")
        assert answer.json() == {"message_id": 5000, "replaced": False, "pinned": True}
        assert transport(app).saved_edits == []


def test_there_is_no_way_to_name_another_peer(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        answer = client.post(
            "/api/auth/saved-messages",
            json={"secret": secret(settings), "text": "Hi", "peer": "user:42"},
        )
        assert answer.status_code == 422
        assert posts(app) == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"address": "172.18.0.5"}, "Saved Messages posts are host commands only"),
        ({"forwarded": True}, "Saved Messages posts are host commands only"),
        ({"secret": "wrong"}, "Server pairing approval required"),
    ],
)
def test_only_the_host_with_the_control_credential_posts(tmp_path, change, reason):
    app, settings = app_for(tmp_path)
    with host(app, change.get("address", "127.0.0.1")) as client:
        answer = client.post(
            "/api/auth/saved-messages",
            json={"secret": change.get("secret", secret(settings)), "text": "Hi"},
            headers={"X-Forwarded-For": "203.0.113.9"}
            if change.get("forwarded")
            else {},
        )
        assert answer.status_code == 400
        assert answer.json()["detail"] == reason
        assert posts(app) == []


@pytest.mark.parametrize(
    ("name", "content", "reason"),
    [
        ("../escape.apk", APK, "Only an APK in the service's builds folder is posted"),
        ("enloq.zip", APK, "Only an APK in the service's builds folder is posted"),
        ("absent.apk", None, "The APK is not in the service's builds folder"),
        ("empty.apk", b"", "A Saved Build is an APK under 200 MB"),
        ("text.apk", b"not an apk", "Only an APK is posted"),
    ],
)
def test_anything_but_an_apk_from_the_builds_folder_is_refused(
    tmp_path, name, content, reason
):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        if content is not None and "/" not in name:
            build(settings, name, content)
        answer = client.post(
            "/api/auth/saved-messages",
            json={"secret": secret(settings), "file": name, "caption": "x"},
        )
        assert answer.status_code == 400
        assert answer.json()["detail"] == reason
        assert posts(app) == []


def test_a_link_out_of_the_builds_folder_is_not_followed(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        outside = tmp_path / "outside.apk"
        outside.write_bytes(APK)
        build(settings, "placeholder.apk")
        (settings.data.parent / "builds" / "linked.apk").symlink_to(outside)
        answer = client.post(
            "/api/auth/saved-messages",
            json={"secret": secret(settings), "file": "linked.apk"},
        )
        assert answer.json()["detail"] == "Only a regular APK file is posted"
        assert posts(app) == []


def test_a_post_needs_exactly_one_apk_or_note(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        path = build(settings)
        for body in ({}, {"file": path.name, "text": "both"}):
            answer = client.post(
                "/api/auth/saved-messages",
                json={"secret": secret(settings), **body},
            )
            assert (
                answer.json()["detail"] == "Post one APK with its caption, or one note"
            )
        assert posts(app) == []


def test_without_an_owner_account_nothing_is_posted(tmp_path):
    app, settings = app_for(tmp_path, owner=0)
    with host(app) as client:
        answer = client.post(
            "/api/auth/saved-messages",
            json={"secret": secret(settings), "text": "Hi"},
        )
        assert answer.json()["detail"] == "No Owner Account is set on this server"


def test_an_owner_account_that_is_not_signed_in_posts_nothing(tmp_path):
    app, settings = app_for(tmp_path, owner=777)
    with host(app) as client:
        answer = client.post(
            "/api/auth/saved-messages",
            json={"secret": secret(settings), "text": "Hi"},
        )
        assert answer.json()["detail"] == (
            "The Owner Account is not signed in on this server"
        )
    app, settings = app_for(tmp_path / "second")
    with host(app) as client:
        app.state.accounts.get(1).transport.connected = False
        answer = client.post(
            "/api/auth/saved-messages",
            json={"secret": secret(settings), "text": "Hi"},
        )
        assert answer.json()["detail"] == (
            "The Owner Account is not connected to Telegram"
        )


def test_a_failed_upload_is_reported_keeps_the_copy_and_is_not_sent_again(
    tmp_path,
):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        transport = app.state.accounts.get(1).transport
        calls = []
        original = transport.post_saved

        async def failing(**post):
            calls.append(post)
            raise ConnectionError("authored upload failure")

        transport.post_saved = failing
        path = build(settings)
        answer = client.post(
            "/api/auth/saved-messages",
            json={"secret": secret(settings), "file": path.name, "caption": "x"},
        )
        assert answer.status_code == 400
        assert answer.json()["detail"] == (
            "Telegram did not take the post (ConnectionError); nothing was sent again"
        )
        assert len(calls) == 1
        assert path.exists()
        transport.post_saved = original


def test_the_host_command_posts_through_the_running_service(tmp_path, monkeypatch):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        import httpx

        class Loopback:
            def __init__(self, base_url, headers, timeout, trust_env):
                assert base_url == f"http://127.0.0.1:{settings.port}"
                assert headers == {
                    "Origin": ORIGIN,
                    "Host": "enloq.example.com",
                }
                self.headers = headers

            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def post(self, path, json):
                return client.post(path, json=json)

        monkeypatch.setattr(httpx, "Client", Loopback)
        path = build(settings)
        assert post(settings, file=path.name, text="Enloq caption") == {
            "message_id": 5000,
            "replaced": False,
            "pinned": True,
        }
        assert posts(app)[-1]["caption"] == "Enloq caption"
        assert post(dataclasses.replace(settings), text="A note")["message_id"] == 5001


def leave_image(app, identity, *, data=PNG, mime="image/png", caption=""):
    app.state.accounts.get(1).transport.saved_images.append(
        {
            "message_id": identity,
            "date": TAKEN,
            "caption": caption,
            "mime": mime,
            "data": data,
        }
    )


def read(client, settings, route="latest-image", **change):
    body = {"secret": change.get("secret", secret(settings))}
    if route == "notes":
        body["count"] = change.get("count", 7)
    return client.post(
        f"/api/auth/saved-messages/{route}",
        json=body,
        headers={"X-Forwarded-For": "203.0.113.9"} if change.get("forwarded") else {},
    )


def test_the_newest_image_is_read_back_as_the_inboxs_only_file(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        leave_image(app, 7001, caption="older")
        leave_image(app, 7002, caption="The GIF did not send")
        answer = read(client, settings)
        assert answer.status_code == 200, answer.text
        assert answer.json() == {
            "message_id": 7002,
            "date": "2026-10-18T10:40:00Z",
            "caption": "The GIF did not send",
            "file": "inbox/saved-7002.png",
        }
        inbox = settings.data.parent / "inbox"
        assert [path.name for path in inbox.iterdir()] == ["saved-7002.png"]
        assert (inbox / "saved-7002.png").read_bytes() == PNG
        assert inbox.stat().st_mode & 0o777 == 0o700
        assert (inbox / "saved-7002.png").stat().st_mode & 0o777 == 0o600
        # The next read replaces it, and reading posts nothing.
        leave_image(app, 7003, mime="image/jpeg", data=b"\xff\xd8authored")
        assert read(client, settings).json()["file"] == "inbox/saved-7003.jpg"
        assert [path.name for path in inbox.iterdir()] == ["saved-7003.jpg"]
        assert posts(app) == []


@pytest.mark.parametrize("route", ["latest-image", "pairing-code", "notes"])
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"address": "172.18.0.5"}, "Saved Messages reads are host commands only"),
        ({"forwarded": True}, "Saved Messages reads are host commands only"),
        ({"secret": "wrong"}, "Server pairing approval required"),
    ],
)
def test_only_the_host_with_the_control_credential_reads(
    tmp_path, change, reason, route
):
    app, settings = app_for(tmp_path)
    with host(app, change.get("address", "127.0.0.1")) as client:
        leave_image(app, 7001)
        leave_code(app, "246810")
        answer = read(client, settings, route, **change)
        assert answer.status_code == 400
        assert answer.json()["detail"] == reason
        assert "246810" not in answer.text
        assert not (settings.data.parent / "inbox").exists()


def test_without_an_image_or_the_owner_account_nothing_is_read(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        answer = read(client, settings)
        assert answer.json()["detail"] == "No image among the last 20 Saved Messages"
        leave_image(app, 7001, mime="image/heic")
        answer = read(client, settings)
        assert answer.json()["detail"] == "Only a photo or a still image is read"
    app, settings = app_for(tmp_path / "second", owner=777)
    with host(app) as client:
        answer = read(client, settings)
        assert answer.json()["detail"] == (
            "The Owner Account is not signed in on this server"
        )


def test_the_host_command_reads_through_the_running_service(tmp_path, monkeypatch):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        import httpx

        class Loopback:
            def __init__(self, base_url, headers, timeout, trust_env):
                assert base_url == f"http://127.0.0.1:{settings.port}"
                assert headers == {"Origin": ORIGIN, "Host": "enloq.example.com"}

            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def post(self, path, json):
                return client.post(path, json=json)

        monkeypatch.setattr(httpx, "Client", Loopback)
        leave_image(app, 7002, caption="The GIF did not send")
        image = latest_image(settings)
        assert image["file"] == "inbox/saved-7002.png"
        assert image["caption"] == "The GIF did not send"


def leave_note(app, identity, text, *, media=None, mime=None, data=None):
    # One of the owner's own notes, as the transport reads it back.
    transport(app).saved_owner_notes.append(
        {
            "message_id": identity,
            "date": TAKEN,
            "text": text,
            "media": media,
            "mime": mime,
            "data": data,
        }
    )


def test_the_owners_last_notes_come_back_oldest_first_with_their_images(
    tmp_path,
):
    # ADR 51 Q1: the owner leaves a list of tasks in Saved Messages.
    app, settings = app_for(tmp_path)
    with host(app) as client:
        leave_note(app, 8001, "An older task")
        leave_note(app, 8002, "show transcript for outgoing", media="image")
        transport(app).saved_owner_notes[-1].update(mime="image/png", data=PNG)
        leave_note(app, 8003, "swipe to reply")
        leave_note(app, 8004, "", media="audio")
        # The last build the service posted is its own, never a note.
        private_write(
            settings.data / "saved-build.json",
            json.dumps({"account": 1, "message_id": 8005, "pinned": True}),
        )
        leave_note(app, 8005, "Enloq 0.1.0", media="document")
        leave_image(app, 7001)
        read(client, settings)
        inbox = settings.data.parent / "inbox"
        assert [path.name for path in inbox.iterdir()] == ["saved-7001.png"]
        answer = read(client, settings, "notes", count=3)
        assert answer.status_code == 200, answer.text
        assert answer.json() == {
            "notes": [
                {
                    "message_id": 8002,
                    "date": "2026-10-18T10:40:00Z",
                    "text": "show transcript for outgoing",
                    "media": "image",
                    "file": "inbox/saved-8002.png",
                },
                {
                    "message_id": 8003,
                    "date": "2026-10-18T10:40:00Z",
                    "text": "swipe to reply",
                    "media": None,
                    "file": None,
                },
                {
                    "message_id": 8004,
                    "date": "2026-10-18T10:40:00Z",
                    "text": "",
                    "media": "audio",
                    "file": None,
                },
            ]
        }
        # The inbox holds only this read's images, privately.
        assert [path.name for path in inbox.iterdir()] == ["saved-8002.png"]
        assert (inbox / "saved-8002.png").read_bytes() == PNG
        assert (inbox / "saved-8002.png").stat().st_mode & 0o777 == 0o600
        assert posts(app) == []
        for count in (0, 21):
            assert read(client, settings, "notes", count=count).status_code == 422


def test_the_host_command_prints_the_owners_notes(tmp_path, monkeypatch):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        import httpx

        class Loopback:
            def __init__(self, base_url, headers, timeout, trust_env):
                assert base_url == f"http://127.0.0.1:{settings.port}"

            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def post(self, path, json):
                return client.post(path, json=json)

        monkeypatch.setattr(httpx, "Client", Loopback)
        leave_note(app, 8001, "add write new message function\nand search")
        leave_note(app, 8002, "", media="image", mime="image/png", data=PNG)
        found = notes(settings, 2)
        assert [note["message_id"] for note in found] == [8001, 8002]
        assert note_lines(found[0]) == [
            "Note 8001 · 2026-10-18T10:40:00Z",
            "add write new message function",
            "and search",
            "",
        ]
        assert note_lines(found[1]) == [
            "Note 8002 · 2026-10-18T10:40:00Z · image · inbox/saved-8002.png",
            "(no text)",
            "",
        ]


class SavedHistory(RPC):
    """The newest Saved Messages first, as `get_messages` returns them."""

    def __init__(self, messages):
        super().__init__()
        self.messages, self.reads, self.downloads = messages, [], []

    async def get_messages(self, peer, limit):
        self.reads.append((peer, limit))
        return self.messages

    async def download_media(self, message, file):
        assert file is bytes
        self.downloads.append(message.id)
        return b"authored image"


def saved(identity, media=None, text=""):
    return types.Message(
        id=identity, peer_id=types.PeerUser(1), date=TAKEN, message=text, media=media
    )


def document(mime, *attributes, size=14):
    return types.MessageMediaDocument(
        document=types.Document(900, -1, b"ref", TAKEN, mime, size, 2, list(attributes))
    )


async def test_the_transport_reads_only_the_newest_still_image():
    code = saved(9, text="123456")
    sticker = saved(
        8,
        document(
            "image/webp",
            types.DocumentAttributeSticker("🙂", types.InputStickerSetEmpty()),
        ),
    )
    animation = saved(7, document("image/png", types.DocumentAttributeAnimated()))
    screenshot = saved(6, document("image/png"), "The GIF did not send")
    photo = saved(
        5,
        types.MessageMediaPhoto(
            photo=types.Photo(
                1,
                2,
                b"ref",
                TAKEN,
                [types.PhotoSizeProgressive("y", 1280, 720, [9, 13])],
                2,
            )
        ),
    )
    client = SavedHistory([code, sticker, animation, screenshot, photo])
    transport = owner(client)
    image = await transport.latest_saved_image(scan=20, max_bytes=100)
    assert (image["message_id"], image["mime"], image["data"]) == (
        6,
        "image/png",
        b"authored image",
    )
    assert image["caption"] == "The GIF did not send" and client.downloads == [6]
    peer, limit = client.reads[0]
    assert isinstance(peer, types.InputPeerSelf) and limit == 20
    with pytest.raises(MediaTooLarge):
        await transport.latest_saved_image(scan=20, max_bytes=13)
    client.messages = [code, photo]
    assert (await transport.latest_saved_image(scan=20, max_bytes=100))["mime"] == (
        "image/jpeg"
    )
    client.messages = [code, sticker, animation]
    with pytest.raises(Refused, match="No image among the last 20"):
        await transport.latest_saved_image(scan=20, max_bytes=100)
    assert client.downloads == [6, 5]


class SavedEdits(RPC):
    """Telethon's edit and pin, as the Saved Build path calls them."""

    def __init__(self, *, gone=False):
        super().__init__()
        self.edits, self.pins, self.gone = [], [], gone

    async def edit_message(self, entity, message, text, *, file, force_document):
        self.edits.append((entity, message, text, file, force_document))
        if self.gone:
            raise errors.MessageIdInvalidError(request=None)
        return types.Message(id=message, peer_id=types.PeerUser(1), date=TAKEN)

    async def pin_message(self, entity, message, *, notify):
        self.pins.append((entity, message, notify))


async def test_the_transport_reads_the_owners_notes_and_skips_the_services_posts():
    photo = types.MessageMediaPhoto(
        photo=types.Photo(1, 2, b"ref", TAKEN, [types.PhotoSize("y", 1280, 720, 14)], 2)
    )
    code = types.Message(
        id=20,
        peer_id=types.PeerUser(1),
        date=TAKEN,
        message="123456",
        entities=[types.MessageEntityCode(0, 6)],
    )
    record = saved(
        19, text="Chromium browser on Linux was paired with enloq.example.com."
    )
    build = saved(18, document("application/vnd.android.package-archive"), "Enloq")
    pin = types.MessageService(
        id=17,
        peer_id=types.PeerUser(1),
        date=TAKEN,
        action=types.MessageActionPinMessage(),
    )
    task = saved(16, text="use image and video carousel")
    screenshot = saved(15, photo, "sending message should look sent")
    large = saved(14, document("image/png", size=500), "too large to fetch")
    older = saved(13, text="swipe to reply")
    # The owner's own six digits in plain text are a note, not a code.
    digits = saved(12, text="246810")
    client = SavedHistory(
        [code, record, build, pin, task, screenshot, large, older, digits]
    )
    transport_ = owner(client)
    found = await transport_.saved_notes(count=4, scan=100, max_bytes=100, build=18)
    assert [
        (note["message_id"], note["text"], note["media"], note["mime"], note["data"])
        for note in found
    ] == [
        (16, "use image and video carousel", None, None, None),
        (
            15,
            "sending message should look sent",
            "image",
            "image/jpeg",
            b"authored image",
        ),
        (14, "too large to fetch", "document", None, None),
        (13, "swipe to reply", None, None, None),
    ]
    assert client.downloads == [15]
    peer, limit = client.reads[0]
    assert isinstance(peer, types.InputPeerSelf) and limit == 100
    found = await transport_.saved_notes(count=20, scan=100, max_bytes=100, build=18)
    assert [note["message_id"] for note in found] == [16, 15, 14, 13, 12]


async def test_the_transport_replaces_and_pins_only_in_its_own_saved_messages(
    tmp_path,
):
    client = SavedEdits()
    transport = owner(client)
    path = tmp_path / "enloq-0.1.0-1a2b3c4.apk"
    path.write_bytes(APK)
    assert await transport.edit_saved(5000, path=path, caption="Second") is True
    entity, identity, text, file, force_document = client.edits[0]
    assert isinstance(entity, types.InputPeerSelf)
    assert (identity, text, file, force_document) == (5000, "Second", str(path), True)
    await transport.pin_saved(5000)
    entity, identity, notify = client.pins[0]
    assert isinstance(entity, types.InputPeerSelf)
    assert (identity, notify) == (5000, False)
    # A message the owner deleted reads as gone, so a new one is posted.
    client.gone = True
    assert await transport.edit_saved(5000, path=path, caption="Third") is False


def leave_code(app, code, *, age=0, monospace=True):
    # As the service posts a pairing code: the six digits alone, in
    # monospace (ADR 47 Q2), `age` seconds ago.
    transport(app).saved_posts.append(
        {
            "id": 6000 + len(posts(app)),
            "file": None,
            "bytes": None,
            "caption": "",
            "text": code,
            "monospace": monospace,
        }
    )
    transport(app).saved_post_times[posts(app)[-1]["id"]] = time.time() - age


AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 HeadlessChrome/140.0"


def test_a_browser_on_the_host_trusts_itself_with_the_code_read_back(tmp_path):
    # ADR 43: a coding agent's browser connects
    # with the ordinary code, which the host reads back from Saved Messages.
    app, settings = app_for(tmp_path)
    browser = closing(
        TestClient(
            app, base_url=ORIGIN, headers={"Origin": ORIGIN, "User-Agent": AGENT}
        )
    )
    with host(app) as service, browser as page:
        started = page.post("/api/auth/code-pairings", json={"browser": True})
        assert started.status_code == 200, started.text
        attempt = started.json()
        answer = read(service, settings, "pairing-code")
        assert answer.status_code == 200, answer.text
        code = answer.json()["code"]
        assert code == posts(app)[-1]["text"] and posts(app)[-1]["monospace"]
        paired = page.post(
            f"/api/auth/code-pairings/{attempt['id']}/browser",
            json={"code": code},
            headers={"X-Pairing-Proof": attempt["proof"]},
        )
        assert paired.json() == {"authenticated": True, "waiting": False}
        # Saved Messages records the pairing, as for any browser.
        assert "was paired with" in posts(app)[-1]["text"]


def test_only_a_code_the_service_posted_comes_back_while_it_works(tmp_path):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        missing = "No pairing code posted to Saved Messages in the last 120 seconds"
        assert read(client, settings, "pairing-code").json()["detail"] == missing
        # A plain six-digit note is the owner's own text, not a code.
        leave_code(app, "135790", monospace=False)
        assert read(client, settings, "pairing-code").json()["detail"] == missing
        # A code past its two minutes is no use.
        leave_code(app, "112233", age=121)
        assert read(client, settings, "pairing-code").json()["detail"] == missing
        leave_code(app, "445566", age=30)
        leave_code(app, "778899", age=5)
        answer = read(client, settings, "pairing-code").json()
        assert answer["code"] == "778899" and answer["message_id"] == 6003


def test_the_host_command_prints_the_code_from_the_running_service(
    tmp_path, monkeypatch
):
    app, settings = app_for(tmp_path)
    with host(app) as client:
        import httpx

        class Loopback:
            def __init__(self, base_url, headers, timeout, trust_env):
                assert base_url == f"http://127.0.0.1:{settings.port}"

            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def post(self, path, json):
                return client.post(path, json=json)

        monkeypatch.setattr(httpx, "Client", Loopback)
        leave_code(app, "975310")
        assert pairing_code(settings)["code"] == "975310"


def posted_code(identity, text, *, age, entities=None, media=None):
    # Seconds before the `owner` rig's clock.
    return types.Message(
        id=identity,
        peer_id=types.PeerUser(1),
        date=datetime.fromtimestamp(2_000_000_000 - age, UTC),
        message=text,
        entities=[types.MessageEntityCode(0, 6)] if entities is None else entities,
        media=media,
    )


async def test_the_transport_reads_only_a_fresh_code_the_service_posted():
    note = posted_code(9, "246810", age=1, entities=[])
    longer = posted_code(8, "1234567", age=2, entities=[types.MessageEntityCode(0, 7)])
    pictured = posted_code(7, "", age=3, media=document("image/png"))
    code = posted_code(6, "424242", age=60)
    older = posted_code(5, "313131", age=90)
    client = SavedHistory([note, longer, pictured, code, older])
    transport_ = owner(client)
    found = await transport_.latest_saved_code(scan=20, max_age=120)
    assert (found["message_id"], found["code"]) == (6, "424242")
    peer, limit = client.reads[0]
    assert isinstance(peer, types.InputPeerSelf) and limit == 20
    # Past its age, a code is not read, and the scan stops at the first old
    # message.
    client.messages = [note, posted_code(4, "424242", age=121), code]
    with pytest.raises(Refused, match="No pairing code"):
        await transport_.latest_saved_code(scan=20, max_age=120)
    assert client.downloads == []
