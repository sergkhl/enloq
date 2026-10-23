"""Local lifecycle guarantees without a Telegram client or account runtime."""

import json
import sqlite3

import pytest
from enloq.domain.delivery import ReceiptBatch
from enloq.domain.models import Chat
from enloq.storage.store import Store

KEY = "1/user:42"


@pytest.fixture
def local(tmp_path):
    store = Store(tmp_path / "rewriting.sqlite3", account_id=1, clock=lambda: 100)
    store.put_chat(Chat(account_id=1, peer="user:42", title="Authored", kind="private"))
    yield store
    store.close()


def insert(db, identity="authored", state="prepared", **extra):
    body = dict(id=identity, state=state, chat_key=KEY, composer_revision=1, **extra)
    db.execute(
        "INSERT INTO operations VALUES (?,?,?,?)",
        (identity, KEY, state, json.dumps(body)),
    )


def test_local_recovery_rolls_back_with_its_callers_transaction(local):
    with local.transaction() as db:
        insert(db, wire=[])
    with pytest.raises(RuntimeError, match="abort"):
        with local.transaction() as db:
            local.send_operations.recover(db)
            assert db.execute("SELECT state FROM operations").fetchone()[0] == "refused"
            raise RuntimeError("abort")
    assert local.send_operations.get("authored")["state"] == "prepared"
    local.recover()
    assert local.send_operations.get("authored")["cause"] == "not_sent"


@pytest.mark.parametrize(
    "state,submitted,pinned",
    [
        ("queued", True, True),
        ("prepared", False, True),
        ("dispatching", True, True),
        ("unknown", True, True),
        # Retry and the retained bubble need a submission's own images.
        ("refused", True, True),
        ("cancelled", True, True),
        # Before the queue, a refused send's images stayed with its draft.
        ("refused", False, False),
        ("confirmed", True, False),
    ],
)
def test_attachment_pins_read_the_collectors_current_transaction(
    local, state, submitted, pinned
):
    with local.transaction() as db:
        insert(db, state=state, attachments=[{"id": "image"}], submitted=submitted)
        pins = local.send_operations.retained_images(db)
        assert pins == ({"image"} if pinned else set())
        # A transition in this same transaction releases the pin immediately.
        db.execute("UPDATE operations SET state='confirmed'")
        assert local.send_operations.retained_images(db) == set()


def test_display_snapshots_are_detached_and_keep_the_current_order(local):
    with local.transaction() as db:
        insert(db, "first", wire=[])
        insert(db, "second", "unknown", wire=[{"outcome": "exposed"}])
    assert local.send_operations.blocks_removal()
    [view] = local.send_operations.recent(KEY, limit=1)
    assert view["id"] == "second"
    view["wire"][0]["outcome"] = "watched"
    assert local.send_operations.get("second")["wire"] == [{"outcome": "exposed"}]
    assert [v["id"] for v in local.send_operations.recent(KEY)] == ["second", "first"]


def prepare_text(local):
    draft = local.mutate_draft(
        KEY, 0, lambda value: value.update(text="Authored reply")
    )
    operation = local.send_operations.prepare(
        KEY, draft["revision"], snapshot=lambda *args, **kwargs: []
    )
    assert local.send_operations.claim(operation["id"])
    assert not local.send_operations.claim(operation["id"])
    return operation


