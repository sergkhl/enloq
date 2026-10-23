import asyncio
import base64
import json
import time

import http_ece
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from enloq.api.server import create_app
from enloq.application.notification_delivery import NotificationDelivery
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from enloq.transport.push import PushTransport, validate_subscription
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_notifications import complete, incoming


def subscription(endpoint="https://web.push.apple.com/Qauthored"):
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    auth = b"authored-seed-12"

    def encoded(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return (
        {
            "endpoint": endpoint,
            "keys": {"p256dh": encoded(public), "auth": encoded(auth)},
        },
        key,
        auth,
    )


@pytest.fixture
def rig(tmp_path):
    app = create_app(Settings(data=tmp_path))
    clients = [TestClient(app, base_url="http://127.0.0.1:8765") for _ in range(2)]
    for client in clients:
        trust_browser(client, tmp_path)
    hub = app.state.notifications
    now = [time.time() + 1]
    hub.clock = lambda: now[0]
    conversation = app.state.accounts.get(1)
    conversation.store.clock = lambda: now[0]
    conversation.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored sender", kind="private")
    )
    return app, clients, hub, conversation, now


def enable(client):
    value, _, _ = subscription()
    response = client.post("/api/notifications/subscription", json=value)
    assert response.status_code == 200
    return response.json()["browser"]


def ready(conversation, now):
    incoming(conversation)
    complete(conversation)
    now[0] += 31
    conversation.notifications.advance()


def test_real_encryption_vapid_and_no_redirects_without_network(tmp_path, monkeypatch):
    value, receiver, auth = subscription("https://fcm.googleapis.com/fcm/send/authored")
    captured = {}

    def post(self, url, **options):
        captured.update(options)
        response = requests.Response()
        response.status_code, response._content = 201, b""
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    transport = PushTransport(Settings(data=tmp_path))
    payload = {"id": "a" * 32, "expires": time.time() + 299, "silent": False}
    assert transport.send(value, payload, 299) == "sent"
    assert captured["allow_redirects"] is False and captured["timeout"] == 10
    assert captured["headers"]["content-encoding"] == "aes128gcm"
    assert "vapid" in captured["headers"]["Authorization"].lower()
    assert json.dumps(payload).encode() not in captured["data"]
    decoded = http_ece.decrypt(
        captured["data"], private_key=receiver, auth_secret=auth, version="aes128gcm"
    )
    assert json.loads(decoded) == payload
    assert transport.path.stat().st_mode & 0o777 == 0o600
    assert PushTransport(Settings(data=tmp_path)).public_key() == transport.public_key()


@pytest.mark.parametrize("test_event", [False, True], ids=["incoming", "test"])
async def test_wire_payload_is_opaque_while_local_validation_keeps_display_content(
    rig, tmp_path, monkeypatch, test_event
):
    app, clients, hub, conversation, now = rig
    value, receiver, auth = subscription("https://fcm.googleapis.com/fcm/send/authored")
    captured = []

    def post(self, url, **options):
        captured.append(options)
        response = requests.Response()
        response.status_code, response._content = 201, b""
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    hub.transport = PushTransport(Settings(data=tmp_path))
    browser = (
        clients[0].post("/api/notifications/subscription", json=value).json()["browser"]
    )
    clients[0].post("/api/notifications/preferences", json={"sound": False})
    if test_event:
        assert await hub.test(browser) == {"state": "sent"}
    else:
        ready(conversation, now)
        await hub.tick()
    assert len(captured) == 1
    decoded = json.loads(
        http_ece.decrypt(
            captured[0]["data"],
            private_key=receiver,
            auth_secret=auth,
            version="aes128gcm",
        )
    )
    assert set(decoded) == {"id", "expires", "silent"}
    assert len(decoded["id"]) == 32 and decoded["silent"] is True
    assert 0 < decoded["expires"] - now[0] <= 300
    # A Test names no conversation, which is not an unavailable locator.
    opened = clients[0].post("/api/notifications/open", json={"id": decoded["id"]})
    assert opened.json() == (
        {"kind": "test"}
        if test_event
        else {"kind": "conversation", "account_id": 1, "peer": "user:42"}
    )
    elsewhere = clients[1].post("/api/notifications/open", json={"id": decoded["id"]})
    assert elsewhere.json() == {"kind": "unavailable"}
    response = clients[0].post(
        "/api/notifications/validate", json={"id": decoded["id"]}
    )
    assert response.status_code == 200
    visible = response.json()
    assert visible["sender"] == ("Enloq" if test_event else "Authored sender")
    assert visible["text"] == (
        "Notifications are ready on this browser."
        if test_event
        else "Validated rewrite"
    )
    assert visible["account"] and visible["count"] == 1 and visible["silent"]
    assert visible["expires"] == decoded["expires"]
    assert not conversation.store.rows("SELECT * FROM reservations")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://web.push.apple.com/a",
        "https://localhost/a",
        "https://127.0.0.1/a",
        "https://web.push.apple.com.attacker.test/a",
        "https://fcm.googleapis.com:8443/a",
        "https://name@fcm.googleapis.com/a",
        "https://fcm.googleapis.com/a#secret",
        "https://example.test/a",
    ],
)
def test_push_destinations_cannot_fetch_arbitrary_urls(endpoint):
    value, _, _ = subscription(endpoint)
    with pytest.raises(Refused):
        validate_subscription(value)


