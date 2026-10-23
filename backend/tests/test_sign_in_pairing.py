"""ADR 47 Q11, Q13, Q15 and ADR 48 Q11, Q12: a phone or browser pairs by
signing in with Telegram, and the account Telegram verifies decides what pairs.

The sign-in runs through the service's own login. A signed-out Owner Account's
new login becomes its session; a signed-in one proves who it is, its new login
signed out at once. An unknown account reads the Member Agreement (its Join
Request is covered in test_join_requests.py). Anyone may start one, at most
five times an hour, only on a server with an Owner Account, and every step is
bound to the attempt's proof. Connect answers 409 while the Owner Account is
signed out, so clients know to offer the sign-in.
"""

from contextlib import closing

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.login_fixture import SyntheticLoginClient

ORIGIN = "https://enloq.example.com"
PROOF = "X-Pairing-Proof"
AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# The synthetic login signs in the account its number ends in.
OWNER_NUMBER = "+1 202 555 0111"


@pytest.fixture(autouse=True)
def fresh_logins():
    SyntheticLoginClient.saved.clear()
    SyntheticLoginClient.instances.clear()
    yield
    SyntheticLoginClient.saved.clear()
    SyntheticLoginClient.instances.clear()


def server(tmp_path, owner=11):
    # Account 11 is not on the server yet: a fresh install's Owner Account.
    return create_app(
        Settings(
            data=tmp_path,
            listen_host="0.0.0.0",
            public_origin=ORIGIN,
            owner_account=owner,
        ),
        login_factory=SyntheticLoginClient,
    )


def phone_client(app):
    # A phone sends no Origin and keeps no cookies.
    return closing(TestClient(app, base_url=ORIGIN))


def start(client, number=OWNER_NUMBER, **body):
    return client.post(
        "/api/auth/sign-in-pairings", json={"phone": number, "name": "SM-S938B", **body}
    )


def step(client, attempt, action, value=""):
    return client.post(
        f"/api/auth/sign-in-pairings/{attempt['id']}/{action}",
        json={"value": value} if value else {},
        headers={PROOF: attempt["proof"]},
    )


def owner_posts(app, owner=11):
    return app.state.accounts.get(owner).transport.saved_posts


