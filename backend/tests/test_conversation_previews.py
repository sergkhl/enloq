"""Source-bound row/opening projections from authored local cache only."""

import pytest
from enloq.api.server import create_app
from enloq.application.blocks import rebuild
from enloq.config import Settings
from enloq.domain.models import MediaInfo
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_local_viewing import ack, see
from backend.tests.test_notifications import complete
from backend.tests.test_rebuild import fixture, message


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    return fixture(tmp_path, clock=lambda: now[0]), now


def preview(app):
    return app.previews.recent()[0]


def test_latest_native_identity_beats_partial_cache_and_drafts(rig):
    app, now = rig
    app.sync.ingest(message(app, 1, text="Older cached"))
    app.store.put_chat(
        app.store.chat("1/user:42").model_copy(update={"latest_message_id": 3}),
        activity=now[0] + 1,
    )
    app.store.mutate_draft("1/user:42", 0, lambda d: d.update(text="Never a preview"))
    row = preview(app)
    assert row.preview.message_id == 3 and row.preview.original == ""
    app.sync.ingest(message(app, 2, text="Still not latest"))
    assert preview(app).preview.original == ""
    app.sync.ingest(message(app, 3, text="Latest cached", date=now[0] + 1))
    assert preview(app).preview.original == "Latest cached"
    app.sync.deleted("user:42", [3])
    # ADR 52 Q10: a deleted message is previewed by what it last said.
    shown = preview(app).preview
    assert (shown.original, shown.ai, shown.deleted) == (
        "Latest cached",
        "Latest cached",
        True,
    )
    # Once hidden, the message before it gives the preview.
    app.store.history.hide("1/user:42", [3])
    shown = preview(app).preview
    assert (shown.message_id, shown.original, shown.deleted) == (
        2,
        "Still not latest",
        False,
    )


def test_unknown_legacy_latest_stays_blank_until_native_identity_arrives(rig):
    app, now = rig
    app.store.put_chat(app.store.chat("1/user:42"), activity=now[0])
    app.store.apply_message(message(app, 1))  # Same timestamp alone proves nothing.
    assert preview(app).preview.original == ""
    app.store.put_chat(
        app.store.chat("1/user:42").model_copy(update={"latest_message_id": 2})
    )
    assert preview(app).preview.original == ""
    app.store.apply_message(message(app, 2, text="Discovered latest"))
    assert preview(app).preview.original == "Discovered latest"


def test_ai_preview_requires_valid_current_completion_and_outgoing_uses_sent_text(rig):
    app, now = rig
    app.sync.ingest(message(app, 1, text="First original"))
    app.sync.ingest(message(app, 2, text="Last original"))
    complete(app, text="Combined valid rewrite")
    assert preview(app).preview.ai == "Combined valid rewrite"
    assert preview(app).preview.original == "Last original"
    with app.store.transaction() as db:
        db.execute("UPDATE blocks SET state='working',rendered='Partial prefix'")
    assert preview(app).preview.ai == "Last original"
    complete(app)
    # Intake/rebuild race: still-ready block has stale source fingerprint.
    app.store.apply_message(message(app, 2, text="Changed before rebuild"))
    assert preview(app).preview.ai == "Changed before rebuild"
    app.sync.ingest(message(app, 3, text="Actually sent", outgoing=True))
    assert (
        preview(app).preview.ai == preview(app).preview.original == "You: Actually sent"
    )
    assert preview(app).preview.date == now[0]


@pytest.mark.parametrize(
    "media,text,filename,expected",
    [
        ("image", "", None, "Image"),
        ("image", "Caption", None, "Image: Caption"),
        ("audio", "", None, "Voice message"),
        ("video_note", "", None, "Video message"),
        ("document", "", "brief.pdf", "File: brief.pdf"),
        ("video", "Caption", None, "Video: Caption"),
    ],
)
def test_typed_media_previews_never_expose_hidden_context(
    rig, media, text, filename, expected
):
    app, _ = rig
    app.sync.ingest(
        message(
            app,
            text=text,
            media=media,
            media_info=MediaInfo(file_name=filename) if filename else None,
        )
    )
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES (?,?,?)",
            ("1/user:42/1", 1, '{"image":"Hidden image context"}'),
        )
    assert preview(app).preview.original == preview(app).preview.ai == expected


def test_opening_and_cached_window_are_bounded_and_do_not_acknowledge_or_fetch(rig):
    app, now = rig
    app.store.viewing.baseline("1/user:42", 10, 250)
    for i in range(1, 251):
        app.store.apply_message(message(app, i, date=now[0] - 3000 + i))
    rebuild(app.store, "1/user:42", target=False)
    with app.store.transaction() as db:
        db.execute("UPDATE source_views SET ready=? WHERE candidate=1", (now[0],))
    row = preview(app)
    assert row.unseen and row.opening_source == 11
    assert app.conversation("1/user:42")["opening_source"] == 11
    assert len(app.conversation("1/user:42")["messages"]) == 100
    page = app.previews.window("1/user:42", 11)
    assert [m["message_id"] for m in page] == list(range(1, 86))
    assert len(app.previews.window("1/user:42", 120)) == 100
    assert all(m["viewed_content"] for m in page)
    assert preview(app).opening_source == 11
    ack(app, see(app, 11, 13))
    assert preview(app).opening_source == 12
    # The Unread Separator's captures arrive with the opening target (ADR 28).
    opened = app.conversation("1/user:42")
    assert opened["unread_sources"][:3] == [12, 14, 15]
    assert len(opened["unread_sources"]) == 100
    assert (
        not app.transport.reads
        and not app.transport.exact_reads
        and not app.engine.calls
    )
    plan = app.store.rows(
        "EXPLAIN QUERY PLAN SELECT body FROM messages WHERE chat_key=? AND message_id>=? ORDER BY message_id LIMIT 75",
        ("1/user:42", 11),
    )
    assert any("conversation_order" in r["detail"] for r in plan)


def test_same_peer_across_accounts_keeps_preview_and_opening_separate(tmp_path):
    a, b = fixture(tmp_path), fixture(tmp_path, account=2)
    a.sync.ingest(message(a, text="Account one"))
    b.sync.ingest(message(b, text="Account two"))
    with a.store.transaction() as db:
        db.execute("UPDATE source_views SET ready=1")
    assert preview(a).unseen and not preview(b).unseen
    assert preview(a).preview.original == "Account one"
    assert preview(b).preview.original == "Account two"


def test_http_local_window_has_no_history_or_model_side_effects(tmp_path):
    server = create_app(Settings(data=tmp_path))
    app = server.state.accounts.get(1)
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path)
        row = client.get("/api/accounts/1/chats").json()[0]
        assert "preview" in row and "unseen" in row
        path = f"/api/accounts/1/chats/{row['peer']}/window"
        assert client.get(path + "?around=1").status_code == 200
        assert client.get(path + "?around=0").status_code == 422
        assert client.get(path + "?around=9999999999999999999").status_code == 422
        assert not app.transport.reads and not app.engine.calls
