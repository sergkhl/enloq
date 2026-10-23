"""ADR 43 Q8 and IB4: a phone pairs with a code in the Owner Account's Saved
Messages.

The service posts a two-minute code, the six digits alone in monospace (ADR 47
Q2); typing it on the phone issues the device credential and posts a note of
the pairing that names the phone. Five wrong codes
end an attempt, at most five code messages go out an hour, each phone has one
live attempt, every step is bound to the attempt's proof and refused codes
count toward the refusal limit. Codes never reach an answer or a log.
"""

import logging
import re
from contextlib import closing

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser

ORIGIN = "https://enloq.example.com"
PROOF = "X-Pairing-Proof"
CODE = re.compile(r"[0-9]{6}")


@pytest.fixture
def rig(tmp_path):
    app = create_app(
        Settings(
            data=tmp_path,
            listen_host="0.0.0.0",
            public_origin=ORIGIN,
            owner_account=1,
        )
    )
    now = [1_800_000_000.0]
    app.state.browser_auth.clock = lambda: now[0]
    # A phone sends no Origin and keeps no cookies.
    with closing(TestClient(app, base_url=ORIGIN)) as phone:
        yield app, phone, now


def posts(app):
    return app.state.accounts.get(1).transport.saved_posts


def latest_code(app):
    for post in reversed(posts(app)):
        if post["monospace"] and CODE.fullmatch(post["text"] or ""):
            return post["text"]
    raise AssertionError("no code posted")


def start(phone, name="SM-S938B"):
    answer = phone.post("/api/auth/code-pairings", json={"name": name})
    assert answer.status_code == 200, answer.text
    return answer.json()


def enter(phone, attempt, code):
    return phone.post(
        f"/api/auth/code-pairings/{attempt['id']}/code",
        json={"code": code},
        headers={PROOF: attempt["proof"]},
    )