def test_connect_says_the_owner_account_is_signed_out(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as phone:
        refused = phone.post("/api/auth/code-pairings", json={"name": "SM-S938B"})
        assert refused.status_code == 409
        assert refused.json()["detail"] == (
            "The Owner Account is not signed in on this server"
        )


def test_signing_the_owner_account_in_pairs_the_phone_and_restores_it(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as phone:
        started = start(phone)
        assert started.status_code == 200, started.text
        attempt = started.json()
        assert attempt["login"]["stage"] == "code"
        # Telegram's code length lets the field submit itself (Q3).
        assert attempt["login"]["code_length"] == 5
        assert "token" not in attempt or not attempt["token"]
        wrong = step(phone, attempt, "code", "54321")
        assert wrong.status_code == 400
        assert wrong.json()["detail"] == (
            "That code or password was not accepted. Check it and try again."
        )
        paired = step(phone, attempt, "code", "12345")
        assert paired.status_code == 200, paired.text
        body = paired.json()
        assert body["login"]["stage"] == "complete"
        assert body["login"]["account_id"] == 11
        token = body["token"]
        session = phone.get(
            "/api/auth/session", headers={"Authorization": f"Bearer {token}"}
        )
        assert session.json() == {"authenticated": True, "waiting": False}
        accounts = phone.get(
            "/api/accounts", headers={"Authorization": f"Bearer {token}"}
        ).json()["accounts"]
        assert [(a["account_id"], a["state"]) for a in accounts] == [
            (1, "authorized"),
            (11, "authorized"),
        ]
        # The attempt is used up.
        assert step(phone, attempt, "code", "12345").status_code == 400
    trusted = app.state.browser_auth.browsers("")
    assert [(row["label"], row["kind"]) for row in trusted] == [
        ("Enloq app on SM-S938B", "device")
    ]
    # The Owner Account is back, so it records the pairing (ADR 47 Q2).
    assert [post["text"] for post in owner_posts(app)] == [
        "SM-S938B was paired with enloq.example.com."
    ]


def test_an_unknown_account_reads_the_agreement_and_cancelling_signs_it_out(
    tmp_path,
):
    app = server(tmp_path)
    with phone_client(app) as phone:
        attempt = start(phone, "+1 202 555 0112").json()
        asked = step(phone, attempt, "code", "12345")
        assert asked.status_code == 200, asked.text
        assert asked.json()["login"]["stage"] == "agreement"
        assert asked.json()["agreement"]["version"]
        assert not asked.json().get("token")
        assert step(phone, attempt, "cancel").status_code == 200
    (client,) = SyntheticLoginClient.instances
    assert client.requests[-1] == "LogOutRequest" and not client.authorized
    assert app.state.browser_auth.browsers("") == []
    assert (
        app.state.accounts.registry.rows("SELECT 1 FROM accounts WHERE account_id=12")
        == []
    )


def test_a_two_step_password_follows_the_code(tmp_path):
    app = server(tmp_path, owner=3)
    with phone_client(app) as phone:
        attempt = start(phone, "+1 202 555 0103").json()
        asked = step(phone, attempt, "code", "12345")
        assert asked.status_code == 200
        assert asked.json()["login"]["stage"] == "password"
        assert "token" not in asked.json() or not asked.json()["token"]
        wrong = step(phone, attempt, "password", "guess")
        assert wrong.status_code == 400
        done = step(phone, attempt, "password", "synthetic-password")
        assert done.status_code == 200
        assert done.json()["login"]["stage"] == "complete" and done.json()["token"]


def test_a_browser_gets_its_cookie_and_needs_its_own_origin(tmp_path):
    app = server(tmp_path)
    with TestClient(
        app, base_url=ORIGIN, headers={"Origin": ORIGIN, "User-Agent": AGENT}
    ) as browser:
        attempt = start(browser, browser=True).json()
        with phone_client(app) as elsewhere:
            stolen = step(elsewhere, attempt, "code", "12345")
            assert stolen.status_code in {400, 403}
        paired = step(browser, attempt, "code", "12345")
        assert paired.status_code == 200
        assert paired.json()["authenticated"] is True
        assert not paired.json().get("token")
        assert app.state.browser_auth.cookie in paired.headers["set-cookie"]
        assert browser.get("/api/auth/session").json() == {
            "authenticated": True,
            "waiting": False,
        }
    (row,) = app.state.browser_auth.browsers("")
    assert row["kind"] == "browser" and row["label"] == "Chromium browser on a Mac"


def test_a_signed_in_owner_account_proves_who_it_is(tmp_path):
    app = server(tmp_path, owner=1)
    session = app.state.accounts.registry.get(1)["session"]
    with phone_client(app) as phone:
        attempt = start(phone, "+1 202 555 0101").json()
        paired = step(phone, attempt, "code", "12345")
        assert paired.status_code == 200, paired.text
        token = paired.json()["token"]
        auth = app.state.browser_auth
        assert auth.client(token, kind="device")["member"] == "owner"
    (client,) = SyntheticLoginClient.instances
    # The new login is signed out at once; the account keeps its session.
    assert client.requests[-1] == "LogOutRequest" and not client.authorized
    assert app.state.accounts.registry.get(1)["session"] == session


def test_without_an_owner_account_nothing_starts(tmp_path):
    app = server(tmp_path, owner=0)
    with phone_client(app) as phone:
        refused = start(phone)
        assert refused.json()["detail"] == "No Owner Account is set on this server"
    assert SyntheticLoginClient.instances == []


def test_a_signed_out_owner_account_signs_back_in(tmp_path):
    app = server(tmp_path)
    registry = app.state.accounts.registry
    registry.register(11, "authorizations/old/telegram.session")
    with registry.transaction() as db:
        db.execute(
            "UPDATE accounts SET state='requires_login', phone='••• 5511' WHERE account_id=11"
        )
    with phone_client(app) as phone:
        attempt = start(phone, "+1 202 555 0111")
        assert attempt.status_code == 200, attempt.text
        assert step(phone, attempt.json(), "code", "12345").status_code == 200
    assert registry.get(11)["state"] == "authorized"


def test_at_most_five_sign_ins_start_an_hour(tmp_path):
    app = server(tmp_path)
    now = [1_800_000_000.0]
    app.state.browser_auth.clock = lambda: now[0]
    with phone_client(app) as phone:
        for index in range(5):
            attempt = start(phone, name=f"Phone {index}").json()
            assert step(phone, attempt, "cancel").status_code == 200
        refused = start(phone, name="Phone 5")
        assert refused.json()["detail"] == (
            "Five sign-ins started in the last hour. Try again later."
        )
        # The same client address is limited too, whatever the number
        # (ADR 48 Q11); another number from elsewhere would start.
        other = start(phone, "+1 202 555 0123", name="Phone 6")
        assert other.json()["detail"] == (
            "Five sign-ins started in the last hour. Try again later."
        )
        auth = app.state.browser_auth
        assert auth.start_sign_in(
            "Elsewhere", phone="+12025550123", address="203.0.113.9"
        )
        now[0] += 3601
        assert start(phone, name="Phone 5").status_code == 200


def test_the_server_caps_sign_ins_across_numbers(tmp_path):
    from enloq.browser_auth import SIGN_IN_SERVER_STARTS

    app = server(tmp_path)
    auth = app.state.browser_auth
    for index in range(SIGN_IN_SERVER_STARTS):
        auth.start_sign_in(f"Phone {index}", phone=f"+1202555{index:04d}")
    with pytest.raises(Exception, match="Too many people are signing in"):
        auth.start_sign_in("Phone X", phone="+12025559999")


def test_starting_again_ends_the_phones_earlier_sign_in(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as phone:
        earlier = start(phone).json()
        later = start(phone).json()
        assert step(phone, earlier, "code", "12345").json()["detail"] == (
            "This sign-in ended. Start again."
        )
        assert step(phone, later, "code", "12345").status_code == 200


def test_every_step_is_bound_to_the_attempts_proof(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as phone:
        attempt = start(phone).json()
        for proof in ("", "another-proof"):
            stolen = step(phone, {**attempt, "proof": proof}, "code", "12345")
            assert stolen.json()["detail"] == (
                "This connection attempt belongs to another device"
            )
        assert step(phone, attempt, "code", "12345").status_code == 200


def test_a_trusted_client_does_not_start_one(tmp_path):
    from backend.tests.auth_helpers import trust_browser

    app = server(tmp_path)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        trust_browser(browser, tmp_path)
        refused = start(browser, browser=True)
        assert refused.json()["detail"] == (
            "Pair the app without another trust credential"
        )
    assert SyntheticLoginClient.instances == []


def test_refused_steps_count_toward_the_refusal_limit(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as phone:
        attempt = start(phone).json()
        for _ in range(29):
            assert (
                step(phone, {**attempt, "proof": "stolen"}, "code", "12345").status_code
                == 400
            )
        assert step(phone, attempt, "code", "54321").status_code == 400
        assert step(phone, attempt, "code", "12345").status_code == 429
