"""Authored browser transport for ADR 22 streaming and ADR 30's following
Emotion Classification; isolated from other stream fixtures."""

import asyncio
import time

from enloq.domain.models import Chat, Message
from enloq.inference.base import EngineResponse
from fastapi import Request


def register(app):
    state = {}

    @app.post("/api/test/emotion-stream")
    async def stream(request: Request):
        body = await request.json()
        conversation = app.state.accounts.get(1)
        if body.get("action") == "reset":
            if state.get("finish"):
                state["finish"].set()
            if state.get("engine"):
                conversation.engine = state.pop("engine")
            conversation.decider.emotion = "unknown"
            return {"ok": True}
        if body.get("action") == "finish":
            state["finish"].set()
            return {"ok": True}
        if body.get("action") == "start":
            with conversation.store.transaction() as db:
                db.execute("UPDATE jobs SET due=0 WHERE scope='1/user:125/1'")
            conversation.run(conversation.process_due())
            return {"ok": True}
        state.setdefault("engine", conversation.engine)
        state["finish"] = finish = asyncio.Event()

        class Streaming:
            name, model, provider = "offline", "authored-emotion-stream", "local"

            async def generate(self, role, packet):
                return await state["engine"].generate(role, packet)

            async def generate_stream(self, role, packet, on_text):
                if role == "incoming_rewrite" and not any(
                    m["text"].startswith("Emotion fixture") for m in packet["messages"]
                ):
                    return await self.generate(role, packet)
                if role == "incoming_rewrite":
                    for index in range(1, 9):
                        on_text("Fast rewrite " + "word " * index)
                        await asyncio.sleep(0.04)
                else:
                    on_text("Partial rewrite 😀")
                try:
                    await finish.wait()
                except asyncio.CancelledError:
                    if not body.get("late"):
                        raise
                    await finish.wait()
                result = {"text": "Complete validated rewrite"}
                if role == "incoming_rewrite":
                    result.update(image_context=[])
                return EngineResponse(result, actual_nanos=0)

        conversation.engine = Streaming()
        # The scheduler's classification after publication assigns it.
        conversation.decider.emotion = body.get("emotion", "unknown")
        if not body.get("outgoing"):
            conversation.store.put_chat(
                Chat(
                    account_id=1,
                    peer="user:125",
                    title="Emotion streaming",
                    kind="private",
                )
            )
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer="user:125",
                    message_id=1,
                    revision=int(time.time() * 1e6),
                    date=time.time(),
                    text=f"Emotion fixture {time.time_ns()}",
                )
            )
            with conversation.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET due=? WHERE scope='1/user:125/1'",
                    (time.time() + 3600,),
                )
        return {"ok": True}
