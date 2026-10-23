"""ADR 38 Q5: phones get their own FCM copy of
each ready burst, beside the single browser destination, quiet while any
trusted client reads the chat. Authored fixtures only: no Google, Telegram or
model calls."""

import asyncio
import base64
import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from enloq.api.server import create_app
from enloq.config import Settings, private_write
from enloq.domain.models import Chat, Refused
from enloq.transport.fcm import OAUTH, SCOPE, FcmTransport
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_device_trust import ORIGIN, bearer, pair_device
from backend.tests.test_notifications import complete, incoming
from backend.tests.test_push import subscription

PROJECT = "enloq-authored"
ACCOUNT = f"enloq-push@{PROJECT}.iam.gserviceaccount.com"
TOKEN = "eAuthoredFcmToken01:APA91b" + "Q" * 120


@pytest.fixture
def rig(tmp_path):
    app = create_app(
        Settings(
            data=tmp_path,
            listen_host="0.0.0.0",
            public_origin=ORIGIN,
            fcm_project=PROJECT,
        )
    )
    browser = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    trust_browser(browser, tmp_path)
    token = pair_device(app, tmp_path, name="Pixel 9")
    phone = closing(TestClient(app, base_url=ORIGIN))
    hub = app.state.notifications
    now = [time.time() + 1]
    hub.clock = lambda: now[0]
    conversation = app.state.accounts.get(1)
    conversation.store.clock = lambda: now[0]
    conversation.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored sender", kind="private")
    )
    with browser, phone as device:
        device.headers.update(bearer(token))
        yield app, browser, device, hub, conversation, now


def ready(conversation, now):
    incoming(conversation)
    complete(conversation)
    now[0] += 31
    conversation.notifications.advance()


def turn_on(device):
    answer = device.post("/api/notifications/device", json={"token": TOKEN})
    assert answer.status_code == 200, answer.text
    return answer.json()


def notice(conversation):
    return conversation.store.rows("SELECT * FROM incoming_notices")[0]


def test_a_phone_gets_the_opaque_payload_and_fetches_the_details(rig):
    app, _, device, hub, conversation, now = rig
    assert turn_on(device) == {
        "push": True,
        "sound": True,
        "available": True,
        "failure": None,
    }
    ready(conversation, now)
    asyncio.run(hub.tick())
    sent = hub.devices.transport.sent
    assert len(sent) == 1
    token, payload, ttl = sent[0]
    assert token == TOKEN and 0 < ttl <= 300
    assert set(payload) == {"id", "expires", "silent"} and payload["silent"] is False
    details = device.post("/api/notifications/validate", json={"id": payload["id"]})
    assert details.status_code == 200
    assert details.json() | {"expires": 0} == {
        "id": payload["id"],
        "account_id": 1,
        "peer": "user:42",
        "sender": "Authored sender",
        "account": app.state.accounts.registry.get(1)["name"][:80],
        "count": 1,
        "text": "Validated rewrite",
        "expires": 0,
        "silent": False,
    }
    target = device.post("/api/notifications/open", json={"id": payload["id"]})
    assert target.json() == {
        "kind": "conversation",
        "account_id": 1,
        "peer": "user:42",
    }
    # The phone that alerted closed the burst.
    assert notice(conversation)["state"] == "delivered"


def test_only_fcm_registration_tokens_are_accepted(rig):
    _, _, device, _, _, _ = rig
    for token in (
        "short:APA91b",
        TOKEN + " ",
        "https://ntfy.example.com/upAuthoredTopic01?up=1",
        TOKEN.replace("Q", "/", 1),
        "x" * 31,
    ):
        answer = device.post("/api/notifications/device", json={"token": token})
        assert answer.status_code == 400, token
    assert turn_on(device)["push"] is True


