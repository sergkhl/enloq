"""ADR 38: Trusted Devices pair without cookies and send a bearer credential.

ADR 38 Q24 lets an already-trusted browser start and approve a pairing, and
ADR 47 Q1 a trusted phone too; only the Enloq app claims those (ADR 47 Q10).
"""

import asyncio
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from enloq.api.accounts import account_routes
from enloq.api.server import create_app
from enloq.browser_auth import BrowserAuth, device_label, digest
from enloq.config import Settings
from enloq.domain.models import Refused
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser

ORIGIN = "https://enloq.example.com"
PROOF = "X-Pairing-Proof"


def remote(tmp_path, **changes):
    return Settings(
        data=tmp_path, listen_host="0.0.0.0", public_origin=ORIGIN, **changes
    )


def device_client(app):
    # A phone sends no Origin and keeps no cookies.
    return closing(TestClient(app, base_url=ORIGIN))


def pair_device(app, directory, name="iPhone 14 Pro Max"):
    # Nothing pairs a public server from its terminal (ADR 47 Q1); the QR
    # pairing test below covers the protocol, so this fixture issues the
    # device credential a pairing would.
    return app.state.browser_auth._issue(device_label(name), "device")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_a_phone_paired_by_another_phone_reads_with_its_bearer(tmp_path):
    app = create_app(remote(tmp_path, owner_account=1))
    approver = pair_device(app, tmp_path, name="SM-S938B")
    with device_client(app) as owner, device_client(app) as phone:
        started = owner.post(
            "/api/auth/client-pairings", json={}, headers=bearer(approver)
        )
        assert started.status_code == 200, started.text
        identity = started.json()["id"]
        assert started.json()["state"] == "waiting"
        code = owner.get(
            f"/api/auth/client-pairings/{identity}/qr.svg", headers=bearer(approver)
        )
        assert code.status_code == 200 and b"<svg" in code.content
        claimed = phone.post(
            f"/api/auth/pairings/{identity}/device", json={"name": "Pixel 9"}
        )
        assert claimed.status_code == 200
        body = claimed.json()
        assert body["kind"] == "device" and body["approver"] == "browser"
        assert body["label"] == "Enloq app on Pixel 9" and len(body["code"]) == 6
        status = phone.get(
            f"/api/auth/pairings/{identity}", headers={PROOF: body["proof"]}
        )
        assert status.json()["state"] == "pending"
        assert "proof" not in status.json()
        seen = owner.get(
            f"/api/auth/client-pairings/{identity}", headers=bearer(approver)
        ).json()
        assert seen["label"] == "Enloq app on Pixel 9" and seen["code"] == body["code"]
        approved = owner.post(
            f"/api/auth/client-pairings/{identity}",
            json={"action": "approve", "code": body["code"]},
            headers=bearer(approver),
        )
        assert approved.json()["state"] == "approved"
        done = phone.post(
            f"/api/auth/pairings/{identity}/device/complete",
            json={},
            headers={PROOF: body["proof"]},
        )
        assert done.status_code == 200
        token = done.json()["token"]
        session = phone.get("/api/auth/session", headers=bearer(token))
        assert session.json() == {"authenticated": True, "waiting": False}
        assert "set-cookie" not in session.headers
        assert not phone.cookies
    labels = sorted(row["label"] for row in app.state.browser_auth.browsers(""))
    assert labels == ["Enloq app on Pixel 9", "Enloq app on SM-S938B"]
    # Saved Messages records every client that joins (ADR 47 Q2).
    posts = app.state.accounts.get(1).transport.saved_posts
    assert [post["text"] for post in posts] == [
        "Pixel 9 was paired with enloq.example.com."
    ]
    assert not posts[0]["monospace"]


def test_a_public_server_pairs_nothing_from_its_terminal(tmp_path):
    from enloq.config import private_read

    app = create_app(remote(tmp_path))
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        secret = private_read(tmp_path / "bootstrap")
        refused = client.post("/api/auth/pairings", json={"secret": secret})
        assert refused.status_code == 400
        assert "trusted phone or browser" in refused.json()["detail"]
        # The control credential still answers its other host command.
        assert not app.state.browser_auth.browsers("")


