"""Authored native group chats, distinct from private Message Group fixtures."""

import asyncio
import json

from enloq.application.delivery import operation_view
from enloq.domain.models import (
    Chat,
    MediaInfo,
    Message,
    Reaction,
    ReactionActor,
    ReactionChip,
    ReactionSnapshot,
    Refused,
)
from fastapi import Request

GROUPS = {"chat:42": "Basic discussion", "channel:42": "Supergroup discussion"}


def register(server):
    deletion_states = {}
    deletion_wrapped = set()

    @server.post("/api/test/group-chats")
    async def fixture(request: Request):
        body = await request.json()
        account = body.get("account_id", 1)
        app = server.state.accounts.get(account)
        store = app.store
        action = body.get("action", "setup")
        if action in {
            "deletion_mode",
            "deletion_release",
            "deletion_edit",
            "deletion_media",
            "reactions",
        }:
            peer = body.get("peer", "chat:42")
            if peer not in GROUPS:
                raise Refused("Unknown authored group")
            key = f"{account}/{peer}"
            state = deletion_states.setdefault(
                key, {"mode": "success", "release": asyncio.Event(), "attempts": []}
            )
            if action == "deletion_mode":
                state["mode"] = body.get("mode", "success")
                if account not in deletion_wrapped:
                    native = app.transport.delete

                    async def deleting(description, evidence):
                        value = deletion_states.get(
                            f"{description.account_id}/{description.peer}"
                        )
                        mode = value["mode"] if value else "success"
                        if value:
                            value["attempts"].append(list(description.identities))
                        if mode == "hold":
                            await value["release"].wait()
                        if mode == "refuse":
                            evidence.refused("Authored group deletion refusal")
                            raise Refused("Authored group deletion refusal")
                        result = await native(description, evidence)
                        if mode == "unknown":
                            raise TimeoutError(
                                "Authored missing group deletion receipt"
                            )
                        return result

                    app.transport.delete = deleting
                    deletion_wrapped.add(account)
            elif action == "deletion_release":
                state["release"].set()
            elif action in {"deletion_edit", "deletion_media"}:
                identity = body.get("identity", 4)
                source = store.message(key, identity)
                if not source:
                    raise Refused("Unknown authored group original")
                values = (
                    {
                        "media": "document",
                        "attachment_id": "authored-document",
                        "media_info": MediaInfo(
                            file_name=body.get("file_name", "reviewed.txt"),
                            size=body.get("size", 10),
                        ),
                    }
                    if action == "deletion_media"
                    else {
                        "author_peer": body.get("author_peer", "user:99"),
                        "text": body.get("text", "Changed authored group original."),
                        "revision": source.revision + 1,
                    }
                )
                revised = source.model_copy(update=values)
                app.sync.ingest(revised, target=False)
                app.transport.messages = [
                    m
                    for m in app.transport.messages
                    if not (m.peer == peer and m.message_id == identity)
                ] + [store.message(key, identity)]
            else:
                source = store.message(key, body.get("identity", 1))
                if not source:
                    raise Refused("Unknown authored group original")
                reaction = Reaction(emoji="👍")
                store.reactions.apply(
                    source.key,
                    ReactionSnapshot(
                        chips=[ReactionChip(reaction=reaction, count=3)],
                        actors=[
                            ReactionActor(
                                actor="peer", reaction=reaction, date=store.clock()
                            )
                        ],
                        observed=store.clock(),
                    ),
                )
            return {"ok": True}
        if action == "access":
            peer = body.get("peer", "chat:42")
            if peer not in GROUPS:
                raise Refused("Unknown authored group")
            store.groups.observe(
                f"{account}/{peer}",
                access=body.get("access", "lost"),
                reason="authored_access_change",
            )
            return {"ok": True}
        if action == "automatic_clock":
            # Journeys only: as if an automatic update had just started, so
            # ADR 34's four-hour clock keeps one from starting mid-journey
            # and covering the messages a journey checks. Deliberate Update
            # bypasses the clock as before.
            peer = body.get("peer", "chat:42")
            if peer not in GROUPS:
                raise Refused("Unknown authored group")
            briefings = store.group_briefings
            with store.transaction() as db:
                run = briefings._run(db, f"{account}/{peer}")
                briefings._write_run(
                    db, run.model_copy(update={"last_auto_started": store.clock()})
                )
            return {"ok": True}
        if action == "stats":
            peer = body.get("peer", "chat:42")
            if peer not in GROUPS:
                raise Refused("Unknown authored group")
            key = f"{account}/{peer}"
            return {
                "processing": {
                    peer: app.group_processing(f"{account}/{peer}").model_dump()
                    for peer in GROUPS
                },
                "model_calls": len(app.engine.calls),
                "reads": len(app.transport.exact_reads),
                "draft": app.store.draft(key),
                "operations": [
                    operation_view(json.loads(row["body"]), store.clock())
                    for row in store.rows(
                        "SELECT body FROM operations WHERE chat_key=? ORDER BY rowid DESC",
                        (key,),
                    )
                ],
                "deletions": app.deletion.recent(key),
                "deletion_attempts": deletion_states.get(key, {}).get("attempts", []),
                "reaction_events": len(
                    store.rows("SELECT 1 FROM reaction_events WHERE chat_key=?", (key,))
                ),
                "sends": [
                    {
                        "text": value["description"].text,
                        "peer": value["description"].peer,
                        "reply_to": value["description"].reply_to,
                        "images": len(value["description"].images),
                        "media": value["description"].media.kind
                        if value["description"].media
                        else None,
                        "native": value["message_id"],
                    }
                    for value in app.transport.deliveries.values()
                    if value["description"].peer == peer
                ],
            }
        if action != "setup":
            raise Refused("Unknown authored group fixture action")
        server.state.accounts.set_processing(account, body.get("master", True))
        app.transport.messages = [
            m for m in app.transport.messages if m.peer not in GROUPS
        ]
        app.transport.deliveries = {
            identity: value
            for identity, value in app.transport.deliveries.items()
            if value["description"].peer not in GROUPS
        }
        for peer, title in GROUPS.items():
            key = f"{account}/{peer}"
            prior = deletion_states.get(key)
            if prior:
                prior["release"].set()
            async with asyncio.timeout(5):
                while store.rows(
                    "SELECT 1 FROM deletion_operations WHERE chat_key=? AND state IN ('queued','deleting')",
                    (key,),
                ):
                    await asyncio.sleep(0.01)
            deletion_states[key] = {
                "mode": "success",
                "release": asyncio.Event(),
                "attempts": [],
            }
            with store.transaction() as db:
                db.execute(
                    "DELETE FROM jobs WHERE scope=? OR scope LIKE ?", (key, key + "/%")
                )
                for table in (
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
                ):
                    db.execute(f"DELETE FROM {table} WHERE chat_key=?", (key,))
                db.execute(
                    "DELETE FROM message_versions WHERE source LIKE ?", (key + "/%",)
                )
                db.execute("DELETE FROM derivatives WHERE source LIKE ?", (key + "/%",))
                db.execute("DELETE FROM chats WHERE key=?", (key,))
            store.put_chat(
                Chat(account_id=account, peer=peer, title=title, kind="group")
            )
            store.groups.observe(
                key, access="available", reason="authored_native_evidence"
            )
            # What the Group Profile reads from Telegram (ADR 47 Q7).
            app.transport.group_profiles[peer] = {
                "title": title,
                "username": "authored_supergroup"
                if peer.startswith("channel:")
                else None,
                "members": 1200 if peer.startswith("channel:") else 4,
                "description": f"Authored description of {title}.",
            }
            # What a member's profile reads from Telegram (ADR 51 Q14).
            app.transport.profiles["user:8"] = {
                "first_name": "Bob",
                "last_name": "Authored",
                "username": "bob_authored",
                "bio": "Authored member bio.",
            }
            sources = [
                ("Alice", "user:7", False, "The planning session is on Friday.", None),
                ("Bob", "user:8", False, "I will bring the printed agenda.", None),
                (
                    "",
                    None,
                    False,
                    "Please include the schedule in the discussion.",
                    None,
                ),
                ("Owner", f"user:{account}", True, "My authored group message.", None),
                (
                    "Team channel",
                    "channel:42",
                    True,
                    "A signed anonymous group post.",
                    "Moderator",
                ),
                ("", None, True, "Direction alone does not prove the sender.", None),
            ]
            if body.get("own_incoming"):
                sources.append(
                    (
                        "Owner",
                        f"user:{account}",
                        False,
                        "Native ownership also applies without an outgoing flag.",
                        None,
                    )
                )
            for identity, (author, author_peer, outgoing, text, signature) in enumerate(
                sources, 1
            ):
                message = Message(
                    account_id=account,
                    peer=peer,
                    message_id=identity,
                    revision=1,
                    date=store.clock() - 60 + identity,
                    text=text,
                    outgoing=outgoing,
                    author=author,
                    author_peer=author_peer,
                    author_signature=signature,
                    forwarded=identity == 2,
                    imported=False,
                    forwarded_from="Earlier announcement" if identity == 2 else None,
                    forward_date=store.clock() - 86400 if identity == 2 else None,
                    forward_read=True,
                )
                app.sync.ingest(message, target=False)
                app.transport.messages.append(message)
        private = f"{account}/user:42"
        store.put_chat(
            Chat(
                account_id=account,
                peer="user:42",
                title="Private discussion",
                kind="private",
            )
        )
        app.sync.ingest(
            Message(
                account_id=account,
                peer="user:42",
                message_id=1,
                revision=1,
                date=store.clock() - 90,
                text="An authored private conversation.",
            ),
            target=False,
        )
        return {"account_id": account, "groups": list(GROUPS), "private": private}