def test_a_server_without_fcm_refuses_phone_push(tmp_path):
    app = create_app(
        Settings(data=tmp_path, listen_host="0.0.0.0", public_origin=ORIGIN)
    )
    token = pair_device(app, tmp_path)
    with closing(TestClient(app, base_url=ORIGIN)) as device:
        device.headers.update(bearer(token))
        assert device.get("/api/notifications/device").json()["available"] is False
        answer = device.post("/api/notifications/device", json={"token": TOKEN})
        assert answer.status_code == 400
        assert answer.json()["detail"] == "Phone push is not set up on this server"


def test_unifiedpush_endpoints_give_way_to_tokens(tmp_path):
    with closing(sqlite3.connect(tmp_path / "accounts.sqlite3")) as db, db:
        db.execute(
            "CREATE TABLE notification_devices (device TEXT PRIMARY KEY, endpoint TEXT, sound INTEGER NOT NULL DEFAULT 1, failure TEXT)"
        )
        db.execute(
            "INSERT INTO notification_devices VALUES ('a', 'https://ntfy.example.com/upAuthoredTopic01?up=1', 0, NULL)"
        )
    app = create_app(Settings(data=tmp_path, fcm_project=PROJECT))
    rows = app.state.notifications.registry.rows("SELECT * FROM notification_devices")
    assert [dict(row) for row in rows] == [
        {"device": "a", "token": None, "sound": 0, "failure": None}
    ]


def test_browsers_and_phones_keep_their_own_destinations(rig):
    _, browser, device, hub, _, _ = rig
    assert browser.get("/api/notifications/device").status_code == 400
    value, _, _ = subscription()
    taken = device.post("/api/notifications/subscription", json=value)
    assert taken.status_code == 400
    assert hub.active() is None


def read_chat(client, tab, audio):
    answer = client.post(
        "/api/notifications/attention",
        json={
            "tab": tab,
            "account_id": 1,
            "peer": "user:42",
            "focused": True,
            "audio": audio,
        },
    )
    assert answer.status_code == 200


def test_a_phone_is_quiet_while_any_trusted_client_reads_the_chat(rig):
    _, browser, device, hub, conversation, now = rig
    turn_on(device)
    ready(conversation, now)
    read_chat(browser, "a" * 32, audio=True)
    asyncio.run(hub.tick())
    assert hub.devices.transport.sent == []
    # The phone reading the chat itself counts the same way; the browser's
    # presence has aged out by the next burst.
    incoming(conversation, identity=2)
    complete(conversation)
    now[0] += 31
    conversation.notifications.advance()
    read_chat(device, "b" * 32, audio=False)
    asyncio.run(hub.tick())
    assert hub.devices.transport.sent == []
    assert hub.registry.rows("SELECT * FROM device_deliveries") == []


def test_a_phone_copy_outlives_a_failed_browser_push(rig):
    _, browser, device, hub, conversation, now = rig
    value, _, _ = subscription()
    assert (
        browser.post("/api/notifications/subscription", json=value).status_code == 200
    )
    turn_on(device)
    hub.transport.outcomes = ["refused"]
    ready(conversation, now)
    asyncio.run(hub.tick())
    assert len(hub.transport.sent) == 1 and len(hub.devices.transport.sent) == 1
    sent = hub.devices.transport.sent[0][1]
    assert (
        device.post("/api/notifications/validate", json={"id": sent["id"]}).status_code
        == 200
    )


def test_a_phone_alert_leaves_no_later_chime_for_the_burst(rig):
    _, browser, device, hub, conversation, now = rig
    turn_on(device)
    ready(conversation, now)
    asyncio.run(hub.tick())
    assert len(hub.devices.transport.sent) == 1
    browser.post(
        "/api/notifications/attention",
        json={
            "tab": "c" * 32,
            "account_id": 1,
            "peer": "user:7",
            "focused": False,
            "audio": True,
        },
    )
    asyncio.run(hub.tick())
    assert hub.registry.rows("SELECT * FROM notification_deliveries") == []


