"""Shared authenticated CT2 setting, independent of account selection and budget."""

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser


def test_threshold_defaults_boundaries_events_and_restart(tmp_path):
    settings = Settings(data=tmp_path / "app")
    app = create_app(settings)
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app, base_url=origin["Origin"]) as client:
        assert client.get("/api/settings").status_code in (401, 403)
        assert client.post(
            "/api/settings/processing-threshold", json={"limit": 0}, headers=origin
        ).status_code in (401, 403)
        trust_browser(client, settings.data)
        manager = app.state.accounts
        manager._create(manager.registry.register(2, "second.synthetic.session"))
        before = client.get("/api/settings").json()
        assert before["processing_threshold"] == 200
        _, sequence = manager.registry.changes(0)
        for limit in (0, 10_000, 137):
            saved = client.post(
                "/api/settings/processing-threshold",
                json={"limit": limit},
                headers=origin,
            )
            assert saved.status_code == 200
            assert saved.json()["processing_threshold"] == limit
            assert saved.json()["budget"] == before["budget"]
            assert saved.json()["guidance"] == before["guidance"]
        scopes, _ = manager.registry.changes(sequence)
        assert "processing_threshold" in scopes
        assert manager.get(1).store.shared_path == manager.get(2).store.shared_path
    with TestClient(create_app(settings), base_url=origin["Origin"]) as client:
        trust_browser(client, settings.data)
        assert client.get("/api/settings").json()["processing_threshold"] == 137


@pytest.mark.parametrize("bad", [-1, 10_001, 1.5, 200.0, "200", True, None])
def test_invalid_threshold_never_changes_saved_setting(tmp_path, bad):
    settings = Settings(data=tmp_path / "app")
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, settings.data)
        response = client.post(
            "/api/settings/processing-threshold",
            json={"limit": bad},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert response.status_code == 422
        assert client.get("/api/settings").json()["processing_threshold"] == 200
