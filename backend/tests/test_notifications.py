"""Finite authored event matrix: no Telegram, model or push-service calls."""

import json

import pytest
from enloq.application.notifications import IncomingNotifications

from backend.tests.test_rebuild import fixture, message


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    return app, now


def incoming(app, identity=1, **changes):
    source = message(app, identity, **changes)
    app.sync.ingest(source, live=True)
    return source


def complete(app, *, text="Validated rewrite", state="ready"):
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state=?,rendered=?,rendered_revision=revision",
            (state, text),
        )


def pending(app):
    app.notifications.advance()
    return [
        app.notifications.candidate(r)
        for r in app.notifications.rows()
        if r["state"] == "pending"
    ]


def test_quiet_extension_ready_unchanged_output_and_new_burst_in_same_block(rig):
    app, now = rig
    incoming(app, text="Same words")
    now[0] += 20
    incoming(app, 2, text="Same words")
    complete(app, text="Same words")
    now[0] += 11
    assert pending(app) == []
    now[0] += 20
    candidate = pending(app)[0]
    assert candidate["count"] == 2 and candidate["text"] == "Same words"
    row = app.notifications.rows()[0]
    assert app.notifications.finish(row)
    assert not app.notifications.finish(row)
    assert pending(app) == []
    incoming(app, 3)
    complete(app)
    now[0] += 31
    second = pending(app)[0]
    assert second["id"] != candidate["id"] and second["count"] == 1
    assert len(app.store.rows("SELECT * FROM blocks")) == 1
    assert not app.store.rows("SELECT * FROM reservations")


def test_multiple_blocks_preview_newest_and_wait_for_completion(rig):
    app, now = rig
    incoming(app)
    app.sync.ingest(message(app, 2, outgoing=True))
    incoming(app, 3)
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='ready',rendered='Older result',rendered_revision=revision WHERE id='1/user:42/1'"
        )
    now[0] += 31
    assert pending(app) == []
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='ready',rendered='Newest result',rendered_revision=revision WHERE id='1/user:42/3'"
        )
    assert pending(app)[0]["text"] == "Newest result"
    assert pending(app)[0]["count"] == 2


@pytest.mark.parametrize("reason", ["off", "budget", "failed", "nothing", "format"])
def test_unavailable_original_fallback_consumes_burst_and_never_realerts(rig, reason):
    app, now = rig
    incoming(
        app,
        text="" if reason == "nothing" else "Original latest",
        media="document" if reason == "nothing" else None,
    )
    if reason in {"off", "budget"}:
        app.notifications.unavailable = lambda: True
    elif reason == "failed":
        complete(app, state="failed")
    elif reason == "format":
        app.rewriting.paused_roles = lambda: ["incoming_rewrite"]
    now[0] += 31
    candidate = pending(app)[0]
    assert candidate["text"] == ("File" if reason == "nothing" else "Original latest")
    app.notifications.finish(app.notifications.rows()[0])
    complete(app)
    assert pending(app) == []


@pytest.mark.parametrize(
    "source", ["catchup", "history", "outgoing", "edit", "recovery", "old_live"]
)
def test_non_new_sources_do_not_manufacture_events(rig, source):
    app, now = rig
    value = message(app)
    if source == "edit":
        app.sync.ingest(value, live=False)
        app.sync.ingest(
            value.model_copy(update={"revision": 2, "text": "Edited"}), live=True
        )
    elif source == "outgoing":
        incoming(app, outgoing=True)
    elif source == "old_live":
        incoming(app, date=now[0] - 60)
    else:
        app.sync.ingest(value, target=source != "history", arrival=source == "recovery")
    complete(app)
    now[0] += 31
    assert pending(app) == []
    assert app.notifications.rows() == []


