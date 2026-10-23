"""ADR 40: a VPS service behind the shared edge, paired over SSH."""

import json
import socket
import sys
from contextlib import closing

import pytest
from enloq import doctor, media_tools
from enloq.api.limits import REFUSALS, WINDOW, RefusalLimit
from enloq.api.server import create_app
from enloq.browser_auth import browser_label
from enloq.config import Settings, private_read, private_write
from enloq.domain.models import Refused
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.proxy_helpers import ELSEWHERE, ENDPOINT

ORIGIN = "https://enloq.example.com"
MAC_SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15"


def remote(tmp_path, **changes):
    return Settings(
        data=tmp_path,
        listen_host="0.0.0.0",
        public_origin=ORIGIN,
        trusted_proxy=changes.pop("trusted_proxy", ""),
        **changes,
    )


def write_config(root, text):
    private_write(root / "config/app.env", "ENLOQ_MODE=personal\n" + text)


def test_public_origin_and_bind_address_load_together(tmp_path):
    write_config(
        tmp_path,
        "ENLOQ_LISTEN_HOST=0.0.0.0\n"
        f"ENLOQ_PUBLIC_ORIGIN={ORIGIN}\n"
        "ENLOQ_TRUSTED_PROXY=lrnki-caddy\n",
    )
    settings = Settings.load("personal", root=tmp_path)
    assert settings.listen_host == "0.0.0.0" and settings.remote
    assert settings.origin == ORIGIN and settings.trusted_proxy == "lrnki-caddy"
    write_config(tmp_path, "")
    local = Settings.load("personal", root=tmp_path)
    assert local.origin == "http://127.0.0.1:8765" and not local.remote


@pytest.mark.parametrize(
    "lines",
    [
        "ENLOQ_LISTEN_HOST=0.0.0.0\n",
        "ENLOQ_LISTEN_HOST=localhost\n",
        f"ENLOQ_PUBLIC_ORIGIN={ORIGIN.replace('https', 'http')}\n",
        f"ENLOQ_PUBLIC_ORIGIN={ORIGIN}/\n",
        f"ENLOQ_PUBLIC_ORIGIN={ORIGIN}/app\n",
        "ENLOQ_PUBLIC_ORIGIN=https://Enloq.example.com\n",
        "ENLOQ_PUBLIC_ORIGIN=https://user@enloq.example.com\n",
        "ENLOQ_TRUSTED_PROXY=lrnki-caddy\n",
        f"ENLOQ_PUBLIC_ORIGIN={ORIGIN}\nENLOQ_TRUSTED_PROXY=a b\n",
    ],
)
def test_loader_refuses_inexact_exposure(tmp_path, lines):
    write_config(tmp_path, lines)
    with pytest.raises(Refused):
        Settings.load("personal", root=tmp_path)


def test_remote_service_accepts_only_its_public_origin(tmp_path):
    app = create_app(remote(tmp_path))
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/health").json()["status"] == "ready"
        assert (
            client.get("/health").headers["Strict-Transport-Security"]
            == "max-age=31536000"
        )
        # Extra clients share the running app without restarting its lifespan.
        for base in ("http://127.0.0.1:8765", "https://other.example.com"):
            with closing(TestClient(app, base_url=base)) as other:
                assert other.get("/health").status_code == 403
        client.headers["Origin"] = "http://127.0.0.1:8765"
        assert client.post("/api/auth/pairings", json={}).status_code == 403
        del client.headers["Origin"]
        refused = client.get("/api/accounts")
        assert refused.status_code == 401
        assert refused.json()["detail"] == "Pair this browser on its connection screen"


def test_remote_cookies_are_secure_and_labels_name_the_platform(tmp_path):
    app = create_app(remote(tmp_path, owner_account=1))
    with TestClient(
        app, base_url=ORIGIN, headers={"User-Agent": MAC_SAFARI, "Origin": ORIGIN}
    ) as c:
        # A browser pairs with the code from Saved Messages (ADR 44 Q10).
        attempt = c.post("/api/auth/code-pairings", json={"browser": True}).json()
        code = app.state.accounts.get(1).transport.saved_posts[-1]["text"]
        completed = c.post(
            f"/api/auth/code-pairings/{attempt['id']}/browser",
            json={"code": code},
            headers={"X-Pairing-Proof": attempt["proof"]},
        )
        cookie = completed.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie
        assert c.get("/api/accounts").status_code == 200
        assert c.get("/api/auth/browsers").json()[0]["label"] == "Safari on a Mac"
    assert browser_label("Mozilla/5.0 (iPhone) Safari/1", remote=True) == (
        "Safari on an iPhone"
    )
    assert browser_label("Chrome/1") == "Chromium browser on this Mac"