def test_a_failed_composer_clear_saves_no_submission(local):
    """Acceptance and clearing are one transaction: a save failure keeps the
    draft and queues nothing (ADR 26)."""
    draft = local.mutate_draft(KEY, 0, lambda value: value.update(text="Authored"))
    with local.transaction() as db:
        db.execute("""CREATE TRIGGER fail_clear BEFORE UPDATE ON drafts
                   BEGIN SELECT RAISE(ABORT, 'authored write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="authored write failure"):
        local.send_operations.prepare(
            KEY, draft["revision"], snapshot=lambda *args, **kwargs: []
        )
    assert not local.rows("SELECT 1 FROM operations")
    assert local.draft(KEY)["text"] == "Authored"
    with local.transaction() as db:
        db.execute("DROP TRIGGER fail_clear")
    operation = local.send_operations.prepare(
        KEY, draft["revision"], snapshot=lambda *args, **kwargs: []
    )
    assert operation["state"] == "queued" and operation["text"] == "Authored"
    assert local.draft(KEY)["text"] == "" and local.draft(KEY)["revision"] == 2
    # The same submitted revision never queues a second message.
    again = local.send_operations.prepare(
        KEY, draft["revision"], snapshot=lambda *args, **kwargs: []
    )
    assert again["id"] == operation["id"]
    assert len(local.rows("SELECT 1 FROM operations")) == 1
    confirmation = local.send_operations.claim(operation["id"]) and (
        local.send_operations.confirm(
            operation["id"], ReceiptBatch(((operation["random_id"], 42),))
        )
    )
    assert confirmation.complete
    assert [m.message_id for m in confirmation.messages] == [42]
    assert local.send_operations.get(operation["id"])["state"] == "confirmed"


def test_confirmation_keeps_newer_text_and_cannot_be_downgraded(local):
    operation = prepare_text(local)
    evidence = local.send_operations.evidence(operation["id"])
    local.mutate_draft(KEY, 2, lambda value: value.update(text="Newer words"))
    local.send_operations.confirm(
        operation["id"], ReceiptBatch(((operation["random_id"], 42),))
    )
    local.send_operations.refuse(operation["id"], "Authored late refusal")
    local.send_operations.settle(evidence, cancelled=True)
    assert (
        local.send_operations.confirm(
            operation["id"], ReceiptBatch(((operation["random_id"], 42),))
        )
        is None
    )
    assert local.send_operations.get(operation["id"])["state"] == "confirmed"
    assert local.draft(KEY)["text"] == "Newer words"


@pytest.mark.parametrize("ingress", ["raw", "gap"])
async def test_receipt_intake_is_durable_without_confirming_and_gap_waits_for_it(
    tmp_path, ingress
):
    from datetime import UTC, datetime

    from enloq.transport.telethon import PersonalTransport
    from telethon import types

    from backend.tests.test_rebuild import edit, fixture

    app = fixture(tmp_path)
    draft = edit(app, "Authored reply")
    app.transport.lose_receipt = True
    unknown = await app.delivery.send(KEY, draft["revision"])
    update = types.UpdateMessageID(id=42, random_id=unknown["random_id"])

    class Client:
        async def __call__(self, request):
            return types.updates.Difference(
                new_messages=[],
                new_encrypted_messages=[],
                other_updates=[update],
                chats=[],
                users=[],
                state=types.updates.State(
                    pts=8,
                    qts=0,
                    date=datetime.fromtimestamp(2000, UTC),
                    seq=0,
                    unread_count=0,
                ),
            )

    transport = object.__new__(PersonalTransport)
    transport.store, transport.client = app.store, Client()
    transport.bind_receipts(app.store.send_operations.record_receipts)
    original = json.dumps(dict(pts=4, qts=0, date=1000))
    with app.store.transaction() as db:
        db.execute("INSERT INTO cursors VALUES ('global_difference', ?)", (original,))
        db.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON cursors
                   WHEN NEW.key LIKE 'receipt:%'
                   BEGIN SELECT RAISE(ABORT, 'authored receipt failure'); END""")

    async def intake():
        if ingress == "gap":
            await transport.catch_up()
        else:
            await transport._receipt(update)

    with pytest.raises(sqlite3.IntegrityError, match="authored receipt failure"):
        await intake()
    assert (
        app.store.rows("SELECT value FROM cursors WHERE key='global_difference'")[0][
            "value"
        ]
        == original
    )
    assert not app.store.send_operations.check_receipts(unknown["id"]).receipts
    with app.store.transaction() as db:
        db.execute("DROP TRIGGER fail_receipt")
    await intake()
    assert app.store.send_operations.get(unknown["id"])["state"] == "unknown"
    newer = app.store.mutate_draft(
        KEY,
        app.store.draft(KEY)["revision"],
        lambda value: value.update(text="Newer words"),
    )
    if ingress == "gap":
        assert (
            json.loads(
                app.store.rows(
                    "SELECT value FROM cursors WHERE key='global_difference'"
                )[0]["value"]
            )["pts"]
            == 8
        )
    # Receipts can be applied while offline, without another native check.
    app.transport.connected = False
    checked = await app.delivery.recheck(unknown["id"])
    assert checked["state"] == "confirmed"
    # Late confirmation never touches the newer composer text.
    assert app.store.draft(KEY) == newer


async def test_partial_album_receipt_prevents_a_total_refusal(tmp_path):
    from backend.tests.test_rebuild import attach, edit, fixture

    app = fixture(tmp_path)
    attach(app)
    attach(app)
    draft = edit(app, "Authored caption")
    app.transport.lose_receipt = True
    unknown = await app.delivery.send(KEY, draft["revision"])
    owner = app.store.send_operations
    owner.record_refusal(unknown["id"], "Authored native refusal")
    owner.record_receipts(ReceiptBatch(((unknown["random_ids"][0], 42),)))
    assert owner.check_receipts(unknown["id"]).needs_refresh
    app.transport.connected = False
    checked = await app.delivery.recheck(unknown["id"])
    assert checked["state"] == "unknown" and "could not be checked" in checked["check"]
    assert [a["id"] for a in checked["attachments"]] == draft["attachments"]
    owner.record_receipts(ReceiptBatch(((unknown["random_ids"][1], 43),)))
    assert (await app.delivery.recheck(unknown["id"]))["state"] == "confirmed"


@pytest.mark.parametrize("pairs", [((99, 42),), (), ((1, True),)])
def test_invalid_or_unrelated_text_receipts_do_not_confirm(local, pairs):
    from enloq.domain.models import Refused

    operation = prepare_text(local)
    with pytest.raises(Refused):
        local.send_operations.confirm(operation["id"], ReceiptBatch(pairs))
    assert local.send_operations.get(operation["id"])["state"] == "dispatching"
    assert local.send_operations.get(operation["id"])["text"] == "Authored reply"