def test_retry_then_expired_retires_the_phone_endpoint(rig):
    _, _, device, hub, conversation, now = rig
    turn_on(device)
    hub.devices.transport.outcomes = ["retry", "expired"]
    ready(conversation, now)
    asyncio.run(hub.tick())
    row = hub.registry.rows("SELECT * FROM device_deliveries")[0]
    assert row["state"] == "retry"
    now[0] += 16
    asyncio.run(hub.tick())
    assert (
        hub.registry.rows("SELECT state FROM device_deliveries")[0]["state"] == "failed"
    )
    status = device.get("/api/notifications/device").json()
    assert status["push"] is False and status["failure"] == "expired"


def test_forgetting_a_phone_retires_its_destination(rig):
    _, browser, device, hub, conversation, now = rig
    turn_on(device)
    phone_row = next(
        row
        for row in browser.get("/api/auth/browsers").json()
        if row["kind"] == "device"
    )
    assert (
        browser.post(
            f"/api/auth/browsers/{phone_row['id']}/revoke", json={}
        ).status_code
        == 200
    )
    ready(conversation, now)
    asyncio.run(hub.tick())
    assert hub.devices.transport.sent == []
    assert hub.registry.rows("SELECT * FROM notification_devices") == []


def test_a_phone_test_push_and_its_preferences(rig):
    _, _, device, hub, _, _ = rig
    refused = device.post("/api/notifications/device/test", json={})
    assert refused.status_code == 400
    turn_on(device)
    quiet = device.post("/api/notifications/device/preferences", json={"sound": False})
    assert quiet.json()["sound"] is False
    assert device.post("/api/notifications/device/test", json={}).json() == {
        "state": "sent"
    }
    payload = hub.devices.transport.sent[-1][1]
    assert payload["silent"] is True
    details = device.post("/api/notifications/validate", json={"id": payload["id"]})
    assert details.json()["text"] == "Notifications are ready on this phone."
    assert device.post(
        "/api/notifications/open", json={"id": payload["id"]}
    ).json() == {"kind": "test"}
    off = device.post("/api/notifications/device/preferences", json={"push": False})
    assert off.json()["push"] is False


def decode(segment):
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