def test_cookie_and_bearer_never_stand_in_for_each_other(tmp_path):
    app = create_app(remote(tmp_path))
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        trust_browser(browser, tmp_path)
        auth = app.state.browser_auth
        cookie = browser.cookies.get(auth.cookie)
        token = pair_device(app, tmp_path)
        with device_client(app) as phone:
            as_device = phone.get("/api/accounts", headers=bearer(cookie))
            assert as_device.status_code == 401
            assert as_device.json()["detail"] == auth.device_hint
            phone.cookies.set(auth.cookie, token, domain="enloq.example.com")
            assert phone.get("/api/accounts").status_code == 401
            # One credential per request; malformed headers are refused too.
            assert phone.get("/api/accounts", headers=bearer(token)).status_code == 400
            phone.cookies.clear()
            for header in ("Basic abc", "Bearer ", "Bearer"):
                answer = phone.get("/api/accounts", headers={"Authorization": header})
                assert answer.status_code == 400
            # Origin still has to match when a device sends one.
            wrong = phone.post(
                "/api/auth/client-pairings",
                json={},
                headers={**bearer(token), "Origin": "https://other.example.com"},
            )
            assert wrong.status_code == 403
            plain = phone.post(
                "/api/auth/client-pairings",
                content=b"{}",
                headers={**bearer(token), "Content-Type": "text/plain"},
            )
            assert plain.status_code == 403
        # Browsers keep ADR 11's same-origin rule.
        del browser.headers["Origin"]
        assert browser.post("/api/auth/client-pairings", json={}).status_code == 403
        browser.headers["Origin"] = ORIGIN
        # A browser claim cannot finish as a device, nor the reverse.
        identity = auth.create_pairing().id
        with closing(
            TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        ) as other:
            claimed = other.post(f"/api/auth/pairings/{identity}/claim", json={})
            assert claimed.status_code == 200
            proof = other.cookies.get(auth.pair_cookie + identity)
            auth.decide(identity, "approve", claimed.json()["code"])
            with device_client(app) as phone:
                stolen = phone.post(
                    f"/api/auth/pairings/{identity}/device/complete",
                    json={},
                    headers={PROOF: proof},
                )
                assert stolen.status_code == 400
                late = phone.post(
                    f"/api/auth/pairings/{identity}/device", json={"name": "X"}
                )
                assert late.status_code == 400
        pairing = auth.create_pairing()
        claimed, proof = auth.claim_device(pairing.id, "Pixel")
        auth.decide(pairing.id, "approve", claimed.code)
        with pytest.raises(Refused, match="another browser or device"):
            auth.complete(pairing.id, proof)
        # A browser that is already trusted cannot claim as a device.
        fresh = auth.create_pairing()
        refused = browser.post(f"/api/auth/pairings/{fresh.id}/device", json={})
        assert refused.status_code == 400


def test_a_trusted_browser_pairs_a_device_and_only_it_approves(tmp_path):
    app = create_app(remote(tmp_path))
    with (
        TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as owner,
        closing(TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})) as other,
    ):
        trust_browser(owner, tmp_path)
        trust_browser(other, tmp_path)
        started = owner.post("/api/auth/client-pairings", json={})
        assert started.status_code == 200
        identity = started.json()["id"]
        assert started.json()["approver"] == "browser"
        assert started.json()["state"] == "waiting"
        code = owner.get(f"/api/auth/client-pairings/{identity}/qr.svg")
        assert code.status_code == 200
        assert code.headers["content-type"].startswith("image/svg+xml")
        assert b"<svg" in code.content and code.headers["cache-control"] == "no-store"
        for path in (identity, f"{identity}/qr.svg"):
            assert other.get(f"/api/auth/client-pairings/{path}").status_code == 400
        assert (
            other.post(
                f"/api/auth/client-pairings/{identity}", json={"action": "cancel"}
            ).status_code
            == 400
        )
        with device_client(app) as phone:
            claimed = phone.post(
                f"/api/auth/pairings/{identity}/device", json={"name": "Galaxy S9+"}
            ).json()
            assert claimed["approver"] == "browser"
            seen = owner.get(f"/api/auth/client-pairings/{identity}").json()
            assert seen["state"] == "pending" and seen["kind"] == "device"
            assert seen["label"] == "Enloq app on Galaxy S9+"
            assert seen["code"] == claimed["code"]
            wrong = "111111" if claimed["code"] == "000000" else "000000"
            assert (
                owner.post(
                    f"/api/auth/client-pairings/{identity}",
                    json={"action": "approve", "code": wrong},
                ).status_code
                == 400
            )
            approved = owner.post(
                f"/api/auth/client-pairings/{identity}",
                json={"action": "approve", "code": claimed["code"]},
            )
            assert approved.json()["state"] == "approved"
            done = phone.post(
                f"/api/auth/pairings/{identity}/device/complete",
                json={},
                headers={PROOF: claimed["proof"]},
            )
            token = done.json()["token"]
            assert phone.get("/api/accounts", headers=bearer(token)).status_code == 200
            # The paired phone can pair the next one (ADR 47 Q1).
            assert (
                phone.post(
                    "/api/auth/client-pairings", json={}, headers=bearer(token)
                ).status_code
                == 200
            )
        # A cancelled attempt cannot complete.
        second = owner.post("/api/auth/client-pairings", json={}).json()["id"]
        with device_client(app) as phone:
            claimed = phone.post(
                f"/api/auth/pairings/{second}/device", json={"name": "Pixel"}
            ).json()
            cancelled = owner.post(
                f"/api/auth/client-pairings/{second}", json={"action": "cancel"}
            )
            assert cancelled.json()["state"] == "cancelled"
            assert (
                phone.post(
                    f"/api/auth/pairings/{second}/device/complete",
                    json={},
                    headers={PROOF: claimed["proof"]},
                ).status_code
                == 400
            )
        # Only the Enloq app claims a trusted client's QR code (ADR 47 Q10).
        third = owner.post("/api/auth/client-pairings", json={}).json()["id"]
        with closing(
            TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        ) as laptop:
            refused = laptop.post(f"/api/auth/pairings/{third}/claim", json={})
            assert refused.status_code == 400
            assert refused.json()["detail"] == (
                "This QR code pairs the Enloq app. Open Enloq on the phone and scan it there."
            )
            assert not laptop.cookies
            assert laptop.get("/api/accounts").status_code == 401
        kinds = sorted(row["kind"] for row in owner.get("/api/auth/browsers").json())
        assert kinds == ["browser", "browser", "device"]


