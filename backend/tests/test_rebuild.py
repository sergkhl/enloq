import sys

import pytest
from enloq.api.server import create_app
from enloq.application.conversations import Conversations
from enloq.config import Settings
from enloq.domain.models import Chat, Message, Refused
from enloq.storage.accounts import AccountRegistry
from enloq.storage.lock import OwnerLock
from enloq.storage.store import WINDOW, Store
from enloq.transport.fake import FakeTransport
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser


def fixture(tmp_path, *, account=1, clock=None):
    registry = AccountRegistry(tmp_path)
    # These baseline scenarios exercise rewriting independently of ADR 35
    # admission, with text rewritten as before ADR 54's combining.
    registry.set_processing_threshold(0)
    registry.set_text_rewriting(True)
    store = Store(
        tmp_path / f"{account}/rewriting.sqlite3",
        account_id=account,
        clock=clock or (lambda: 2_000_000_000),
        shared_path=registry.path,
    )
    store.put_chat(
        Chat(
            account_id=account,
            peer="user:42",
            title="Authored conversation",
            kind="private",
            ai_eligible=True,
        )
    )
    transport = FakeTransport()
    transport.account_id = account
    return Conversations(store, transport)


def message(app, identity=1, **kwargs):
    return Message(
        account_id=app.store.account_id,
        peer="user:42",
        message_id=identity,
        revision=kwargs.pop("revision", 1),
        date=kwargs.pop("date", app.store.clock()),
        text=kwargs.pop("text", "Authored request, not a promise."),
        **kwargs,
    )


def test_fresh_schema_and_account_isolation(tmp_path):
    a, b = fixture(tmp_path), fixture(tmp_path, account=2)
    a.sync.ingest(message(a))
    assert a.store.recent_chats() and not b.store.recent_chats()
    with pytest.raises(Refused):
        b.store.apply_message(message(a))
    with pytest.raises(Refused):
        b.store.chat("1/user:42")
    assert not any(
        r["name"].startswith("quality_")
        for r in AccountRegistry(tmp_path).rows("SELECT name FROM sqlite_master")
    )
    assert "enloq.evaluation.service" not in sys.modules
    assert a.store.path.stat().st_mode & 0o777 == 0o600


def test_window_activity_and_draft_persistence(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.store.put_chat(
        a.store.chat("1/user:42").model_copy(update={"muted": True, "archived": True})
    )
    a.sync.ingest(message(a, date=now[0] - WINDOW))
    a.store.mutate_draft("1/user:42", 0, lambda d: d.update(text="Keep this draft"))
    assert len(a.store.recent_chats()) == 1
    now[0] += 0.1
    assert not a.store.recent_chats()
    restarted = fixture(tmp_path, clock=lambda: now[0])
    restarted.store.expire()
    assert restarted.store.draft("1/user:42")["text"] == "Keep this draft"
    a.sync.ingest(message(a, 2, outgoing=True))
    assert len(a.store.recent_chats()) == 1


async def test_exact_reply_read_and_unread_catchup(tmp_path):
    a = fixture(tmp_path)
    a.store.put_chat(
        a.store.chat("1/user:42").model_copy(update={"read_through": 2}),
        activity=a.store.clock(),
    )
    seen = []
    a.sync.changed = lambda m, target, **_: seen.append((m.message_id, target))
    a.transport.messages = [message(a, i) for i in range(1, 5)]
    await a.sync.scan("1/user:42")
    assert seen == [(1, False), (2, False), (3, True), (4, True)]
    a.transport.messages.append(
        message(a, 99, date=a.store.clock() - 40 * 86400, reply_to=98)
    )
    found = await a.direct("1/user:42", 99)
    assert found.message_id == 99
    assert a.store.message("1/user:42", 99) is None
    assert not a.store.rows("SELECT * FROM jobs")


def test_session_lock_and_authenticated_minimal_api(tmp_path):
    with OwnerLock(tmp_path / "session.owner.lock"):
        with pytest.raises(Refused):
            OwnerLock(tmp_path / "session.owner.lock").acquire()
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/accounts/1/chats").status_code == 401
        trust_browser(client, tmp_path / "app")
        chats = client.get("/api/accounts/1/chats").json()
        assert chats and all(c["kind"] == "private" for c in chats)
        assert client.get("/api/accounts/1/chats/user:101").status_code == 200
        assert client.get("/api/accounts/2/chats/user:101").status_code == 400
        assert client.get("/api/quality-control").status_code == 404
        assert not app.state.store.rows("SELECT * FROM jobs")


async def test_blocks_quiet_pause_boundaries_and_no_repeated_work(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a, 1, text="Do not confirm yet."))
    now[0] += 29
    await a.process_due()
    assert not a.engine.calls
    a.sync.ingest(message(a, 2, text="Only if the price is under 20."))
    now[0] += 29
    await a.process_due()
    assert not a.engine.calls
    now[0] += 1
    await a.process_due()
    assert len(a.engine.calls) == 1
    data = a.conversation("1/user:42")
    assert data["blocks"][0]["sources"] == [1, 2]
    assert "Do not confirm" in data["blocks"][0]["rendered"]
    a.sync.ingest(
        message(a, 2, text="Only if the price is under 20.", date=now[0] - 30)
    )
    for _ in range(3):
        a.conversation("1/user:42")
        await a.process_due()
    restarted = fixture(tmp_path, clock=lambda: now[0])
    restarted.store.recover()
    await restarted.process_due()
    assert not restarted.engine.calls
    a.sync.ingest(message(a, 3, outgoing=True))
    a.sync.ingest(message(a, 4, text="A separate request."))
    now[0] += 180
    a.sync.ingest(message(a, 5, text="After the gap."))
    assert [b["sources"] for b in a.conversation("1/user:42")["blocks"]] == [
        [1, 2],
        [4, 5],
    ]


