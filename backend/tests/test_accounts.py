import asyncio
import json
from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.application.accounts import AccountManager
from enloq.config import Settings, private_read, private_write
from enloq.domain.models import Refused
from enloq.login import LoginAdmission, LoginManager
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import Store
from enloq.transport.onboarding import OnboardingRefused
from fastapi.testclient import TestClient
from telethon import functions, types

from backend.tests.auth_helpers import trust_browser
from backend.tests.login_fixture import SyntheticLoginClient

# These cases exercise the rewrite path with text (ADR 54 keeps it for
# voice notes, images and calming).
pytestmark = pytest.mark.usefixtures("text_rewriting")


def user(identity):
    return SimpleNamespace(
        id=identity,
        bot=False,
        first_name=f"Demo {identity}",
        last_name="",
        username="",
        phone="",
    )


@pytest.fixture
def manager(tmp_path):
    return AccountManager(Settings(data=tmp_path / "offline"))


async def test_accounts_isolate_identical_peers_drafts_and_delivery(manager):
    await manager.start()
    try:
        await manager.activate(user(2), "synthetic-2.session")
        one, two = manager.get(1), manager.get(2)
        assert one.store.path != two.store.path
        assert {m.message_id for m in one.store.messages("1/user:101")} == {
            m.message_id for m in two.store.messages("2/user:101")
        }
        c1 = one.store.mutate_draft(
            "1/user:101", 0, lambda d: d.update(text="Only account one")
        )
        c2 = two.store.mutate_draft(
            "2/user:101", 0, lambda d: d.update(text="Only account two")
        )
        with pytest.raises(Refused):
            await two.delivery.send("1/user:101", c1["revision"])
        sent = await one.delivery.send(c1["chat_key"], c1["revision"])
        assert sent["account_id"] == 1 and sent["state"] == "confirmed"
        assert not two.transport.deliveries
        assert two.store.draft(c2["chat_key"])["text"] == "Only account two"
        assert one.rewriting.work.capacity is two.rewriting.work.capacity
    finally:
        await manager.close()


def test_legacy_migration_registers_in_place_and_does_not_reappear(tmp_path):
    Store(tmp_path / "assistant.sqlite3", account_id=77, mode="personal")
    session = tmp_path / "personal.session"
    private_write(session, "synthetic-canary")
    registry = AccountRegistry(tmp_path)
    settings = Settings(
        mode="personal",
        data=tmp_path,
        refs={"EXPECTED_ACCOUNT_ID": "77", "PERSONAL_SESSION_REF": str(session)},
    )
    registry.migrate_legacy(settings)
    registry.migrate_legacy(settings)
    entry = registry.get(77)
    assert entry["directory"] == "." and entry["session"] == "personal.session"
    assert private_read(session) == "synthetic-canary"
    with registry.transaction() as db:
        db.execute("DELETE FROM accounts")
    registry.migrate_legacy(settings)
    assert registry.accounts() == []


async def test_logout_restore_retains_work_remove_requires_resolved_delivery(manager):
    await manager.start()
    await manager.activate(user(2), "synthetic.session")
    assistant = manager.get(2)
    c = assistant.store.mutate_draft(
        "2/user:101", 0, lambda d: d.update(text="Retain me")
    )
    assistant.transport.lose_receipt = True
    op = await assistant.delivery.send(c["chat_key"], c["revision"])
    assert op["state"] == "unknown"
    await manager.logout(2)
    with pytest.raises(Refused):
        manager.get(2)
    with pytest.raises(Refused, match="deliveries"):
        await manager.remove(2)
    await manager.activate(user(2), "new-synthetic.session")
    # The uncertain submission keeps its exact content across logout.
    kept = manager.get(2).store.send_operations.get(op["id"])
    assert kept["state"] == "unknown" and kept["text"] == "Retain me"
    manager.get(2).store.send_operations.refuse(op["id"], "Authored confirmed refusal")
    await manager.logout(2)
    await manager.remove(2)
    assert [a["account_id"] for a in manager.registry.accounts()] == [1]
    await manager.close()


