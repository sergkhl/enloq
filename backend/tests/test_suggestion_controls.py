"""S3: owner controls, the conversation projection and revision-safe insertion."""

import base64

import pytest
from enloq.api.server import create_app
from enloq.application.suggestions import splice
from enloq.config import Settings
from enloq.domain.models import Chat
from enloq.inference.guidance import DEFAULTS
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import image_bytes, message
from backend.tests.test_suggestion_state import OPTIONS, prepare, publish

KEY = "1/user:42"
BASE = "/api/accounts/1/chats/user:42"


@pytest.fixture
def served(tmp_path):
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        manager = app.state.accounts
        conversation = manager.get(1)
        conversation.store.put_chat(
            Chat(account_id=1, peer="user:42", title="Authored", kind="private")
        )
        yield client, manager, conversation


async def ready(conversation, text="Today was hard.", attempt="a" * 32):
    conversation.sync.ingest(message(conversation, 1, text=text))
    record = await prepare(conversation, attempt=attempt)
    assert publish(conversation, attempt)
    return record


def draft(client, **change):
    current = client.get(BASE).json()["draft"]
    if change:
        current = client.post(
            f"{BASE}/draft",
            json={
                "revision": current["revision"],
                "text": change.get("text", current["text"]),
                "reply_to": change.get("reply_to", current["reply_to"]),
            },
        ).json()
    return current


def insert(client, current, attempt="a" * 32, index=0, cursor=None, base=BASE):
    return client.post(
        f"{base}/suggestions/insert",
        json={
            "revision": current["revision"],
            "attempt": attempt,
            "index": index,
            "cursor": cursor,
        },
    )


def no_work(conversation):
    jobs = conversation.store.rows(
        "SELECT role FROM jobs WHERE role IN ('outgoing_rewrite','response_suggestions')"
    )
    assert not jobs
    assert not conversation.store.rows("SELECT * FROM operations")
    assert not conversation.transport.deliveries


@pytest.mark.parametrize(
    "text,cursor,expected,caret",
    [
        ("", None, "OPTION", 6),
        ("", 0, "OPTION", 6),
        ("Hello there", None, "Hello there OPTION", 18),
        ("Hello there", 5, "Hello OPTION there", 12),
        ("Hello there", 0, "OPTION Hello there", 7),
        ("Hello there", 6, "Hello OPTION there", 13),
        ("Line\n", 5, "Line\nOPTION", 11),
        ("Hello", 99, "Hello OPTION", 12),
        # Browser carets count UTF-16 code units, including surrogate pairs.
        ("Привет 😀 мир", 9, "Привет 😀 OPTION мир", 16),
        ("😀😀", 2, "😀 OPTION 😀", 10),
        ("😀😀", 1, "😀😀 OPTION", 11),  # Mid-pair is unknown: the end.
    ],
)
def test_splice_keeps_existing_text_at_the_utf16_caret(text, cursor, expected, caret):
    assert splice(text, "OPTION", cursor) == (expected, caret)


async def test_settings_switch_and_guidance_are_shared_and_versioned(served):
    client, manager, conversation = served
    settings = client.get("/api/settings").json()
    assert settings["suggestions"] is True
    assert settings["guidance"]["fields"]["suggestions"] == DEFAULTS["suggestions"]
    record = await ready(conversation)
    view = client.get(BASE).json()["suggestions"]
    assert view["state"] == "ready" and view["attempt"] == record["attempt"]
    assert [o["text"] for o in view["options"]] == [o.text for o in OPTIONS]
    off = client.post("/api/settings/suggestions", json={"enabled": False}).json()
    assert off["suggestions"] is False
    assert client.get(BASE).json()["suggestions"] == {
        "state": "none",
        "kind": "automatic",
        "style": "friendly",
        "reason": "off",
        "attempt": None,
        "options": [],
        "folded": False,
        "source_time": None,
        "expires_at": None,
    }
    assert client.get("/api/settings").json()["suggestions"] is False
    on = client.post("/api/settings/suggestions", json={"enabled": True}).json()
    assert on["suggestions"] is True
    # On reuses the cached valid set and backfills nothing.
    assert client.get(BASE).json()["suggestions"]["attempt"] == record["attempt"]
    version = on["guidance"]["version"]
    saved = client.post(
        "/api/settings/guidance",
        json={"fields": {"suggestions": "Keep options very short."}},
    ).json()["guidance"]
    assert saved["version"] == version + 1
    assert saved["fields"]["suggestions"] == "Keep options very short."
    reset = client.post(
        "/api/settings/guidance", json={"fields": {"suggestions": None}}
    ).json()["guidance"]
    assert reset["fields"]["suggestions"] == DEFAULTS["suggestions"]
    # Guidance changes affect future normal work only: nothing is regenerated.
    assert client.get(BASE).json()["suggestions"]["attempt"] == record["attempt"]
    no_work(conversation)


async def test_off_cancels_queued_work_in_every_account(served):
    client, manager, conversation = served
    entry = manager.registry.register(2, "synthetic2.session", name="Second")
    second = manager._create(entry).assistant
    second.store.put_chat(
        Chat(account_id=2, peer="user:42", title="Authored", kind="private")
    )
    for app in (conversation, second):
        app.sync.ingest(message(app, 1))
        record = await prepare(app)
        with app.store.transaction() as db:
            app.suggestions.enqueue(
                db, f"{app.store.account_id}/user:42", record, record["attempt"], 1e12
            )
    client.post("/api/settings/suggestions", json={"enabled": False})
    for app in (conversation, second):
        key = f"{app.store.account_id}/user:42"
        assert [
            r["state"]
            for r in app.store.rows(
                "SELECT state FROM jobs WHERE role='response_suggestions'"
            )
        ] == ["cancelled"]
        assert app.suggestions.record(key)["state"] == "cancelled"
        assert not app.store.rows("SELECT * FROM reservations")
    client.post("/api/settings/suggestions", json={"enabled": True})


