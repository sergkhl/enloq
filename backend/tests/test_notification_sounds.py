"""Authored browser-owned audio tests; no native, model or push-service calls."""

import base64
import io
import json
import wave
from types import SimpleNamespace
from uuid import UUID

import pytest
from enloq.api.notifications import (
    MAX_SOUND_REQUEST_BYTES,
    NotificationSoundUpload,
)
from enloq.application.notification_delivery import NotificationDelivery
from enloq.application.notification_sounds import MAX_AUDIO_BYTES
from enloq.config import Settings
from enloq.storage.accounts import AccountRegistry
from fastapi.testclient import TestClient

from backend.tests.test_notification_attention import claim, presence
from backend.tests.test_push import enable, ready
from backend.tests.test_push import rig as rig


def authored_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 80)
    return output.getvalue()


WAV = authored_wav()
FORMATS = [
    (WAV, "audio/wav"),
    (b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x00", "audio/mpeg"),
    (b"\xff\xfb\x90\x00" + b"\x00" * 20, "audio/mpeg"),
    (b"OggS\x00" + b"\x00" * 22, "audio/ogg"),
    (b"fLaC\x80\x00\x00\x00", "audio/flac"),
    (b"\xff\xf1\x50\x80\x00\x1f\xfc", "audio/aac"),
    (b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom", "audio/mp4"),
    (b"FORM\x00\x00\x00\x04AIFF", "audio/aiff"),
    (b"\x1a\x45\xdf\xa3\x84webm", "audio/webm"),
]


def upload(client, body=WAV, name="Authored bell.wav"):
    return client.post(
        "/api/notifications/sound",
        json={"name": name, "data": base64.b64encode(body).decode("ascii")},
    )


def read(client, revision):
    return client.get("/api/notifications/sound", params={"revision": revision})


def status(client):
    response = client.get("/api/notifications")
    assert response.status_code == 200
    return response.json()


async def streamed_upload(app, client, chunks, content_length=None):
    """Exercise the actual ASGI route without buffering a request first."""
    cookie = app.state.browser_auth.cookie
    headers = [
        (b"host", b"127.0.0.1:8765"),
        (b"origin", b"http://127.0.0.1:8765"),
        (b"content-type", b"application/json"),
        (b"cookie", f"{cookie}={client.cookies.get(cookie)}".encode("ascii")),
    ]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/notifications/sound",
        "raw_path": b"/api/notifications/sound",
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 10000),
        "server": ("127.0.0.1", 8765),
    }
    received, sent = [], []

    async def receive():
        index = len(received)
        if index >= len(chunks):
            return {"type": "http.disconnect"}
        received.append(len(chunks[index]))
        return {
            "type": "http.request",
            "body": chunks[index],
            "more_body": index < len(chunks) - 1,
        }

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    response_status = next(
        message["status"]
        for message in sent
        if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return response_status, json.loads(response_body), received


def test_default_choice_and_attention_are_browser_owned(rig):
    app, clients, hub, conversation, now = rig
    for client in clients:
        value = status(client)
        assert value["sound"] is True
        assert value["sound_preset"] == "harp"
        assert value["sound_file_name"] is None and value["sound_revision"] is None
        attention = presence(client)
        assert {key: attention[key] for key in value if key.startswith("sound")} == {
            key: value[key] for key in value if key.startswith("sound")
        }
    clients[0].post("/api/notifications/preferences", json={"sound_preset": "bell"})
    assert status(clients[0])["sound_preset"] == "bell"
    assert status(clients[1])["sound_preset"] == "harp"
    assert not conversation.store.rows("SELECT * FROM reservations")


@pytest.mark.parametrize("projection", ["status", "attention"])
@pytest.mark.parametrize("transition", ["first_upload", "replacement", "choice_upload"])
def test_sound_projection_keeps_one_snapshot_during_interleaved_changes(
    rig, monkeypatch, projection, transition
):
    app, clients, hub, conversation, now = rig
    if transition != "first_upload":
        upload(clients[0], name="First authored.wav")
    if transition == "replacement":
        clients[0].post(
            "/api/notifications/preferences",
            json={"sound_preset": "bell", "sound": False},
        )
    before = status(clients[0])
    browser = before["browser"]
    metadata, interleaved = hub.sounds.metadata, []

    def change_before_projection(current):
        if not interleaved:
            # Another tab changes durable state after this response captures
            # its browser row, before it renders the sound fields.
            interleaved.append(True)
            if transition == "choice_upload":
                hub.preferences(browser, sound_preset="digital", sound=False)
            hub.upload_sound(
                browser,
                "Second authored.wav",
                base64.b64encode(WAV + b"\x00\x00").decode("ascii"),
            )
        return metadata(current)

    monkeypatch.setattr(hub.sounds, "metadata", change_before_projection)
    value = status(clients[0]) if projection == "status" else presence(clients[0])
    assert interleaved == [True]
    fields = ["sound", "sound_preset", "sound_file_name", "sound_revision"]
    assert {key: value[key] for key in fields} == {key: before[key] for key in fields}
    after = status(clients[0])
    assert after["sound_preset"] == "custom"
    assert after["sound_file_name"] == "Second authored.wav"
    assert after["sound_revision"] != before["sound_revision"]
    assert after["sound"] is (transition == "first_upload")


@pytest.mark.parametrize("preset", ["harp", "bell", "chime", "pop", "digital"])
def test_preset_selection_keeps_sound_off_and_push_enabled(rig, preset):
    app, clients, hub, conversation, now = rig
    browser = enable(clients[0])
    clients[0].post("/api/notifications/preferences", json={"sound": False})
    response = clients[0].post(
        "/api/notifications/preferences", json={"sound_preset": preset}
    )
    assert response.status_code == 200
    value = response.json()
    assert value["sound_preset"] == preset
    assert value["sound"] is False and value["push"] is True
    assert hub.active() == browser


@pytest.mark.parametrize("body,mime", FORMATS)
def test_signature_controls_mime_and_browser_owned_download(rig, body, mime):
    app, clients, hub, conversation, now = rig
    response = upload(clients[0], body, name="Authored audio.html")
    assert response.status_code == 200
    value = response.json()
    assert value["sound_preset"] == "custom"
    assert value["sound_file_name"] == "Authored audio.html"
    assert UUID(value["sound_revision"]).version == 4
    downloaded = read(clients[0], value["sound_revision"])
    assert downloaded.status_code == 200 and downloaded.content == body
    assert downloaded.headers["content-type"] == mime
    assert downloaded.headers["cache-control"] == "no-store"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in downloaded.headers
    assert read(clients[1], value["sound_revision"]).status_code == 400
    assert status(clients[1])["sound_file_name"] is None
    assert hub.registry.path.stat().st_mode & 0o777 == 0o600


def test_replacement_revision_restart_and_reselect_preserve_one_file(rig):
    app, clients, hub, conversation, now = rig
    browser = enable(clients[0])
    clients[0].post("/api/notifications/preferences", json={"sound": False})
    first = upload(clients[0]).json()
    assert first["sound"] is False and first["push"] is True
    second_body = WAV + b"\x00\x00"
    second = upload(clients[0], second_body, "Second authored.wav").json()
    assert second["sound_revision"] != first["sound_revision"]
    assert read(clients[0], first["sound_revision"]).status_code == 400
    selected = (
        clients[0]
        .post("/api/notifications/preferences", json={"sound_preset": "digital"})
        .json()
    )
    assert selected["sound_file_name"] == second["sound_file_name"]
    assert selected["sound_revision"] == second["sound_revision"]
    assert read(clients[0], second["sound_revision"]).content == second_body
    reopened = NotificationDelivery(hub.manager, hub.auth, clock=hub.clock)
    assert reopened.status(browser) == selected
    reselected = reopened.preferences(browser, sound_preset="custom")
    assert reselected["sound_revision"] == second["sound_revision"]
    assert reselected["sound"] is False and reselected["push"] is True
    assert reopened.sounds.get(browser, second["sound_revision"])[0] == second_body
    assert hub.registry.rows("SELECT count(*) AS count FROM notification_sounds") == [
        {"count": 1}
    ]


@pytest.mark.parametrize("preset", ["custom", "unknown"])
def test_unavailable_custom_and_invalid_preset_cannot_change_other_preferences(
    rig, preset
):
    app, clients, hub, conversation, now = rig
    enable(clients[0])
    before = status(clients[0])
    response = clients[0].post(
        "/api/notifications/preferences",
        json={"sound_preset": preset, "sound": False, "push": False},
    )
    assert response.status_code == (400 if preset == "custom" else 422)
    assert status(clients[0]) == before
    upload(clients[1])
    assert (
        clients[0]
        .post("/api/notifications/preferences", json={"sound_preset": "custom"})
        .status_code
        == 400
    )


@pytest.mark.parametrize(
    "data",
    ["", "%%%", "Zg", "Zh==", "Zg===", "Zg==\n", "_w==", "аудио"],
)
def test_invalid_base64_never_replaces_saved_file_or_echoes_payload(rig, data):
    app, clients, hub, conversation, now = rig
    before = upload(clients[0]).json()
    response = clients[0].post(
        "/api/notifications/sound", json={"name": "Rejected audio", "data": data}
    )
    assert response.status_code in {400, 422}
    assert "Rejected audio" not in response.text
    assert status(clients[0]) == before
    assert read(clients[0], before["sound_revision"]).content == WAV


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html><script>authored</script></html>",
        b"ID3",
        b"RIFF\x00\x00\x00\x00AVI ",
        b"OggS\x01" + b"\x00" * 22,
        b"\xff\xff\xff\xff",
        b"\x00\x00\x00\x10ftypUNKN\x00\x00\x00\x00",
    ],
)
def test_unrecognized_or_empty_audio_cannot_replace_saved_file(rig, body):
    app, clients, hub, conversation, now = rig
    before = upload(clients[0]).json()
    assert upload(clients[0], body).status_code in {400, 422}
    assert status(clients[0]) == before
    assert read(clients[0], before["sound_revision"]).content == WAV


