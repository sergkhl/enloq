"""Authored browser fixture for ADR 62's per-chat switches; registered only by
test_server.py, which holds authored data only.

- `POST /api/test/switches-off` gives account 1 a private chat, "Switches
  Off", whose five switches it turns Off as the chat's AI Menu would, then a
  text block, the owner's reply and a captioned photo block, past their quiet
  seconds. Both blocks settle as their originals with no model request (Q14).
  Each call starts the chat afresh.
- `GET /api/test/switches-off` lists the questions each of the chat's
  decision requests asked (Q5).
"""

import asyncio
import json
import time
from io import BytesIO

from enloq.domain.models import Chat, Message
from PIL import Image

PEER = "user:131"
MARK = "Switches Off fixture"


def register(app):
    def ours(packet):
        return MARK in json.dumps(packet, ensure_ascii=False)

    @app.post("/api/test/switches-off")
    async def switches_off():
        conversation = app.state.accounts.get(1)
        store = conversation.store
        key = f"1/{PEER}"
        with store.transaction() as db:
            for table in ("blocks", "messages"):
                db.execute(f"DELETE FROM {table} WHERE chat_key=?", (key,))
            db.execute("DELETE FROM emotion_state WHERE chat_key=?", (key,))
        store.put_chat(
            Chat(account_id=1, peer=PEER, title="Switches Off", kind="private")
        )
        # The chat's own choices, as its AI Menu's switches make them.
        conversation.suggestions.set_settings(key, False, "friendly")
        conversation.emotion_switch.set(key, False)
        conversation.calming.set(key, False)
        conversation.media_switch.set(key, False)
        conversation.memory.remember(key, False)
        picture = BytesIO()
        Image.new("RGB", (240, 180), (90, 120, 200)).save(picture, format="PNG")
        conversation.transport.media[PEER, 3] = picture.getvalue()
        now = time.time()
        sources = (
            (1, False, None, f"{MARK}: are we still on for Saturday?"),
            (2, True, None, "Yes, see you then."),
            (3, False, "image", f"{MARK}: the view from the lake"),
        )
        for identity, outgoing, media, text in sources:
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer=PEER,
                    message_id=identity,
                    revision=1,
                    date=now - 120 + identity,
                    text=text,
                    media=media,
                    outgoing=outgoing,
                ),
                live=True,
            )
        with store.transaction() as db:
            db.execute(
                "UPDATE incoming_deferred SET due=0 WHERE scope IN "
                "(SELECT id FROM blocks WHERE chat_key=?)",
                (key,),
            )
        for _ in range(10):
            await conversation.process_due()
            await asyncio.sleep(0.05)
        return {
            "ok": True,
            "states": [block["state"] for block in conversation.stages(key)],
            **asked(conversation),
        }

    def asked(conversation):
        return {
            "asks": [
                packet.get("asks")
                for role, packet in conversation.decider.calls
                if role == "emotion_classification" and ours(packet)
            ]
        }

    @app.get("/api/test/switches-off")
    def switches_asked():
        return asked(app.state.accounts.get(1))
