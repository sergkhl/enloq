"""ADR 48 MB3: Join Requests, the Member Agreement and leaving.

Someone the server doesn't know signs in with Telegram, accepts the Member
Agreement and waits: their account sits unused and their client can only
wait. The owner's phones get a push that opens People, where the owner
approves or declines. Declining, withdrawing or a week without a decision
deletes everything, five requests wait at most, and a member's home account
proves who they are on a new client. A member may leave and the owner may
remove one, each signing the server out and deleting everything but spending.
"""

import time
from contextlib import closing

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain import agreement
from enloq.domain.models import ProcessingPaused, Refused
from enloq.storage.members import OWNER
from fastapi.testclient import TestClient

from backend.tests.login_fixture import SyntheticLoginClient
from backend.tests.test_device_trust import bearer

ORIGIN = "https://enloq.example.com"
PROOF = "X-Pairing-Proof"
FCM = "eAuthoredFcmOwner01:APA91b" + "Q" * 120


@pytest.fixture(autouse=True)
def fresh_logins():
    SyntheticLoginClient.saved.clear()
    SyntheticLoginClient.instances.clear()
    yield
    SyntheticLoginClient.saved.clear()
    SyntheticLoginClient.instances.clear()


def server(tmp_path):
    # Account 1, the offline fixture's Demo account, is the Owner Account.
    return create_app(
        Settings(
            data=tmp_path,
            listen_host="0.0.0.0",
            public_origin=ORIGIN,
            owner_account=1,
            fcm_project="enloq-test",
        ),
        login_factory=SyntheticLoginClient,
    )


def phone_client(app):
    return closing(TestClient(app, base_url=ORIGIN))


def sign_in(phone, number, name="Pixel 8"):
    attempt = phone.post(
        "/api/auth/sign-in-pairings", json={"phone": number, "name": name}
    ).json()
    response = phone.post(
        f"/api/auth/sign-in-pairings/{attempt['id']}/code",
        json={"value": "12345"},
        headers={PROOF: attempt["proof"]},
    )
    return attempt, response


def ask(phone, number="+1 202 555 0123", name="Pixel 8", version=agreement.VERSION):
    """A stranger signs in, reads the agreement and asks to join."""
    attempt, asked = sign_in(phone, number, name)
    assert asked.json()["login"]["stage"] == "agreement", asked.text
    return phone.post(
        f"/api/auth/sign-in-pairings/{attempt['id']}/agree",
        json={"version": version},
        headers={PROOF: attempt["proof"]},
    )


def owner_device(app):
    auth = app.state.browser_auth
    token = auth._issue("Enloq app on SM-S938B", "device")
    app.state.notifications.devices.enable(auth.client(token, kind="device")["id"], FCM)
    return token


def waiting_member(app):
    rows = app.state.accounts.registry.rows(
        "SELECT * FROM members WHERE state='waiting'"
    )
    return rows[-1]


def test_a_stranger_asks_to_join_and_waits_unused(tmp_path):
    app = server(tmp_path)
    owner = owner_device(app)
    with phone_client(app) as phone:
        asked = ask(phone)
        assert asked.status_code == 200, asked.text
        body = asked.json()
        assert body["waiting"] is True and body["token"]
        friend = body["token"]
        member = waiting_member(app)
        assert (member["home_account"], member["name"], member["phone"]) == (
            23,
            "Demo 23",
            "••• 0123",
        )
        assert member["agreed_version"] == agreement.VERSION
        # The account is held for the request but never started.
        registry = app.state.accounts.registry
        assert registry.member_of(23) == member["id"]
        assert 23 not in app.state.accounts.runtimes
        # The waiting client can only wait.
        session = phone.get("/api/auth/session", headers=bearer(friend)).json()
        assert session == {"authenticated": True, "waiting": True}
        me = phone.get("/api/members/me", headers=bearer(friend)).json()
        assert (me["role"], me["state"], me["agreement"]) == ("member", "waiting", None)
        for path in ("/api/accounts", "/api/settings", "/api/auth/browsers"):
            refused = phone.get(path, headers=bearer(friend))
            assert refused.status_code == 403, path
            assert refused.json() == {"detail": "Waiting for the owner's approval"}
        # The owner's phone got the push, which opens People.
        devices = app.state.notifications.devices
        rows = registry.rows(
            "SELECT * FROM device_deliveries WHERE subject IS NOT NULL"
        )
        assert [row["subject"] for row in rows] == [member["id"]]
        event = devices.event(rows[0])
        assert (event["kind"], event["text"]) == ("join", "Demo 23 asks to join Enloq")
        owner_id = app.state.browser_auth.client(owner, kind="device")["id"]
        assert devices.target(owner_id, rows[0]["id"]) == {"kind": "people"}
        # People lists the request for the owner alone.
        people = phone.get("/api/people", headers=bearer(owner)).json()
        assert [r["name"] for r in people["requests"]] == ["Demo 23"]
        assert people["members"] == []