@pytest.mark.parametrize("extra", [1, 3])
def test_encoded_and_decoded_size_bounds_preserve_saved_file(rig, extra):
    app, clients, hub, conversation, now = rig
    before = upload(clients[0]).json()
    body = WAV + b"\x00" * (MAX_AUDIO_BYTES + extra - len(WAV))
    assert upload(clients[0], body).status_code == (400 if extra == 1 else 422)
    assert status(clients[0]) == before
    assert read(clients[0], before["sound_revision"]).content == WAV


def test_exact_two_mib_limit_and_bounded_sanitized_display_name(rig):
    app, clients, hub, conversation, now = rig
    body = WAV + b"\x00" * (MAX_AUDIO_BYTES - len(WAV))
    name = "C:\\private\\folder/\x00\nAuthored\u202e bell.wav"
    response = upload(clients[0], body, name)
    assert response.status_code == 200
    value = response.json()
    assert value["sound_file_name"] == "Authored bell.wav"
    assert read(clients[0], value["sound_revision"]).content == body
    value = upload(clients[0], name="a" * 1000).json()
    assert len(value["sound_file_name"]) == 128
    for invalid in ["..", "\x00\n", "folder/", "a" * 1025]:
        assert upload(clients[0], name=invalid).status_code in {400, 422}
        assert status(clients[0]) == value


