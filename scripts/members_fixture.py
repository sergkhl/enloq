"""Authored members for the journeys (ADR 48); registered only by the fixture
server, which holds authored data only.

- `POST /fixture/requests` seeds a Join Request from an authored stranger, as
  if they had signed in and accepted the Member Agreement, so a journey can
  open People with one waiting.
- `POST /fixture/approve` and `POST /fixture/decline` decide the newest
  waiting request, as the owner would in People, so a journey can walk a
  stranger's waiting screen to either end on one phone.
- `POST /api/test/join-push`, from a trusted browser, seeds a request and
  claims its join push for that browser, so a journey can open the push's
  `?notification=` locator.

A stranger signs in through the synthetic login like anyone else: the
number's last two digits name the authored account.
"""

import time
import uuid

from enloq.domain import agreement
from fastapi import HTTPException, Request


def register(app):
    manager = app.state.accounts

    def newest():
        rows = manager.registry.rows(
            "SELECT id FROM members WHERE state='waiting' ORDER BY requested_at DESC"
        )
        if not rows:
            raise HTTPException(404, "No request is waiting")
        return rows[0]["id"]

    @app.post("/fixture/requests")
    async def seed_request(request: Request):
        body = await request.json()
        account = int(body.get("account_id", 77))
        joining = {
            "id": account,
            "name": body.get("name", "Authored friend"),
            "username": body.get("username", f"authored_{account}"),
            "phone": str(account).zfill(4)[-4:],
        }
        member = await manager.request(
            joining,
            f"authorizations/fixture-{account}/telegram.session",
            agreement.VERSION,
        )
        return {"member": member}

    @app.post("/fixture/approve")
    async def approve():
        member = newest()
        await manager.approve(member)
        return {"member": member}

    @app.post("/fixture/decline")
    async def decline():
        member = newest()
        await manager.remove_member(member)
        return {"member": member}

    @app.post("/api/test/join-push")
    async def join_push(request: Request):
        body = await request.json()
        account = int(body.get("account_id", 78))
        member = await manager.request(
            {
                "id": account,
                "name": body.get("name", "Authored friend"),
                "username": f"authored_{account}",
                "phone": str(account).zfill(4)[-4:],
            },
            f"authorizations/fixture-{account}/telegram.session",
            agreement.VERSION,
        )
        event = {
            "id": uuid.uuid4().hex,
            "account_id": None,
            "peer": None,
            "subject": member,
            "expires": time.time() + 300,
        }
        app.state.notifications.claim(event, request.state.local_session, "push")
        return {"id": event["id"], "member": member}
