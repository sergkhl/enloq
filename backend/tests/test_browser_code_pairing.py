"""ADR 44 Q10 and MW4: a browser pairs with the code in the Owner Account's
Saved Messages, as a phone does (ADR 43 Q8).

The browser's user agent names it in the pairing note. Typing the code sets the
browser's trust cookie, so its token never reaches the page, and posts the
pairing note. The phone's limits hold: two-minute codes and five tries. Each
attempt finishes the way it started, a browser's claim needs its own Origin,
and a browser that is already trusted can't start one.
"""

import re
from contextlib import closing

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser

ORIGIN = "https://enloq.example.com"
PROOF = "X-Pairing-Proof"
AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
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
    with TestClient(
        app, base_url=ORIGIN, headers={"Origin": ORIGIN, "User-Agent": AGENT}
    ) as browser:
        yield app, browser, now


def posts(app):
    return app.state.accounts.get(1).transport.saved_posts


def latest(app):
    # The code alone, in monospace (ADR 47 Q2).
    for post in reversed(posts(app)):
        if post["monospace"] and CODE.fullmatch(post["text"] or ""):
            return post["text"]
    raise AssertionError("no code posted")


def start(client, **body):
    answer = client.post("/api/auth/code-pairings", json={"browser": True, **body})
    assert answer.status_code == 200, answer.text
    return answer.json()


def enter(client, attempt, code, route="browser"):
    return client.post(
        f"/api/auth/code-pairings/{attempt['id']}/{route}",
        json={"code": code},
        headers={PROOF: attempt["proof"]},
    )


def test_the_code_trusts_the_browser_through_its_cookie(rig):
    app, browser, _ = rig
    assert browser.get("/api/auth/session").json() == {
        "authenticated": False,
        "waiting": False,
    }
    attempt = start(browser)
    code = latest(app)
    paired = enter(browser, attempt, code)
    assert paired.status_code == 200
    assert paired.json() == {"authenticated": True, "waiting": False}
    assert app.state.browser_auth.cookie in paired.headers["set-cookie"]
    assert "HttpOnly" in paired.headers["set-cookie"]
    assert browser.get("/api/auth/session").json() == {
        "authenticated": True,
        "waiting": False,
    }
    listed = browser.get("/api/auth/browsers").json()
    assert [(row["kind"], row["label"], row["current"]) for row in listed] == [
        ("browser", "Chromium browser on a Mac", True)
    ]
    assert posts(app)[-1]["text"] == (
        "Chromium browser on a Mac was paired with enloq.example.com."
    )
    # The code is used up with its attempt.
    assert enter(browser, attempt, code).status_code == 400


def test_a_browser_keeps_the_phone_limits(rig):
    app, browser, now = rig
    attempt = start(browser)
    code = latest(app)
    wrong = "000000" if code != "000000" else "111111"
    answers = [enter(browser, attempt, wrong).json()["detail"] for _ in range(5)]
    assert answers[0] == "That code doesn't match. 4 tries left."
    assert answers[-1] == "Too many wrong codes. Start again."
    again = start(browser)
    first = latest(app)
    now[0] += 121
    assert enter(browser, again, first).json()["detail"] == (
        "This code expired. Send a new one."
    )
    assert browser.get("/api/auth/session").json() == {
        "authenticated": False,
        "waiting": False,
    }


def test_each_attempt_finishes_the_way_it_started(rig):
    app, browser, _ = rig
    attempt = start(browser)
    code = latest(app)
    # A browser's attempt never yields a device credential.
    refused = enter(browser, attempt, code, route="code")
    assert refused.status_code == 400 and "token" not in refused.json()
    with closing(TestClient(app, base_url=ORIGIN)) as phone:
        phone_attempt = phone.post(
            "/api/auth/code-pairings", json={"name": "SM-S938B"}
        ).json()
        phone_code = latest(app)
        # Nor does a phone's attempt set a browser cookie.
        assert enter(browser, phone_attempt, phone_code).status_code == 400
        assert enter(phone, phone_attempt, phone_code, route="code").status_code == 200
    assert enter(browser, attempt, code).status_code == 200


def test_a_browser_claim_needs_its_own_origin_and_no_live_trust(rig, tmp_path):
    app, browser, _ = rig
    with closing(TestClient(app, base_url=ORIGIN)) as bare:
        answer = bare.post("/api/auth/code-pairings", json={"browser": True})
        assert answer.status_code == 400
        assert answer.json()["detail"] == "Pair this browser from its own page"
    # A stale cookie, left after the browser was forgotten elsewhere, doesn't
    # block a new pairing.
    browser.cookies.set(app.state.browser_auth.cookie, "stale", path="/api")
    start(browser)
    browser.cookies.clear()
    trust_browser(browser, tmp_path)
    trusted = browser.post("/api/auth/code-pairings", json={"browser": True})
    assert trusted.status_code == 400
    assert trusted.json()["detail"] == "Pair the app without another trust credential"
