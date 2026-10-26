"""Authored reminders for phone/web journeys, offline fixture only (ADR 58).

`setup` gives the fixture chat one pending reminder with a Message Idea and
one upcoming check-in, and lets the offline engine prepare their set.
"""

from datetime import datetime, timedelta, timezone

from enloq.domain.models import Chat
from enloq.domain.reminders import ReminderRule
from fastapi import Request

# Keep this empty-context chat separate from calming (128) and combined text
# (129), which run earlier in the shared browser fixture.
PEER = "user:130"
KEY = "1/" + PEER
IDEA = "Ask how the trip went"


def register(app):
    # Maestro's authored fixture requests carry no client credential.
    @app.post("/fixture/reminders")
    @app.post("/api/test/reminders")
    async def reminders(request: Request):
        body = await request.json()
        conversation = app.state.accounts.get(1)
        store = conversation.store
        if body.get("action") == "stats":
            return {
                "requests": len(
                    [
                        1
                        for role, _ in conversation.engine.calls
                        if role == "response_suggestions"
                    ]
                ),
                "record": conversation.suggestions.record(KEY),
            }
        store.put_chat(
            Chat(
                account_id=1,
                peer=PEER,
                title="Reminder fixture",
                kind="private",
                ai_eligible=True,
            )
        )
        conversation.suggestions.set_settings(KEY, True, "friendly")
        for item in conversation.reminders.chat(KEY).items:
            conversation.reminders.remove(KEY, item.id, item.revision)
        draft = store.draft(KEY)
        if draft["text"]:
            store.mutate_draft(
                KEY,
                draft["revision"],
                lambda d: (d.update(text="", undo=None), d.pop("reminders", None)),
            )
        if body.get("action") == "cleanup":
            return conversation.reminders.chat(KEY)
        now = datetime.fromtimestamp(store.clock(), timezone.utc)
        today = now.date()
        # Weekly at the minute just past, so its latest occurrence fell due now.
        due = now - timedelta(minutes=1)
        pending = conversation.reminders.create(
            KEY,
            ReminderRule(
                repeat="weeks",
                start=due.date().isoformat(),
                weekday=due.weekday(),
                timezone="UTC",
                delivery="exact",
                time=due.strftime("%H:%M"),
                language="English",
                idea=IDEA,
            ),
        )
        conversation.reminders.create(
            KEY,
            ReminderRule(
                repeat="weeks",
                start=today.isoformat(),
                weekday=(today.weekday() + 3) % 7,
                timezone="UTC",
                language="English",
            ),
        )
        # The first one fell due a minute ago: the tick creates its pending
        # occurrence and prepares the chat's set.
        with store.transaction() as db:
            db.execute(
                "UPDATE reminder_schedules SET next_at=? WHERE id=?",
                (now.timestamp() - 60, pending.id),
            )
        conversation.reminders.tick()
        return conversation.reminders.chat(KEY)
