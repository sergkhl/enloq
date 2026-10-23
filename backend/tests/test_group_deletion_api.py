"""Group deletion audience and exact review through HTTP, using authored data."""

import time
import uuid

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.deletion import deletion_fingerprint
from enloq.domain.models import Chat, MediaInfo, Message
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser


@pytest.fixture(params=["chat:42", "channel:42"])
def served(tmp_path, request):
    directory = tmp_path / "app"
    server = create_app(Settings(data=directory))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        app = server.state.accounts.get(1)
        server.state.accounts.set_processing(1, False)
        peer, key = request.param, f"1/{request.param}"
        app.store.put_chat(
            Chat(account_id=1, peer=peer, title="Authored group", kind="group")
        )
        app.store.groups.observe(key, access="available", reason="authored_native")
        for identity, author, outgoing in (
            (1, "user:1", True),
            (2, "user:1", False),
            (3, "user:7", True),
            (4, "channel:42", True),
            (5, None, True),
        ):
            source = Message(
                account_id=1,
                peer=peer,
                message_id=identity,
                revision=1,
                date=app.store.clock(),
                text=f"Authored group original {identity}.",
                author_peer=author,
                outgoing=outgoing,
            )
            app.sync.ingest(source, target=False)
            app.transport.messages.append(app.store.message(key, identity))
        yield directory, app, client, key, f"/api/accounts/1/chats/{peer}/deletions"


def payload(app, key, *identities):
    return {
        "id": uuid.uuid4().hex,
        "targets": [
            {
                "message_id": identity,
                "revision": app.store.message(key, identity).revision,
                "source_fingerprint": deletion_fingerprint(
                    app.store.message(key, identity)
                ),
            }
            for identity in identities
        ],
    }


def terminal(client, base, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(f"{base}/{identity}")
        assert result.status_code == 200, result.text
        if result.json()["state"] not in {"queued", "deleting"}:
            return result.json()
        time.sleep(0.01)
    pytest.fail("Authored group deletion worker did not settle")


def test_group_delete_requires_trusted_origin_and_native_own_author_with_ai_off(served):
    directory, app, client, key, base = served
    body = payload(app, key, 1, 2)
    assert client.post(base, json=body).status_code == 403
    client.headers["Origin"] = str(client.base_url).rstrip("/")
    assert client.post(base, json=body).status_code == 401
    trust_browser(client, directory)
    accepted = client.post(base, json=body)
    assert accepted.status_code == 200, accepted.text
    repeated = client.post(base, json=body)
    assert repeated.status_code == 200 and repeated.json()["id"] == body["id"]
    result = terminal(client, base, body["id"])
    assert result["state"] == "completed"
    assert result["counts"]["acknowledged"] == 2
    assert len(app.transport.deletion_calls) == 1
    description = app.transport.deletion_calls[0]
    assert description.peer == app.store.chat(key).peer
    assert description.identities == (1, 2)
    assert [s.author_peer for s in description.sources] == ["user:1", "user:1"]
    assert not app.engine.calls
    assert (
        client.get(
            f"{base.replace('accounts/1', 'accounts/2')}/{body['id']}"
        ).status_code
        == 400
    )


@pytest.mark.parametrize("identity", [3, 4, 5])
def test_other_channel_unknown_authors_are_refused_even_when_outgoing(served, identity):
    directory, app, client, key, base = served
    trust_browser(client, directory)
    result = client.post(base, json=payload(app, key, identity))
    assert result.status_code == 400, result.text
    assert not app.transport.deletion_calls
    assert not app.store.rows("SELECT 1 FROM deletion_operations")


def test_changed_review_and_lost_access_refuse_before_acceptance(served):
    directory, app, client, key, base = served
    trust_browser(client, directory)
    body = payload(app, key, 1)
    original = app.store.message(key, 1)
    app.sync.ingest(
        original.model_copy(
            update={"text": "Changed authored original.", "revision": 2}
        ),
        target=False,
    )
    assert client.post(base, json=body).status_code == 409
    app.store.groups.observe(key, access="lost", reason="authored_loss")
    assert client.post(base, json=payload(app, key, 2)).status_code == 400
    assert not app.transport.deletion_calls
    assert not app.store.rows("SELECT 1 FROM deletion_operations")


def test_group_confirmation_proof_binds_media_changes_under_unchanged_revision(served):
    directory, app, client, key, base = served
    trust_browser(client, directory)
    original = app.store.message(key, 1).model_copy(
        update={
            "media": "document",
            "attachment_id": "authored-document",
            "media_info": MediaInfo(file_name="reviewed.txt", size=10),
        }
    )
    app.sync.ingest(original, target=False)
    public = client.post(
        base.removesuffix("/deletions") + "/originals/current",
        json={"identities": [1]},
    ).json()["messages"][0]
    body = payload(app, key, 1)
    assert body["targets"][0]["source_fingerprint"] == public["deletion_fingerprint"]
    no_proof = {**body, "targets": [{"message_id": 1, "revision": public["revision"]}]}
    assert client.post(base, json=no_proof).status_code == 400
    app.sync.ingest(
        app.store.message(key, 1).model_copy(
            update={"media_info": MediaInfo(file_name="different.txt", size=20)}
        ),
        target=False,
    )
    assert app.store.message(key, 1).revision == public["revision"]
    assert client.post(base, json=body).status_code == 409
    assert not app.store.rows("SELECT 1 FROM deletion_operations")
    assert not app.transport.deletion_calls


def test_unknown_group_receipt_check_observes_absence_without_replaying_or_ack(served):
    directory, app, client, key, base = served
    trust_browser(client, directory)
    native = app.transport.delete

    async def lose_receipt(description, evidence):
        await native(description, evidence)
        raise TimeoutError("Authored missing acknowledgement")

    app.transport.delete = lose_receipt
    body = payload(app, key, 1)
    assert client.post(base, json=body).status_code == 200
    result = terminal(client, base, body["id"])
    assert result["state"] == "uncertain"
    checked = client.post(f"{base}/{body['id']}/check", json={})
    assert checked.status_code == 200, checked.text
    value = checked.json()
    assert value["state"] == "uncertain" and value["counts"]["acknowledged"] == 0
    assert value["retry_targets"] == []
    assert all(chunk.get("receipt") is None for chunk in value["chunks"])
    assert len(app.transport.deletion_calls) == 1
    assert len(app.transport.deletion_reads) == 1
