"""ADR 49 Q14: the decision model's route state, its owner notice and its one
push per outage. Authored state only; no provider is called."""

from contextlib import closing

from enloq.api.server import create_app
from enloq.application.notification_devices import DECISION_MODEL
from enloq.storage.accounts import AccountRegistry
from fastapi.testclient import TestClient

from backend.tests.test_device_trust import ORIGIN, bearer, remote
from backend.tests.test_join_requests import owner_device, server
from backend.tests.test_members import add_member


def test_the_registry_keeps_one_outage_until_a_request_answers(tmp_path):
    registry = AccountRegistry(tmp_path)
    assert registry.decision_route() is None
    assert not registry.set_decision_route(True)
    assert registry.set_decision_route(False, "refused") is True
    outage = registry.decision_route()
    assert outage["reason"] == "refused" and outage["since"] > 0
    # Another failure continues the same outage: no second start.
    assert registry.set_decision_route(False, "http") is False
    assert registry.decision_route() == outage
    assert not registry.set_decision_route(True)
    assert registry.decision_route() is None
    assert registry.set_decision_route(False, "listing") is True
    # Browsers reread Settings when it changes.
    scopes, _ = registry.changes()
    assert "decision_model" in scopes


def test_the_owner_hears_once_per_outage(tmp_path):
    app = server(tmp_path)
    manager = app.state.accounts
    starts = []
    manager.on_decision_outage = lambda: starts.append(1)
    manager.decision_route(True)
    manager.decision_route(False, "refused")
    manager.decision_route(False, "connection")
    assert len(starts) == 1
    manager.decision_route(True)
    manager.decision_route(False, "http")
    assert len(starts) == 2


def test_the_push_names_no_chat_and_opens_settings_ai_while_it_lasts(tmp_path):
    app = server(tmp_path)
    owner = owner_device(app)
    owner_id = app.state.browser_auth.client(owner, kind="device")["id"]
    manager, delivery = app.state.accounts, app.state.notifications
    manager.on_decision_outage = delivery.decision_outage  # As the server wires it.
    manager.decision_route(False, "refused")
    registry = manager.registry
    [row] = registry.rows("SELECT * FROM device_deliveries WHERE subject IS NOT NULL")
    assert row["subject"] == DECISION_MODEL
    assert row["account_id"] is None and row["peer"] is None
    event = delivery.devices.event(row)
    assert (event["kind"], event["sender"]) == ("decider", "Enloq")
    assert "unavailable" in event["text"] and event["account_id"] is None
    assert delivery.devices.target(owner_id, row["id"]) == {"kind": "ai"}
    # Recovered before the phone asked: nothing stale is shown.
    manager.decision_route(True)
    assert delivery.devices.event(row) is None
    # Another failure during the same outage queues nothing new.
    manager.decision_route(False, "http")
    manager.decision_route(False, "http")
    assert (
        len(registry.rows("SELECT * FROM device_deliveries WHERE subject IS NOT NULL"))
        == 2
    )


def test_only_the_owner_reads_the_decision_model_state(tmp_path):
    app = create_app(remote(tmp_path))
    friend = add_member(app)
    owner = app.state.browser_auth._issue("Enloq app on SM-S938B", "device")
    with closing(TestClient(app, base_url=ORIGIN)) as client:
        mine = client.get("/api/settings", headers=bearer(owner)).json()
        assert mine["decision_model"] == {"available": True, "since": None}
        app.state.accounts.decision_route(False, "refused")
        mine = client.get("/api/settings", headers=bearer(owner)).json()
        outage = app.state.accounts.registry.decision_route()
        assert mine["decision_model"] == {"available": False, "since": outage["since"]}
        theirs = client.get("/api/settings", headers=bearer(friend)).json()
        assert theirs["decision_model"] is None