async def test_logout_resolves_a_prepared_send_and_removal_ignores_one(manager):
    await manager.start()
    await manager.activate(user(2), "synthetic.session")
    assistant = manager.get(2)
    c = assistant.store.mutate_draft(
        "2/user:101", 0, lambda d: d.update(text="Never sent")
    )
    operation = assistant.store.send_operations.prepare(
        c["chat_key"], c["revision"], snapshot=assistant.attachments.snapshot
    )
    await manager.logout(2)
    store = manager._store(manager.registry.get(2))
    [row] = store.rows(
        "SELECT state,body FROM operations WHERE id=?", (operation["id"],)
    )
    assert row["state"] == "refused"
    assert json.loads(row["body"])["cause"] == "not_sent"
    # A send that may have reached Telegram still blocks removal.
    with store.transaction() as db:
        db.execute(
            "UPDATE operations SET state='dispatching' WHERE id=?", (operation["id"],)
        )
    with pytest.raises(Refused, match="deliveries"):
        await manager.remove(2)
    # One left prepared by an older version never reached the transport.
    with store.transaction() as db:
        db.execute(
            "UPDATE operations SET state='prepared' WHERE id=?", (operation["id"],)
        )
    store.close()
    await manager.remove(2)
    assert [a["account_id"] for a in manager.registry.accounts()] == [1]
    await manager.close()


async def test_removing_legacy_root_clears_media_pins_and_preserves_other_account(
    manager,
):
    await manager.start()
    try:
        await manager.activate(user(2), "synthetic-2.session")
        other = manager.get(2)
        authored = other.store.mutate_draft(
            "2/user:101", 0, lambda draft: draft.update(text="Other account draft")
        )
        await manager.logout(1)
        retained = manager.settings.data / "selected-media" / "authored.bin"
        private_write(retained, "Authored local media sentinel")
        registry = manager.registry.path
        await manager.remove(1)
        assert not retained.parent.exists()
        assert registry.is_file()
        assert manager.get(2) is other
        assert other.store.draft(authored["chat_key"]) == authored
        assert [entry["account_id"] for entry in manager.registry.accounts()] == [2]
    finally:
        await manager.close()


async def test_a_restart_resolves_a_send_a_crash_left_prepared(tmp_path, manager):
    await manager.start()
    assistant = manager.get(1)
    c = assistant.store.mutate_draft(
        "1/user:101", 0, lambda d: d.update(text="Never sent")
    )
    operation = assistant.store.send_operations.prepare(
        c["chat_key"], c["revision"], snapshot=assistant.attachments.snapshot
    )
    await manager.close()
    # The process was killed between prepare and claim, skipping the drain.
    with assistant.store.transaction() as db:
        db.execute(
            "UPDATE operations SET state='prepared',body=? WHERE id=?",
            (json.dumps(operation), operation["id"]),
        )
    assistant.store.close()
    restarted = AccountManager(Settings(data=tmp_path / "offline"))
    await restarted.start()
    try:
        resolved = restarted.get(1).store.send_operations.get(operation["id"])
        assert resolved["state"] == "refused" and resolved["cause"] == "not_sent"
    finally:
        await restarted.close()


async def test_phone_login_resend_wrong_code_password_and_secret_retention(manager):
    logins = LoginManager(manager, client_factory=SyntheticLoginClient)
    state = await logins.begin("browser", "phone", "+12025550003")
    assert state["stage"] == "code" and state["delivery"] == "telegram_app"
    state = await logins.submit(state["id"], "browser", "resend")
    assert state["delivery"] == "sms" and not state["can_resend"]
    with pytest.raises(Refused):
        await logins.submit(state["id"], "browser", "resend")
    state = await logins.submit(state["id"], "browser", "code", "wrong")
    assert state["stage"] == "code"
    state = await logins.submit(state["id"], "browser", "code", "12345")
    assert state["stage"] == "password"
    client = logins.flow.client
    state = await logins.submit(
        state["id"], "browser", "password", "synthetic-password"
    )
    assert state["stage"] == "complete" and state["account_id"] == 3 and client.closed
    assert manager.get(3).store.account_id == 3
    assert not manager.registry.rows("SELECT * FROM pending_logins")
    for secret in ("12345", "synthetic-password", "synthetic-hash", "+12025550003"):
        assert secret not in json.dumps(state)
        assert secret.encode() not in manager.registry.path.read_bytes()
    await logins.close()
    await manager.close()


async def test_qr_listener_precedes_image_refreshes_and_cancel_releases_owner(manager):
    class FastQR(SyntheticLoginClient):
        qr_seconds = 0.02

    logins = LoginManager(manager, client_factory=FastQR)
    state = await logins.begin("browser", "qr")
    # Another browser's login runs beside it (ADR 48) but can't read this one.
    other = await logins.begin("other-browser", "phone", "+12025550002")
    assert other["id"] != state["id"]
    await logins.cancel(other["id"], "other-browser")
    with pytest.raises(Refused):
        logins.get(state["id"], "other-browser")
    flow = logins.get(state["id"], "browser")
    for _ in range(100):
        await asyncio.sleep(0.01)
        if flow.qr_revision >= 2:
            break
    assert flow.qr_revision >= 2
    assert flow.client.listening and flow.qr.startswith(b"\x89PNG")
    client = flow.client
    await logins.cancel(state["id"], "browser")
    assert client.closed and not flow.qr
    assert not manager.registry.rows("SELECT id FROM pending_logins")
    await logins.close()
    await manager.close()