async def test_direct_context_bound_and_no_reply_chain(tmp_path):
    from enloq.application.context import incoming

    a = fixture(tmp_path)
    a.transport.messages = [
        message(
            a,
            1,
            date=a.store.clock() - 20 * 86400,
            text="Older exact reference",
            reply_to=99,
        )
    ]
    for i in range(2, 28):
        a.sync.ingest(message(a, i, text=f"Context {i}", outgoing=True), target=False)
    a.sync.ingest(message(a, 28, text="Please use that option.", reply_to=1))
    packet = await incoming(a, a.store.rows("SELECT * FROM blocks")[0])
    assert len(packet["context"]) == 21
    assert packet["context"][0]["id"] == 1
    assert 99 not in [m["id"] for m in packet["context"]]
    assert len(a.store.rows("SELECT * FROM jobs")) == 1


async def test_stale_publication_deletion_and_expired_retry(tmp_path):
    import asyncio

    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a))
    now[0] += 30
    await a.process_due()
    assert a.conversation("1/user:42")["blocks"][0]["rendered"]
    await a.process_due()  # The Emotion Classification that follows it.
    a.sync.ingest(message(a, 2, text="Another request"))
    assert a.conversation("1/user:42")["blocks"][0]["rendered"] is None
    started, finish = asyncio.Event(), asyncio.Event()
    original = a.engine.generate

    async def delayed(role, packet):
        started.set()
        await finish.wait()
        return await original(role, packet)

    a.engine.generate = delayed
    now[0] += 30
    task = asyncio.create_task(a.process_due())
    await started.wait()
    a.sync.ingest(message(a, 2, revision=2, text="Changed request", date=now[0] - 30))
    finish.set()
    await task
    assert a.conversation("1/user:42")["blocks"][0]["rendered"] is None
    a.sync.deleted("user:42", [2])
    assert a.conversation("1/user:42")["blocks"][0]["rendered"] is None
    now[0] += WINDOW + 1
    with pytest.raises(Refused):
        a.rewriting.retry("1/user:42/1")


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_failed_output_explicit_retry_and_shared_unknown_budget(tmp_path):
    from enloq.inference.base import EngineResponse
    from enloq.inference.work import FLAT_UNKNOWN

    a, b = fixture(tmp_path), fixture(tmp_path, account=2)
    AccountRegistry(tmp_path).set_budget(2 * FLAT_UNKNOWN)

    class Engine:
        name, model, provider = "muse", "exact-test-route", "meta"

        async def generate(self, role, packet):
            return EngineResponse({"wrong": "format"})

    for app in (a, b):
        app.engine = Engine()
        app.sync.ingest(message(app, date=app.store.clock() - 31))
        with app.store.transaction() as db:
            db.execute("UPDATE jobs SET due=0")
    await a.process_due()
    assert a.conversation("1/user:42")["blocks"][0]["state"] == "failed"
    assert len(a.store.rows("SELECT * FROM receipts")) == 1
    await a.process_due()
    assert len(a.store.rows("SELECT * FROM receipts")) == 1
    a.rewriting.retry("1/user:42/1")
    await a.process_due()
    await b.process_due()
    # Two unknown outcomes reach the shared $0.02 budget before account 2 runs.
    assert len(a.store.rows("SELECT * FROM reservations")) == 2
    assert not b.store.rows("SELECT * FROM reservations")
    assert "budget" in b.conversation("2/user:42")["blocks"][0]["error"].lower()


