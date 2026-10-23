"""Authenticated independent limits and authored shared spending projections."""

from decimal import Decimal

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import Chat
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from scripts.group_budget_fixture import register


def test_group_budget_default_shared_save_events_and_restart_preserve_other_state(
    tmp_path,
):
    settings = Settings(data=tmp_path / "app")
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as first:
        assert first.get("/api/settings").status_code in (401, 403)
        assert first.post(
            "/api/settings/group-budget",
            json={"limit_usd": "0"},
            headers={"Origin": str(first.base_url).rstrip("/")},
        ).status_code in (401, 403)
        trust_browser(first, settings.data)
        manager = app.state.accounts
        manager._create(manager.registry.register(2, "second.synthetic.session"))
        owner = manager.get(1)
        key = "1/chat:42"
        owner.store.put_chat(
            Chat(account_id=1, peer="chat:42", title="Authored group", kind="group")
        )
        owner.store.groups.observe(key, access="available", reason="authored_native")
        owner.store.groups.set_processing_enabled(key, True)
        owner.store.mutate_draft(
            key, 0, lambda draft: draft.update(text="Exact authored group draft")
        )
        manager.set_processing(1, False)
        manager.registry.set_processing_threshold(345)
        manager.registry.set_budget(1_250_000_000)
        preserved = (
            owner.store.groups.get(key),
            owner.store.draft(key),
            manager.registry.processing(1),
            manager.registry.preferences(),
        )
        before = first.get("/api/settings").json()
        assert before["group_budget"]["limit"] == 2_000_000_000
        assert before["budget"]["limit"] == 1_250_000_000
        _, sequence = manager.registry.changes()
        second = TestClient(app, base_url="http://127.0.0.1:8765")
        trust_browser(second, settings.data)
        for limit in ("0.00", "10.00", "2.37"):
            response = first.post(
                "/api/settings/group-budget", json={"limit_usd": limit}
            )
            assert response.status_code == 200
            value = response.json()
            assert value["group_budget"]["limit"] == int(Decimal(limit) * 1_000_000_000)
            assert (
                second.get("/api/settings").json()["group_budget"]
                == value["group_budget"]
            )
            assert value["budget"] == before["budget"]
            assert value["processing_threshold"] == 345
            assert value["guidance"] == before["guidance"]
        # An unchanged Save creates no second change record. The route owns
        # purpose; callers cannot redirect this field into the other family.
        assert (
            first.post("/api/settings/group-budget", json={"limit_usd": "2.37"}).json()
            == value
        )
        assert (
            first.post(
                "/api/settings/group-budget",
                json={"limit_usd": "0", "family": "daily"},
            ).status_code
            == 422
        )
        assert (
            first.post(
                "/api/settings/group-budget",
                json={"limit_usd": "0"},
                headers={"Origin": "https://foreign.example"},
            ).status_code
            == 403
        )
        assert first.get("/api/settings").json() == value
        assert (
            owner.store.groups.get(key),
            owner.store.draft(key),
            manager.registry.processing(1),
            manager.registry.preferences(),
        ) == preserved
        scopes, _ = manager.registry.changes(sequence)
        assert "group_briefing_budget" in scopes
        assert [
            (entry["old"], entry["new"])
            for entry in first.get("/api/settings").json()["group_budget"]["changes"]
        ] == [(10_000_000_000, 2_370_000_000), (0, 10_000_000_000), (2_000_000_000, 0)]
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, settings.data)
        saved = client.get("/api/settings").json()
        assert saved["group_budget"]["limit"] == 2_370_000_000
        assert saved["budget"]["limit"] == 1_250_000_000
        assert saved["processing_threshold"] == 345


@pytest.mark.parametrize(
    "bad", ["-0.01", "10.01", "0.001", "1e-3", "NaN", "Infinity", True, None]
)
def test_group_budget_invalid_value_keeps_both_limits_and_spending(tmp_path, bad):
    settings = Settings(data=tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, settings.data)
        before = client.get("/api/settings").json()
        response = client.post("/api/settings/group-budget", json={"limit_usd": bad})
        assert response.status_code == 422
        assert client.get("/api/settings").json() == before
        assert (
            app.state.accounts.registry.rows("SELECT * FROM model_reservations") == []
        )


def test_authored_budget_fixture_projects_accounts_families_and_preserves_exact_evidence(
    tmp_path,
):
    settings = Settings(data=tmp_path)
    app = create_app(settings)
    register(app)
    manager = app.state.accounts
    manager._create(manager.registry.register(2, "second.synthetic.session"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, settings.data)
        assert client.post("/api/test/group-budget", json={}).status_code == 200
        records = [
            (1, "daily", "receipted", 50_000_000),
            (1, "group_briefing", "receipted", 100_000_000),
            (2, "group_briefing", "receipted", 250_000_000),
            (2, "group_briefing", "unknown", 0),
            (1, "group_briefing", "refused", 5_000_000_000),
        ]
        for account, family, status, amount in records:
            assert (
                client.post(
                    "/api/test/group-budget",
                    json={
                        "action": "record",
                        "account_id": account,
                        "family": family,
                        "status": status,
                        "amount_nanos": amount,
                    },
                ).status_code
                == 200
            )
        value = client.get("/api/settings").json()
        assert value["budget"]["spent"] == 50_000_000
        assert value["group_budget"]["spent"] == 360_000_000
        assert (
            value["group_budget"]["calls"] == 4
            and value["group_budget"]["unknown"] == 1
        )
        assert value["group_budget"]["day"] == value["budget"]["day"]
        assert value["group_budget"]["resets_at"] == value["budget"]["resets_at"]
        preserved = (
            manager.registry.rows("SELECT * FROM model_reservations ORDER BY id"),
            manager.registry.rows("SELECT * FROM model_receipts ORDER BY id"),
        )
        client.post(
            "/api/settings/group-budget", json={"limit_usd": "0"}
        ).raise_for_status()
        changed = client.get("/api/settings").json()
        assert changed["group_budget"]["reached"] and not changed["budget"]["reached"]
        assert changed["budget"] == value["budget"]
        assert changed["group_budget"]["spent"] == value["group_budget"]["spent"]
        assert (
            manager.registry.rows("SELECT * FROM model_reservations ORDER BY id"),
            manager.registry.rows("SELECT * FROM model_receipts ORDER BY id"),
        ) == preserved
        client.post("/api/settings/budget", json={"limit_usd": "0"}).raise_for_status()
        assert (
            client.get("/api/settings").json()["group_budget"]
            == changed["group_budget"]
        )
        assert client.get("/api/accounts").status_code == 200
        assert (
            client.post(
                "/api/test/group-budget", json={"action": "record", "family": "unknown"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/test/group-budget",
                json={"action": "record", "amount_nanos": True},
            ).status_code
            == 422
        )
        assert len(manager.registry.rows("SELECT * FROM model_reservations")) == len(
            records
        )
