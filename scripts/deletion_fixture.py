"""Authored browser deletion cases over the disposable server's FakeTransport."""

import asyncio

from enloq.domain.models import Chat, Message, Refused
from fastapi import Request

# Its own chat: CU2's "Message details" holds user:170.
PEER = "user:180"
KEY = "1/" + PEER


def register(server):
    state = {"release": None, "mode": "success", "attempts": []}

    @server.post("/api/test/deletion")
    async def fixture(request: Request):
        body = await request.json()
        app = server.state.accounts.get(1)
        store, transport = app.store, app.transport
        action = body.get("action", "setup")
        if action == "release":
            if state["release"]:
                state["release"].set()
            return {"ok": True}
        if action == "stats":
            return {
                "attempts": state["attempts"],
                "reads": [
                    list(identities)
                    for peer, identities in transport.deletion_reads[
                        state.get("read_start", 0) :
                    ]
                    if peer == PEER
                ],
            }
        if action == "mode":
            state["mode"] = body.get("mode", "success")
            return {"ok": True}
        if action in ("edit", "external_delete", "new"):
            identity = body.get("identity", 2)
            if action == "external_delete":
                app.sync.deleted(PEER, [identity])
                transport.messages = [
                    m
                    for m in transport.messages
                    if not (m.peer == PEER and m.message_id == identity)
                ]
            else:
                current = store.message(KEY, identity)
                source = (
                    current.model_copy(
                        update={
                            "text": body.get("text", "Edited authored original"),
                            "revision": current.revision + 1,
                        }
                    )
                    if current
                    else Message(
                        account_id=1,
                        peer=PEER,
                        message_id=identity,
                        revision=1,
                        date=store.clock(),
                        text=body.get("text", "A new unchecked original"),
                    )
                )
                app.sync.ingest(source, target=False)
                transport.messages = [
                    m
                    for m in transport.messages
                    if not (m.peer == PEER and m.message_id == identity)
                ] + [source]
            return {"ok": True}
        if action != "setup":
            raise Refused("Unknown authored deletion fixture action")
        if state["release"]:
            state["release"].set()
        # This fixture's prior held request must settle before its rows reset.
        async with asyncio.timeout(5):
            while store.rows(
                "SELECT 1 FROM deletion_operations WHERE chat_key=? AND state IN ('queued','deleting')",
                (KEY,),
            ):
                await asyncio.sleep(0.01)
        with store.transaction() as db:
            db.execute("DELETE FROM jobs WHERE scope LIKE ?", (KEY + "%",))
            for table in (
                "deletion_operations",
                "operations",
                "blocks",
                "messages",
                "drafts",
                "hidden_messages",
            ):
                db.execute(f"DELETE FROM {table} WHERE chat_key=?", (KEY,))
            db.execute(
                "DELETE FROM message_versions WHERE source LIKE ?", (KEY + "/%",)
            )
            db.execute("DELETE FROM derivatives WHERE source LIKE ?", (KEY + "/%",))
            db.execute("DELETE FROM source_views WHERE chat_key=?", (KEY,))
        state.update(
            mode=body.get("mode", "success"),
            attempts=[],
            release=asyncio.Event(),
            read_start=len(transport.deletion_reads),
        )
        store.put_chat(
            Chat(
                account_id=1,
                peer=PEER,
                title="Deletion fixture",
                kind="private",
                ai_eligible=True,
            )
        )
        sources = []
        count = body.get("count", 6)
        for identity in range(1, count + 1):
            source = Message(
                account_id=1,
                peer=PEER,
                message_id=identity,
                revision=1,
                date=store.clock() - 120 + identity / max(1, count),
                text={
                    1: "Please send the authored layout.",
                    2: "Please confirm the illustration price.",
                    3: "I will check the authored proposal.",
                }.get(identity, f"Authored original {identity}"),
                outgoing=identity in (3, 6),
                service=identity == 5,
            )
            app.sync.ingest(source, target=False)
            sources.append(source)
        if len(sources) > 1 and body.get("history", True):
            original = sources[1]
            revised = original.model_copy(
                update={
                    "revision": 2,
                    "text": "Please confirm the revised illustration price.",
                }
            )
            app.sync.ingest(revised, target=False)
            sources[1] = store.message(KEY, 2)
        # Native absence is only provable when no other chat holds the same ID,
        # so an earlier journey's leftovers must not share this fixture's IDs.
        identities = {m.message_id for m in sources}
        transport.messages = [
            m
            for m in transport.messages
            if m.peer != PEER and m.message_id not in identities
        ] + sources
        # Authored cached AI text avoids using any provider or wait shortcut.
        with store.transaction() as db:
            blocks = db.execute(
                "SELECT id,revision FROM blocks WHERE chat_key=?", (KEY,)
            ).fetchall()
            for block in blocks:
                db.execute(
                    "UPDATE blocks SET state='ready',rendered=?,rendered_revision=revision WHERE id=?",
                    ("Authored AI wording for this group.", block["id"]),
                )
            db.execute(
                "UPDATE jobs SET state='cancelled' WHERE scope LIKE ? AND state='queued'",
                (KEY + "/%",),
            )
        if "native_delete" not in state:
            state["native_delete"] = transport.delete

            async def deleting(description, evidence):
                if description.peer != PEER:
                    return await state["native_delete"](description, evidence)
                state["attempts"].append(
                    {
                        "id": description.operation_id,
                        "identities": list(description.identities),
                    }
                )
                mode = state["mode"]
                if mode == "hold":
                    evidence.calling()
                    evidence.watching()
                    await state["release"].wait()
                if mode == "fail" or (
                    mode == "partial" and len(state["attempts"]) == 2
                ):
                    evidence.calling()
                    evidence.watching()
                    evidence.refused("Authored native refusal")
                    raise Refused("Authored native refusal")
                if mode == "uncertain_present":
                    evidence.calling()
                    evidence.watching()
                    evidence.handing_off()
                    raise TimeoutError("Authored lost deletion acknowledgement")
                result = await state["native_delete"](description, evidence)
                if mode == "uncertain_absent":
                    raise TimeoutError("Authored lost deletion acknowledgement")
                return result

            transport.delete = deleting
        return {
            "ok": True,
            "peer": PEER,
            "identities": [m.message_id for m in sources],
            "revisions": {m.message_id: m.revision for m in sources},
        }
