from enloq.api.server import create_app
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser


def test_calendar_timezone_is_the_configured_application_timezone(tmp_path):
    app = create_app(Settings(data=tmp_path, timezone="Pacific/Auckland"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path)
        assert client.get("/api/accounts").json()["timezone"] == "Pacific/Auckland"
        assert (
            client.get("/api/accounts/1/chats/user:101").json()["timezone"]
            == "Pacific/Auckland"
        )