async def test_already_read_initial_history_and_expired_dispatch(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a), target=False)
    assert not a.store.rows("SELECT * FROM jobs")
    a.sync.ingest(message(a))
    assert not a.store.rows("SELECT * FROM jobs")
    a.sync.ingest(message(a, revision=2, text="Edited source"))
    assert len(a.store.rows("SELECT * FROM jobs")) == 1
    now[0] += WINDOW + 1
    await a.process_due()
    assert not a.engine.calls
    assert a.conversation("1/user:42")["blocks"][0]["state"] == "failed"


async def test_deleted_first_fragment_keeps_block_identity_and_clears_text(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.sync.ingest(message(a, 1, text="First fragment"))
    a.sync.ingest(message(a, 2, text="Second fragment"))
    now[0] += 30
    await a.process_due()
    a.sync.deleted("user:42", [1])
    block = a.conversation("1/user:42")["blocks"][0]
    assert block["id"] == "1/user:42/1" and block["sources"] == [2]
    assert block["rendered"] is None and block["state"] == "queued"


def edit(app, text, reply_to=None):
    key = f"{app.store.account_id}/user:42"
    draft = app.store.draft(key)
    return app.store.mutate_draft(
        key,
        draft["revision"],
        lambda d: d.update(text=text, reply_to=reply_to, undo=None, state="idle"),
    )


async def test_outgoing_tones_undo_and_no_implicit_delivery(tmp_path):
    a = fixture(tmp_path)
    registry = AccountRegistry(tmp_path)
    for tone in ("neutral", "professional", "friendly"):
        registry.set_tone(tone)
        draft = edit(
            a, "I may agree only if the total is below 20. Do not confirm yet."
        )
        result = await a.rewriting.outgoing("1/user:42", draft["revision"])
        assert a.engine.calls[-1][1]["tone"] == tone
        assert result["undo"] == draft["text"] and result["text"] == draft["text"]
        assert not a.transport.deliveries
        restored = a.rewriting.undo("1/user:42", result["revision"])
        assert restored["text"] == draft["text"] and restored["undo"] is None
    assert fixture(tmp_path, account=2).rewriting.preferences()["tone"] == "friendly"
    empty = edit(a, " ")
    with pytest.raises(Refused):
        await a.rewriting.outgoing("1/user:42", empty["revision"])


@pytest.mark.parametrize("change", ["text", "tone"])
async def test_late_outgoing_does_not_overwrite_edits_or_tone(tmp_path, change):
    import asyncio

    from enloq.domain.models import Conflict

    a = fixture(tmp_path)
    draft = edit(a, "My original words")
    start, finish = asyncio.Event(), asyncio.Event()

    async def delayed(role, packet):
        start.set()
        await finish.wait()
        return {"text": "A rewritten version"}

    a.engine.generate = delayed
    task = asyncio.create_task(a.rewriting.outgoing("1/user:42", draft["revision"]))
    await start.wait()
    if change == "text":
        edit(a, "New words typed while waiting")
    else:
        AccountRegistry(tmp_path).set_tone("professional")
    finish.set()
    with pytest.raises(Conflict):
        await task
    assert a.store.draft("1/user:42")["text"] == (
        "New words typed while waiting" if change == "text" else "My original words"
    )
    assert not a.transport.deliveries


async def test_send_snapshot_unknown_restart_reconciliation_and_new_edits(tmp_path):
    a = fixture(tmp_path)
    a.sync.ingest(message(a))
    draft = edit(a, "This is the reviewed text", reply_to=1)
    a.transport.lose_receipt = True
    result = await a.delivery.send("1/user:42", draft["revision"])
    assert result["state"] == "unknown" and result["reply_to"] == 1
    repeated = await a.delivery.send("1/user:42", draft["revision"])
    assert repeated["id"] == result["id"] and len(a.transport.deliveries) == 1
    # A newer draft is the owner's own; the uncertain send holds back only
    # submissions, which wait behind it rather than bypassing it.
    edit(a, "A newer unsent draft")
    a.store.recover()
    from enloq.application.delivery import Delivery

    restarted = Delivery(
        Store(
            a.store.path,
            account_id=1,
            clock=a.store.clock,
            shared_path=a.store.shared_path,
        ),
        a.transport,
    )
    confirmed = await restarted.recheck(result["id"])
    assert confirmed["state"] == "confirmed" and len(a.transport.deliveries) == 1
    assert a.store.draft("1/user:42")["text"] == "A newer unsent draft"


async def test_send_limits_wrong_account_and_confirmed_revision_clear(tmp_path):
    a = fixture(tmp_path)
    draft = edit(a, "Ordinary unquoted message")
    a.transport.account_id = 2
    with pytest.raises(Refused):
        await a.delivery.send("1/user:42", draft["revision"])
    a.transport.account_id = 1
    too_long = edit(a, "😀" * 2049)
    with pytest.raises(Refused):
        await a.delivery.send("1/user:42", too_long["revision"])
    assert not a.transport.deliveries
    draft = edit(a, "Ordinary unquoted message")
    confirmed = await a.delivery.send("1/user:42", draft["revision"])
    assert confirmed["state"] == "confirmed" and confirmed["reply_to"] is None
    assert a.store.draft("1/user:42")["text"] == ""
    assert a.store.messages("1/user:42")[-1].text == draft["text"]


def image_bytes():
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (32, 24), (40, 100, 70)).save(output, format="PNG")
    return output.getvalue()