async def test_qr_success_duplicate_and_recovery_do_not_replace_live_session(manager):
    logins = LoginManager(manager, client_factory=SyntheticLoginClient)
    await logins.begin("browser", "qr")
    for _ in range(100):
        if logins.flow.qr:
            break
        await asyncio.sleep(0.01)
    logins.flow.client.user_id = 2
    logins.flow.client.scan.set()
    await logins.flow.task
    assert logins.flow.stage == "complete"
    original = manager.registry.get(2)["session"]
    duplicate = await logins.begin("browser", "phone", "+12025550002")
    await logins.submit(duplicate["id"], "browser", "code", "12345")
    assert manager.registry.get(2)["session"] == original
    # A crash after registration and before clearing pending metadata cannot
    # open or revoke the session now owned by the runtime.
    with manager.registry.transaction() as db:
        db.execute(
            "INSERT INTO pending_logins VALUES ('recovery',?,NULL,1)", (original,)
        )
    before = len(SyntheticLoginClient.instances)
    await logins.recover()
    assert len(SyntheticLoginClient.instances) == before
    await logins.close()
    await manager.close()


def test_account_http_requires_explicit_scope_and_redacts_validation(tmp_path):
    app = create_app(Settings(data=tmp_path), login_factory=SyntheticLoginClient)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path)
        assert client.post("/api/composer", json={}).status_code == 404
        login = client.post(
            "/api/logins", json={"method": "phone", "phone": "+12025550002"}
        ).json()
        assert (
            client.post(
                f"/api/logins/{login['id']}/code", json={"value": "12345"}
            ).json()["stage"]
            == "complete"
        )
        assert len(client.get("/api/accounts").json()["accounts"]) == 2
        assert client.get("/api/accounts/2/chats/user:999").status_code == 400
        assert (
            client.post(
                "/api/accounts/2/chats/user:999/draft",
                json={"revision": 0, "text": "wrong account"},
            ).status_code
            == 400
        )
        canary = "synthetic-sensitive-value"
        invalid = client.post("/api/logins", json={"method": canary})
        assert invalid.status_code == 422 and canary not in invalid.text
        assert client.post("/api/accounts/2/logout", json={}).status_code == 200
        assert client.get("/api/accounts/2/chats").status_code == 400
        assert client.get("/api/accounts/1/chats").status_code == 200


def test_login_admission_cannot_read_chats_or_approve_another_owner():
    admission = LoginAdmission()
    for request in (
        functions.messages.GetHistoryRequest(
            types.InputPeerSelf(), 0, None, 0, 1, 0, 0, 0
        ),
        functions.auth.AcceptLoginTokenRequest(b"synthetic"),
        functions.messages.SendMessageRequest(
            types.InputPeerSelf(), "authored", random_id=1
        ),
    ):
        with pytest.raises(OnboardingRefused):
            admission(request)


async def test_login_expiry_wrong_identity_and_restart_never_resend(manager):
    logins = LoginManager(manager, client_factory=SyntheticLoginClient)
    state = await logins.begin("browser", "phone", "+12025550007")
    original = logins.flow.client
    await logins.close()
    restarted = LoginManager(manager, client_factory=SyntheticLoginClient)
    await restarted.recover()
    assert original.requests == ["send_code"]
    assert not manager.registry.rows("SELECT id FROM pending_logins")
    assert not (manager.settings.data / logins.flow.session).exists()
    await manager.activate(user(7), "synthetic-seven.session")
    await manager.logout(7)
    state = await restarted.begin("browser", "phone", "+12025550008", expected_id=7)
    state = await restarted.submit(state["id"], "browser", "code", "12345")
    assert state["stage"] == "error"
    assert manager.registry.get(7)["state"] == "logged_out"
    assert not manager.registry.rows("SELECT 1 FROM accounts WHERE account_id=8")
    await restarted.recover()
    state = await restarted.begin("browser", "phone", "+12025550007", expected_id=7)
    restarted.flow.expires_at = 0
    with pytest.raises(Refused, match="ended"):
        await restarted.submit(state["id"], "browser", "code", "12345")
    await restarted.cancel(state["id"], "browser", expired=True)
    assert restarted.flow.stage == "expired"
    await restarted.close()
    await manager.close()


