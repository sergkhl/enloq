import asyncio
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from enloq.api.accounts import account_routes
from enloq.api.server import create_app
from enloq.application.accounts import AccountManager
from enloq.browser_auth import COOKIE_AGE, BrowserAuth
from enloq.config import Settings, private_read
from enloq.domain.models import Refused
from enloq.launcher import confirm_browser, open_browser, pair_browser
from enloq.login import LoginManager
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.login_fixture import SyntheticLoginClient

ORIGIN = "http://127.0.0.1:8765"


def start_pairing(client, settings):
    client.headers["Origin"] = ORIGIN
    secret = private_read(settings.data / "bootstrap")
    created = client.post("/api/auth/pairings", json={"secret": secret})
    assert created.status_code == 200
    return f"/api/auth/pairings/{created.json()['id']}", secret


def test_A16_pairing_requires_launcher_and_exact_browser_and_cannot_replay(tmp_path):
    settings = Settings(data=tmp_path)
    app = create_app(settings)
    with (
        TestClient(app, base_url=ORIGIN) as first,
        closing(TestClient(app, base_url=ORIGIN)) as second,
    ):
        assert not first.get("/api/auth/session").json()["authenticated"]
        assert first.get("/api/accounts").status_code == 401
        first.headers["Origin"] = ORIGIN
        assert first.post("/api/accounts/1/send", json={}).status_code == 401
        assert (
            first.post("/api/bootstrap", json={"secret": "retired"}).status_code == 401
        )
        prefix, secret = start_pairing(first, settings)
        second.headers["Origin"] = ORIGIN
        claim = first.post(prefix + "/claim", json={})
        assert claim.status_code == 200
        assert second.post(prefix + "/claim", json={}).status_code == 400
        assert second.get(prefix).status_code == 400
        assert first.post(prefix + "/complete", json={}).status_code == 400
        assert (
            first.post(
                prefix + "/control",
                json={
                    "secret": "wrong",
                    "action": "approve",
                    "code": claim.json()["code"],
                },
            ).status_code
            == 400
        )
        assert (
            first.post(
                prefix + "/control",
                json={"secret": secret, "action": "approve", "code": "bad"},
            ).status_code
            == 400
        )
        assert (
            first.post(
                prefix + "/control",
                json={
                    "secret": secret,
                    "action": "approve",
                    "code": claim.json()["code"],
                },
            ).status_code
            == 200
        )
        assert second.post(prefix + "/complete", json={}).status_code == 400
        # Keep the original pending cookie to attempt an actual consumed replay.
        pending = first.cookies.get(
            app.state.browser_auth.pair_cookie + prefix.rsplit("/", 1)[1]
        )
        completed = first.post(prefix + "/complete", json={})
        assert completed.status_code == 200
        assert "HttpOnly" in completed.headers["set-cookie"]
        assert "SameSite=strict" in completed.headers["set-cookie"]
        assert f"Max-Age={COOKIE_AGE}" in completed.headers["set-cookie"]
        replay = first.post(
            prefix + "/complete",
            json={},
            headers={
                "Cookie": f"{app.state.browser_auth.pair_cookie}{prefix.rsplit('/', 1)[1]}={pending}"
            },
        )
        assert replay.status_code == 400
        assert first.get("/api/accounts").status_code == 200
        assert second.get("/api/accounts").status_code == 401
        assert len(first.get("/api/auth/browsers").json()) == 1
        assert not app.state.assistant.transport.deliveries


def test_A16_cancel_expiry_restart_and_same_browser_concurrent_attempts(tmp_path):
    auth = BrowserAuth(Settings(data=tmp_path), clock=lambda: 100)
    one, two = auth.create_pairing(), auth.create_pairing()
    claim1, proof1 = auth.claim(one.id, "", "Chrome/1", None)
    claim2, proof2 = auth.claim(two.id, "", "Safari/1", None)
    auth.decide(one.id, "approve", claim1.code)
    with pytest.raises(Refused):
        auth.complete(one.id, proof2)
    auth.decide(one.id, "cancel", "")
    with pytest.raises(Refused):
        auth.complete(one.id, proof1)
    auth.decide(two.id, "approve", claim2.code)
    auth.clock = lambda: 221
    with pytest.raises(Refused):
        auth.complete(two.id, proof2)
    assert auth.pairing(two.id).state == "expired"
    restarted = BrowserAuth(Settings(data=tmp_path))
    with pytest.raises(Refused):
        restarted.pairing(two.id)
    assert restarted.browsers("") == []


