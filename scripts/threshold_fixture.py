"""Authored CT browser fixture; bounded quiet advancement, no live adapters."""

import time

from enloq.domain.models import Chat, Message
from fastapi import Request

PEER, KEY = "user:245", "1/user:245"


def register(server):
    @server.post("/api/test/threshold")
    async def threshold(request: Request):
        body = await request.json()
        app = server.state.accounts.get(1)
        if body.get("action") == "setup":
            app.store.put_chat(
                Chat(
                    account_id=1, peer=PEER, title="Character admission", kind="private"
                )
            )
            app.sync.ingest(
                Message(
                    account_id=1,
                    peer=PEER,
                    message_id=1,
                    revision=int(time.time() * 1e6),
                    date=time.time(),
                    text="Authored original " + "x" * (body["characters"] - 18),
                )
            )
        if body.get("action") == "quiet":
            now = app.store.clock()
            with app.store.transaction() as db:
                db.execute(
                    "UPDATE source_views SET arrived=? WHERE chat_key=?",
                    (now - 31, KEY),
                )
                db.execute(
                    "UPDATE incoming_notices SET quiet=? WHERE chat_key=?",
                    (now - 31, KEY),
                )
                db.execute(
                    "UPDATE incoming_deferred SET due=0 WHERE scope=?", (KEY + "/1",)
                )
                db.execute("UPDATE jobs SET due=0 WHERE scope=?", (KEY + "/1",))
            app.unseen.advance()
            app.notifications.advance()
        return {
            "calls": len(
                app.store.rows(
                    "SELECT id FROM receipts WHERE id IN (SELECT id FROM jobs WHERE scope=? AND role='incoming_rewrite')",
                    (KEY + "/1",),
                )
            ),
            "blocks": app.stages(KEY)
            if body.get("action") != "state"
            or app.store.rows("SELECT key FROM chats WHERE key=?", (KEY,))
            else [],
            "ready": app.store.rows(
                "SELECT ready FROM source_views WHERE chat_key=?", (KEY,)
            ),
        }
