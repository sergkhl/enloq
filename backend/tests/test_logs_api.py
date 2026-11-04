"""ADR 61: a client's log reaches the service's, the owner reads the service's
own, and a store's lock timeout answers as busy."""

import logging
import sqlite3

import pytest
from enloq.api.server import BUSY, create_app
from enloq.config import Settings
from enloq.domain.models import Refused
from enloq.logs import ExtendedFormatter, recent
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser

ORIGIN = {"Origin": "http://127.0.0.1:8765"}


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(
        app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False
    ) as value:
        trust_browser(value, tmp_path / "app")
        yield value


def test_a_clients_log_reaches_the_services(client, caplog):
    caplog.set_level(logging.INFO, logger="enloq.client")
    response = client.post(
        "/api/logs/client",
        headers=ORIGIN,
        json={
            "build": "Enloq 0.1.0 · abc1234",
            "entries": [
                {
                    "time": 1790000000000,
                    "level": "error",
                    "source": "screen",
                    "message": "TypeError: Cannot read properties of undefined",
                    "stack": "at ReminderRow (bundle:1:2)",
                },
                {
                    "time": 1790000000001,
                    "level": "info",
                    "source": "api",
                    "message": "GET reminders 200 12 ms",
                },
            ],
        },
    )
    assert response.status_code == 204
    error, info = [r for r in caplog.records if r.name == "enloq.client"]
    assert error.levelno == logging.ERROR and info.levelno == logging.INFO
    line = ExtendedFormatter().format(error)
    assert "member=owner" in line and "kind=browser" in line
    assert "build=Enloq 0.1.0 · abc1234" in line
    assert "screen: TypeError: Cannot read properties" in line
    assert line.endswith("at ReminderRow (bundle:1:2)")


def test_a_clients_log_needs_a_trusted_client(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as value:
        response = value.post("/api/logs/client", headers=ORIGIN, json={"entries": []})
        assert response.status_code == 401


def test_a_clients_log_is_bounded(client):
    entry = {"time": 1, "level": "warn", "source": "api", "message": "m"}
    too_many = client.post(
        "/api/logs/client", headers=ORIGIN, json={"entries": [entry] * 201}
    )
    too_long = client.post(
        "/api/logs/client",
        headers=ORIGIN,
        json={"entries": [{**entry, "message": "m" * 20001}]},
    )
    assert too_many.status_code == too_long.status_code == 422


def test_the_owner_reads_the_services_last_lines(client):
    recent.setFormatter(ExtendedFormatter())
    recent.emit(
        logging.LogRecord(
            "enloq.http", logging.WARNING, "", 0, "Event loop stalled", (), None
        )
    )
    lines = client.get("/api/logs?limit=1").json()["lines"]
    assert len(lines) == 1 and lines[0].endswith("Event loop stalled")


def test_a_member_does_not_read_the_services_lines(client, monkeypatch):
    # They may name other members' chats.
    registry = client.app.state.accounts.registry
    member = registry.member
    monkeypatch.setattr(
        registry, "member", lambda identity: {**member(identity), "role": "member"}
    )
    assert client.get("/api/logs").status_code == 403


def test_a_locked_store_answers_busy_and_other_store_errors_stay_errors(
    client, monkeypatch
):
    manager = client.app.state.accounts

    def locked(identity):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(manager, "get", locked)
    response = client.get("/api/accounts/1/chats")
    assert response.status_code == 503
    assert response.json() == {"detail": BUSY}

    def broken(identity):
        raise sqlite3.OperationalError("no such table: chats")

    monkeypatch.setattr(manager, "get", broken)
    assert client.get("/api/accounts/1/chats").status_code == 500


def test_a_locked_store_above_the_routes_answers_busy_too(client, monkeypatch):
    registry = client.app.state.accounts.registry

    def locked(identity):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(registry, "member", locked)
    assert client.get("/api/accounts/1/chats").status_code == 503


def test_requests_and_refusals_reach_the_log(client, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="enloq.http")
    manager = client.app.state.accounts

    def refused(identity):
        raise Refused("Waiting for this account's Telegram connection")

    monkeypatch.setattr(manager, "get", refused)
    client.get("/api/accounts/1/chats")
    lines = [r.getMessage() for r in caplog.records if r.name == "enloq.http"]
    assert any(
        line.endswith("refused: Waiting for this account's Telegram connection")
        for line in lines
    )
    assert any(
        line.startswith("GET /api/accounts/1/chats 400 ") and "member=owner" in line
        for line in lines
    )
    # Pairing routes name their attempts after the operation.
    client.get("/api/auth/client-pairings/abc/qr.svg")
    assert not any(
        "abc" in r.getMessage() for r in caplog.records if r.name == "enloq.http"
    )