def test_A16_trust_survives_restart_and_arbitrary_time_without_losing_drafts(tmp_path):
    settings = Settings(data=tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as first:
        trust_browser(first, tmp_path)
        cookie = first.cookies.get(app.state.browser_auth.cookie)
        identity = app.state.browser_auth.authenticate(cookie)
        response = first.post(
            "/api/accounts/1/chats/user:101/draft",
            json={
                "revision": 0,
                "text": "Authored recovery draft",
            },
        )
        assert response.status_code == 200
        pending, _ = start_pairing(first, settings)
    restarted = create_app(settings)
    restarted.state.browser_auth.clock = lambda: 100_000_000_000
    with TestClient(restarted, base_url=ORIGIN) as second:
        second.cookies.set(restarted.state.browser_auth.cookie, cookie)
        assert second.get("/api/auth/session").json()["authenticated"]
        assert restarted.state.browser_auth.authenticate(cookie) == identity
        assert (
            second.get("/api/accounts/1/chats/user:101").json()["draft"]["text"]
            == "Authored recovery draft"
        )
        assert second.get(pending).status_code == 400
        other_scope = BrowserAuth(Settings(data=tmp_path, port=8776))
        assert other_scope.authenticate(cookie) is None
        with sqlite3.connect(tmp_path / "browsers.sqlite3") as db:
            saved = json.dumps(db.execute("SELECT * FROM browsers").fetchall())
        assert bool(cookie not in saved)
        assert (tmp_path / "browsers.sqlite3").stat().st_mode & 0o777 == 0o600


def test_A16_revoke_cancels_login_but_preserves_accounts_drafts_and_other_browser(
    tmp_path,
):
    app = create_app(Settings(data=tmp_path), login_factory=SyntheticLoginClient)
    with TestClient(app, base_url=ORIGIN) as first:
        second = TestClient(app, base_url=ORIGIN)
        try:
            trust_browser(first, tmp_path)
            trust_browser(second, tmp_path)
            first_id = next(
                b["id"] for b in first.get("/api/auth/browsers").json() if b["current"]
            )
            login = first.post(
                "/api/logins", json={"method": "phone", "phone": "+12025550003"}
            )
            assert login.status_code == 200 and login.json()["stage"] == "code"
            assert (
                first.post(
                    "/api/accounts/1/chats/user:101/draft",
                    json={
                        "revision": 0,
                        "text": "Keep this authored draft",
                    },
                ).status_code
                == 200
            )
            assert (
                second.post(
                    f"/api/auth/browsers/{first_id}/revoke", json={}
                ).status_code
                == 200
            )
            assert first.get("/api/accounts").status_code == 401
            assert not first.get("/api/auth/session").json()["authenticated"]
            assert first.post("/api/accounts/1/send", json={}).status_code == 401
            assert app.state.logins.flow.stage == "cancelled"
            assert app.state.logins.flow.client is None
            assert (
                second.get("/api/accounts").json()["accounts"][0]["state"]
                == "authorized"
            )
            assert (
                second.get("/api/accounts/1/chats/user:101").json()["draft"]["text"]
                == "Keep this authored draft"
            )
            assert not app.state.assistant.transport.deliveries
        finally:
            second.close()


async def test_A16_open_event_stream_ends_after_revocation(tmp_path):
    auth = BrowserAuth(Settings(data=tmp_path))
    pairing = auth.create_pairing()
    pairing, proof = auth.claim(pairing.id, "", "", None)
    auth.decide(pairing.id, "approve", pairing.code)
    browser_id = auth.authenticate(auth.complete(pairing.id, proof))
    manager = SimpleNamespace(
        registry=SimpleNamespace(
            accounts=lambda member=None: [], changes=lambda after: ([], 0)
        ),
        runtimes={},
    )
    router = account_routes(manager, None, auth)
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/events")
    response = await endpoint(
        SimpleNamespace(state=SimpleNamespace(local_session=browser_id))
    )
    iterator = response.body_iterator
    assert "refresh" in await anext(iterator)
    auth.revoke(browser_id)

    assert "revoked" in await asyncio.wait_for(anext(iterator), 1)
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


async def test_A16_revocation_during_login_promotion_cannot_add_an_account(tmp_path):
    settings = Settings(data=tmp_path)
    auth = BrowserAuth(settings)
    pairing = auth.create_pairing()
    pairing, proof = auth.claim(pairing.id, "", "", None)
    auth.decide(pairing.id, "approve", pairing.code)
    owner = auth.authenticate(auth.complete(pairing.id, proof))
    manager = AccountManager(settings)
    logins = LoginManager(
        manager, client_factory=SyntheticLoginClient, owner_active=auth.active
    )
    flow = await logins.begin(owner, "phone", "+12025550002")
    client = logins.flow.client
    try:
        # Account lifecycle work delays promotion after Telegram has signed in.
        async with manager.lifecycle:
            pending = asyncio.create_task(
                logins.submit(flow["id"], owner, "code", "12345")
            )
            async with asyncio.timeout(3):
                while not client.closed:
                    await asyncio.sleep(0.01)
            auth.revoke(owner)
        with pytest.raises(Refused, match="no longer trusted"):
            await pending
        await logins.cancel_owner(owner)
        assert {row["account_id"] for row in manager.registry.accounts()} == {1}
        # Keep the interrupted authorization discoverable until Telegram logout
        # is confirmed; do not silently discard a still-authorized session file.
        assert manager.registry.rows("SELECT id FROM pending_logins")
        await logins.recover()
        assert not manager.registry.rows("SELECT id FROM pending_logins")
        assert client.path not in SyntheticLoginClient.saved
    finally:
        await logins.close()
        await manager.close()


def test_A16_mac_dialog_safe_arguments_and_cancel_timeout(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0, stdout="button returned:Trust browser, gave up:false"
        )

    monkeypatch.setattr("enloq.launcher.subprocess.run", run)
    assert confirm_browser("123456", 25)
    assert calls[0][0] == ["/usr/bin/osascript", "-"]
    assert 'default button "Cancel"' in calls[0][1]["input"]
    with pytest.raises(Refused):
        confirm_browser('" malicious script', 1)
    monkeypatch.setattr(
        "enloq.launcher.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=""),
    )
    assert not confirm_browser("123456", 1)


def test_A16_launcher_uses_control_channel_and_public_locator(
    tmp_path, monkeypatch, capsys
):
    app = create_app(Settings(data=tmp_path))
    real_client = TestClient(app, base_url=ORIGIN)
    browser = TestClient(app, base_url=ORIGIN)
    captured = []
    monkeypatch.setattr("enloq.launcher.httpx.Client", lambda **kwargs: real_client)
    real_client.headers["Origin"] = ORIGIN
    browser.headers["Origin"] = ORIGIN

    def open_url(argv, **kwargs):
        captured.append(argv)
        identity = argv[-1].split("connect=")[1]
        assert (
            browser.post(f"/api/auth/pairings/{identity}/claim", json={}).status_code
            == 200
        )

    monkeypatch.setattr("enloq.launcher.subprocess.run", open_url)

    # Complete from the browser once the launcher has approved it.
    def complete(seconds):
        identity = captured[0][-1].split("connect=")[1]
        assert (
            browser.post(f"/api/auth/pairings/{identity}/complete", json={}).status_code
            == 200
        )

    monkeypatch.setattr("enloq.launcher.time.sleep", complete)
    pair_browser(
        tmp_path,
        8765,
        browser="Safari",
        confirm=lambda code, seconds, claimant: len(code) == 6,
    )
    secret = private_read(tmp_path / "bootstrap")
    assert captured[0][:3] == ["/usr/bin/open", "-a", "Safari"]
    assert bool(secret not in json.dumps(captured) + capsys.readouterr().out)
    assert browser.get("/api/auth/session").json()["authenticated"]
    browser.close()


def test_A16_explicit_launcher_mode_does_not_open_installed_personal_mode(
    tmp_path, monkeypatch
):
    from enloq.config import private_write

    private_write(
        tmp_path / ".local/service/installed.json",
        json.dumps({"mode": "personal", "port": 8765}),
    )
    calls = []
    monkeypatch.setattr(
        "enloq.launcher.pair_browser",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    open_browser(tmp_path, mode="offline", port=8776, browser="Safari")
    assert calls[0][0] == (tmp_path / ".local/offline", 8776)
    assert calls[0][1] == {"browser": "Safari"}
