"""Authored reminder timing, sets, completion and Kept Messages (ADR 58).

No Telegram or provider calls: the offline engine writes every set.
"""

from datetime import datetime

import pytest
from enloq.domain.models import Conflict, ProcessingPaused, Refused
from enloq.domain.reminders import LEAD, ReminderRule

from backend.tests.test_manual_suggestions import finish
from backend.tests.test_rebuild import edit, fixture, message

KEY = "1/user:42"
# A Sunday; the default digest is 09:00 UTC.
START = "2026-10-25T08:00:00+00:00"


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def rule(**values):
    values.setdefault("start", "2026-10-25")
    values.setdefault("timezone", "UTC")
    values.setdefault("language", "English")
    return ReminderRule(**values)


def scheduled(tmp_path, **values):
    now = [stamp(START)]
    app = fixture(tmp_path, clock=lambda: now[0])
    values.setdefault("weekday", 6)
    view = app.reminders.create(KEY, rule(**values))
    return app, now, view.id


def due(app, now, schedule):
    now[0] = app.reminders.get(KEY, schedule).next_at
    app.reminders.tick()
    return app.reminders.get(KEY, schedule).occurrence.id


def reminder_record(app):
    record = app.suggestions.record(KEY)
    assert record and record.get("reminders") is not None
    return record


def writers(app):
    return sum(role == "response_suggestions" for role, _ in app.engine.calls)


async def test_an_occurrence_is_prepared_two_minutes_early_and_pending_once_due(
    tmp_path,
):
    app, now, schedule = scheduled(tmp_path)
    next_at = app.reminders.get(KEY, schedule).next_at
    assert next_at == stamp("2026-10-25T09:00:00+00:00")
    now[0] = next_at - LEAD - 1
    app.reminders.tick()
    assert not app.store.rows("SELECT * FROM reminder_occurrences")
    assert app.suggestions.record(KEY) is None
    now[0] = next_at - LEAD + 1
    app.reminders.tick()
    # Being prepared, not yet pending: no bar, no alert, the set queued.
    occurrence = app.store.rows("SELECT * FROM reminder_occurrences")[0]
    assert app.reminders.get(KEY, schedule).occurrence is None
    assert app.reminders.get(KEY, schedule).next_at == next_at
    assert app.reminders.pending(KEY) == []
    record = reminder_record(app)
    assert record["reminders"] == [occurrence["id"]]
    assert record["packet"]["reminders"] == [
        {"intent": "check_in", "idea": "", "fallback_language": "English"}
    ]
    assert app.suggestions.view(KEY).state == "preparing"
    await finish(app)
    assert app.suggestions.view(KEY).state == "ready"
    now[0] = next_at
    app.reminders.tick()
    assert app.reminders.get(KEY, schedule).occurrence.id == occurrence["id"]
    assert [p.occurrence for p in app.reminders.pending(KEY)] == [occurrence["id"]]
    # The schedule moved on to its next delivery; no second set was made.
    assert app.reminders.get(KEY, schedule).next_at == stamp(
        "2026-11-01T09:00:00+00:00"
    )
    assert writers(app) == 1


async def test_one_set_covers_every_pending_reminder_and_answers_later_messages(
    tmp_path,
):
    app, now, first = scheduled(tmp_path)
    second = app.reminders.create(
        KEY,
        rule(
            weekday=6,
            delivery="exact",
            time="09:30",
            idea="Ask how the trip went [mention the photos]\nSecond line",
            language="Russian",
        ),
    ).id
    app.sync.ingest(message(app, 1, text="Can you bring the chairs?"), target=False)
    due(app, now, first)
    now[0] = stamp("2026-10-25T09:10:00+00:00")
    app.sync.ingest(message(app, 2, text="Are you free on Friday?"), target=False)
    due(app, now, second)
    record = reminder_record(app)
    assert len(record["reminders"]) == 2
    packet = record["packet"]
    assert [r["intent"] for r in packet["reminders"]] == ["check_in", "message_idea"]
    assert packet["reminders"][1]["fallback_language"] == "Russian"
    assert packet["fallback_language"] == "English"
    # Later messages are answered; earlier ones are reference only (Q6).
    assert [m["id"] for m in packet["messages"]] == [2]
    assert [m["id"] for m in packet["context"]] == [1]
    await finish(app)
    view = app.suggestions.view(KEY)
    assert view.state == "ready" and view.kind == "manual"
    sent = next(p for r, p in reversed(app.engine.calls) if r == "response_suggestions")
    assert [r["idea"] for r in sent["reminders"]][1].startswith("Ask how the trip")
    assert [p.idea for p in app.reminders.pending(KEY)][1].startswith("Ask how")