async def test_projection_is_account_and_chat_scoped_and_starts_no_work(served):
    client, manager, conversation = served
    entry = manager.registry.register(2, "synthetic2.session", name="Second")
    second = manager._create(entry).assistant
    second.store.put_chat(
        Chat(account_id=2, peer="user:42", title="Authored", kind="private")
    )
    await ready(conversation)
    before = conversation.store.rows("SELECT id,state FROM jobs")
    for _ in range(3):
        assert client.get(BASE).json()["suggestions"]["state"] == "ready"
    other = client.get("/api/accounts/1/chats/user:101").json()["suggestions"]
    assert (other["state"], other["reason"]) == ("none", "unavailable")
    assert (
        client.get("/api/accounts/2/chats/user:42").json()["suggestions"]["reason"]
        == "unavailable"
    )
    assert conversation.store.rows("SELECT id,state FROM jobs") == before
    assert not conversation.store.rows("SELECT * FROM reservations")
    # Cached valid options stay readable while Account AI Processing is Off.
    manager.set_processing(1, False)
    assert client.get(BASE).json()["suggestions"]["state"] == "ready"
    current = draft(client)
    assert insert(client, current).status_code == 200
    manager.set_processing(1, True)


async def test_insert_preserves_draft_parts_and_undo_restores_everything(served):
    client, manager, conversation = served
    await ready(conversation)
    conversation.sync.ingest(message(conversation, 2, outgoing=False, text="More"))
    # A newer incoming message replaces the set; prepare a current one again.
    record = await prepare(conversation, attempt="b" * 32)
    assert publish(conversation, "b" * 32)
    current = draft(client, text="Hello there", reply_to=1)
    image = client.post(
        f"{BASE}/images",
        json={
            "revision": current["revision"],
            "name": "authored.png",
            "data": base64.b64encode(image_bytes()).decode(),
        },
    ).json()
    assert image["attachments"]
    inserted = insert(client, image, "b" * 32, index=1, cursor=5)
    assert inserted.status_code == 200, inserted.text
    body = inserted.json()
    option = OPTIONS[1].text
    assert body["draft"]["text"] == f"Hello {option} there"
    assert body["caret"] == len(f"Hello {option}")
    assert body["draft"]["attachments"] == image["attachments"]
    assert body["draft"]["reply_to"] == 1
    assert body["draft"]["undo"] == "Hello there"
    assert body["draft"]["state"] == "inserted"
    no_work(conversation)
    undone = client.post(
        f"{BASE}/undo", json={"revision": body["draft"]["revision"]}
    ).json()
    assert undone["text"] == "Hello there" and undone["undo"] is None
    assert undone["attachments"] == image["attachments"] and undone["reply_to"] == 1
    # Inserted text is the owner's: later expiry/withdrawal never removes it.
    again = insert(client, undone, "b" * 32, index=0).json()["draft"]
    conversation.sync.ingest(message(conversation, 3, text="Newest incoming"))
    assert client.get(BASE).json()["suggestions"]["state"] == "none"
    assert client.get(BASE).json()["draft"]["text"] == again["text"]
    assert OPTIONS[0].text in again["text"]
    assert record["attempt"] == "b" * 32


async def test_stale_tabs_cannot_insert_elsewhere_or_overwrite_newer_text(served):
    client, manager, conversation = served
    await ready(conversation)
    current = draft(client, text="Owner words")
    # Wrong attempt, index beyond the set, another chat or another account.
    assert insert(client, current, attempt="c" * 32).status_code == 409
    assert insert(client, current, index=2).status_code == 409
    other = client.get("/api/accounts/1/chats/user:101").json()["draft"]
    assert (
        insert(client, other, base="/api/accounts/1/chats/user:101").status_code == 409
    )
    assert (
        insert(client, current, base="/api/accounts/2/chats/user:42").status_code == 400
    )
    assert (
        client.post(
            f"{BASE}/suggestions/insert",
            json={"revision": current["revision"], "attempt": "x", "index": 0},
        ).status_code
        == 422
    )
    # Another browser saved newer text: the stale revision is refused intact.
    newer = draft(client, text="Newer words from another browser")
    assert insert(client, current).status_code == 409
    assert client.get(BASE).json()["draft"]["text"] == newer["text"]
    # Suggestions Off hides the set and refuses selection.
    client.post("/api/settings/suggestions", json={"enabled": False})
    assert insert(client, newer).status_code == 409
    client.post("/api/settings/suggestions", json={"enabled": True})
    # A later outgoing message withdraws the set.
    conversation.sync.ingest(message(conversation, 2, outgoing=True, text="Sent"))
    assert insert(client, newer).status_code == 409
    assert client.get(BASE).json()["draft"]["text"] == newer["text"]
    no_work(conversation)


async def test_generate_after_insert_preserves_the_selected_draft_and_rewrite_is_removed(
    served,
):
    client, manager, conversation = served
    await ready(conversation)
    inserted = insert(client, draft(client)).json()["draft"]
    assert inserted["text"] == OPTIONS[0].text
    started = client.post(
        f"{BASE}/suggestions/generate", json={"revision": inserted["revision"]}
    )
    assert started.status_code == 200 and started.json()["kind"] == "manual"
    assert client.get(BASE).json()["draft"] == inserted
    assert (
        client.post(
            f"{BASE}/rewrite", json={"revision": inserted["revision"]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{BASE}/rewrite/stop", json={"revision": inserted["revision"]}
        ).status_code
        == 404
    )
