"""Authored browser fixtures for ADR 53's calming, one private chat whose
incoming block the offline decider marks angry, calmed through the real
scheduler, and ADR 54's combined text, a chat whose text isn't rewritten. No
provider, Telegram or user content is involved."""

import asyncio
import json
import time

from enloq.domain.models import Chat, Message
from enloq.inference.fake import choice
from fastapi import Request

PEER = "user:128"
MARK = "Calming fixture:"
COMBINED = "user:129"


def register(app):
    state = {"identity": 0}

    def angry_here(decider):
        """Only this fixture's messages are angry; every other chat keeps
        the authored decider's answers."""
        if getattr(decider, "calming_fixture", False):
            return
        generate = decider.generate

        async def answer(role, packet):
            value = await generate(role, packet)
            state_ = json.dumps(packet.get("state", {}), ensure_ascii=False)
            if role == "emotion_classification" and MARK in state_ and value:
                value = {**value, "emotion": choice("angry", 0.9)}
            return value

        decider.generate = answer
        decider.calming_fixture = True

    @app.post("/api/test/calming")
    async def calming():
        conversation = app.state.accounts.get(1)
        angry_here(conversation.decider)
        conversation.store.put_chat(
            Chat(account_id=1, peer=PEER, title="Calming fixture", kind="private")
        )
        state["identity"] += 1
        conversation.sync.ingest(
            Message(
                account_id=1,
                peer=PEER,
                message_id=state["identity"],
                revision=1,
                date=time.time(),
                text=f"{MARK} WHERE IS MY REFUND OF 240?? Answer me by Friday.",
            )
        )
        key = f"1/{PEER}"
        with conversation.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET due=0 WHERE state='queued' AND scope IN "
                "(SELECT id FROM blocks WHERE chat_key=?)",
                (key,),
            )
        for _ in range(100):
            blocks = conversation.stages(key)
            if blocks and blocks[-1]["state"] == "ready":
                break
            await conversation.process_due()
            await asyncio.sleep(0.05)
        return {"ok": True, "calmed": bool(blocks and blocks[-1]["calmed"])}

    @app.post("/api/test/text-rewriting")
    async def text_rewriting(request: Request):
        body = await request.json()
        app.state.accounts.registry.set_text_rewriting(body["enabled"] is True)
        return {"ok": True}

    @app.post("/api/test/combined")
    async def combined():
        """Two text messages in one block, decided and left combined."""
        conversation = app.state.accounts.get(1)
        conversation.store.put_chat(
            Chat(account_id=1, peer=COMBINED, title="Combined fixture", kind="private")
        )
        for text in ("Combined fixture: the plumber comes Thursday.", "Or Friday."):
            state["identity"] += 1
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer=COMBINED,
                    message_id=state["identity"],
                    revision=1,
                    date=time.time(),
                    text=text,
                ),
                live=True,
            )
        key = f"1/{COMBINED}"
        with conversation.store.transaction() as db:
            db.execute(
                "UPDATE incoming_deferred SET due=0 WHERE scope IN "
                "(SELECT id FROM blocks WHERE chat_key=?)",
                (key,),
            )
        for _ in range(100):
            if not conversation.calming.deciding(key):
                break
            await conversation.process_due()
            await asyncio.sleep(0.05)
        (block,) = conversation.stages(key)
        return {"ok": True, "state": block["state"]}