def test_the_code_in_saved_messages_pairs_the_phone_and_is_recorded(rig, caplog):
    app, phone, _ = rig
    caplog.set_level(logging.DEBUG)
    attempt = start(phone)
    assert set(attempt) == {"id", "proof", "expires_at"}
    # Only the code, in monospace, so a tap copies it (ADR 47 Q2).
    message = posts(app)[0]
    assert message["monospace"] and CODE.fullmatch(message["text"])
    code = message["text"]
    # No pairing route serves the code.
    status = phone.get(
        f"/api/auth/pairings/{attempt['id']}", headers={PROOF: attempt["proof"]}
    )
    assert status.status_code == 400 and code not in status.text
    paired = enter(phone, attempt, code)
    assert paired.status_code == 200
    token = paired.json()["token"]
    assert code not in paired.text
    assert (
        phone.get(
            "/api/accounts", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 200
    )
    assert posts(app)[-1]["text"] == "SM-S938B was paired with enloq.example.com."
    trusted = app.state.browser_auth.browsers("")
    assert [(row["label"], row["kind"]) for row in trusted] == [
        ("Enloq app on SM-S938B", "device")
    ]
    # The code is used up with its attempt, and no log line carries it.
    assert enter(phone, attempt, code).status_code == 400
    assert all(code not in record.getMessage() for record in caplog.records)


def test_five_wrong_codes_end_the_attempt(rig):
    app, phone, _ = rig
    attempt = start(phone)
    code = latest_code(app)
    wrong = "000000" if code != "000000" else "111111"
    answers = [enter(phone, attempt, wrong).json()["detail"] for _ in range(5)]
    assert answers == [
        "That code doesn't match. 4 tries left.",
        "That code doesn't match. 3 tries left.",
        "That code doesn't match. 2 tries left.",
        "That code doesn't match. 1 try left.",
        "Too many wrong codes. Start again.",
    ]
    assert enter(phone, attempt, code).json()["detail"] == (
        "This pairing ended. Start again."
    )
    assert app.state.browser_auth.browsers("") == []


def test_an_expired_code_needs_a_new_one(rig):
    app, phone, now = rig
    attempt = start(phone)
    first = latest_code(app)
    now[0] += 121
    assert enter(phone, attempt, first).json()["detail"] == (
        "This code expired. Send a new one."
    )
    resent = phone.post(
        f"/api/auth/code-pairings/{attempt['id']}/resend",
        json={},
        headers={PROOF: attempt["proof"]},
    )
    assert resent.status_code == 200
    assert resent.json()["expires_at"] == now[0] + 120
    second = latest_code(app)
    if second != first:
        assert enter(phone, attempt, first).status_code == 400
    assert enter(phone, attempt, second).status_code == 200


def test_at_most_five_code_messages_go_out_an_hour(rig):
    app, phone, now = rig
    attempt = start(phone, "Phone 1")
    for _ in range(3):
        assert (
            phone.post(
                f"/api/auth/code-pairings/{attempt['id']}/resend",
                json={},
                headers={PROOF: attempt["proof"]},
            ).status_code
            == 200
        )
    start(phone, "Phone 2")
    refused = phone.post("/api/auth/code-pairings", json={"name": "Phone 3"})
    assert refused.json()["detail"] == (
        "Five pairing codes went out in the last hour. Try again later."
    )
    assert len(posts(app)) == 5
    now[0] += 3601
    start(phone, "Phone 3")


def test_a_phone_has_one_live_attempt(rig):
    app, phone, _ = rig
    earlier = start(phone)
    earlier_code = latest_code(app)
    later = start(phone)
    assert enter(phone, earlier, earlier_code).json()["detail"] == (
        "This pairing ended. Start again."
    )
    assert enter(phone, later, latest_code(app)).status_code == 200


def test_every_step_is_bound_to_the_attempts_proof(rig):
    app, phone, _ = rig
    attempt = start(phone)
    code = latest_code(app)
    for proof in ("", "another-proof"):
        stolen = phone.post(
            f"/api/auth/code-pairings/{attempt['id']}/code",
            json={"code": code},
            headers={PROOF: proof} if proof else {},
        )
        assert stolen.json()["detail"] == (
            "This connection attempt belongs to another device"
        )
        assert (
            phone.post(
                f"/api/auth/code-pairings/{attempt['id']}/resend",
                json={},
                headers={PROOF: proof} if proof else {},
            ).status_code
            == 400
        )
    assert enter(phone, attempt, code).status_code == 200


def test_without_the_owner_account_no_code_is_sent(tmp_path):
    for owner, reason in (
        (0, "No Owner Account is set on this server"),
        (777, "The Owner Account is not signed in on this server"),
    ):
        app = create_app(
            Settings(
                data=tmp_path / str(owner),
                listen_host="0.0.0.0",
                public_origin=ORIGIN,
                owner_account=owner,
            )
        )
        with closing(TestClient(app, base_url=ORIGIN)) as phone:
            refused = phone.post("/api/auth/code-pairings", json={"name": "SM"})
            assert refused.json()["detail"] == reason
        assert app.state.accounts.get(1).transport.saved_posts == []


def test_a_failed_code_message_ends_the_attempt(rig):
    app, phone, _ = rig
    app.state.accounts.get(1).transport.saved_failure = "Authored post failure"
    refused = phone.post("/api/auth/code-pairings", json={"name": "SM-S938B"})
    assert refused.json()["detail"] == "Authored post failure"
    assert all(a.state == "ended" for a in app.state.browser_auth._codes.values())


def test_a_trusted_client_does_not_start_a_code_pairing(rig, tmp_path):
    app, _, _ = rig
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        trust_browser(browser, tmp_path)
        refused = browser.post("/api/auth/code-pairings", json={"name": "SM"})
        assert refused.json()["detail"] == (
            "Pair the app without another trust credential"
        )
        assert posts(app) == []


def test_refused_codes_count_toward_the_refusal_limit(rig):
    app, phone, _ = rig
    attempt = start(phone)
    code = latest_code(app)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        assert enter(phone, attempt, wrong).status_code == 400
    for _ in range(26):
        assert enter(phone, {**attempt, "proof": "stolen"}, code).status_code == 400
    limited = enter(phone, attempt, code)
    assert limited.status_code == 429