def test_forgetting_a_device_ends_its_requests(tmp_path):
    app = create_app(remote(tmp_path))
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as browser:
        trust_browser(browser, tmp_path)
        token = pair_device(app, tmp_path)
        device = next(
            row
            for row in browser.get("/api/auth/browsers").json()
            if row["kind"] == "device"
        )
        assert (
            browser.post(
                f"/api/auth/browsers/{device['id']}/revoke", json={}
            ).status_code
            == 200
        )
        with device_client(app) as phone:
            gone = phone.get("/api/accounts", headers=bearer(token))
            assert gone.status_code == 401 and "Pair it again" in gone.json()["detail"]
        assert browser.get("/api/accounts").status_code == 200


async def test_forgetting_a_device_ends_its_event_stream(tmp_path):
    auth = BrowserAuth(Settings(data=tmp_path))
    pairing = auth.create_pairing()
    pairing, proof = auth.claim_device(pairing.id, "Pixel")
    auth.decide(pairing.id, "approve", pairing.code)
    device_id = auth.authenticate(
        auth.complete(pairing.id, proof, kind="device"), kind="device"
    )
    manager = SimpleNamespace(
        registry=SimpleNamespace(
            accounts=lambda member=None: [], changes=lambda after: ([], 0)
        ),
        runtimes={},
    )
    router = account_routes(manager, None, auth)
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/events")
    response = await endpoint(
        SimpleNamespace(state=SimpleNamespace(local_session=device_id))
    )
    iterator = response.body_iterator
    assert "refresh" in await anext(iterator)
    auth.revoke(device_id)
    assert "revoked" in await asyncio.wait_for(anext(iterator), 1)


def test_earlier_trust_rows_stay_browsers_and_labels_stay_plain(tmp_path):
    path = tmp_path / "browsers.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE browsers (id TEXT PRIMARY KEY, scope TEXT NOT NULL,"
            " token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL,"
            " approved_at REAL NOT NULL, last_access REAL NOT NULL,"
            " revoked INTEGER NOT NULL DEFAULT 0)"
        )
        db.execute(
            "INSERT INTO browsers VALUES ('a',?,?,'Safari on this Mac',1,1,0)",
            ("offline_8765", digest("earlier")),
        )
    path.chmod(0o600)
    auth = BrowserAuth(Settings(data=tmp_path))
    assert auth.authenticate("earlier") == "a"
    assert auth.authenticate("earlier", kind="device") is None
    assert auth.browsers("a")[0]["kind"] == "browser"
    assert device_label("  Pixel\n9\x00 Pro ") == "Enloq app on Pixel 9 Pro"
    assert device_label("\x07") == "Enloq app"
    assert len(device_label("x" * 500)) == len("Enloq app on ") + 60


def test_a_finished_pairing_keeps_its_outcome_past_two_minutes(tmp_path):
    now = [1_800_000_000.0]
    auth = BrowserAuth(Settings(data=tmp_path), clock=lambda: now[0])
    done = auth.create_pairing(owner="approver")
    claimed, proof = auth.claim_device(done.id, "Pixel")
    auth.decide(done.id, "approve", claimed.code)
    auth.complete(done.id, proof, kind="device")
    cancelled = auth.create_pairing(owner="approver")
    auth.decide(cancelled.id, "cancel", "")
    waiting = auth.create_pairing(owner="approver")
    now[0] += 121
    assert auth.pairing(done.id).state == "complete"
    assert auth.pairing(cancelled.id).state == "cancelled"
    assert auth.pairing(waiting.id).state == "expired"
