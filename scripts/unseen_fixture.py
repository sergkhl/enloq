"""Disposable authored viewing fixtures; registered only by test_server.py."""

import time

from enloq.application.blocks import rebuild
from enloq.domain.models import Chat, Message
from fastapi import Request


def register(server):
    @server.post("/api/test/unseen")
    async def change(request: Request):
        body = await request.json()
        account, peer = body.get("account", 1), body["peer"]
        app = server.state.accounts.get(account)
        key, now = f"{account}/{peer}", time.time()
        if body.get("action", "seed") == "seed":
            app.store.put_chat(
                Chat(
                    account_id=account,
                    peer=peer,
                    title=body.get("title", "Unseen fixture"),
                    kind="private",
                    muted=body.get("muted", True),
                    read_through=1_000_000,
                )
            )
        if body.get("deleted"):
            app.sync.deleted(peer, body["deleted"])
        if body.get("action") != "state":
            for source in body.get("messages", []):
                app.store.apply_message(
                    Message(
                        account_id=account,
                        peer=peer,
                        message_id=source["id"],
                        revision=source.get("revision", 1),
                        date=source.get("date", now - 120),
                        text=source.get("text", "Authored source"),
                        outgoing=source.get("outgoing", False),
                        media=source.get("media"),
                    ),
                    intake="arrival",
                )
            rebuild(app.store, key, target=False)
            with app.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET state='superseded' WHERE scope IN (SELECT id FROM blocks WHERE chat_key=?) AND state='queued'",
                    (key,),
                )
                if "rendered" in body:
                    db.execute(
                        "UPDATE blocks SET state='ready',rendered=?,rendered_revision=revision WHERE chat_key=?",
                        (body["rendered"], key),
                    )
                if body.get("ready"):
                    db.execute(
                        "UPDATE source_views SET arrived=? WHERE chat_key=?",
                        (now - 31, key),
                    )
                for identity in body.get("viewed", []):
                    db.execute(
                        "UPDATE source_views SET viewed=? WHERE chat_key=? AND message_id=?",
                        (now, key, identity),
                    )
            app.unseen.advance()
        return {
            "sources": [
                {
                    "id": r["message_id"],
                    "ready": r["ready"] is not None,
                    "viewed": r["viewed"] is not None,
                }
                for r in app.store.rows(
                    "SELECT * FROM source_views WHERE chat_key=? ORDER BY message_id",
                    (key,),
                )
            ]
        }
