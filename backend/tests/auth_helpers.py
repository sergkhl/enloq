"""Exercise the real pairing protocol against authored TestClient fixtures."""

from urllib.parse import urlsplit

from enloq.config import private_read


def trust_browser(client, directory):
    client.headers["Origin"] = str(client.base_url).rstrip("/")
    auth = client.app.state.browser_auth
    if auth.remote:
        # Nothing pairs a public server from its terminal (ADR 47 Q1); the
        # pairing tests cover the code and QR paths, so this fixture issues
        # the trust a pairing would.
        token = auth._issue("Chromium browser on Linux", "browser")
        client.cookies.set(
            auth.cookie, token, domain=urlsplit(str(client.base_url)).hostname
        )
        return None
    secret = private_read(directory / "bootstrap")
    created = client.post("/api/auth/pairings", json={"secret": secret})
    assert created.status_code == 200
    prefix = f"/api/auth/pairings/{created.json()['id']}"
    claimed = client.post(prefix + "/claim", json={})
    assert claimed.status_code == 200
    if claimed.json()["state"] == "already_trusted":
        return
    approved = client.post(
        prefix + "/control",
        json={
            "secret": secret,
            "action": "approve",
            "code": claimed.json()["code"],
        },
    )
    assert approved.status_code == 200
    completed = client.post(prefix + "/complete", json={})
    assert completed.status_code == 200
    return completed