def test_approval_starts_the_members_account(tmp_path):
    app = server(tmp_path)
    owner = owner_device(app)
    with phone_client(app) as phone:
        friend = ask(phone).json()["token"]
        member = waiting_member(app)["id"]
        assert phone.get("/api/people", headers=bearer(friend)).status_code == 403
        approved = phone.post(
            f"/api/people/{member}/approve", json={}, headers=bearer(owner)
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["requests"] == []
        (person,) = approved.json()["members"]
        assert (person["name"], person["accounts"]) == ("Demo 23", 1)
        assert person["budget"]["limit"] == 500_000_000
        assert 23 in app.state.accounts.runtimes
        accounts = phone.get("/api/accounts", headers=bearer(friend)).json()
        assert [a["account_id"] for a in accounts["accounts"]] == [23]
        # The push is withdrawn once the request no longer waits.
        rows = app.state.accounts.registry.rows(
            "SELECT * FROM device_deliveries WHERE subject IS NOT NULL"
        )
        assert app.state.notifications.devices.event(rows[0]) is None


def test_declining_deletes_the_request(tmp_path):
    app = server(tmp_path)
    owner = owner_device(app)
    with phone_client(app) as phone:
        friend = ask(phone).json()["token"]
        member = waiting_member(app)
        session = app.state.accounts.registry.get(23)["session"]
        declined = phone.post(
            f"/api/people/{member['id']}/decline", json={}, headers=bearer(owner)
        )
        assert declined.status_code == 200
        assert declined.json() == {"requests": [], "members": []}
        assert phone.get("/api/auth/session", headers=bearer(friend)).json() == {
            "authenticated": False,
            "waiting": False,
        }
    registry = app.state.accounts.registry
    assert registry.member(member["id"]) is None
    assert registry.rows("SELECT 1 FROM accounts WHERE account_id=23") == []
    assert not (tmp_path / session).parent.exists()


def test_withdrawing_from_the_waiting_client_deletes_the_request(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as phone:
        friend = ask(phone).json()["token"]
        member = waiting_member(app)["id"]
        assert (
            phone.post(
                "/api/auth/disconnect", json={}, headers=bearer(friend)
            ).status_code
            == 200
        )
    assert app.state.accounts.registry.member(member) is None


async def test_a_request_expires_after_a_week(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as phone:
        ask(phone)
    manager = app.state.accounts
    member = waiting_member(app)["id"]
    await manager.expire_requests()
    assert manager.registry.member(member) is not None
    with manager.registry.transaction() as db:
        db.execute(
            "UPDATE members SET requested_at=? WHERE id=?",
            (time.time() - 7 * 86400 - 1, member),
        )
    await manager.expire_requests()
    assert manager.registry.member(member) is None
    assert manager.registry.rows("SELECT 1 FROM accounts WHERE account_id=23") == []


def test_five_requests_wait_at_most(tmp_path):
    app = server(tmp_path)
    now = [time.time()]
    app.state.browser_auth.clock = lambda: now[0]
    with phone_client(app) as phone:
        for index in range(5):
            assert (
                ask(phone, f"+1 202 555 01{30 + index}", f"Phone {index}").status_code
                == 200
            )
        now[0] += 3601  # Past the hourly limit on sign-ins.
        refused = ask(phone, "+1 202 555 0140", "Phone 5")
        assert refused.status_code == 400
        assert refused.json()["detail"] == (
            "Enloq isn't taking new requests right now. Try again later."
        )
    last = SyntheticLoginClient.instances[-1]
    assert last.requests[-1] == "LogOutRequest" and not last.authorized
    assert (
        app.state.accounts.registry.rows("SELECT 1 FROM accounts WHERE account_id=40")
        == []
    )


def test_a_stale_agreement_version_is_refused(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as phone:
        refused = ask(phone, version="1999-01-01")
        assert refused.status_code == 400
        assert refused.json()["detail"] == "The agreement changed. Read it again."
    assert (
        app.state.accounts.registry.rows("SELECT * FROM members WHERE state='waiting'")
        == []
    )


def test_a_members_home_account_proves_who_they_are_on_a_new_client(tmp_path):
    app = server(tmp_path)
    owner = owner_device(app)
    with phone_client(app) as phone:
        ask(phone)
        member = waiting_member(app)["id"]
        phone.post(f"/api/people/{member}/approve", json={}, headers=bearer(owner))
        session = app.state.accounts.registry.get(23)["session"]
        _, paired = sign_in(phone, "+1 202 555 0123", "Moto g")
        assert paired.status_code == 200, paired.text
        token = paired.json()["token"]
        assert paired.json()["waiting"] is False
        assert app.state.browser_auth.client(token, kind="device")["member"] == member
        # The new login is signed out; the account keeps its own session.
        assert SyntheticLoginClient.instances[-1].requests[-1] == "LogOutRequest"
        assert app.state.accounts.registry.get(23)["session"] == session
    # A member's pairing never reaches the owner's Saved Messages.
    assert not [
        post
        for post in app.state.accounts.get(1).transport.saved_posts
        if "Moto g" in (post["text"] or "")
    ]


def test_another_account_a_member_holds_cannot_sign_them_in(tmp_path):
    app = server(tmp_path)
    registry = app.state.accounts.registry
    with registry.transaction() as db:
        db.execute(
            "INSERT INTO members (id,role,state,home_account,agreed_version,approved_at) VALUES ('friend','member','active',24,?,1)",
            (agreement.VERSION,),
        )
    registry.register(25, "authorizations/second/telegram.session", member="friend")
    with phone_client(app) as phone:
        _, refused = sign_in(phone, "+1 202 555 0125")
        assert refused.status_code == 400
        assert refused.json()["detail"] == (
            "Sign in with the first Telegram account you used here."
        )


def test_a_member_leaves_and_everything_goes_but_spending(tmp_path):
    app = server(tmp_path)
    owner = owner_device(app)
    with phone_client(app) as phone:
        friend = ask(phone).json()["token"]
        member = waiting_member(app)["id"]
        phone.post(f"/api/people/{member}/approve", json={}, headers=bearer(owner))
        registry = app.state.accounts.registry
        with registry.transaction() as db:
            db.execute(
                "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created,family,member) VALUES (23,'spent','s','r','i','day:2026-10-17','mimo',0,5,'receipted',1,'daily',?)",
                (member,),
            )
        unconfirmed = phone.post(
            "/api/members/me/leave", json={"confirm": False}, headers=bearer(friend)
        )
        assert unconfirmed.status_code == 400
        left = phone.post(
            "/api/members/me/leave", json={"confirm": True}, headers=bearer(friend)
        )
        assert left.status_code == 200 and left.json() == {"confirmed": True}
        assert phone.get("/api/accounts", headers=bearer(friend)).status_code == 401
        assert (
            phone.post(
                "/api/members/me/leave", json={"confirm": True}, headers=bearer(owner)
            ).status_code
            == 400
        )
    assert registry.member(member) is None
    assert registry.rows("SELECT 1 FROM accounts WHERE account_id=23") == []
    assert 23 not in app.state.accounts.runtimes
    assert registry.rows("SELECT member FROM model_reservations") == [
        {"member": member}
    ]


def test_the_owner_removes_a_member(tmp_path):
    app = server(tmp_path)
    owner = owner_device(app)
    with phone_client(app) as phone:
        friend = ask(phone).json()["token"]
        member = waiting_member(app)["id"]
        phone.post(f"/api/people/{member}/approve", json={}, headers=bearer(owner))
        assert (
            phone.post(
                f"/api/people/{member}/remove",
                json={"confirm": True},
                headers=bearer(friend),
            ).status_code
            == 403
        )
        removed = phone.post(
            f"/api/people/{member}/remove",
            json={"confirm": True},
            headers=bearer(owner),
        )
        assert removed.json() == {"confirmed": True}
        assert phone.get("/api/accounts", headers=bearer(friend)).status_code == 401
        assert (
            phone.post(
                f"/api/people/{OWNER}/remove",
                json={"confirm": True},
                headers=bearer(owner),
            ).status_code
            == 400
        )


def test_a_changed_agreement_holds_ai_work_until_the_member_accepts(
    tmp_path, monkeypatch
):
    app = server(tmp_path)
    owner = owner_device(app)
    with phone_client(app) as phone:
        friend = ask(phone).json()["token"]
        member = waiting_member(app)["id"]
        phone.post(f"/api/people/{member}/approve", json={}, headers=bearer(owner))
        manager = app.state.accounts
        manager.admit_ai(23)
        monkeypatch.setattr(agreement, "VERSION", "2026-12-01")
        with pytest.raises(ProcessingPaused):
            manager.admit_ai(23)
        manager.admit_ai(1)  # The owner never sees it.
        me = phone.get("/api/members/me", headers=bearer(friend)).json()
        assert me["agreement"]["version"] == "2026-12-01"
        stale = phone.post(
            "/api/members/me/agreement",
            json={"version": "2026-10-17"},
            headers=bearer(friend),
        )
        assert stale.status_code == 400
        accepted = phone.post(
            "/api/members/me/agreement",
            json={"version": "2026-12-01"},
            headers=bearer(friend),
        )
        assert accepted.json()["agreement"] is None
        manager.admit_ai(23)


def test_several_people_sign_in_at_once(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as first, phone_client(app) as second:
        one = first.post(
            "/api/auth/sign-in-pairings", json={"phone": "+1 202 555 0150", "name": "A"}
        )
        two = second.post(
            "/api/auth/sign-in-pairings", json={"phone": "+1 202 555 0151", "name": "B"}
        )
        assert one.status_code == two.status_code == 200
        assert one.json()["login"]["stage"] == two.json()["login"]["stage"] == "code"


def test_a_sign_in_never_starts_on_a_server_without_an_owner_account(tmp_path):
    app = create_app(
        Settings(data=tmp_path, listen_host="0.0.0.0", public_origin=ORIGIN),
        login_factory=SyntheticLoginClient,
    )
    with phone_client(app) as phone:
        refused = phone.post(
            "/api/auth/sign-in-pairings", json={"phone": "+1 202 555 0123"}
        )
        assert refused.json()["detail"] == "No Owner Account is set on this server"


def test_only_a_waiting_request_is_approved_or_declined(tmp_path):
    app = server(tmp_path)
    owner = owner_device(app)
    with phone_client(app) as phone:
        for path in ("approve", "decline"):
            unknown = phone.post(
                f"/api/people/nobody/{path}", json={}, headers=bearer(owner)
            )
            assert unknown.status_code == 400
    with pytest.raises(Refused):
        app.state.accounts.admit_request("1999-01-01")


def test_the_fixture_seeds_and_decides_requests(tmp_path):
    from scripts.members_fixture import register

    app = server(tmp_path)
    register(app)
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        seeded = client.post("/fixture/requests", json={"name": "Authored Anna"})
        assert seeded.status_code == 200
        member = seeded.json()["member"]
        assert waiting_member(app)["name"] == "Authored Anna"
        assert client.post("/fixture/approve", json={}).json() == {"member": member}
        assert app.state.accounts.registry.member(member)["state"] == "active"
        assert client.post("/fixture/decline", json={}).status_code == 404


def test_the_same_stranger_asking_twice_at_once_gets_one_request(tmp_path):
    app = server(tmp_path)
    with phone_client(app) as first, phone_client(app) as second:
        one, asked_one = sign_in(first, "+1 202 555 0160", "Phone A")
        two, asked_two = sign_in(second, "+1 202 555 0160", "Phone B")
        assert asked_one.json()["login"]["stage"] == "agreement"
        assert asked_two.json()["login"]["stage"] == "agreement"
        agreed = first.post(
            f"/api/auth/sign-in-pairings/{one['id']}/agree",
            json={"version": agreement.VERSION},
            headers={PROOF: one["proof"]},
        )
        assert agreed.status_code == 200
        again = second.post(
            f"/api/auth/sign-in-pairings/{two['id']}/agree",
            json={"version": agreement.VERSION},
            headers={PROOF: two["proof"]},
        )
        assert again.status_code == 400
        assert again.json()["detail"] == (
            "This account already asked to join. Sign in again to wait here too."
        )
    rows = app.state.accounts.registry.rows(
        "SELECT * FROM members WHERE home_account=60"
    )
    assert len(rows) == 1


# The independent review's findings (MB6): removal races, sign-in limits and
# the label shared by two people's phones.


async def test_nothing_attaches_to_a_member_who_is_leaving(tmp_path):
    from types import SimpleNamespace

    app = server(tmp_path)
    manager = app.state.accounts
    with manager.registry.transaction() as db:
        db.execute(
            "INSERT INTO members (id,role,state,home_account,agreed_version,approved_at) VALUES ('friend','member','leaving',24,?,1)",
            (agreement.VERSION,),
        )
    user = SimpleNamespace(
        id=26, first_name="Late", last_name="", username="", phone=""
    )
    with pytest.raises(Refused, match="leaving"):
        await manager.activate(
            user, "authorizations/late/telegram.session", member="friend"
        )
    assert manager.registry.member_of(26) is None
    assert manager.sign_in_decision(24) == (
        "refuse",
        None,
        "This person is leaving Enloq.",
    )
    # A removal a restart interrupted resumes in the retention pass.
    await manager.expire_requests()
    assert manager.registry.member("friend") is None


async def test_an_expiry_never_removes_a_request_approved_meanwhile(tmp_path):
    app = server(tmp_path)
    owner = owner_device(app)
    with phone_client(app) as phone:
        ask(phone)
        member = waiting_member(app)["id"]
        phone.post(f"/api/people/{member}/approve", json={}, headers=bearer(owner))
    manager = app.state.accounts
    with manager.registry.transaction() as db:
        db.execute("UPDATE members SET requested_at=0 WHERE id=?", (member,))
    assert await manager.remove_member(member, expiring=True) is True
    assert manager.registry.member(member)["state"] == "active"


def test_one_address_starts_at_most_five_sign_ins_an_hour(tmp_path):
    app = server(tmp_path)
    auth = app.state.browser_auth
    for index in range(5):
        auth.start_sign_in(
            f"Phone {index}", phone=f"+1202555{index:04d}", address="198.51.100.7"
        )
    with pytest.raises(Refused, match="Five sign-ins"):
        auth.start_sign_in("Phone 5", phone="+12025550099", address="198.51.100.7")
    auth.start_sign_in("Phone 6", phone="+12025550098", address="198.51.100.8")


def test_a_phone_model_shared_by_two_people_ends_neither_sign_in(tmp_path):
    app = server(tmp_path)
    auth = app.state.browser_auth
    first, _ = auth.start_sign_in("SM-A546B", phone="+12025550170")
    second, _ = auth.start_sign_in("SM-A546B", phone="+12025550171")
    assert auth.earlier_sign_ins(second) == []
    again, _ = auth.start_sign_in("SM-A546B", phone="+1 202 555 0170")
    assert auth.earlier_sign_ins(again) == [first]


def test_sign_in_again_names_only_the_members_own_accounts(tmp_path):
    from backend.tests.test_members import add_member

    app = server(tmp_path)
    friend = add_member(app, account=27)
    with phone_client(app) as phone:
        refused = phone.post(
            "/api/logins",
            json={"method": "qr", "expected_id": 1},
            headers=bearer(friend),
        )
        assert refused.status_code == 400
        assert refused.json() == {
            "detail": "Sign in to this account to access its workspace"
        }


async def test_strangers_sign_ins_never_hold_up_add_account(tmp_path):
    from enloq.login import PAIRING_FLOWS

    app = server(tmp_path)
    logins = app.state.logins
    auth = app.state.browser_auth
    logins.owner_active = lambda owner: True
    for index in range(PAIRING_FLOWS):
        state = await logins.begin(
            f"sign-in:stranger-{index}",
            "phone",
            f"+1202555{80 + index:04d}",
            pairing=True,
        )
        assert state["stage"] == "code"
    with pytest.raises(Refused, match="Too many sign-ins"):
        await logins.begin("sign-in:one-more", "phone", "+12025550199", pairing=True)
    client = auth.client(auth._issue("Safari on a Mac", "browser"))["id"]
    added = await logins.begin(client, "phone", "+12025550191")
    assert added["stage"] == "code"
    await logins.close()


async def test_a_slow_login_holds_up_no_one_else(tmp_path):
    import asyncio

    release = asyncio.Event()

    class Slow(SyntheticLoginClient):
        blocking = True

        async def connect(self):
            if Slow.blocking:
                Slow.blocking = False
                await release.wait()
            await super().connect()

    app = server(tmp_path)
    logins = app.state.logins
    logins.factory = Slow
    logins.owner_active = lambda owner: True
    slow = asyncio.create_task(
        logins.begin("sign-in:slow", "phone", "+12025550193", pairing=True)
    )
    await asyncio.sleep(0.05)
    quick = await asyncio.wait_for(
        logins.begin("sign-in:quick", "phone", "+12025550194", pairing=True), 2
    )
    assert quick["stage"] == "code" and not slow.done()
    release.set()
    assert (await slow)["stage"] == "code"
    await logins.close()


async def test_one_removal_runs_for_a_member_at_once(tmp_path):
    import asyncio

    app = server(tmp_path)
    manager = app.state.accounts
    from backend.tests.test_members import add_member

    add_member(app, account=28)
    release = asyncio.Event()
    logouts = []

    async def slow_logout(identity):
        logouts.append(identity)
        await release.wait()

    manager.logout = slow_logout
    first = asyncio.create_task(manager.remove_member("friend"))
    await asyncio.sleep(0.05)
    assert manager.registry.member("friend")["state"] == "leaving"
    # The owner's second Remove and the retention pass wait for the first.
    second = asyncio.create_task(manager.remove_member("friend"))
    retention = asyncio.create_task(manager.expire_requests())
    await asyncio.sleep(0.05)
    assert not second.done() and not retention.done()
    release.set()
    assert await first is True and await second is True
    await retention
    assert logouts == [28]
    assert manager.registry.member("friend") is None
    assert manager.removals == {}


def test_a_login_finishing_while_its_member_leaves_signs_out_at_once(tmp_path):
    from backend.tests.test_members import add_member

    app = server(tmp_path)
    friend = add_member(app, account=29)
    with phone_client(app) as phone:
        login = phone.post(
            "/api/logins",
            json={"method": "phone", "phone": "+1 202 555 0130"},
            headers=bearer(friend),
        ).json()
        assert login["stage"] == "code"
        # Their removal has begun; the client is about to be forgotten.
        registry = app.state.accounts.registry
        with registry.transaction() as db:
            db.execute("UPDATE members SET state='leaving' WHERE id='friend'")
        ended = phone.post(
            f"/api/logins/{login['id']}/code",
            json={"value": "12345"},
            headers=bearer(friend),
        ).json()
        assert ended["stage"] == "error"
        assert ended["message"] == "This person is leaving Enloq."
    client = SyntheticLoginClient.instances[-1]
    assert "LogOutRequest" in client.requests and not client.authorized
    assert registry.member_of(30) is None
    assert registry.rows("SELECT * FROM pending_logins") == []
