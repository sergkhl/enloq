"""Authored reminder occurrences for phone/web journeys, offline fixture only."""

from datetime import datetime, timezone

from enloq.domain.models import Chat
from enloq.domain.reminders import ReminderRule
from fastapi import Request

PEER = "user:128"
KEY = "1/" + PEER


def register(app):
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
        current = conversation.reminders.view(KEY)
        if body.get("action") == "cleanup":
            return conversation.reminders.edit(KEY, current.revision, None)
        now = datetime.fromtimestamp(store.clock(), timezone.utc)
        rule = ReminderRule(
            frequency="weekly",
            weekday=now.weekday(),
            timezone="UTC",
            delivery="exact",
            time=now.strftime("%H:%M"),
            language="English",
        )
        conversation.reminders.edit(KEY, current.revision, rule)
        with store.transaction() as db:
            db.execute(
                "UPDATE reminder_schedules SET next_at=? WHERE chat_key=?",
                (now.replace(second=0, microsecond=0).timestamp(), KEY),
            )
        conversation.reminders.tick()
        return conversation.reminders.view(KEY)