async def test_new_messages_refresh_the_set_after_quiet_seconds_and_stop_automatic_sets(
    tmp_path,
):
    app, now, schedule = scheduled(tmp_path)
    due(app, now, schedule)
    await finish(app)
    first = reminder_record(app)["attempt"]
    app.decider.emotion, app.decider.useful = "sad", 0.9
    app.sync.ingest(message(app, 5, text="Authored sad news."))
    assert app.suggestions.view(KEY).reason == "changed"
    now[0] += 29
    app.reminders.tick()
    assert reminder_record(app)["attempt"] == first
    now[0] += 2
    app.reminders.tick()
    refreshed = reminder_record(app)
    assert refreshed["attempt"] != first and refreshed["trigger"] == "refresh"
    assert [m["id"] for m in refreshed["packet"]["messages"]] == [5]
    for _ in range(6):
        now[0] += 31
        await app.process_due()
    # The incoming block's processing prepared no automatic set (Q5).
    assert reminder_record(app)["attempt"] == refreshed["attempt"]
    assert app.suggestions.view(KEY).state == "ready"


async def test_opening_replaces_only_a_set_that_no_longer_fits(tmp_path):
    app, now, schedule = scheduled(tmp_path)
    due(app, now, schedule)
    await finish(app)
    shown = app.suggestions.view(KEY)
    assert app.suggestions.open_reminders(KEY).attempt == shown.attempt
    assert writers(app) == 1
    draft = edit(app, "Authored idea for the check-in")
    assert app.suggestions.view(KEY).reason == "changed"
    opened = app.suggestions.open_reminders(KEY)
    assert opened.attempt != shown.attempt and opened.state == "preparing"
    assert reminder_record(app)["packet"]["draft"] == draft["text"]

    async def failure(role, packet):
        raise RuntimeError("Authored provider failure")

    app.engine.generate = failure
    await finish(app)
    failed = app.suggestions.view(KEY)
    assert failed.state == "failed"
    # Never an unchanged failure again on opening; a press asks afresh.
    assert app.suggestions.open_reminders(KEY).attempt == failed.attempt
    pressed = app.suggestions.generate(KEY, draft["revision"])
    assert pressed.attempt != failed.attempt and pressed.state == "preparing"
    # Duplicate presses share the active attempt.
    assert app.suggestions.generate(KEY, draft["revision"]).attempt == pressed.attempt


async def test_without_an_idea_any_later_outgoing_message_completes_it(tmp_path):
    app, now, schedule = scheduled(tmp_path)
    # Recent conversation never suppresses a reminder.
    app.sync.ingest(message(app, date=now[0], outgoing=True))
    identity = due(app, now, schedule)
    app.sync.ingest(message(app, 2, text="Authored incoming after due"))
    app.suggestions.open_reminders(KEY)
    assert app.reminders.get(KEY, schedule).occurrence.id == identity
    now[0] += 1
    # Native intake from any Telegram client completes it, of any type.
    app.store.apply_message(message(app, 3, outgoing=True, media="sticker"))
    assert app.reminders.get(KEY, schedule).occurrence is None
    assert app.suggestions.view(KEY).reason == "changed"


