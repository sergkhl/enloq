"""The boundary between members (ADR 48).

Every route under `/api/accounts/{account_id}` depends on `member_account`, so
a trusted client reaches only its own member's accounts. Another member's
account answers exactly as an account the server doesn't hold, so nothing
confirms that it exists. `owner_only` guards the settings only the owner
changes.
"""

from fastapi import Depends, HTTPException, Request

from ..domain.models import Refused
from ..storage.members import UNAVAILABLE


def owns(request: Request, account_id) -> bool:
    registry = request.app.state.accounts.registry
    member = getattr(request.state, "member", None)
    return member is not None and registry.member_of(account_id) == member


def member_account(request: Request, account_id: int):
    if not owns(request, account_id):
        raise Refused(UNAVAILABLE)


def require_account(request: Request, account_id):
    """The same check for an account named in a body or query."""
    if account_id is not None and not owns(request, account_id):
        raise Refused(UNAVAILABLE)


def owner_only(request: Request):
    if getattr(request.state, "member_role", None) != "owner":
        raise HTTPException(403, "Only the owner changes this")


MEMBER_ACCOUNT = [Depends(member_account)]
OWNER_ONLY = [Depends(owner_only)]
