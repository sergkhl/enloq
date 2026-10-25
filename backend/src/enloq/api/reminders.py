"""Account-owned schedules/actions and the member's combined reminders."""

from fastapi import APIRouter, Request

from ..domain.reminders import (
    DigestSettings,
    ReminderAction,
    ReminderEdit,
    ReminderList,
    ReminderPreview,
    ReminderReview,
    ReminderRevision,
    ReminderRule,
    ReminderView,
    next_occurrence,
)
from ..domain.suggestions import SuggestionsView
from .scope import MEMBER_ACCOUNT


def reminder_routes(manager):
    router = APIRouter()
    account = APIRouter(
        prefix="/api/accounts/{account_id}/chats/{peer}/reminder",
        dependencies=MEMBER_ACCOUNT,
    )

    def target(account_id, peer):
        app = manager.get(account_id)
        return app, f"{account_id}/{peer}"

    @account.get("", response_model=ReminderView)
    def read(account_id: int, peer: str):
        app, key = target(account_id, peer)
        app.reminders.tick()
        return app.reminders.view(key)

    @account.post("/preview", response_model=ReminderPreview)
    def preview(account_id: int, peer: str, body: ReminderRule, request: Request):
        app, key = target(account_id, peer)
        app.reminders.private(key)
        settings = manager.reminders.settings(request.state.member, body.timezone)
        return {"next_at": next_occurrence(body, app.store.clock(), settings)[0]}

    @account.post("", response_model=ReminderView)
    def save(account_id: int, peer: str, body: ReminderEdit, request: Request):
        app, key = target(account_id, peer)
        app.reminders.private(key)
        settings = manager.reminders.settings(request.state.member, body.timezone)
        if body.delivery == "digest":
            manager.reminders.save_settings(
                request.state.member, settings, initialize=True
            )
        return app.reminders.edit(
            key, body.revision, ReminderRule(**body.model_dump(exclude={"revision"}))
        )

    @account.delete("", response_model=ReminderView)
    def remove(account_id: int, peer: str, body: ReminderRevision):
        app, key = target(account_id, peer)
        return app.reminders.edit(key, body.revision, None)

    @account.post("/dismiss", response_model=ReminderView)
    def dismiss(account_id: int, peer: str, body: ReminderAction):
        app, key = target(account_id, peer)
        app.reminders.tick()
        return app.reminders.dismiss(key, body.occurrence)

    @account.post("/review", response_model=SuggestionsView)
    def review(account_id: int, peer: str, body: ReminderReview):
        app, key = target(account_id, peer)
        return app.reminders.review(
            key, body.occurrence, body.revision, body.cursor, body.regenerate
        )

    @router.get("/api/reminders", response_model=ReminderList)
    def listing(request: Request):
        return {"items": manager.reminders.items(request.state.member)}

    @router.get("/api/reminders/settings", response_model=DigestSettings)
    def settings(request: Request):
        return manager.reminders.settings(request.state.member)

    @router.post("/api/reminders/settings", response_model=DigestSettings)
    def save_settings(body: DigestSettings, request: Request):
        manager.reminders.tick()
        return manager.reminders.save_settings(request.state.member, body)

    router.include_router(account)
    return router