@pytest.mark.parametrize(
    "invalid", ["edit", "delete", "mute", "authorization", "expiry", "output"]
)
def test_pending_candidates_are_revalidated_before_dispatch(rig, invalid):
    app, now = rig
    source = incoming(app)
    complete(app)
    now[0] += 31
    assert len(pending(app)) == 1
    row = app.notifications.rows()[0]
    if invalid == "edit":
        app.sync.ingest(
            source.model_copy(update={"text": "Changed within same timestamp"})
        )
    elif invalid == "delete":
        app.sync.deleted("user:42", [1])
    elif invalid == "mute":
        app.store.put_chat(
            app.store.chat("1/user:42").model_copy(update={"muted": True})
        )
    elif invalid == "authorization":
        app.authorized = lambda: False
    elif invalid == "expiry":
        now[0] += 301
    else:
        with app.store.transaction() as db:
            db.execute("UPDATE blocks SET state='working',rendered='Provisional'")
    assert app.notifications.candidate(row) is None
    assert pending(app) == []


def test_restart_and_latest_pending_only_without_content_in_event_state(rig):
    app, now = rig
    incoming(app, text="Private authored text")
    complete(app)
    now[0] += 31
    first = pending(app)[0]
    app.notifications = IncomingNotifications(app)
    assert pending(app)[0]["id"] == first["id"]
    app.sync.new_incoming = app.notifications.admit
    incoming(app, 2)
    assert pending(app) == []
    complete(app)
    now[0] += 31
    assert pending(app)[0]["id"] != first["id"]
    assert len(app.store.rows("SELECT * FROM incoming_notices")) == 1
    assert "Private authored text" not in json.dumps(app.notifications.rows())


def test_muted_and_excluded_targets_and_same_peer_across_accounts(tmp_path):
    now = [2_000_000_000.0]
    apps = [fixture(tmp_path, account=i, clock=lambda: now[0]) for i in (1, 2)]
    for app in apps:
        incoming(app)
        complete(app)
    now[0] += 31
    values = [pending(app)[0] for app in apps]
    assert {v["account_id"] for v in values} == {1, 2}
    assert len({v["id"] for v in values}) == 2
    for app, changes in zip(apps, [{"muted": True}, {"ai_eligible": False}]):
        app.store.put_chat(
            app.store.chat(f"{app.store.account_id}/user:42").model_copy(update=changes)
        )
        incoming(app, 2)
        assert pending(app) == []


def test_edit_during_quiet_updates_candidate_without_extending_burst(rig):
    app, now = rig
    source = incoming(app)
    original_due = app.notifications.rows()[0]["quiet"]
    now[0] += 10
    app.sync.ingest(
        source.model_copy(update={"text": "Current edited source"}), live=True
    )
    assert app.notifications.rows()[0]["quiet"] == original_due
    complete(app, text="Current validated edit")
    now[0] += 21
    assert pending(app)[0]["text"] == "Current validated edit"


async def test_native_edited_event_is_not_a_new_message_even_when_unseen(rig):
    from enloq.transport.telethon import PersonalTransport
    from telethon import events, types

    app, now = rig
    transport = object.__new__(PersonalTransport)
    app.store.viewing.baseline("1/user:42", 1, 1)
    transport.verified, transport.sync = True, app.sync
    transport.admit_peer = lambda *args: True

    async def normalize(native):
        return message(app, native.id)

    transport.normalize = normalize
    for event_type, identity in [
        (events.MessageEdited.Event, 1),
        (events.NewMessage.Event, 2),
    ]:
        event = event_type(
            types.Message(
                peer_id=types.PeerUser(42), id=identity, message="Authored", date=None
            )
        )
        await transport._incoming(event)
    assert json.loads(app.notifications.rows()[0]["sources"]) == [2]
    viewed = {r["message_id"]: r for r in app.store.rows("SELECT * FROM source_views")}
    assert viewed[1]["candidate"] == 0 and viewed[1]["viewed"]
    assert viewed[2]["candidate"] == 1 and viewed[2]["viewed"] is None
