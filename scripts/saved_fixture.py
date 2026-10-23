"""Authored Saved Messages for the journeys (ADR 50); registered only by the
fixture server, which holds authored data only.

- `POST /fixture/saved-messages` stores two authored notes in an account's own
  Saved Messages (account 1, the synthetic Owner Account, by default), as if
  Telegram had delivered them, so a journey can open the Saved tab with
  something in it. Repeating it changes nothing.
"""

import time

from enloq.domain.models import Message
from fastapi import Request

# Low message IDs, below the fake transport's sends (1000 and up), so a note
# sent in a journey reads as the newest, as it would in Telegram.
NOTES = (
    (1, "Saved fixture: pick up the keys at six"),
    (2, "Saved fixture: bring the charger"),
)


def register(app):
    @app.post("/fixture/saved-messages")
    async def saved_messages(request: Request):
        body = await request.json()
        account = int(body.get("account_id", 1))
        conversation = app.state.accounts.get(account)
        conversation.store.ensure_saved()
        now = time.time()
        for offset, (identity, text) in enumerate(NOTES):
            conversation.sync.ingest(
                Message(
                    account_id=account,
                    peer=f"user:{account}",
                    message_id=identity,
                    revision=1,
                    date=now - 3600 + offset * 60,
                    text=text,
                    outgoing=True,
                )
            )
        return {"ok": True}