def test_invalid_receiver_key_is_refused_before_destination_replacement(rig):
    app, clients, hub, conversation, now = rig
    first = enable(clients[0])
    value, _, _ = subscription()
    value["keys"]["p256dh"] = "a" * 87
    response = clients[1].post("/api/notifications/subscription", json=value)
    assert response.status_code == 400
    assert hub.active() == first


async def test_owner_replacement_sound_off_and_no_implicit_fallback(rig):
    app, clients, hub, conversation, now = rig
    first = enable(clients[0])
    second = enable(clients[1])
    assert first != second and hub.active() == second
    assert not clients[0].get("/api/notifications").json()["push"]
    clients[1].post("/api/notifications/preferences", json={"sound": False})
    ready(conversation, now)
    await hub.tick()
    assert len(hub.transport.sent) == 1
    event = hub.transport.sent[0][1]
    assert event["silent"] and set(event) == {"id", "expires", "silent"}
    assert (
        clients[1]
        .post("/api/notifications/validate", json={"id": event["id"]})
        .status_code
        == 200
    )
    assert (
        clients[0]
        .post("/api/notifications/validate", json={"id": event["id"]})
        .status_code
        == 400
    )
    target = clients[1].post("/api/notifications/open", json={"id": event["id"]}).json()
    assert target == {"kind": "conversation", "account_id": 1, "peer": "user:42"}
    # The locator is bound to the browser it was delivered to; elsewhere, and
    # for an unknown identity, it opens nothing rather than a guessed chat.
    for client, identity in ((clients[0], event["id"]), (clients[1], "0" * 32)):
        other = client.post("/api/notifications/open", json={"id": identity})
        assert other.status_code == 200
        assert other.json() == {"kind": "unavailable"}
    hub.auth.revoke(second)
    await hub.tick()
    assert hub.active() is None
    assert not clients[0].get("/api/notifications").json()["push"]


@pytest.mark.parametrize(
    "invalidate", ["delete", "logout", "revoke", "expiry", "replace"]
)
async def test_pending_delivery_rechecks_boundaries(rig, invalidate):
    app, clients, hub, conversation, now = rig
    first = enable(clients[0])
    ready(conversation, now)
    event = conversation.notifications.candidate(conversation.notifications.rows()[0])
    hub.claim(event, first, "push")
    if invalidate == "delete":
        conversation.sync.deleted("user:42", [1])
    elif invalidate == "logout":
        hub.manager.registry.set_state(1, "requires_login")
    elif invalidate == "revoke":
        hub.auth.revoke(first)
    elif invalidate == "expiry":
        now[0] += 301
    else:
        enable(clients[1])
    await hub.tick()
    assert not hub.transport.sent


async def test_retry_keeps_identity_and_expired_endpoint_retires_without_replay(rig):
    app, clients, hub, conversation, now = rig
    enable(clients[0])
    ready(conversation, now)
    hub.transport.outcomes = ["retry", "expired"]
    await asyncio.gather(hub.tick(), hub.tick())
    assert len(hub.transport.sent) == 1
    now[0] += 16
    await hub.tick()
    assert len(hub.transport.sent) == 2
    assert hub.transport.sent[0][1]["id"] == hub.transport.sent[1][1]["id"]
    assert hub.active() is None
    assert clients[0].get("/api/notifications").json()["failure"] == "expired"
    enable(clients[1])
    await hub.tick()
    assert len(hub.transport.sent) == 2


async def test_restart_does_not_redeliver_and_event_state_contains_no_content(rig):
    app, clients, hub, conversation, now = rig
    enable(clients[0])
    ready(conversation, now)
    await hub.tick()
    reopened = NotificationDelivery(hub.manager, hub.auth, clock=hub.clock)
    await reopened.tick()
    assert not reopened.transport.sent
    state = json.dumps(hub.registry.rows("SELECT * FROM notification_deliveries"))
    assert "Validated rewrite" not in state and "Authored sender" not in state
    assert not conversation.store.rows("SELECT * FROM reservations")


def test_auth_origin_json_guards_cover_subscription_and_test(rig):
    app, clients, hub, conversation, now = rig
    stranger = TestClient(app, base_url="http://127.0.0.1:8765")
    assert stranger.get("/api/notifications").status_code == 401
    value, _, _ = subscription()
    assert (
        clients[0]
        .post(
            "/api/notifications/subscription",
            json=value,
            headers={"Origin": "https://example.test"},
        )
        .status_code
        == 403
    )
    assert (
        clients[0]
        .post(
            "/api/notifications/subscription",
            content=json.dumps(value),
            headers={"Content-Type": "text/plain"},
        )
        .status_code
        == 403
    )
