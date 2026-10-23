"""Exact, browser-owned review and deliberate group send Resume (ADR 34)."""

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import Field

from ..application.delivery import operation_view
from ..domain.models import Model, Refused
from .scope import MEMBER_ACCOUNT


class SendResumeReviewRequest(Model):
    pass


class SendResumeRequest(Model):
    token: str = Field(pattern=r"^[a-f0-9]{64}$")


class SendResumeReview(Model):
    token: str
    expires: float
    account_id: int
    peer: str
    title: str
    operation: dict


def send_resume_routes(manager):
    router = APIRouter(
        prefix="/api/accounts/{account_id}/operations", dependencies=MEMBER_ACCOUNT
    )

    def current(request, account_id, app):
        def guard():
            if not request.app.state.browser_auth.active(request.state.local_session):
                raise HTTPException(
                    401,
                    "Connect this browser from the Mac launcher",
                    headers={"Clear-Site-Data": '"cache"'},
                )
            if manager.get(account_id) is not app:
                raise Refused("The active account workspace changed")

        return guard

    @router.post("/{identity}/review-resume", response_model=SendResumeReview)
    async def review(
        account_id: int,
        request: Request,
        body: SendResumeReviewRequest,
        identity: str = Path(pattern=r"^[a-f0-9]{32}$"),
    ):
        async with manager.lease(account_id) as app:
            return await app.delivery.review_resume(
                identity,
                browser=request.state.local_session,
                guard=current(request, account_id, app),
            )

    @router.post("/{identity}/resume")
    async def resume(
        account_id: int,
        request: Request,
        body: SendResumeRequest,
        identity: str = Path(pattern=r"^[a-f0-9]{32}$"),
    ):
        async with manager.lease(account_id) as app:
            operation = await app.delivery.resume(
                identity,
                body.token,
                browser=request.state.local_session,
                guard=current(request, account_id, app),
            )
            return operation_view(operation, app.store.clock())

    return router
