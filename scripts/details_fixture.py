"""Authored message details for both clients' journeys (ADR 46 CU2).

A private chat with a forwarded and an imported message, an edited one with
two captured versions and one without, a message carrying reactions (the
owner's 👍 with a correspondent's, a paid and a custom one), a poll, a venue,
a contact and a link preview, and enough later messages for Scroll to latest,
with a profile for CU3. Every run sets the chat up afresh; nothing reaches
Telegram.
"""

import time

PEER = "user:170"
TITLE = "Message details"


def register(app):
    started = time.time()

    @app.post("/api/test/details")
    async def details():
        from enloq.domain.models import (
            Chat,
            MediaInfo,
            Message,
            Reaction,
            ReactionActor,
            ReactionChip,
            ReactionSnapshot,
        )

        conversation = app.state.accounts.get(1)
        key = f"1/{PEER}"
        with conversation.store.transaction() as db:
            db.execute("DELETE FROM jobs WHERE scope LIKE ?", (key + "%",))
            for table in ("blocks", "messages"):
                db.execute(f"DELETE FROM {table} WHERE chat_key=?", (key,))
            for table in ("derivatives", "message_versions"):
                db.execute(f"DELETE FROM {table} WHERE source LIKE ?", (key + "/%",))
        conversation.store.put_chat(
            Chat(account_id=1, peer=PEER, title=TITLE, kind="private")
        )
        base = started - 3600

        def put(identity, text, revision=1, **values):
            values.setdefault("forwarded", False)
            conversation.sync.ingest(
                Message(
                    account_id=1,
                    peer=PEER,
                    message_id=identity,
                    revision=revision,
                    date=base + identity * 30,
                    text=text,
                    forward_read=True,
                    **values,
                ),
                target=False,
            )

        put(
            1,
            "Forwarded note",
            forwarded=True,
            forwarded_from="Authored channel",
            forward_date=started - 2 * 86400,
        )
        put(2, "Imported note", forwarded=True, imported=True)
        # Each later revision keeps the one before as a captured version.
        for revision, text in (
            (1, "First wording"),
            (2, "Second wording"),
            (3, "Latest wording"),
        ):
            put(3, text, revision=revision, native_edited=revision > 1)
        put(4, "Edited once", native_edited=True)
        put(5, "React to me")
        put(
            6,
            "",
            media="poll",
            media_info=MediaInfo(question="Which day works?", options=["Mon", "Fri"]),
        )
        put(7, "", media="location", media_info=MediaInfo(title="Authored cafe"))
        put(8, "", media="contact", media_info=MediaInfo(title="Authored Contact"))
        put(
            9,
            "https://e.test/page",
            media="link",
            media_info=MediaInfo(site="Example", title="Authored page"),
        )
        for identity in range(10, 40):
            put(identity, f"Later message {identity}")
        now = conversation.store.clock()
        thumbs, paid = Reaction(emoji="👍"), Reaction(paid=True)
        custom = Reaction(custom="5000000000000000001")
        conversation.sync.reactions(
            PEER,
            5,
            ReactionSnapshot(
                chips=[
                    ReactionChip(reaction=thumbs, count=2, mine=True, order=1),
                    ReactionChip(reaction=paid, count=1, mine=False),
                    ReactionChip(reaction=custom, count=1, mine=False),
                ],
                actors=[
                    ReactionActor(actor="own", reaction=thumbs, date=now),
                    ReactionActor(actor="peer", reaction=thumbs, date=now),
                    ReactionActor(actor="peer", reaction=paid, date=now),
                    ReactionActor(actor="peer", reaction=custom, date=now),
                ],
                complete=True,
                observed=now,
            ),
        )
        # What the fake transport answers after a reaction is sent: the
        # owner's and the correspondent's ordinary emoji.
        conversation.transport.reaction_state[(PEER, 5)] = {
            "own": [thumbs],
            "peer": {"👍": now},
        }
        # The Correspondent Profile's Telegram details (ADR 46 CU3).
        conversation.transport.profiles[PEER] = {
            "first_name": "Morgan",
            "last_name": "Lee",
            "username": "authored_morgan",
            "phone": None,
            "bio": "Authored bio for the journeys.",
        }
        return {"ok": True}
