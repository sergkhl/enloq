"""Manual suggestion behavior through the shared service, with fake providers."""

import asyncio
import json
import logging

import pytest
from enloq.domain.models import Conflict, Refused
from enloq.logs import SafeFormatter
from enloq.storage.accounts import AccountRegistry

from backend.tests.test_rebuild import fixture, message
from backend.tests.test_suggestion_generation import Engine

KEY = "1/user:42"


async def finish(app):
    for _ in range(4):
        await app.process_due()


async def test_manual_draft_only_ignores_automation_and_preserves_cursor_and_undo(
    tmp_path,
):
    app = fixture(tmp_path)
    app.suggestions.enabled = lambda: False
    app.suggestions.set_settings(KEY, False, "professional")
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="Friday does not work."))
    before = app.store.draft(KEY)
    first = app.suggestions.generate(KEY, before["revision"], 20)
    assert first.state == "preparing" and first.kind == "manual"
    assert (
        app.suggestions.generate(KEY, before["revision"], 20).source_time
        == first.source_time
    )
    assert (
        len(app.store.rows("SELECT id FROM jobs WHERE role='response_suggestions'"))
        == 1
    )
    await finish(app)
    view = app.suggestions.view(KEY)
    assert view.state == "ready" and view.style == "professional"
    assert app.store.draft(KEY) == before
    packet = next(p for role, p in app.engine.calls if role == "response_suggestions")
    assert packet["draft"] == before["text"] and packet["cursor"] == 20
    assert packet["style"] == "professional" and packet["manual"] is True
    selected = app.suggestions.insert(KEY, before["revision"], view.attempt, 0)
    assert selected["draft"]["text"].startswith(before["text"][:20])
    assert selected["draft"]["text"].endswith(before["text"][20:])
    assert selected["draft"]["undo"] == before["text"]
    assert not app.store.rows("SELECT id FROM operations")


async def test_manual_after_outgoing_fresh_attempts_and_style_snapshot(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, outgoing=True))
    app.suggestions.generate(KEY, 0)
    app.suggestions.set_settings(KEY, True, "professional")
    await finish(app)
    first = app.suggestions.view(KEY)
    assert first.state == "ready" and first.style == "friendly"
    app.suggestions.generate(KEY, 0)
    await finish(app)
    second = app.suggestions.view(KEY)
    assert second.state == "ready" and second.attempt != first.attempt
    assert second.style == "professional"


async def test_manual_late_output_and_existing_set_withdraw_on_draft_or_context_change(
    tmp_path,
):
    app = fixture(tmp_path)
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="My intended reply."))
    app.suggestions.generate(KEY, 1)
    app.store.mutate_draft(KEY, 1, lambda d: d.update(text="Revised intent."))
    await finish(app)
    assert app.suggestions.view(KEY).reason == "changed"
    assert not app.engine.calls
    app.suggestions.generate(KEY, 2)
    await finish(app)
    view = app.suggestions.view(KEY)
    assert view.state == "ready"
    app.sync.ingest(message(app))
    assert app.suggestions.view(KEY).reason == "changed"
    with pytest.raises(Conflict):
        app.suggestions.insert(KEY, 2, view.attempt, 0)
    assert app.store.draft(KEY)["text"] == "Revised intent."


async def test_manual_lifetime_from_request_and_no_empty_context_generation(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    with pytest.raises(Refused):
        app.suggestions.generate(KEY, 0)
    app.sync.ingest(message(app, date=now[0] - 24 * 3600, outgoing=True))
    app.suggestions.generate(KEY, 0)
    await finish(app)
    assert app.suggestions.view(KEY).state == "ready"
    now[0] += 12 * 3600
    assert app.suggestions.view(KEY).reason == "expired"


async def test_a_new_message_arriving_out_of_order_withdraws_manual_options(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 10), target=False)
    app.suggestions.generate(KEY, 0)
    await finish(app)
    assert app.suggestions.view(KEY).state == "ready"
    app.sync.ingest(message(app, 9, text="Authored late arrival"), target=False)
    assert app.suggestions.view(KEY).reason == "changed"


async def test_manual_dispatch_rechecks_private_target_eligibility(tmp_path):
    app = fixture(tmp_path)
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="Authored intent."))
    app.suggestions.generate(KEY, 1)
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"ai_eligible": False}))
    await finish(app)
    assert not app.engine.calls
    assert app.suggestions.view(KEY).state != "ready"
    assert app.store.draft(KEY)["text"] == "Authored intent."


@pytest.mark.parametrize("state", ["rewriting", "rewritten", "stopped"])
def test_removed_rewrite_recovery_unlocks_drafts_without_changing_kept_composition(
    tmp_path, state
):
    app = fixture(tmp_path)
    draft = app.store.mutate_draft(
        KEY,
        0,
        lambda d: d.update(
            text="Kept authored words.",
            undo="Original authored words.",
            reply_to=7,
            state=state,
            attempt="a" * 32,
        ),
    )
    app.store.recover()
    kept = app.store.draft(KEY)
    assert kept["state"] == "idle" and kept["undo"] is None and kept["attempt"] is None
    assert kept["text"] == (draft["undo"] if state == "rewriting" else draft["text"])
    assert (
        kept["reply_to"] == draft["reply_to"]
        and kept["attachments"] == draft["attachments"]
    )
    assert kept["revision"] > draft["revision"]


