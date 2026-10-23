"""Authored large-group browser evidence; registered only by the fixture server."""

import time
from io import BytesIO
from pathlib import Path

from enloq.domain.models import Chat, MediaInfo, Message
from fastapi import Request
from PIL import Image


def register(app):
    started = time.time()
    titles = {
        "user:141": "Complete group",
        "user:142": "Group source recovery",
        "user:143": "Partial source cache",
        "user:144": "Group scrolling",
        "user:145": "Group null snapshot",
        "user:146": "Group incomplete snapshot",
        "user:147": "Group retained snapshot",
        "user:148": "Group rendering freshness",
    }

    @app.post("/api/test/group")
    async def group(request: Request):
        body = await request.json()
        conversation = app.state.accounts.get(1)
        store = conversation.store
        peer = body.get("peer", "user:141")
        assert peer in titles
        key = f"1/{peer}"
        action = body.get("action", "seed")
        if action == "seed":
            store.put_chat(
                Chat(
                    account_id=1,
                    peer=peer,
                    title=titles[peer],
                    kind="private",
                )
            )
            image = BytesIO()
            Image.new("RGB", (64, 40), (70, 130, 160)).save(image, format="PNG")
            conversation.transport.media[peer, 1] = b"Authored local file"
            conversation.transport.media[peer, 2] = image.getvalue()
            conversation.transport.media[peer, 3] = (
                Path(__file__).resolve().parents[1] / "backend/tests/fixtures/tone.ogg"
            ).read_bytes()
            for identity in range(1, 126):
                media, info = {
                    1: (
                        "document",
                        MediaInfo(file_name="earlier.txt", mime="text/plain"),
                    ),
                    2: ("image", MediaInfo(mime="image/png")),
                    3: ("audio", MediaInfo(voice=True, duration=2)),
                    4: ("document", MediaInfo(file_name="unavailable.txt")),
                }.get(identity, (None, None))
                conversation.sync.ingest(
                    Message(
                        account_id=1,
                        peer=peer,
                        message_id=identity,
                        revision=1,
                        date=started - 125 + identity / 100,
                        text=f"Authored group source {identity}",
                        media=media,
                        media_info=info,
                        forwarded=False,
                    ),
                    target=False,
                    arrival=False,
                )
        elif action == "edit":
            message = store.message(key, 1)
            conversation.sync.ingest(
                message.model_copy(
                    update={
                        "revision": message.revision + 1,
                        "text": f"Authored edited file source {message.revision + 1}",
                        "media_info": MediaInfo(
                            file_name=body.get("name", "revised.txt"), mime="text/plain"
                        ),
                    }
                ),
                target=False,
                arrival=False,
            )
        elif action == "delete":
            conversation.sync.deleted(peer, [125])
        with store.transaction() as db:
            db.execute(
                "UPDATE blocks SET state='ready',rendered='A coherent authored group.',rendered_revision=revision,error=NULL,unanalysed='[]' WHERE chat_key=?",
                (key,),
            )
            db.execute(
                "UPDATE jobs SET state='superseded' WHERE scope IN (SELECT id FROM blocks WHERE chat_key=?) AND state='queued'",
                (key,),
            )
        return {"ok": True}
