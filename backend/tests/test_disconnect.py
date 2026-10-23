"""ADR 43 Q2 and IB3: a trusted client disconnects itself, with Forget's effects.

Its credential ends at once, the logins it started are cancelled, its push
destination retires and the rest of the server's trust is untouched.
"""

import asyncio
from contextlib import closing

from enloq.api.server import create_app
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.login_fixture import SyntheticLoginClient
from backend.tests.test_device_push import ready, rig, turn_on  # noqa: F401
from backend.tests.test_device_trust import ORIGIN, bearer, pair_device


def remote(tmp_path):
    return Settings(data=tmp_path, listen_host="0.0.0.0", public_origin=ORIGIN)


def test_a_disconnected_phone_gets_no_more_pushes(rig):  # noqa: F811
    _, browser, device, hub, conversation, now = rig
    turn_on(device)
    assert device.post("/api/auth/disconnect", json={}).status_code == 200
    assert device.get("/api/accounts").status_code == 401
    ready(conversation, now)
    asyncio.run(hub.tick())
    assert hub.devices.transport.sent == []
    assert hub.registry.rows("SELECT * FROM notification_devices") == []
    # The browser that trusted it stays, and the phone has left its list.
    assert [row["kind"] for row in browser.get("/api/auth/browsers").json()] == [
        "browser"
    ]


def test_a_disconnected_phone_cancels_the_login_it_started(tmp_path):
    app = create_app(remote(tmp_path), login_factory=SyntheticLoginClient)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        trust_browser(browser, tmp_path)
        token = pair_device(app, tmp_path)
        with closing(TestClient(app, base_url=ORIGIN)) as phone:
            login = phone.post(
                "/api/logins",
                json={"method": "phone", "phone": "+12025550003"},
                headers=bearer(token),
            )
            assert login.status_code == 200 and login.json()["stage"] == "code"
            left = phone.post("/api/auth/disconnect", json={}, headers=bearer(token))
            assert left.status_code == 200
            assert phone.get("/api/accounts", headers=bearer(token)).status_code == 401
        assert app.state.logins.flow.stage == "cancelled"
        assert app.state.logins.flow.client is None
        assert browser.get("/api/accounts").status_code == 200


def test_a_browser_disconnects_and_drops_its_cookie(tmp_path):
    app = create_app(remote(tmp_path))
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        trust_browser(browser, tmp_path)
        left = browser.post("/api/auth/disconnect", json={})
        assert left.status_code == 200
        assert left.headers["clear-site-data"] == '"cache"'
        assert browser.get("/api/accounts").status_code == 401


def test_disconnecting_needs_a_trusted_client(tmp_path):
    app = create_app(remote(tmp_path))
    with closing(TestClient(app, base_url=ORIGIN)) as stranger:
        refused = stranger.post("/api/auth/disconnect", json={})
        assert refused.status_code in {401, 403}
