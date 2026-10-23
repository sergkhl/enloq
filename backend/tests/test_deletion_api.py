"""Deletion API audience, durable acceptance and read-only recovery (authored only)."""

import time
import uuid

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser

BASE = "/api/accounts/1/chats/user:101/deletions"


@pytest.fixture
def served(tmp_path):
    directory = tmp_path / "app"
    server = create_app(Settings(data=directory))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        assistant = server.state.accounts.get(1)
        assistant.transport.messages = assistant.store.messages("1/user:101")
        yield directory, assistant, client


def payload(assistant, *identities):
    return {
        "id": uuid.uuid4().hex,
        "targets": [
            {
                "message_id": identity,
                "revision": assistant.store.message("1/user:101", identity).revision,
            }
            for identity in identities
        ],
    }


def terminal(client, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(f"{BASE}/{identity}")
        assert result.status_code == 200, result.text
        value = result.json()
        if value["state"] not in {"queued", "deleting"}:
            return value
        time.sleep(0.01)
    pytest.fail("Authored deletion worker did not settle within five seconds")


def test_delete_requires_authenticated_origin_and_reviewed_current_membership(served):
    directory, assistant, client = served
    # ID 2 exists only in this chat; demo IDs are reused between authored chats.
    body = payload(assistant, 2)
    assert client.post(BASE, json=body).status_code == 403
    client.headers["Origin"] = str(client.base_url).rstrip("/")
    assert client.post(BASE, json=body).status_code == 401
    trust_browser(client, directory)
    stale = {**body, "targets": [{"message_id": 2, "revision": -1}]}
    assert client.post(BASE, json=stale).status_code == 422
    assert client.post(BASE, json={**body, "targets": []}).status_code == 422
    assert client.post(BASE, json={**body, "id": "not-an-identity"}).status_code == 422
    wrong = [{"message_id": 2, "revision": body["targets"][0]["revision"] + 1}]
    assert client.post(BASE, json={**body, "targets": wrong}).status_code == 409
    assert client.post(BASE.replace("user:101", "user:102"), json=body).status_code in (
        400,
        409,
    )
    assert (
        client.post(BASE.replace("accounts/1", "accounts/2"), json=body).status_code
        == 400
    )
    assert not assistant.store.rows("SELECT 1 FROM deletion_operations")


def test_repeated_confirm_returns_same_operation_and_browser_closure_keeps_progress(
    served,
):
    directory, assistant, client = served
    trust_browser(client, directory)
    body = payload(assistant, 1, 2)
    calls = []
    native = assistant.transport.delete

    async def capture(description, evidence):
        calls.append(description)
        return await native(description, evidence)

    assistant.transport.delete = capture
    first = client.post(BASE, json=body)
    assert first.status_code == 200, first.text
    again = client.post(BASE, json=body)
    assert again.status_code == 200 and again.json()["id"] == body["id"]
    assert (
        client.post(BASE, json={**body, "targets": body["targets"][:1]}).status_code
        == 409
    )
    # A browser disconnect does not end the service or its accepted operation.
    client.cookies.clear()
    assert client.get(f"{BASE}/{body['id']}").status_code == 401
    trust_browser(client, directory)
    completed = terminal(client, body["id"])
    assert completed["state"] == "completed"
    assert len(calls) == 1 and calls[0].identities == (1, 2)
    assert all(assistant.store.message("1/user:101", i).deleted for i in (1, 2))
    assert (
        client.get(f"{BASE.replace('user:101', 'user:102')}/{body['id']}").status_code
        == 400
    )
    checked = client.post(f"{BASE}/{body['id']}/check", json={})
    assert checked.status_code == 200 and len(calls) == 1
    assert "deletions" in client.get(BASE.removesuffix("/deletions")).json()


def test_lost_receipt_check_reads_without_claiming_for_both_success_or_deleting_again(
    served,
):
    directory, assistant, client = served
    trust_browser(client, directory)
    body = payload(assistant, 1)
    native = assistant.transport.delete
    calls = []

    async def lose_acknowledgement(description, evidence):
        calls.append(description)
        await native(description, evidence)
        raise TimeoutError("Authored lost acknowledgement")

    assistant.transport.delete = lose_acknowledgement
    accepted = client.post(BASE, json=body)
    assert accepted.status_code == 200, accepted.text
    uncertain = terminal(client, body["id"])
    assert uncertain["state"] == "uncertain"
    checked = client.post(f"{BASE}/{body['id']}/check", json={})
    assert checked.status_code == 200, checked.text
    value = checked.json()
    assert value["state"] == "uncertain" and len(calls) == 1
    assert all(chunk.get("receipt") is None for chunk in value["chunks"])
    assert value["retry_targets"] == []


def test_current_originals_reader_is_bounded_cached_only_and_keeps_tombstones(served):
    directory, assistant, client = served
    trust_browser(client, directory)
    base = BASE.removesuffix("/deletions") + "/originals/current"

    async def forbidden(*args, **kwargs):
        pytest.fail("Current-originals lookup must never query Telegram")

    assistant.transport.exact = forbidden
    assistant.transport.exact_many = forbidden
    assert client.post(base, json={"identities": []}).status_code == 422
    assert (
        client.post(base, json={"identities": list(range(1, 102))}).status_code == 422
    )
    assistant.sync.deleted("user:101", [1])
    result = client.post(base, json={"identities": [1, 2, 9999]})
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["missing"] == [9999]
    assert [m["message_id"] for m in value["messages"]] == [1, 2]
    assert value["messages"][0]["deleted"]
    assert not assistant.transport.deletion_calls