def test_personal_api_can_start_before_first_telegram_login(tmp_path):
    app = create_app(Settings(mode="personal", data=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path)
        overview = client.get("/api/accounts").json()
        assert overview["accounts"] == [] and overview["login_available"]
        assert client.get("/health").status_code == 200


async def test_uncertain_remote_logout_stays_paused_and_retry_checks_identity(
    manager, monkeypatch, tmp_path
):
    from dataclasses import replace

    from enloq import setup_login

    private_write(tmp_path / "api-id", "1")
    private_write(tmp_path / "api-hash", "synthetic")
    manager.settings = replace(
        manager.settings,
        mode="personal",
        refs={
            "TELEGRAM_API_ID_REF": str(tmp_path / "api-id"),
            "TELEGRAM_API_HASH_REF": str(tmp_path / "api-hash"),
        },
    )
    entry = manager.registry.get(1)
    path = manager.registry.path_for(entry["session"])
    private_write(path, "Synthetic session")
    SyntheticLoginClient.saved[str(path)] = 1

    class Uncertain(SyntheticLoginClient):
        async def __call__(self, request):
            await super().__call__(request)
            raise TimeoutError()

    monkeypatch.setattr(setup_login, "PersonalLoginClient", Uncertain)
    with pytest.raises(Refused, match="unconfirmed"):
        await manager.logout(1)
    assert manager.registry.get(1)["state"] == "logout_pending"
    with pytest.raises(Refused):
        manager.get(1)
    with pytest.raises(Refused):
        await manager.remove(1)
    monkeypatch.setattr(setup_login, "PersonalLoginClient", SyntheticLoginClient)
    await manager.logout(1)
    assert manager.registry.get(1)["state"] == "logged_out"
    assert not path.exists()


async def test_actual_telethon_qr_wait_handles_migration_before_activation(manager):
    from datetime import UTC, datetime, timedelta

    from telethon.client.auth import AuthMethods

    class MigratingClient(SyntheticLoginClient):
        api_id = 1
        api_hash = "synthetic"
        qr_login = AuthMethods.qr_login

        def add_event_handler(self, handler, event):
            self.handler = handler
            self.listening = True

        def remove_event_handler(self, handler):
            self.handler = None

        async def _switch_dc(self, dc):
            self.requests.append(f"migrate:{dc}")

        async def _on_login(self, user):
            self.authorized = self.user_id = user.id

        async def __call__(self, request):
            if isinstance(request, functions.auth.ExportLoginTokenRequest):
                self.requests.append("export")
                if self.requests.count("export") == 1:
                    return types.auth.LoginToken(
                        datetime.now(UTC) + timedelta(seconds=30), b"synthetic"
                    )
                return types.auth.LoginTokenMigrateTo(3, b"synthetic-migration")
            if isinstance(request, functions.auth.ImportLoginTokenRequest):
                self.requests.append("import")
                return types.auth.LoginTokenSuccess(
                    types.auth.Authorization(types.User(id=9))
                )
            return await super().__call__(request)

    logins = LoginManager(manager, client_factory=MigratingClient)
    await logins.begin("browser", "qr")
    for _ in range(100):
        await asyncio.sleep(0.01)
        if logins.flow.qr:
            break
    client = logins.flow.client
    assert client.listening
    await client.handler(None)
    await logins.flow.task
    assert logins.flow.stage == "complete" and logins.flow.account_id == 9
    assert client.requests == ["export", "export", "migrate:3", "import"]
    assert client.closed
    await logins.close()
    await manager.close()


async def test_background_sync_and_reconnect_are_independent_per_account(
    tmp_path, monkeypatch
):
    from enloq.application import accounts
    from enloq.domain.models import Chat, Message
    from enloq.inference.fake import FakeDecider, FakeEmbedder, FakeEngine
    from enloq.transport.fake import FakeTransport

    connections = []

    class PersonalFake(FakeTransport):
        requires_login = False

        def __init__(self, settings, store, **kwargs):
            super().__init__()
            self.connected = False
            self.store = store
            self.account_id = store.account_id
            self.identity = user(self.account_id)

        async def connect(self, sync):
            connections.append(self.account_id)
            if self.account_id == 1 and connections.count(1) == 1:
                raise ConnectionError("Authored first-account outage")
            self.connected = True

        async def discover(self):
            self.store.put_chat(
                Chat(
                    account_id=self.account_id,
                    peer="user:9",
                    title="Authored peer",
                    kind="private",
                ),
                activity=self.store.clock(),
            )
            self.messages = [
                Message(
                    account_id=self.account_id,
                    peer="user:9",
                    message_id=1,
                    revision=0,
                    date=self.store.clock(),
                    text="An authored incoming message",
                )
            ]
            return {"complete": True}

        async def catch_up(self):
            pass

    monkeypatch.setattr(accounts, "PersonalTransport", PersonalFake)
    monkeypatch.setattr(
        accounts, "OpenRouterEngine", lambda settings, user: FakeEngine()
    )
    monkeypatch.setattr(accounts, "MuseEngine", lambda settings, user: FakeEngine())
    monkeypatch.setattr(accounts, "DeciderEngine", lambda settings, user: FakeDecider())
    monkeypatch.setattr(
        accounts, "EmbeddingEngine", lambda settings, route, user: FakeEmbedder()
    )
    monkeypatch.setattr(accounts, "RerankEngine", lambda settings, route, user: None)
    registry = AccountRegistry(tmp_path)
    registry.register(1, "one.session")
    registry.register(2, "two.session")
    manager = AccountManager(Settings(mode="personal", data=tmp_path))
    await manager.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if manager.get(2).store.messages("2/user:9"):
                break
        assert connections == [1, 2]
        assert not manager.get(1).transport.connected
        assert manager.summaries()[0]["status"] == "Reconnecting"
        assert len(manager.get(2).store.messages("2/user:9")) == 1
        await manager.reconnect(1)
        for _ in range(100):
            await asyncio.sleep(0.01)
            if manager.get(1).store.messages("1/user:9"):
                break
        assert connections == [1, 2, 1]
        assert manager.get(1).transport.connected and manager.get(2).transport.connected
        assert manager.registry.usage() == []
    finally:
        await manager.close()


async def test_identity_withdrawn_during_sync_stops_only_that_account(
    tmp_path, monkeypatch
):
    from enloq.application import accounts
    from enloq.domain.models import Chat, Message, Refused
    from enloq.inference.fake import FakeDecider, FakeEmbedder, FakeEngine
    from enloq.transport.fake import FakeTransport

    class PersonalFake(FakeTransport):
        requires_login = False

        def __init__(self, settings, store, **kwargs):
            super().__init__()
            self.store = store
            self.account_id = store.account_id
            self.identity = user(self.account_id)

        async def connect(self, sync):
            self.connected = True

        async def discover(self):
            if self.account_id == 1:
                # The five-minute recheck found another identity.
                self.requires_login, self.connected = True, False
                raise Refused("Connected Telegram identity mismatch")
            self.store.put_chat(
                Chat(
                    account_id=2,
                    peer="user:9",
                    title="Authored peer",
                    kind="private",
                ),
                activity=self.store.clock(),
            )
            self.messages = [
                Message(
                    account_id=2,
                    peer="user:9",
                    message_id=1,
                    revision=0,
                    date=self.store.clock(),
                    text="An authored incoming message",
                )
            ]

        async def catch_up(self):
            pass

    monkeypatch.setattr(accounts, "PersonalTransport", PersonalFake)
    monkeypatch.setattr(
        accounts, "OpenRouterEngine", lambda settings, user: FakeEngine()
    )
    monkeypatch.setattr(accounts, "MuseEngine", lambda settings, user: FakeEngine())
    monkeypatch.setattr(accounts, "DeciderEngine", lambda settings, user: FakeDecider())
    monkeypatch.setattr(
        accounts, "EmbeddingEngine", lambda settings, route, user: FakeEmbedder()
    )
    monkeypatch.setattr(accounts, "RerankEngine", lambda settings, route, user: None)
    registry = AccountRegistry(tmp_path)
    registry.register(1, "one.session")
    registry.register(2, "two.session")
    manager = AccountManager(Settings(mode="personal", data=tmp_path))
    await manager.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if manager.registry.get(1)["state"] == "requires_login" and (
                manager.get(2).store.messages("2/user:9")
            ):
                break
        summary = {s["account_id"]: s for s in manager.summaries()}
        assert summary[1]["state"] == "requires_login"
        assert summary[1]["status"] == "Sign in again"
        assert not summary[1]["connected"]
        assert summary[2]["connected"] and summary[2]["status"] == "Connected"
        assert len(manager.get(2).store.messages("2/user:9")) == 1
    finally:
        await manager.close()