@pytest.fixture
def fcm(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "fcm-key.pem"
    private_write(
        path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    settings = Settings(
        mode="personal",
        data=tmp_path,
        refs={"FCM_KEY_REF": str(path)},
        fcm_project=PROJECT,
        fcm_account=ACCOUNT,
        fcm_key_id="0123456789abcdef0123456789abcdef01234567",
    )
    return FcmTransport(settings), key.public_key()


def answer(status, body=None):
    return SimpleNamespace(status_code=status, json=lambda: body or {})


def failure(status, code):
    return answer(status, {"error": {"details": [{"errorCode": code}]}})


def test_the_fcm_transport_signs_for_its_account_and_sends_data_only(fcm, monkeypatch):
    transport, public = fcm
    calls = []
    replies = [answer(200, {"access_token": "ya29.authored", "expires_in": 3599})]
    replies += [answer(200, {"name": "projects/x/messages/1"})] * 2

    def post(session, url, **kwargs):
        calls.append((url, kwargs, session.trust_env))
        return replies.pop(0)

    monkeypatch.setattr(requests.Session, "post", post)
    payload = {"id": "a" * 32, "expires": 1792000000.5, "silent": False}
    assert transport.send(TOKEN, payload, 299.7) == "sent"
    assert transport.send(TOKEN, payload, 0) == "sent"
    # One token exchange serves both sends.
    assert [call[0] for call in calls].count(OAUTH) == 1
    url, kwargs, trust_env = calls[0]
    assert kwargs["allow_redirects"] is False and not trust_env
    header, claims, signature = kwargs["data"]["assertion"].split(".")
    assert json.loads(decode(header)) == {
        "alg": "RS256",
        "typ": "JWT",
        "kid": "0123456789abcdef0123456789abcdef01234567",
    }
    claims = json.loads(decode(claims))
    assert claims["iss"] == ACCOUNT and claims["scope"] == SCOPE
    assert claims["aud"] == OAUTH and claims["exp"] - claims["iat"] == 3600
    public.verify(
        decode(signature),
        f"{header}.{kwargs['data']['assertion'].split('.')[1]}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    url, kwargs, trust_env = calls[1]
    assert url == f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send"
    assert kwargs["allow_redirects"] is False and not trust_env
    assert kwargs["headers"]["Authorization"] == "Bearer ya29.authored"
    assert kwargs["json"] == {
        "message": {
            "token": TOKEN,
            "data": {"payload": json.dumps(payload)},
            "android": {"priority": "HIGH", "ttl": "299s"},
        }
    }
    assert calls[2][1]["json"]["message"]["android"]["ttl"] == "0s"


def test_fcm_outcomes_retire_retry_or_refuse(fcm, monkeypatch):
    transport, _ = fcm
    exchanges = []
    replies = [
        answer(404),
        failure(400, "UNREGISTERED"),
        failure(403, "SENDER_ID_MISMATCH"),
        failure(400, "INVALID_ARGUMENT"),
        failure(429, "QUOTA_EXCEEDED"),
        failure(503, "UNAVAILABLE"),
        answer(401),
        answer(200),
    ]

    def post(session, url, **kwargs):
        if url == OAUTH:
            exchanges.append(url)
            return answer(200, {"access_token": "ya29.authored", "expires_in": 3599})
        return replies.pop(0)

    monkeypatch.setattr(requests.Session, "post", post)
    outcomes = [transport.send(TOKEN, {"id": "a"}, 60) for _ in range(8)]
    assert outcomes == [
        "expired",
        "expired",
        "expired",
        "refused",
        "retry",
        "retry",
        "retry",
        "sent",
    ]
    # The 401 dropped the cached access token, so the last send minted another.
    assert len(exchanges) == 2


def test_fcm_failures_before_sending_never_reach_the_phone(fcm, monkeypatch):
    transport, _ = fcm
    calls = []

    def refused_exchange(session, url, **kwargs):
        calls.append(url)
        return answer(400, {"error": "invalid_grant"})

    monkeypatch.setattr(requests.Session, "post", refused_exchange)
    assert transport.send(TOKEN, {"id": "a"}, 60) == "retry"
    assert calls == [OAUTH]

    def unreachable(session, url, **kwargs):
        raise requests.ConnectionError()

    monkeypatch.setattr(requests.Session, "post", unreachable)
    assert transport.send(TOKEN, {"id": "a"}, 60) == "retry"
    assert transport.send("not a token", {"id": "a"}, 60) == "refused"


def test_fcm_settings_need_an_exact_project_account_and_key(tmp_path):
    key = tmp_path / "secrets/fcm-key.pem"
    private_write(key, "authored")
    lines = [
        f"ENLOQ_FCM_PROJECT={PROJECT}",
        f"ENLOQ_FCM_ACCOUNT={ACCOUNT}",
        "ENLOQ_FCM_KEY_REF=secrets/fcm-key.pem",
    ]
    private_write(tmp_path / "config/app.env", "\n".join(lines))
    loaded = Settings.load("personal", root=tmp_path)
    assert (loaded.fcm_project, loaded.fcm_account, loaded.fcm_key_id) == (
        PROJECT,
        ACCOUNT,
        "",
    )
    for broken in (
        lines[:2],
        [
            lines[0],
            "ENLOQ_FCM_ACCOUNT=enloq-push@other-project.iam.gserviceaccount.com",
            lines[2],
        ],
        lines + ["ENLOQ_FCM_KEY_ID=not-hex"],
    ):
        private_write(tmp_path / "config/app.env", "\n".join(broken))
        with pytest.raises(Refused):
            Settings.load("personal", root=tmp_path)