@pytest.mark.parametrize("content_length", [None, 10])
async def test_raw_stream_limit_stops_before_unread_tail_and_keeps_saved_audio(
    rig, content_length
):
    app, clients, hub, conversation, now = rig
    before = upload(clients[0]).json()
    chunks = [
        b'{"name":"Rejected raw upload","extra":"'
        + b"x" * (MAX_SOUND_REQUEST_BYTES - 100),
        b"x" * 100,
        b"Unread private authored tail",
    ]
    response_status, response, received = await streamed_upload(
        app, clients[0], chunks, content_length
    )
    assert response_status == 413
    assert response == {"detail": "The audio upload is too large"}
    assert received == [len(chunks[0]), len(chunks[1])]
    assert sum(received) > MAX_SOUND_REQUEST_BYTES
    assert status(clients[0]) == before
    assert read(clients[0], before["sound_revision"]).content == WAV


async def test_oversized_declared_length_refuses_without_reading_body(rig):
    app, clients, hub, conversation, now = rig
    before = upload(clients[0]).json()
    response_status, response, received = await streamed_upload(
        app, clients[0], [b"Must not be read"], MAX_SOUND_REQUEST_BYTES + 1
    )
    assert response_status == 413
    assert response == {"detail": "The audio upload is too large"}
    assert received == []
    assert status(clients[0]) == before