async def test_with_an_idea_only_a_send_from_its_set_completes_it(tmp_path):
    app, now, schedule = scheduled(tmp_path, idea="Ask about the exam")
    identity = due(app, now, schedule)
    await finish(app)
    now[0] += 1
    app.store.apply_message(message(app, 3, outgoing=True, text="Elsewhere"))
    assert app.reminders.get(KEY, schedule).occurrence.id == identity
    app.suggestions.open_reminders(KEY)
    await finish(app)
    view = app.suggestions.view(KEY)
    draft = app.store.draft(KEY)
    chosen = app.suggestions.insert(KEY, draft["revision"], view.attempt, 0)["draft"]
    assert chosen["reminders"] == [identity]
    # Undo drops the mark: a send of the restored draft completes nothing.
    undone = app.rewriting.undo(KEY, chosen["revision"])
    assert "reminders" not in undone
    # The changed draft withdrew the set; opening prepares a fresh one.
    assert app.suggestions.view(KEY).reason == "changed"
    app.suggestions.open_reminders(KEY)
    await finish(app)
    view = app.suggestions.view(KEY)
    draft = app.store.draft(KEY)
    chosen = app.suggestions.insert(KEY, draft["revision"], view.attempt, 0)["draft"]
    # Edits keep the mark.
    edited = app.store.mutate_draft(
        KEY, chosen["revision"], lambda d: d.update(text=d["text"] + " Edited.")
    )
    assert edited["reminders"] == [identity]
    now[0] += 1
    sent = await app.delivery.send(KEY, edited["revision"])
    assert sent["state"] == "confirmed" and sent["reminders"] == [identity]
    assert app.reminders.get(KEY, schedule).occurrence is None
    assert "reminders" not in app.store.draft(KEY)


@pytest.mark.parametrize("pending", [True, False])
def test_with_an_idea_a_plain_send_never_completes_it(tmp_path, pending):
    app, now, schedule = scheduled(tmp_path, idea="Ask about the exam")
    identity = due(app, now, schedule)
    draft = edit(app, "My own words")
    import asyncio

    if pending:
        asyncio.run(app.delivery.submit(KEY, draft["revision"]))
    else:
        asyncio.run(app.delivery.send(KEY, draft["revision"]))
    assert app.reminders.get(KEY, schedule).occurrence.id == identity


def test_dismiss_completes_and_a_once_schedule_ends_with_its_idea(tmp_path):
    app, now, schedule = scheduled(
        tmp_path, repeat="once", start="2026-10-27", idea="Wish luck on 25 oct"
    )
    view = app.reminders.get(KEY, schedule)
    assert view.kept.day == "2026-10-25"
    identity = due(app, now, schedule)
    assert app.reminders.get(KEY, schedule).next_at is None
    with pytest.raises(Conflict):
        app.reminders.dismiss(KEY, schedule, "f" * 32)
    before = app.store.draft(KEY)
    listed = app.reminders.dismiss(KEY, schedule, identity)
    assert listed.items == []
    assert app.store.draft(KEY) == before
    assert not app.store.rows("SELECT * FROM reminder_schedules")
    assert not app.store.rows("SELECT * FROM reminder_kept_days")


def test_a_pending_occurrence_never_lapses_and_only_the_latest_stays(tmp_path):
    app, now, schedule = scheduled(tmp_path)
    first = due(app, now, schedule)
    now[0] += 6 * 86400
    app.reminders.tick()
    assert app.reminders.get(KEY, schedule).occurrence.id == first
    now[0] = stamp("2026-11-01T09:00:00+00:00") - LEAD
    app.reminders.tick()
    rows = app.store.rows("SELECT id FROM reminder_occurrences")
    assert len(rows) == 1 and rows[0]["id"] != first


def test_downtime_keeps_only_the_latest_missed_and_digest_waits(tmp_path):
    app, now, schedule = scheduled(tmp_path)
    now[0] = stamp("2027-02-10T14:00:00+00:00")
    app.reminders.tick()
    view = app.reminders.get(KEY, schedule)
    assert view.occurrence.calendar_date == "2027-02-07"
    assert view.next_at == stamp("2027-02-14T09:00:00+00:00")
    assert app.store.rows("SELECT notify_at FROM reminder_occurrences")[0][
        "notify_at"
    ] == stamp("2027-02-11T09:00:00+00:00")