async def test_manual_owns_the_area_while_earlier_automatic_work_finishes(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app))
    requested = app.suggestions.generate(KEY, 0)
    now[0] += 31
    for _ in range(8):
        await app.process_due()
    assert app.suggestions.view(KEY).attempt == requested.attempt
    assert app.suggestions.record(KEY)["kind"] == "manual"
    assert (
        len(app.store.rows("SELECT id FROM jobs WHERE role='response_suggestions'"))
        == 1
    )


async def test_auto_off_during_manual_call_does_not_hide_it_and_edit_rejects_late_output(
    tmp_path,
):
    app = fixture(tmp_path)
    app.store.mutate_draft(
        KEY, 0, lambda d: d.update(text="Keep this explicit intent.")
    )
    app.suggestions.generate(KEY, 1)
    entered, release = asyncio.Event(), asyncio.Event()
    generate = app.engine.generate

    async def held(role, packet):
        if role == "response_suggestions":
            entered.set()
            await release.wait()
        return await generate(role, packet)

    app.engine.generate = held
    running = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    app.suggestions.enabled = lambda: False
    app.suggestions.set_settings(KEY, False, "idioms")
    release.set()
    await running
    assert app.suggestions.view(KEY).state == "ready"
    assert app.suggestions.view(KEY).style == "friendly"
    app.suggestions.set_settings(KEY, False, "professional")
    app.store.mutate_draft(KEY, 1, lambda d: d.update(text="The revised draft."))
    app.suggestions.generate(KEY, 2)
    entered.clear()
    release.clear()
    running = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    app.store.mutate_draft(
        KEY, 2, lambda d: d.update(text="New words during the call.")
    )
    release.set()
    await running
    assert app.suggestions.view(KEY).reason == "changed"
    assert app.store.draft(KEY)["text"] == "New words during the call."


async def test_manual_context_is_bounded_and_uses_only_a_cached_direct_reply(tmp_path):
    app = fixture(tmp_path)
    for identity in range(1, 32):
        app.sync.ingest(
            message(app, identity, text="Authored input " + "x" * 400), target=False
        )
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="My intent.", reply_to=1))
    app.suggestions.generate(KEY, 1)
    record = app.suggestions.record(KEY)
    items = record["packet"]["messages"]
    assert items[0]["id"] == 1 and items[-1]["id"] == 31
    assert len(items) <= 21 and all(m["id"] == 1 or m["id"] >= 12 for m in items)
    assert len(json.dumps(items, ensure_ascii=False).encode()) < 6000
    assert not app.transport.exact_reads


async def test_old_settings_convert_without_changing_global_automation_or_spending(
    tmp_path,
):
    app = fixture(tmp_path)
    app.engine = Engine()
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="Authored draft."))
    app.suggestions.generate(KEY, 1)
    await finish(app)
    before = app.store.rows("SELECT * FROM reservations")
    assert before
    registry = AccountRegistry(tmp_path)
    registry.set_suggestions_enabled(False)
    with app.store.transaction() as db:
        db.execute(
            "CREATE TABLE suggestion_choice (chat_key TEXT PRIMARY KEY,choice TEXT,changed REAL)"
        )
        db.executemany(
            "INSERT INTO suggestion_choice VALUES (?,?,1)",
            [("1/user:41", "off"), (KEY, "on"), ("1/user:43", "idioms")],
        )
    restarted = fixture(tmp_path)
    assert restarted.store.rows(
        "SELECT chat_key,automatic,style FROM suggestion_settings ORDER BY chat_key"
    ) == [
        {"chat_key": "1/user:41", "automatic": 0, "style": "friendly"},
        {"chat_key": KEY, "automatic": 1, "style": "friendly"},
        {"chat_key": "1/user:43", "automatic": 1, "style": "idioms"},
    ]
    assert not registry.suggestions_enabled()
    assert restarted.store.rows("SELECT * FROM reservations") == before
    assert not restarted.store.rows(
        "SELECT name FROM sqlite_master WHERE name='suggestion_choice'"
    )


async def test_debug_log_explains_scheduling_and_outcome_without_correspondence(
    tmp_path, caplog
):
    caplog.set_level(logging.DEBUG, logger="enloq.suggestions")
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.decider.emotion, app.decider.useful = "sad", 0.9
    app.sync.ingest(message(app, text="Private canary message"))
    now[0] += 31
    for _ in range(8):
        await app.process_due()
    app.suggestions.set_settings(KEY, False, "friendly")
    app.sync.ingest(message(app, 2, text="Private canary follow-up"))
    now[0] += 31
    for _ in range(8):
        await app.process_due()
    lines = [
        SafeFormatter().format(r)
        for r in caplog.records
        if r.name == "enloq.suggestions"
    ]
    assert any('"reason": "scheduled"' in line for line in lines)
    assert any('"reason": "published"' in line for line in lines)
    assert any('"reason": "chat_automatic_off"' in line for line in lines)
    assert all(
        "canary" not in line and "user:42" not in line and "chat_key" not in line
        for line in lines
    )
