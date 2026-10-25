"""Authored reminder timing and review cases; no Telegram or provider calls."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from enloq.domain.models import Conflict, ProcessingPaused
from enloq.domain.reminders import (
    DigestSettings,
    ReminderRule,
    local_time,
    next_occurrence,
)

from backend.tests.test_manual_suggestions import finish
from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def rule(**values):
    return ReminderRule(timezone="UTC", language="English", **values)


def scheduled(tmp_path, **values):
    now = [stamp("2026-10-25T08:00:00+00:00")]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.reminders.edit(KEY, 0, rule(weekday=6, **values))
    return app, now


def due(app, now):
    now[0] = app.reminders.view(KEY).next_at
    app.reminders.tick()
    return app.reminders.view(KEY).occurrence.id


@pytest.mark.parametrize("year,expected", [(2024, "2024-02-29"), (2025, "2025-02-28")])
def test_monthly_short_month_keeps_selected_date(year, expected):
    selected = rule(frequency="monthly", month_day=31, delivery="exact")
    settings = DigestSettings()
    feb, day = next_occurrence(
        selected, stamp(f"{year}-02-01T00:00:00+00:00"), settings
    )
    assert day == expected and selected.month_day == 31
    _, following = next_occurrence(selected, feb, settings)
    assert following == f"{year}-03-31"


def test_weekly_saved_zone_digest_mapping_and_future_only():
    selected = ReminderRule(timezone="Asia/Tokyo", language="ja", weekday=0)
    settings = DigestSettings(timezone="America/Los_Angeles", time="09:00")
    due_at, day = next_occurrence(
        selected, stamp("2026-10-25T00:00:00+00:00"), settings
    )
    assert day == "2026-10-26"
    assert (
        datetime.fromtimestamp(due_at, ZoneInfo(settings.timezone)).isoformat()
        == "2026-10-25T09:00:00-07:00"
    )
    assert next_occurrence(selected, due_at, settings)[1] == "2026-11-02"
    assert selected.timezone == "Asia/Tokyo"


def test_dst_gap_advances_to_first_valid_time_and_fold_has_one_occurrence():
    settings = DigestSettings()
    selected = ReminderRule(
        timezone="America/New_York",
        language="en",
        weekday=6,
        delivery="exact",
        time="02:30",
    )
    spring, _ = next_occurrence(selected, stamp("2026-03-08T00:00:00-05:00"), settings)
    assert (
        datetime.fromtimestamp(spring, ZoneInfo(selected.timezone)).isoformat()
        == "2026-03-08T03:00:00-04:00"
    )
    selected = selected.model_copy(update={"time": "01:30"})
    autumn, _ = next_occurrence(selected, stamp("2026-11-01T00:00:00-04:00"), settings)
    assert datetime.fromtimestamp(autumn, ZoneInfo(selected.timezone)).fold == 0
    assert next_occurrence(selected, autumn, settings)[1] == "2026-11-08"


def test_migration_is_empty_and_recurring_state_survives_content_expiry_and_restart(
    tmp_path,
):
    app, now = scheduled(tmp_path)
    identity = due(app, now)
    app.store.expire()
    restarted = fixture(tmp_path, clock=lambda: now[0])
    assert restarted.reminders.view(KEY).occurrence.id == identity
    assert restarted.reminders.view(KEY).schedule.weekday == 6
    other = fixture(tmp_path / "empty")
    assert other.store.rows("SELECT * FROM reminder_schedules") == []
    assert not app.store.recent_chats()


def test_ticks_coalesce_downtime_to_latest_missed_and_delay_digest(tmp_path):
    app, now = scheduled(tmp_path)
    now[0] = stamp("2027-02-10T14:00:00+00:00")
    app.reminders.tick()
    first = app.reminders.view(KEY)
    assert first.occurrence.calendar_date == "2027-02-07"
    assert first.next_at == stamp("2027-02-14T09:00:00+00:00")
    assert app.store.rows("SELECT notify_at FROM reminder_occurrences")[0][
        "notify_at"
    ] == stamp("2027-02-11T09:00:00+00:00")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: app.reminders.tick(), range(12)))
    assert app.reminders.view(KEY).occurrence.id == first.occurrence.id
    now[0] = first.next_at
    app.reminders.tick()
    assert app.reminders.view(KEY).occurrence.id != first.occurrence.id
    assert len(app.store.rows("SELECT * FROM reminder_occurrences")) == 1


async def test_only_confirmed_later_outgoing_or_dismiss_completes(tmp_path):
    app, now = scheduled(tmp_path)
    # An active conversation before delivery never suppresses the occurrence.
    app.sync.ingest(message(app, date=now[0], outgoing=True))
    identity = due(app, now)
    app.sync.ingest(message(app, 2, text="Authored incoming after due"))
    assert app.reminders.view(KEY).occurrence.id == identity
    app.store.mutate_draft(
        KEY, 0, lambda draft: draft.update(text="Authored pending message")
    )
    operation = await app.delivery.submit(KEY, 1)
    assert app.reminders.view(KEY).occurrence.id == identity
    now[0] += 1
    # Native intake from any Telegram client completes it, without app UI.
    app.store.apply_message(message(app, 3, outgoing=True, media="sticker"))
    assert app.reminders.view(KEY).occurrence is None
    assert operation is not None
    next_identity = due(app, now)
    draft = app.store.draft(KEY)
    next_at = app.reminders.view(KEY).next_at
    app.reminders.dismiss(KEY, next_identity)
    assert app.store.draft(KEY) == draft
    assert app.reminders.view(KEY).next_at == next_at
    with pytest.raises(Conflict):
        app.reminders.dismiss(KEY, identity)


def test_pause_resume_no_backfill_and_edit_delete_revision_guards(tmp_path):
    app, now = scheduled(tmp_path)
    identity = due(app, now)
    app.suggestions.set_settings(KEY, False, "professional")
    assert app.reminders.view(KEY).status == "paused"
    now[0] += 60 * 86400
    app.suggestions.set_settings(KEY, True, "professional")
    assert app.reminders.view(KEY).occurrence is None
    assert app.reminders.view(KEY).next_at > now[0]
    before = app.store.draft(KEY)
    edited = app.reminders.edit(KEY, 1, rule(frequency="monthly", month_day=31))
    with pytest.raises(Conflict):
        app.reminders.edit(KEY, 1, rule())
    with pytest.raises(Conflict):
        app.reminders.review(KEY, identity, 0)
    removed = app.reminders.edit(KEY, edited.revision, None)
    assert removed.schedule is None and removed.revision == 3
    assert app.store.draft(KEY) == before


@pytest.mark.parametrize("style", ["friendly", "professional", "idioms"])
async def test_first_review_empty_context_style_language_and_reopen(tmp_path, style):
    app, now = scheduled(tmp_path)
    app.suggestions.set_settings(KEY, True, style)
    identity = due(app, now)
    before = app.store.draft(KEY)
    first = app.reminders.review(KEY, identity, 0, 0)
    assert first.kind == "manual"
    assert app.reminders.review(KEY, identity, 0).attempt == first.attempt
    await finish(app)
    view = app.suggestions.view(KEY)
    assert view.state == "ready" and view.style == style
    calls = len(app.engine.calls)
    assert app.reminders.review(KEY, identity, 0).attempt == view.attempt
    assert len(app.engine.calls) == calls
    packet = next(
        packet for role, packet in app.engine.calls if role == "response_suggestions"
    )
    assert packet["check_in"] == {
        "intent": "check_in",
        "occurrence": identity,
        "fallback_language": "English",
    }
    assert not packet.get("memory") and packet["messages"] == []
    assert app.store.draft(KEY) == before
    assert app.reminders.view(KEY).occurrence.id == identity


async def test_draft_additions_undo_and_changed_context_requires_explicit_generation(
    tmp_path,
):
    app, now = scheduled(tmp_path)
    identity = due(app, now)
    app.store.mutate_draft(
        KEY, 0, lambda draft: draft.update(text="My authored intent.")
    )
    app.reminders.review(KEY, identity, 1, 3)
    await finish(app)
    view = app.suggestions.view(KEY)
    inserted = app.suggestions.insert(KEY, 1, view.attempt, 0)
    assert inserted["draft"]["text"].startswith("My ")
    assert inserted["draft"]["text"].endswith("authored intent.")
    assert inserted["draft"]["undo"] == "My authored intent."
    calls = len(app.engine.calls)
    assert app.reminders.review(KEY, identity, 2).reason == "changed"
    await finish(app)
    assert len(app.engine.calls) == calls
    app.reminders.review(KEY, identity, 2, regenerate=True)
    await finish(app)
    assert len(app.engine.calls) == calls + 1
    now[0] += 12 * 3600
    assert app.reminders.review(KEY, identity, 2).reason == "expired"


async def test_account_ai_off_keeps_reminder_and_reopen_needs_retry(tmp_path):
    app, now = scheduled(tmp_path)
    identity = due(app, now)

    def paused():
        raise ProcessingPaused("AI processing is Off for this account")

    app.admit_ai = paused
    with pytest.raises(ProcessingPaused):
        app.reminders.review(KEY, identity, 0)
    assert app.reminders.view(KEY).occurrence.id == identity
    app.admit_ai = lambda: None
    assert app.reminders.review(KEY, identity, 0).reason == "changed"
    assert not app.engine.calls
    app.reminders.review(KEY, identity, 0, regenerate=True)
    await finish(app)
    assert app.suggestions.view(KEY).state == "ready"


async def test_dismiss_during_generation_rejects_late_output_without_draft_changes(
    tmp_path,
):
    app, now = scheduled(tmp_path)
    identity = due(app, now)
    entered, release = asyncio.Event(), asyncio.Event()
    generate = app.engine.generate

    async def held(role, packet):
        entered.set()
        await release.wait()
        return await generate(role, packet)

    app.engine.generate = held
    app.reminders.review(KEY, identity, 0)
    work = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    app.reminders.dismiss(KEY, identity)
    release.set()
    await work
    assert app.suggestions.view(KEY).state != "ready"
    assert app.store.draft(KEY)["text"] == ""


async def test_reminder_context_is_reference_data_instead_of_an_unanswered_reply_target(
    tmp_path,
):
    app, now = scheduled(tmp_path)
    app.sync.ingest(
        message(app, text="Can you bring the chairs to the rehearsal?"), target=False
    )
    identity = due(app, now)
    app.reminders.review(KEY, identity, 0)
    packet = app.suggestions.record(KEY)["packet"]
    assert packet["messages"] == []
    assert [item["id"] for item in packet["context"]] == [1]
    assert packet["check_in"]["intent"] == "check_in"
    await finish(app)
    assert app.suggestions.view(KEY).state == "ready"
    writers = sum(role == "response_suggestions" for role, _ in app.engine.calls)
    app.sync.ingest(message(app, 2, text="Authored changed context"), target=False)
    await finish(app)
    assert app.suggestions.view(KEY).reason == "changed"
    assert (
        sum(role == "response_suggestions" for role, _ in app.engine.calls) == writers
    )


def test_digest_dst_gap():
    settings = DigestSettings(time="02:30", timezone="America/New_York")
    due_at = local_time(datetime(2026, 3, 8).date(), settings.time, settings.timezone)
    assert datetime.fromtimestamp(due_at, ZoneInfo(settings.timezone)).hour == 3


async def test_concurrent_reviews_share_an_attempt_and_failed_output_needs_explicit_retry(
    tmp_path,
):
    app, now = scheduled(tmp_path)
    identity = due(app, now)
    with ThreadPoolExecutor(4) as pool:
        reviews = list(
            pool.map(lambda _: app.reminders.review(KEY, identity, 0), range(8))
        )
    assert len({view.attempt for view in reviews}) == 1
    assert (
        len(app.store.rows("SELECT id FROM jobs WHERE role='response_suggestions'"))
        == 1
    )

    async def failure(role, packet):
        raise RuntimeError("Authored provider failure")

    app.engine.generate = failure
    await finish(app)
    assert app.suggestions.view(KEY).state == "failed"
    assert app.reminders.review(KEY, identity, 0).state == "failed"
    assert (
        len(app.store.rows("SELECT id FROM jobs WHERE role='response_suggestions'"))
        == 1
    )


async def test_review_waits_for_budget_without_mutating_draft_or_dismissing(tmp_path):
    app, now = scheduled(tmp_path)
    identity = due(app, now)
    from enloq.storage.accounts import AccountRegistry

    from backend.tests.test_suggestion_generation import Engine

    app.engine = Engine()
    AccountRegistry(tmp_path).set_budget(0)
    app.reminders.review(KEY, identity, 0)
    await finish(app)
    assert app.suggestions.view(KEY).state == "waiting"
    assert app.suggestions.view(KEY).reason == "budget"
    assert app.reminders.view(KEY).occurrence.id == identity
    assert not app.engine.calls and app.store.draft(KEY)["text"] == ""