def test_pause_resume_no_backfill_and_revision_guards(tmp_path):
    app, now, schedule = scheduled(tmp_path)
    due(app, now, schedule)
    app.suggestions.set_settings(KEY, False, "professional")
    assert app.reminders.get(KEY, schedule).status == "paused"
    assert app.reminders.pending(KEY) == [] and not app.reminders.owns(KEY)
    now[0] += 60 * 86400
    app.suggestions.set_settings(KEY, True, "professional")
    assert app.reminders.get(KEY, schedule).occurrence is None
    assert app.reminders.get(KEY, schedule).next_at > now[0]
    before = app.store.draft(KEY)
    edited = app.reminders.edit(KEY, schedule, 1, rule(repeat="monthly", month_day=31))
    assert edited.revision == 2
    with pytest.raises(Conflict):
        app.reminders.edit(KEY, schedule, 1, rule())
    with pytest.raises(Refused):
        app.reminders.edit(KEY, schedule, 2, rule(repeat="once", start="2020-01-01"))
    removed = app.reminders.remove(KEY, schedule, edited.revision)
    assert removed.items == []
    with pytest.raises(Conflict):
        app.reminders.get(KEY, schedule)
    assert app.store.draft(KEY) == before


async def test_account_ai_off_keeps_timing_and_prepares_no_set(tmp_path):
    app, now, schedule = scheduled(tmp_path)

    def paused():
        raise ProcessingPaused("AI processing is Off for this account")

    app.admit_ai = paused
    identity = due(app, now, schedule)
    assert app.suggestions.record(KEY) is None
    assert app.suggestions.open_reminders(KEY).state == "none"
    with pytest.raises(ProcessingPaused):
        app.suggestions.generate(KEY, 0)
    app.admit_ai = lambda: None
    assert app.suggestions.open_reminders(KEY).state == "preparing"
    await finish(app)
    assert app.suggestions.view(KEY).state == "ready"
    assert app.reminders.get(KEY, schedule).occurrence.id == identity


async def test_a_set_waits_for_budget_without_touching_the_draft(tmp_path):
    from enloq.storage.accounts import AccountRegistry

    from backend.tests.test_suggestion_generation import Engine

    app, now, schedule = scheduled(tmp_path)
    app.engine = Engine()
    AccountRegistry(tmp_path).set_budget(0)
    identity = due(app, now, schedule)
    await finish(app)
    assert app.suggestions.view(KEY).model_dump(include={"state", "reason"}) == {
        "state": "waiting",
        "reason": "budget",
    }
    assert app.reminders.get(KEY, schedule).occurrence.id == identity
    assert not app.engine.calls and app.store.draft(KEY)["text"] == ""


async def test_removing_a_schedule_withdraws_its_set_and_rejects_late_output(
    tmp_path,
):
    import asyncio

    app, now, schedule = scheduled(tmp_path)
    due(app, now, schedule)
    entered, release = asyncio.Event(), asyncio.Event()
    generate = app.engine.generate

    async def held(role, packet):
        entered.set()
        await release.wait()
        return await generate(role, packet)

    app.engine.generate = held
    work = asyncio.create_task(app.process_due())
    await asyncio.wait_for(entered.wait(), 5)
    app.reminders.remove(KEY, schedule, 1)
    release.set()
    await work
    assert app.suggestions.view(KEY).state != "ready"
    assert app.store.draft(KEY)["text"] == ""


def test_kept_messages_copy_a_past_day_and_mirror_edits_and_deletions(tmp_path):
    now = [stamp(START)]
    app = fixture(tmp_path, clock=lambda: now[0])
    day = stamp("2026-10-23T12:00:00+00:00")
    for identity in range(1, 24):
        app.sync.ingest(
            message(app, identity, date=day + identity, text=f"Authored {identity}"),
            target=False,
        )
    app.sync.ingest(
        message(app, 30, date=stamp("2026-10-24T12:00:00+00:00"), text="Next day"),
        target=False,
    )
    view = app.reminders.create(KEY, rule(weekday=6, idea="About 23 Oct"))
    assert view.kept.model_dump() == {"day": "2026-10-23", "state": "kept", "count": 20}
    kept = app.store.rows("SELECT message_id FROM reminder_kept ORDER BY message_id")
    # The newest 20 of that day, none of the next.
    assert [r["message_id"] for r in kept] == list(range(4, 24))
    app.store.apply_message(
        message(app, 23, date=day + 23, revision=2, text="Authored edit")
    )
    app.store.apply_message(message(app, 22, date=day + 22, revision=2, deleted=True))
    now[0] += 31
    app.reminders.tick()
    rows = {
        r["message_id"]: r["body"]
        for r in app.store.rows("SELECT message_id,body FROM reminder_kept")
    }
    assert 22 not in rows and "Authored edit" in rows[23]
    # An expired original leaves its copy as it was.
    app.store.expire()
    now[0] += 40 * 86400
    app.store.expire()
    app.reminders.tick()
    assert len(app.store.rows("SELECT * FROM reminder_kept")) == 19
    # The date leaving the idea deletes the copy.
    edited = app.reminders.edit(KEY, view.id, view.revision, rule(weekday=6))
    assert edited.kept is None
    assert not app.store.rows("SELECT * FROM reminder_kept")


