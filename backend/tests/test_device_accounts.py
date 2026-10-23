"""ADR 43 IB5 and IB6: the Enloq app manages trust and accounts as a browser does.

A phone reads the same trusted list, and its Forget has Forget's effects. A
login belongs to the phone that started it: only that phone reads or answers
it, and forgetting or disconnecting the phone cancels it.
"""

from contextlib import closing

from enloq.api.server import create_app
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.login_fixture import SyntheticLoginClient
from backend.tests.test_device_trust import ORIGIN, bearer, pair_device


def remote(tmp_path):
    return Settings(data=tmp_path, listen_host="0.0.0.0", public_origin=ORIGIN)


def test_a_phone_reads_the_trusted_list_and_forgets_a_browser(tmp_path):
    app = create_app(remote(tmp_path), login_factory=SyntheticLoginClient)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        trust_browser(browser, tmp_path)
        token = pair_device(app, tmp_path, name="SM-S938B")
        started = browser.post(
            "/api/logins", json={"method": "phone", "phone": "+12025550004"}
        )
        assert started.json()["stage"] == "code"
        with closing(TestClient(app, base_url=ORIGIN)) as phone:
            listed = phone.get("/api/auth/browsers", headers=bearer(token)).json()
            assert [(row["kind"], row["current"]) for row in listed] == [
                ("browser", False),
                ("device", True),
            ]
            assert listed[1]["label"] == "Enloq app on SM-S938B"
            assert listed == [
                {**row, "current": not row["current"]}
                for row in browser.get("/api/auth/browsers").json()
            ]
            forgotten = phone.post(
                f"/api/auth/browsers/{listed[0]['id']}/revoke",
                json={},
                headers=bearer(token),
            )
            assert forgotten.status_code == 200
            assert browser.get("/api/accounts").status_code == 401
            assert app.state.logins.flow.stage == "cancelled"
            # The phone keeps its own access.
            assert phone.get("/api/accounts", headers=bearer(token)).status_code == 200


def test_a_phone_adds_an_account_through_its_own_login(tmp_path):
    app = create_app(remote(tmp_path), login_factory=SyntheticLoginClient)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        trust_browser(browser, tmp_path)
        token = pair_device(app, tmp_path)
        with closing(TestClient(app, base_url=ORIGIN)) as phone:
            started = phone.post(
                "/api/logins",
                json={"method": "phone", "phone": "+12025550005"},
                headers=bearer(token),
            )
            assert started.status_code == 200 and started.json()["stage"] == "code"
            flow = started.json()["id"]
            # Only the phone that started it reads or answers it.
            assert browser.get(f"/api/logins/{flow}").status_code == 400
            assert (
                browser.post(
                    f"/api/logins/{flow}/code", json={"value": "12345"}
                ).status_code
                == 400
            )
            signed = phone.post(
                f"/api/logins/{flow}/code",
                json={"value": "12345"},
                headers=bearer(token),
            )
            assert signed.json()["stage"] == "complete"
            assert signed.json()["account_id"] == 5
            accounts = phone.get("/api/accounts", headers=bearer(token)).json()
            assert 5 in [row["account_id"] for row in accounts["accounts"]]


def test_forgetting_a_phone_cancels_the_login_it_started(tmp_path):
    app = create_app(remote(tmp_path), login_factory=SyntheticLoginClient)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        trust_browser(browser, tmp_path)
        token = pair_device(app, tmp_path)
        with closing(TestClient(app, base_url=ORIGIN)) as phone:
            started = phone.post(
                "/api/logins",
                json={"method": "phone", "phone": "+12025550006"},
                headers=bearer(token),
            )
            assert started.json()["stage"] == "code"
            device = next(
                row
                for row in browser.get("/api/auth/browsers").json()
                if row["kind"] == "device"
            )
            browser.post(f"/api/auth/browsers/{device['id']}/revoke", json={})
            assert app.state.logins.flow.stage == "cancelled"
            assert app.state.logins.flow.client is None