async def test_incoming_image_caption_hidden_context_cache_and_cleanup(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.transport.media["user:42", 1] = image_bytes()
    a.sync.ingest(message(a, 1, text="Please use the marked option.", media="image"))
    now[0] += 30
    await a.process_due()
    assert a.conversation("1/user:42")["blocks"][0]["state"] == "ready"
    role, packet = a.engine.calls[0]
    assert packet["messages"][0]["text"] == "Please use the marked option."
    assert packet["images"][0]["message_id"] == 1
    assert not list(a.media.workspace.path.glob("*.bin"))
    cached = a.media.cached(a.store.message("1/user:42", 1))
    assert cached["image_context"]
    assert "image_context" not in a.conversation("1/user:42")
    await a.process_due()  # The Emotion Classification that follows it.
    a.sync.ingest(message(a, 2, text="The smaller size, please."))
    now[0] += 30
    await a.process_due()
    assert not a.engine.calls[-1][1]["images"]
    assert a.engine.calls[-1][1]["messages"][0]["image_context"]
    a.sync.deleted("user:42", [1])
    assert not a.store.rows("SELECT * FROM derivatives")


async def test_image_only_and_unreadable_target_image_is_marked_not_analysed(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    a.transport.media["user:42", 1] = image_bytes()
    a.sync.ingest(message(a, 1, text="", media="image"))
    now[0] += 30
    await a.process_due()
    block = a.conversation("1/user:42")["blocks"][0]
    assert block["state"] == "ready" and block["rendered"] == ""
    await a.process_due()  # The Emotion Classification that follows it.
    # ADR 16: an undecodable target image becomes a visible placeholder.
    a.sync.ingest(message(a, 2, text="A broken image", media="image"))
    a.transport.media["user:42", 2] = b"not an image"
    now[0] += 30
    await a.process_due()
    assert len(a.engine.calls) == 2
    packet = a.engine.calls[-1][1]
    assert (
        not packet["images"] and packet["messages"][1]["placeholder"]["kind"] == "image"
    )
    block = a.conversation("1/user:42")["blocks"][0]
    assert block["state"] == "ready"
    assert [i["id"] for i in block["unanalysed"]] == [2]
    assert not list(a.media.workspace.path.glob("*.bin"))


async def test_voice_transcript_cache_restart_and_explicit_failure_retry(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    calls = []

    class Speech:
        fail = True

        async def transcribe(self, path):
            calls.append(path)
            if self.fail:
                raise TimeoutError()
            return {"text": "Please do not confirm yet.", "language": "en"}

    speech = Speech()
    a.media.transcriber = speech
    a.transport.media["user:42", 1] = b"authored fake audio input"
    a.sync.ingest(message(a, 1, text="", media="audio"))
    now[0] += 30
    await a.process_due()
    assert len(calls) == 1 and not calls[0].exists()
    assert not a.engine.calls
    a.sync.ingest(message(a, 2, text="Another fragment"))
    now[0] += 30
    await a.process_due()
    assert len(calls) == 1
    speech.fail = False
    a.rewriting.retry("1/user:42/1")
    await a.process_due()
    assert len(calls) == 2
    assert a.conversation("1/user:42")["blocks"][0]["state"] == "ready"
    restarted = fixture(tmp_path, clock=lambda: now[0])
    cached = await restarted.media.transcript(restarted.store.message("1/user:42", 1))
    assert cached["text"] == "Please do not confirm yet."
    assert not list(a.media.workspace.path.iterdir())


async def test_cancelled_local_voice_is_not_automatically_retried(tmp_path):
    import asyncio

    a = fixture(tmp_path)
    started = asyncio.Event()

    class Speech:
        async def transcribe(self, path):
            started.set()
            await asyncio.Future()

    a.media.transcriber = Speech()
    a.transport.media["user:42", 1] = b"authored fake input"
    a.sync.ingest(message(a, 1, text="", media="audio"))
    task = asyncio.create_task(a.media.transcript(a.store.message("1/user:42", 1)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(a.media.workspace.path.iterdir())
    with pytest.raises(Refused, match="explicitly retry"):
        await a.media.transcript(a.store.message("1/user:42", 1))


def attach(app, name="authored.png"):
    import base64

    key = f"{app.store.account_id}/user:42"
    return app.attachments.add(
        key,
        app.store.draft(key)["revision"],
        name,
        base64.b64encode(image_bytes()).decode(),
    )


async def test_durable_image_only_send_and_caption_rewrite_preserve_selection(tmp_path):
    a = fixture(tmp_path)
    draft = attach(a)
    identity = draft["attachments"][0]
    value, path = a.attachments.get("1/user:42", identity)
    assert path.stat().st_mode & 0o777 == 0o600
    assert fixture(tmp_path).store.draft("1/user:42")["attachments"] == [identity]
    draft = edit(a, "I prefer this one, if it fits.")
    rewritten = await a.rewriting.outgoing("1/user:42", draft["revision"])
    assert rewritten["attachments"] == [identity]
    undone = a.rewriting.undo("1/user:42", rewritten["revision"])
    assert undone["attachments"] == [identity]
    draft = edit(a, "")
    sent = await a.delivery.send("1/user:42", draft["revision"])
    assert sent["state"] == "confirmed" and sent["text"] == ""
    assert len(a.transport.deliveries) == 1
    assert not path.exists() and not a.store.draft("1/user:42")["attachments"]


async def test_partial_media_receipts_never_resend_and_upload_failure_keeps_images(
    tmp_path,
):
    a = fixture(tmp_path)
    attach(a)
    draft = attach(a, "second.png")
    a.transport.upload_failure = True
    failed = await a.delivery.send("1/user:42", draft["revision"])
    assert failed["state"] == "refused" and not a.transport.deliveries
    # The submission keeps both images for a deliberate Retry.
    assert len(failed["attachments"]) == 2
    a.attachments.collect()
    assert all(a.attachments.get("1/user:42", i["id"]) for i in failed["attachments"])
    a.transport.upload_failure = False
    a.transport.partial_receipts = 1
    a.delivery.retry(failed["id"])
    result = await a.delivery.drain(failed["id"])
    assert result["state"] == "unknown" and len(result["receipts"]) == 1
    assert len(a.transport.deliveries) == 2
    # Partial receipts never resend the album or allow Cancel.
    with pytest.raises(Refused):
        a.delivery.retry(result["id"])
    with pytest.raises(Refused):
        a.delivery.cancel(result["id"])
    repeated = await a.delivery.send("1/user:42", draft["revision"])
    assert repeated["id"] == result["id"] and len(a.transport.deliveries) == 2
    a.store.recover()
    confirmed = await a.delivery.recheck(result["id"])
    assert confirmed["state"] == "confirmed" and len(confirmed["receipts"]) == 2
    assert len(a.transport.deliveries) == 2


async def test_media_limits_ownership_and_pending_snapshot_lifetime(tmp_path):
    a = fixture(tmp_path)
    attach(a)
    draft = edit(a, "x" * 1025)
    with pytest.raises(Refused, match="1,024"):
        await a.delivery.send("1/user:42", draft["revision"])
    with pytest.raises(Refused):
        fixture(tmp_path, account=2).attachments.get(
            "2/user:42", draft["attachments"][0]
        )
    draft = edit(a, "Shared caption")
    operation = a.store.send_operations.prepare(
        "1/user:42", draft["revision"], snapshot=a.attachments.snapshot
    )
    identity = draft["attachments"][0]
    _, path = a.attachments.get("1/user:42", identity)
    # Accepted, the submission owns its image; the cleared composer does not.
    assert not a.store.draft("1/user:42")["attachments"]
    a.attachments.collect()
    assert path.exists()
    # Cancelled before any hand-off, it still shows exactly what was submitted.
    assert a.delivery.cancel(operation["id"])["state"] == "cancelled"
    assert not a.store.send_operations.claim(operation["id"])
    a.attachments.collect()
    assert path.exists()
    # Its bytes go only when the cancelled item itself expires.
    with a.store.transaction() as db:
        a.store.send_operations.expire(db, a.store.clock() + 1)
    a.attachments.collect()
    assert not path.exists()