def test_a_day_still_ahead_is_kept_once_it_ends_and_an_old_one_has_nothing(
    tmp_path,
):
    now = [stamp(START)]
    app = fixture(tmp_path, clock=lambda: now[0])
    ahead = app.reminders.create(
        KEY, rule(repeat="once", start="2026-10-30", idea="Party on 2026-10-26")
    )
    assert ahead.kept.model_dump() == {
        "day": "2026-10-26",
        "state": "waiting",
        "count": 0,
    }
    now[0] = stamp("2026-10-26T15:00:00+00:00")
    app.sync.ingest(message(app, 1, date=now[0], text="Authored party talk"))
    app.reminders.tick()
    assert app.reminders.get(KEY, ahead.id).kept.state == "waiting"
    now[0] = stamp("2026-10-27T00:00:01+00:00")
    app.reminders.tick()
    assert app.reminders.get(KEY, ahead.id).kept.model_dump() == {
        "day": "2026-10-26",
        "state": "kept",
        "count": 1,
    }
    old = app.reminders.create(KEY, rule(weekday=6, idea="On 2026-08-01"))
    assert old.kept.state == "expired" and old.kept.count == 0


async def test_kept_messages_reach_the_set_of_their_schedule(tmp_path):
    now = [stamp(START)]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(
        message(app, 1, date=stamp("2026-10-20T12:00:00+00:00"), text="Authored plan"),
        target=False,
    )
    schedule = app.reminders.create(KEY, rule(weekday=6, idea="About 20.10")).id
    due(app, now, schedule)
    packet = reminder_record(app)["packet"]
    assert packet["kept"] == [
        {
            "day": "2026-10-20",
            "messages": [
                {
                    "id": 1,
                    "revision": 1,
                    "text": "Authored plan",
                    "links": [],
                    "outgoing": False,
                    "date": stamp("2026-10-20T12:00:00+00:00"),
                    "media": None,
                    "reply_to": None,
                    "forwarded": None,
                    "forwarded_from": None,
                }
            ],
        }
    ]
    await finish(app)
    assert app.suggestions.view(KEY).state == "ready"


def test_a_reminder_set_writes_with_its_frame_and_keeps_bracketed_ideas_out():
    import json

    from enloq.inference.base import EngineResponse, InvalidOutput
    from enloq.inference.guidance import REMINDERS_FRAME, instructions
    from enloq.inference.work import validate_output

    packet = {
        "manual": True,
        "draft": "",
        "reminders": [
            {"intent": "message_idea", "idea": "Ask [warmly] about the exam"},
            {"intent": "check_in", "idea": ""},
        ],
    }
    assert REMINDERS_FRAME in instructions("response_suggestions", packet)
    assert REMINDERS_FRAME not in instructions(
        "response_suggestions", {"manual": True, "draft": ""}
    )

    def written(text):
        content = {"options": [{"purpose": "clarify", "text": text}]}
        return validate_output(
            "response_suggestions", EngineResponse(json.dumps(content)), packet
        )

    with pytest.raises(InvalidOutput, match="^bracketed_text$"):
        written("How did the exam go? [warmly]")
    assert written("How did the exam go?").options[0].text == "How did the exam go?"
