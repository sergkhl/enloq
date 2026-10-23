"""People (ADR 48): the owner's Join Requests and members, and each member's
own standing.

Only the owner reads People, approves or declines a request, removes a member
or sets a friend's budgets. Every trusted client reads its own member, and a
member may accept a changed Member Agreement or leave.
"""

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from ..domain import agreement
from ..domain.models import Model, Refused
from .accounts import BudgetState
from .auth import Agreement, AgreementAcceptance
from .scope import OWNER_ONLY


class MemberBudgetEdit(Model):
    family: Literal["daily", "group_briefing"]
    limit_usd: Decimal = Field(ge=0, le=10, decimal_places=2)


class MemberBudgets(Model):
    member: str
    budget: BudgetState
    group_budget: BudgetState


class JoinRequest(Model):
    id: str
    name: str
    username: str
    phone: str
    requested_at: float


class Person(MemberBudgets):
    name: str
    username: str
    phone: str
    approved_at: float | None
    accounts: int
    last_active: float | None


class People(Model):
    requests: list[JoinRequest]
    members: list[Person]


class Confirmation(Model):
    confirm: bool


class Removed(Model):
    # Whether Telegram confirmed every sign-out; otherwise the person should
    # end the Enloq session in Telegram's Devices.
    confirmed: bool


class Me(Model):
    id: str
    role: Literal["owner", "member"]
    state: Literal["active", "waiting"]
    name: str
    # The Member Agreement while a member hasn't accepted its current version.
    agreement: Agreement | None = None


def people_routes(manager, auth):
    router = APIRouter(prefix="/api")

    def budgets(member):
        timezone = manager.settings.timezone
        return {
            "member": member,
            "budget": manager.registry.budget(timezone=timezone, member=member),
            "group_budget": manager.registry.budget(
                timezone=timezone, family="group_briefing", member=member
            ),
        }

    def person(member, *states):
        row = manager.registry.member(member)
        if (
            not row
            or row["role"] != "member"
            or (states and row["state"] not in states)
        ):
            raise Refused("This person isn't on this server")
        return row

    def people():
        requests, members = [], []
        for row in manager.registry.members():
            if row["role"] != "member":
                continue
            if row["state"] == "waiting":
                requests.append(
                    {key: row[key] or "" for key in ("id", "name", "username", "phone")}
                    | {"requested_at": row["requested_at"] or 0}
                )
                continue
            members.append(
                budgets(row["id"])
                | {key: row[key] or "" for key in ("name", "username", "phone")}
                | {
                    "approved_at": row["approved_at"],
                    "accounts": len(manager.registry.accounts(row["id"])),
                    "last_active": auth.member_activity(row["id"]),
                }
            )
        return {"requests": requests, "members": members}

    @router.get("/people", response_model=People, dependencies=OWNER_ONLY)
    def list_people():
        return people()

    @router.post(
        "/people/{member}/approve", response_model=People, dependencies=OWNER_ONLY
    )
    async def approve(member: str):
        person(member, "waiting")
        await manager.approve(member)
        return people()

    @router.post(
        "/people/{member}/decline", response_model=People, dependencies=OWNER_ONLY
    )
    async def decline(member: str):
        person(member, "waiting")
        await manager.remove_member(member)
        return people()

    @router.post(
        "/people/{member}/remove", response_model=Removed, dependencies=OWNER_ONLY
    )
    async def remove(member: str, body: Confirmation):
        # A removal that stopped partway can be repeated.
        person(member, "active", "leaving")
        if not body.confirm:
            raise Refused(
                "Confirm removing this person and everything Enloq keeps for them"
            )
        return {"confirmed": await manager.remove_member(member)}

    @router.post(
        "/people/{member}/budget", response_model=MemberBudgets, dependencies=OWNER_ONLY
    )
    def set_member_budget(member: str, body: MemberBudgetEdit):
        """A friend's Daily or Group Briefing Budget (ADR 48 Q2); the owner's
        own stay in Settings → Budget."""
        person(member)
        manager.registry.set_budget(
            int(body.limit_usd * 1_000_000_000), family=body.family, member=member
        )
        return budgets(member)

    def me(request):
        row = manager.registry.member(request.state.member)
        current = row["role"] == "owner" or row["agreed_version"] == agreement.VERSION
        return {
            "id": row["id"],
            "role": row["role"],
            "state": row["state"],
            "name": row["name"] or "",
            "agreement": None if current else agreement.public(),
        }

    @router.get("/members/me", response_model=Me)
    def read_me(request: Request):
        return me(request)

    @router.post("/members/me/agreement", response_model=Me)
    def accept(body: AgreementAcceptance, request: Request):
        """A member accepts a changed Member Agreement (ADR 48 Q3)."""
        if request.state.member_role != "member":
            raise Refused("Only a member accepts the agreement")
        manager.accept_agreement(request.state.member, body.version)
        return me(request)

    @router.post("/members/me/leave", response_model=Removed)
    async def leave(body: Confirmation, request: Request):
        """A member leaves (ADR 48 Q9): everything goes, their clients too."""
        if request.state.member_role != "member":
            raise Refused("The owner can't leave their own server")
        if not body.confirm:
            raise Refused("Confirm leaving and deleting everything Enloq keeps for you")
        return {"confirmed": await manager.remove_member(request.state.member)}

    return router
