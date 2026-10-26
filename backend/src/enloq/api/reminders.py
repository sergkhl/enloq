"""Per-schedule reminder routes and the member's combined reminders (ADR 58)."""

from fastapi import APIRouter, Request

from ..domain.reminders import (
    LANGUAGES,
    ChatReminders,
    DigestSettings,
    Languages,
    ReminderAction,
    ReminderEdit,
    ReminderList,
    ReminderPreview,
    ReminderRevision,
    ReminderRule,
    ReminderView,
    Timezones,
    timezones,
)
from ..domain.suggestions import SuggestionsView
from .scope import MEMBER_ACCOUNT


def reminder_routes(manager):
    router = APIRouter()
    account = APIRouter(
        prefix="/api/accounts/{account_id}/chats/{peer}/reminders",
        dependencies=MEMBER_ACCOUNT,
    )

    def target(account_id, peer):
        app = manager.get(account_id)
        return app, f"{account_id}/{peer}"

    def settings(request, rule):
        """The member's digest settings, first saved with an initial
        timezone when a digest schedule is."""
        value = manager.reminders.settings(request.state.member, rule.timezone)
        if rule.delivery == "digest":
            manager.reminders.save_settings(
                request.state.member, value, initialize=True
            )
        return value

    @account.get("", response_model=ChatReminders)
    def chat(account_id: int, peer: str):
        app, key = target(account_id, peer)
        app.reminders.tick()
        return app.reminders.chat(key)

    @account.post("/preview", response_model=ReminderPreview)
    def preview(account_id: int, peer: str, body: ReminderRule, request: Request):
        """The next delivery and what saving would keep, before saving."""
        app, key = target(account_id, peer)
        value = manager.reminders.settings(request.state.member, body.timezone)
        return app.reminders.preview(key, body, value)

    @account.post("", response_model=ReminderView)
    def create(account_id: int, peer: str, body: ReminderRule, request: Request):
        app, key = target(account_id, peer)
        app.reminders.private(key)
        settings(request, body)
        return app.reminders.create(key, body)

    @account.post("/open", response_model=SuggestionsView)
    def open_chat(account_id: int, peer: str):
        """Opening a chat with pending reminders prepares its set when the
        current one no longer fits; never an unchanged failure again (Q5)."""
        app, key = target(account_id, peer)
        return app.suggestions.open_reminders(key)

    @account.get("/{schedule}", response_model=ReminderView)
    def read(account_id: int, peer: str, schedule: str):
        app, key = target(account_id, peer)
        app.reminders.tick()
        return app.reminders.get(key, schedule)

    @account.post("/{schedule}", response_model=ReminderView)
    def edit(
        account_id: int, peer: str, schedule: str, body: ReminderEdit, request: Request
    ):
        app, key = target(account_id, peer)
        app.reminders.private(key)
        rule = ReminderRule(**body.model_dump(exclude={"revision"}))
        settings(request, rule)
        return app.reminders.edit(key, schedule, body.revision, rule)

    @account.delete("/{schedule}", response_model=ChatReminders)
    def remove(account_id: int, peer: str, schedule: str, body: ReminderRevision):
        app, key = target(account_id, peer)
        return app.reminders.remove(key, schedule, body.revision)

    @account.post("/{schedule}/dismiss", response_model=ChatReminders)
    def dismiss(account_id: int, peer: str, schedule: str, body: ReminderAction):
        app, key = target(account_id, peer)
        app.reminders.tick()
        return app.reminders.dismiss(key, schedule, body.occurrence)

    @router.get("/api/reminders", response_model=ReminderList)
    def listing(request: Request):
        return {"items": manager.reminders.items(request.state.member)}

    @router.get("/api/reminders/settings", response_model=DigestSettings)
    def digest(request: Request):
        return manager.reminders.settings(request.state.member)

    @router.post("/api/reminders/settings", response_model=DigestSettings)
    def save_digest(body: DigestSettings, request: Request):
        manager.reminders.tick()
        return manager.reminders.save_settings(request.state.member, body)

    @router.get("/api/reminders/timezones", response_model=Timezones)
    def zones():
        return {"timezones": timezones()}

    @router.get("/api/reminders/languages", response_model=Languages)
    def languages():
        return {
            "languages": [
                {"code": code, "name": name, "native": native}
                for code, name, native in LANGUAGES
            ]
        }

    router.include_router(account)
    return router
