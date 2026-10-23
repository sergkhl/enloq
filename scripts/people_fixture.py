"""Authored people for New Message's journeys (ADR 51 Q16, Q17); registered
only by the fixture server, which holds authored data only.

- `POST /fixture/new-message-people` gives account 1's fake transport one
  contact it has never chatted with and one person only Telegram's people
  search finds. Repeating it changes nothing.
"""

from fastapi import Request

CONTACT = {
    "peer": "user:113",
    "name": "Zoe Contact",
    "username": "zoe_contact",
    "photo_id": None,
}
PUBLIC = {
    "peer": "user:114",
    "name": "Zoe Public",
    "username": "zoe_public",
    "photo_id": None,
}


def register(app):
    @app.post("/fixture/new-message-people")
    async def people(request: Request):
        await request.body()
        transport = app.state.accounts.get(1).transport
        transport.people = {"contacts": [dict(CONTACT)], "search": [dict(PUBLIC)]}
        return {"ok": True}