@pytest.mark.parametrize("difference", [-1, 1])
async def test_mismatched_content_length_refuses_before_replacing_audio(
    rig, difference
):
    app, clients, hub, conversation, now = rig
    before = upload(clients[0]).json()
    payload = json.dumps(
        {"name": "Rejected mismatch.wav", "data": base64.b64encode(WAV).decode()}
    ).encode()
    response_status, response, received = await streamed_upload(
        app, clients[0], [payload], len(payload) + difference
    )
    assert response_status == 400
    assert response == {"detail": "The audio upload has an invalid length"}
    assert received == [len(payload)]
    assert status(clients[0]) == before


@pytest.mark.parametrize("content_length", ["-1", "invalid"])
async def test_invalid_content_length_refuses_without_reading_body(rig, content_length):
    app, clients, hub, conversation, now = rig
    before = upload(clients[0]).json()
    response_status, response, received = await streamed_upload(
        app, clients[0], [b"Must not be read"], content_length
    )
    assert response_status == 400
    assert response == {"detail": "The audio upload has an invalid length"}
    assert received == []
    assert status(clients[0]) == before


@pytest.mark.parametrize("declared", [False, True])
async def test_streamed_exact_two_mib_audio_accepts_with_or_without_content_length(
    rig, declared
):
    app, clients, hub, conversation, now = rig
    audio = WAV + b"\x00" * (MAX_AUDIO_BYTES - len(WAV))
    payload = json.dumps(
        {"name": "Streamed authored.wav", "data": base64.b64encode(audio).decode()}
    ).encode()
    chunks = [payload[index : index + 65536] for index in range(0, len(payload), 65536)]
    response_status, response, received = await streamed_upload(
        app, clients[0], chunks, len(payload) if declared else None
    )
    assert response_status == 200
    assert received == [len(chunk) for chunk in chunks]
    assert response["sound_preset"] == "custom"
    assert response["sound_file_name"] == "Streamed authored.wav"
    assert read(clients[0], response["sound_revision"]).content == audio


