"""An authored photo album for the journeys (ADR 51 Q10-Q12); registered only
by the fixture server, which holds authored data only.

- `POST /fixture/album` gives account 1 a private chat, "Photo album", with a
  note, three photos Telegram sent together as one album (the first with a
  caption) and one photo of its own, as if Telegram had delivered them. Each
  call starts the chat afresh.
"""

import time
from io import BytesIO

from enloq.domain.models import Chat, Message
from PIL import Image

PEER = "user:112"
ALBUM = "4242424242"
COLOURS = {2: (200, 80, 60), 3: (60, 140, 90), 4: (60, 90, 180), 5: (220, 180, 40)}


def register(app):
    @app.post("/fixture/album")
    async def album():
        conversation = app.state.accounts.get(1)
        store = conversation.store
        key = f"1/{PEER}"
        with store.transaction() as db:
            for table in ("blocks", "messages"):
                db.execute(f"DELETE FROM {table} WHERE chat_key=?", (key,))
            db.execute("DELETE FROM message_albums WHERE key LIKE ?", (key + "/%",))
        store.put_chat(
            Chat(account_id=1, peer=PEER, title="Photo album", kind="private")
        )
        now = time.time()
        for identity, colour in COLOURS.items():
            picture = BytesIO()
            Image.new("RGB", (240, 180), colour).save(picture, format="PNG")
            conversation.transport.media[PEER, identity] = picture.getvalue()
        sources = [
            (1, None, "Here are the photos from Saturday."),
            (2, "image", "Saturday at the lake"),
            (3, "image", ""),
            (4, "image", ""),
            (5, "image", ""),
        ]
        for identity, media, text in sources:
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer=PEER,
                    message_id=identity,
                    revision=1,
                    date=now - 600 + identity * (60 if identity == 5 else 1),
                    text=text,
                    media=media,
                ),
                target=False,
            )
        store.observe_albums(
            [(f"{key}/{identity}", ALBUM, now - 600) for identity in (2, 3, 4)]
        )
        return {"ok": True}
