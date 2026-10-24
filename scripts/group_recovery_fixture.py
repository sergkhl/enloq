"""Authored GB9 viewing/recovery evidence in the disposable offline rig only."""

import base64
import json
from io import BytesIO

from enloq.application import group_context
from enloq.application.delivery import operation_view
from enloq.domain.models import Chat, Message, Refused
from enloq.inference.guidance import briefing_fingerprint
from enloq.inference.work import ROUTES
from fastapi import Request
from PIL import Image

PEERS = {"chat:940", "channel:940", "chat:941"}
TABLES = (
    "group_sources",
    "group_state",
    "group_intake",
    "group_briefing_days",
    "group_briefing_runs",
    "group_briefing_batches",
    "group_briefing_attempts",
    "group_briefing_chunks",
    "group_briefing_results",
    "viewing_baselines",
    "source_views",
    "messages",
    "drafts",
    "blocks",
    "attachments",
    "selected_media",
    "operations",
    "deletion_operations",
    "incoming_notices",
)


def register(server):
    history_reads = []
    wrapped = set()

    def state(app, key):
        peer = app.store.chat(key).peer
        operations = [
            operation_view(json.loads(row["body"]), app.store.clock())
            for row in app.store.rows(
                "SELECT body FROM operations WHERE chat_key=? ORDER BY rowid", (key,)
            )
        ]
        return {
            "peer": peer,
            "draft": app.store.draft(key),
            "operations": operations,
            "sources": [
                {
                    "id": row["message_id"],
                    "ready": row["ready"] is not None,
                    "viewed": row["viewed"] is not None,
                }
                for row in app.store.rows(
                    "SELECT * FROM source_views WHERE chat_key=? ORDER BY message_id",
                    (key,),
                )
            ],
            "model_calls": sum(
                packet.get("chat_key") == key
                for _, packet in app.engine.calls + app.decider.calls
            ),
            "notices": len(
                app.store.rows(
                    "SELECT 1 FROM incoming_notices WHERE chat_key=?", (key,)
                )
            ),
            "native_history": history_reads.count((app.store.account_id, peer)),
            "native_reads": len(
                [value for value in app.transport.reads if value[0] == peer]
            ),
            "native_downloads": len(
                [value for value in app.transport.downloads if value[0] == peer]
            ),
            "reviews": sum(
                value.peer == peer for value in app.transport.group_send_reviews
            ),
            "sends": [
                {
                    "peer": value["description"].peer,
                    "text": value["description"].text,
                    "reply_to": value["description"].reply_to,
                    "images": [image.sha256 for image in value["description"].images],
                    "random_ids": list(value["description"].random_ids),
                }
                for value in app.transport.deliveries.values()
                if value["description"].peer == peer
            ],
        }

    @server.post("/api/test/group-recovery")
    async def fixture(request: Request):
        body = await request.json()
        manager = server.state.accounts
        if manager.settings.mode != "offline":
            raise Refused("Authored recovery fixture requires disposable offline data")
        account = body.get("account_id", 1)
        peer = body.get("peer", "chat:940")
        if account not in {1, 2} or peer not in PEERS:
            raise Refused("Unknown authored recovery group")
        app = manager.get(account)
        store, key = app.store, f"{account}/{peer}"
        action = body.get("action", "setup")
        if account not in wrapped:
            previous = app.transport.history

            async def history(target, before, limit):
                if target in PEERS:
                    history_reads.append((account, target))
                return await previous(target, before, limit)

            app.transport.history = history
            wrapped.add(account)
        if action == "cleanup":
            manager.set_processing(account, True)
            manager.registry.set_processing_threshold(0)
            manager.registry.set_budget(2_000_000_000, family="group_briefing")
            return {"ok": True}
        if action == "state":
            return state(app, key)
        if action == "access":
            store.groups.observe(
                key, access=body["access"], reason="authored_recovery_access"
            )
            return state(app, key)
        if action == "expire":
            # Exercise the normal retention boundary without changing shared
            # runtime clocks, accepted content or another fixture's evidence.
            with store.transaction() as db:
                cutoff = store.clock() - 31 * 86400
                db.execute(
                    "UPDATE messages SET date=?,body=json_set(body,'$.date',?) "
                    "WHERE chat_key=?",
                    (cutoff, cutoff, key),
                )
            store.expire()
            return state(app, key)
        if action == "quiet":
            age = body.get("age", 31)
            with store.transaction() as db:
                db.execute(
                    "UPDATE source_views SET arrived=? WHERE chat_key=? AND ready IS NULL",
                    (store.clock() - age, key),
                )
            app.unseen.advance()
            return state(app, key)
        if action not in {"setup", "append"}:
            raise Refused("Unknown authored recovery fixture action")
        if action == "setup":
            # Only the fixture-owned exact peer is replaced. No live session,
            # provider settings, general cost records or other journey is read.
            manager.set_processing(account, body.get("master", False))
            manager.registry.set_processing_threshold(body.get("threshold", 0))
            manager.registry.set_budget(
                body.get("group_budget", 2_000_000_000), family="group_briefing"
            )
            history_reads[:] = [
                value for value in history_reads if value != (account, peer)
            ]
            app.transport.messages = [
                value for value in app.transport.messages if value.peer != peer
            ]
            app.transport.deliveries = {
                identity: value
                for identity, value in app.transport.deliveries.items()
                if value["description"].peer != peer
            }
            app.transport.group_send_reviews = [
                value
                for value in app.transport.group_send_reviews
                if value.peer != peer
            ]
            with store.transaction() as db:
                db.execute(
                    "DELETE FROM jobs WHERE scope=? OR scope LIKE ?", (key, key + "/%")
                )
                for table in TABLES:
                    db.execute(f"DELETE FROM {table} WHERE chat_key=?", (key,))
                db.execute(
                    "DELETE FROM message_versions WHERE source LIKE ?", (key + "/%",)
                )
                db.execute("DELETE FROM derivatives WHERE source LIKE ?", (key + "/%",))
                db.execute("DELETE FROM chats WHERE key=?", (key,))
            app.attachments.collect()
            store.put_chat(
                Chat(
                    account_id=account,
                    peer=peer,
                    title=body.get(
                        "title",
                        "Retained authored discussion"
                        if peer == "chat:941"
                        else "Authored local viewing",
                    ),
                    kind="group",
                    muted=False,
                    read_through=0,
                )
            )
            store.groups.observe(
                key, access="available", reason="authored_native_evidence"
            )
            intake = store.groups.intake(key)
            store.groups.scanned(
                key,
                epoch=intake.epoch,
                before=0,
                top=0,
                count=0,
                cutoff=store.clock() - 7 * 86400,
                oldest=None,
                complete=True,
            )
        messages = body.get(
            "messages", [{"id": 1, "text": "An authored participant update."}]
        )
        for value in messages:
            source = Message(
                account_id=account,
                peer=peer,
                message_id=value["id"],
                revision=value.get("revision", 1),
                date=value.get("date", store.clock() - 1),
                text=value.get("text", f"Authored participant update {value['id']}."),
                author=value.get("author", "Alice"),
                author_peer=value.get("author_peer", "user:7"),
                outgoing=value.get("outgoing", False),
                service=value.get("service", False),
            )
            app.sync.ingest(source, target=False)
            app.transport.messages = [
                message
                for message in app.transport.messages
                if not (
                    message.peer == peer and message.message_id == source.message_id
                )
            ] + [source]
        if action == "setup" and body.get("group"):
            app.set_group_processing(key, True)
        if body.get("covered"):
            ids = body["covered"]
            sources = [store.message(key, identity) for identity in ids]
            day = group_context.calendar_day(store, sources[0])
            packet = group_context.packet(store, key, day, sources)
            snapshot = store.group_briefings.freeze(
                key,
                day,
                [packet],
                intended_ids=ids,
                guidance_hash=briefing_fingerprint(
                    app.rewriting.work.guidance(),
                    role="group_briefing",
                    route=ROUTES["group_briefing"],
                ),
                route=ROUTES["group_briefing"],
            )
            attempt = store.group_briefings.create_attempt(snapshot)
            store.group_briefings.publish(
                attempt.id,
                {
                    "outcome": "briefing",
                    "topics": [
                        {
                            "text": "The authored discussion agreed the planning date.",
                            "citations": ids,
                        }
                    ],
                },
                covered_ids=ids,
            )
            store.group_briefings.finish_batch(attempt.batch_id)
        if action == "setup" and body.get("accepted"):
            draft = store.mutate_draft(
                key,
                store.draft(key)["revision"],
                lambda value: value.update(
                    text=body.get("submitted", "The exact paused authored reply."),
                    reply_to=1,
                ),
            )
            if body.get("image"):
                output = BytesIO()
                Image.new("RGB", (40, 30), (65, 140, 110)).save(output, "PNG")
                draft = app.attachments.add(
                    key,
                    draft["revision"],
                    "authored-kept.png",
                    base64.b64encode(output.getvalue()).decode(),
                )
            operation = await app.delivery.submit(key, draft["revision"])
            if body.get("unknown"):
                app.transport.lose_receipt = True
                try:
                    await app.delivery.drain(operation["id"])
                finally:
                    app.transport.lose_receipt = False
            # No yield occurs between acceptance and this confirmed loss. The
            # actual access observer proves and pauses the unhanded submission.
            store.groups.observe(key, access="lost", reason="authored_confirmed_loss")
        if "draft" in body:
            store.mutate_draft(
                key,
                store.draft(key)["revision"],
                lambda value: value.update(text=body["draft"]),
            )
        if body.get("retained"):
            with store.transaction() as db:
                db.execute(
                    "UPDATE chats SET activity=? WHERE key=?",
                    (store.clock() - 8 * 86400, key),
                )
        if body.get("ready"):
            with store.transaction() as db:
                db.execute(
                    "UPDATE source_views SET arrived=? WHERE chat_key=?",
                    (store.clock() - 31, key),
                )
            app.unseen.advance()
        if body.get("viewed"):
            with store.transaction() as db:
                for identity in body["viewed"]:
                    db.execute(
                        "UPDATE source_views SET viewed=? WHERE chat_key=? AND message_id=?",
                        (store.clock(), key, identity),
                    )
        if body.get("access"):
            store.groups.observe(
                key, access=body["access"], reason="authored_recovery_access"
            )
        return state(app, key)