def test_extra_json_cannot_bypass_raw_bound_or_expose_payload(rig):
    app, clients, hub, conversation, now = rig
    before = upload(clients[0]).json()
    payload = {"name": "Rejected extra.wav", "data": base64.b64encode(WAV).decode()}
    response = clients[0].post(
        "/api/notifications/sound",
        json=payload | {"extra": "Private authored metadata"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Check the requested fields and try again"}
    response = clients[0].post(
        "/api/notifications/sound",
        json=payload | {"extra": "x" * MAX_SOUND_REQUEST_BYTES},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "The audio upload is too large"}
    assert status(clients[0]) == before


def test_auth_same_origin_json_and_current_revision_guards(rig):
    app, clients, hub, conversation, now = rig
    value = upload(clients[0]).json()
    stranger = TestClient(app, base_url="http://127.0.0.1:8765")
    stranger.headers["Origin"] = "http://127.0.0.1:8765"
    assert upload(stranger).status_code == 401
    assert read(stranger, value["sound_revision"]).status_code == 401
    assert clients[0].get("/api/notifications/sound").status_code == 422
    assert read(clients[0], "malformed").status_code == 422
    assert read(clients[0], "a" * 32).status_code == 400
    payload = {"name": "Authored.wav", "data": base64.b64encode(WAV).decode()}
    assert (
        clients[0]
        .post(
            "/api/notifications/sound",
            json=payload,
            headers={"Origin": "https://example.test"},
        )
        .status_code
        == 403
    )
    assert (
        clients[0]
        .post(
            "/api/notifications/sound",
            content=json.dumps(payload),
            headers={"Content-Type": "text/plain"},
        )
        .status_code
        == 403
    )
    assert (
        clients[0]
        .get(
            "/api/notifications/sound",
            params={"revision": value["sound_revision"]},
            headers={"Origin": "https://example.test"},
        )
        .status_code
        == 403
    )
    represented = repr(NotificationSoundUpload(**payload))
    assert payload["data"] not in represented and payload["name"] not in represented


async def test_forgetting_browser_removes_private_audio_but_preserves_other_browser(
    rig,
):
    app, clients, hub, conversation, now = rig
    first = upload(clients[0]).json()
    second = upload(clients[1], name="Other authored.wav").json()
    hub.auth.revoke(first["browser"])
    assert read(clients[0], first["sound_revision"]).status_code == 401
    assert upload(clients[0]).status_code == 401
    await hub.tick()
    assert hub.registry.rows("SELECT browser FROM notification_sounds") == [
        {"browser": second["browser"]}
    ]
    assert read(clients[1], second["sound_revision"]).content == WAV
    assert status(clients[1]) == second


async def test_attention_metadata_refreshes_and_sound_off_does_not_suppress_push(rig):
    app, clients, hub, conversation, now = rig
    enable(clients[0])
    value = upload(clients[0]).json()
    attention = presence(clients[0], focused=False)
    assert attention["sound_preset"] == "custom"
    assert attention["sound_file_name"] == value["sound_file_name"]
    assert attention["sound_revision"] == value["sound_revision"]
    clients[0].post("/api/notifications/preferences", json={"sound": False})
    ready(conversation, now)
    await hub.tick()
    assert len(hub.transport.sent) == 1
    assert hub.transport.sent[0][1]["silent"] is True
    assert set(hub.transport.sent[0][1]) == {"id", "expires", "silent"}
    attention = presence(clients[0], focused=False)
    assert attention["sound"] is False and attention["events"] == []
    assert attention["sound_revision"] == value["sound_revision"]
    assert not conversation.store.rows("SELECT * FROM reservations")


async def test_preset_change_keeps_one_existing_chime_claim(rig):
    app, clients, hub, conversation, now = rig
    ready(conversation, now)
    presence(clients[0], peer="user:101")
    await hub.tick()
    original = presence(clients[0], peer="user:101")["events"]
    assert len(original) == 1
    value = upload(clients[0]).json()
    clients[0].post("/api/notifications/preferences", json={"sound_preset": "bell"})
    attention = presence(clients[0], peer="user:101")
    assert attention["events"] == original
    assert attention["sound_preset"] == "bell"
    assert attention["sound_revision"] == value["sound_revision"]
    assert claim(clients[0], original[0])["id"] == original[0]
    assert claim(clients[0], original[0]) is None
    await hub.tick()
    assert presence(clients[0], peer="user:101")["events"] == []
    assert not hub.transport.sent


def test_additive_legacy_registry_migration_keeps_switch_subscription_and_claims(
    tmp_path,
):
    registry = AccountRegistry(tmp_path)
    browser = "a" * 32
    saved_subscription = json.dumps({"endpoint": "authored", "keys": {}})
    with registry.transaction() as db:
        db.execute(
            "CREATE TABLE notification_browsers (browser TEXT PRIMARY KEY, subscription TEXT, sound INTEGER NOT NULL DEFAULT 1, failure TEXT)"
        )
        db.execute(
            "INSERT INTO notification_browsers VALUES (?,?,0,'authored_failure')",
            (browser, saved_subscription),
        )
        db.execute(
            "CREATE TABLE notification_deliveries (id TEXT PRIMARY KEY, account_id INTEGER, peer TEXT, browser TEXT NOT NULL, channel TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL, retry REAL NOT NULL, expires REAL NOT NULL)"
        )
        db.execute(
            "INSERT INTO notification_deliveries VALUES (?,1,'user:42',?,'push','sent',1,0,9999999999)",
            ("b" * 32, browser),
        )
        db.execute("INSERT INTO metadata VALUES ('push_destination',?)", (browser,))
    # Reopening moves the owner's destination to their member settings (ADR 48).
    registry = AccountRegistry(tmp_path)
    manager = SimpleNamespace(
        registry=registry, settings=Settings(data=tmp_path), runtimes={}
    )
    auth = SimpleNamespace(
        active=lambda identity: identity == browser,
        browsers=lambda current: [{"id": browser, "label": "Authored browser"}],
        member_of=lambda identity: "owner",
        member_clients=lambda member: [browser],
    )
    for _ in range(2):
        delivery = NotificationDelivery(manager, auth)
        current = delivery.status(browser)
        assert current["sound"] is False and current["push"] is True
        assert current["failure"] == "authored_failure"
        assert current["sound_preset"] == "harp"
        assert current["sound_file_name"] is None and current["sound_revision"] is None
        assert delivery.browser(browser)["subscription"] == saved_subscription
        assert registry.rows(
            "SELECT id,state,attempts,tab FROM notification_deliveries"
        ) == [{"id": "b" * 32, "state": "sent", "attempts": 1, "tab": None}]