def test_local_cookies_stay_plain(tmp_path):
    app = create_app(Settings(data=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        cookie = trust_browser(client, tmp_path).headers["set-cookie"]
        assert "Secure" not in cookie
        assert "Strict-Transport-Security" not in client.get("/health").headers


def test_refusals_from_one_address_are_limited(tmp_path):
    app = create_app(remote(tmp_path))
    with TestClient(app, base_url=ORIGIN) as client:
        for _ in range(REFUSALS):
            assert client.get("/api/accounts").status_code == 401
        limited = client.get("/api/auth/session")
        assert limited.status_code == 429
        assert 0 < int(limited.headers["Retry-After"]) <= WINDOW
        # Static pages and health are outside the limited API.
        assert client.get("/health").status_code == 200


def test_local_service_has_no_refusal_limit(tmp_path):
    app = create_app(Settings(data=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        for _ in range(REFUSALS + 5):
            assert client.get("/api/accounts").status_code == 401


async def test_forwarded_address_counts_only_from_the_edge(monkeypatch):
    now = [0.0]
    limit = RefusalLimit("lrnki-caddy", clock=lambda: now[0])
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(None, None, None, "", ("172.18.0.2", 0))],
    )
    assert await limit.client("172.18.0.2", "203.0.113.9") == "203.0.113.9"
    assert await limit.client("172.18.0.2", "1.1.1.1, 203.0.113.9") == "203.0.113.9"
    assert await limit.client("172.18.0.7", "203.0.113.9") == "172.18.0.7"
    for _ in range(REFUSALS):
        limit.record("203.0.113.9")
    assert limit.blocked("203.0.113.9") and not limit.blocked("172.18.0.7")
    now[0] = WINDOW
    assert not limit.blocked("203.0.113.9")


def test_media_tools_prefer_homebrew_then_the_system(tmp_path, monkeypatch):
    brew, system = tmp_path / "brew", tmp_path / "usr"
    system.mkdir()
    (system / "ffprobe").write_text("")
    monkeypatch.setattr(media_tools, "DIRECTORIES", (str(brew), str(system)))
    assert media_tools.media_tool("ffprobe") == str(system / "ffprobe")
    brew.mkdir()
    (brew / "ffprobe").write_text("")
    assert media_tools.media_tool("ffprobe") == str(brew / "ffprobe")
    assert media_tools.media_tool("ffmpeg") == str(system / "ffmpeg")


def test_proxy_key_setup_writes_private_bound_references(tmp_path, monkeypatch):
    from enloq import setup
    from enloq.config import parse_config

    monkeypatch.setattr(setup, "ROOT", tmp_path)
    write_config(tmp_path, f"ENLOQ_PUBLIC_ORIGIN={ORIGIN}\n")
    monkeypatch.setattr("builtins.input", lambda prompt: ENDPOINT)
    for refused in ("sk-or-v1-openrouter", "not-a-key"):
        monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: refused)
        with pytest.raises(Refused):
            setup.proxy_key()
    # An endpoint that is not a proxy URL is refused before the key is asked.
    asked = []
    monkeypatch.setattr(setup.getpass, "getpass", asked.append)
    for refused in ("http://proxy.example.test/v1", "https://proxy.example.test/api"):
        monkeypatch.setattr("builtins.input", lambda prompt: refused)
        with pytest.raises(Refused, match="ending in /v1"):
            setup.proxy_key()
    assert asked == [] and not (tmp_path / "secrets").exists()
    monkeypatch.setattr("builtins.input", lambda prompt: f" {ELSEWHERE}/ \n")
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: " sk-synthetic \n")
    setup.proxy_key()
    assert private_read(tmp_path / "secrets/litellm-api-key") == "sk-synthetic"
    assert private_read(tmp_path / "secrets/muse-endpoint") == ELSEWHERE
    values = parse_config(tmp_path / "config/app.env")
    assert values["PUBLIC_ORIGIN"] == ORIGIN
    assert values["MUSE_API_KEY_REF"] == "secrets/litellm-api-key"
    assert values["MUSE_ENDPOINT_REF"] == "secrets/muse-endpoint"


def test_doctor_reports_the_configured_proxy_endpoint(tmp_path, monkeypatch, capsys):
    settings = Settings(
        mode="personal",
        data=tmp_path,
        refs={"MUSE_ENDPOINT_REF": str(tmp_path / "secrets/muse-endpoint")},
    )
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    monkeypatch.setattr(doctor.Settings, "load", lambda mode: settings)
    monkeypatch.setattr(sys, "argv", ["doctor", "--redacted"])

    def proxy():
        doctor.main()
        return json.loads(capsys.readouterr().out)["routes"]["proxy"]

    assert proxy() is None, "not configured yet"
    private_write(tmp_path / "secrets/muse-endpoint", ELSEWHERE + "/")
    assert proxy() == ELSEWHERE
    private_write(tmp_path / "secrets/muse-endpoint", "http://proxy.example.test/v1")
    assert proxy() is None
