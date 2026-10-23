import time
from dataclasses import dataclass
from datetime import UTC, datetime

from ..domain.catalog import CatalogLibrary, CatalogSearchData
from ..domain.deletion import (
    DeleteDescription,
    DeleteEvidence,
    DeleteReceipt,
    DeletionRead,
    deletion_ids,
    deletion_peer,
)
from ..domain.delivery import ReceiptBatch, ReceiptSink, SendDescription, SendEvidence
from ..domain.models import (
    DeliveryRefused,
    MediaInfo,
    MediaTooLarge,
    Message,
    Reaction,
    ReactionActor,
    ReactionChip,
    ReactionSnapshot,
    Refused,
    Unavailable,
)

# An authored palette; real accounts use Telegram's available reactions.
PALETTE = ["👍", "👎", "❤️", "🔥", "🤝", "👌", "🙏", "😁"]


@dataclass(frozen=True)
class _GroupSendReview:
    owner: object
    account_id: int
    version: int
    current: object

    def guard(self, *, fresh=False):
        self.current()
        if (
            not self.owner.connected
            or self.owner.account_id != self.account_id
            or self.owner.group_send_rights_version != self.version
        ):
            raise Refused("Authored native group send rights changed")


class FakeTransport:
    """Deterministic authored data; no configuration, sockets, or secret imports."""

    account_id = 1

    def __init__(self):
        self.connected = True
        # What reached this account's own Saved Messages (ADR 43 Q4), and an
        # authored refusal for the next post.
        self.saved_posts: list[dict] = []
        self.saved_failure: str | None = None
        # The Saved Build message's pins and edits (ADR 50 Q5); a post's
        # removal stands for the owner deleting it in Telegram.
        self.saved_pins: list[int] = []
        self.saved_edits: list[int] = []
        self.pin_failure: str | None = None
        # Authored images in that Saved Messages, newest last, for the host's
        # read of the newest one (ADR 43).
        self.saved_images: list[dict] = []
        # The owner's own authored notes there, newest last, for the host's
        # read of the last few (ADR 51 Q1); the service's posts are never
        # among them.
        self.saved_owner_notes: list[dict] = []
        # Authored people for New Message (ADR 51 Q16): "contacts" and
        # "search" results, each {peer, name, username, photo_id}.
        self.people: dict[str, list[dict]] = {}
        # When each post went out, for the host's read of a pairing code
        # (ADR 43).
        self.saved_post_times: dict[int, float] = {}
        # Authored correspondents' profiles (ADR 46 Q11), groups' profiles
        # (ADR 47 Q7) and their reads.
        self.profiles: dict[str, dict] = {}
        self.profile_reads: list[str] = []
        self.group_profiles: dict[str, dict] = {}
        self.group_profile_reads: list[str] = []
        self.deliveries: dict[int, dict] = {}
        self.messages = []
        self.media = {}
        self.downloads = []
        self.exact_reads = []
        self.deletion_calls = []
        self.deletion_reads = []
        self.deletion_failure = None
        self.deletion_answers = []
        self.lose_delete_receipt = False
        self.deletion_check_failure = None
        self.deletion_unavailable = set()
        self.avatars = {}
        self.avatar_reads = []
        self.reads = []
        self.typing_events = []
        self.lose_receipt = False
        self.upload_failure = False
        self.partial_receipts = None
        # Seconds a synthetic flood hold keeps a send from being handed off.
        self.held = 0
        self.check_failure = None
        # (peer, id) -> {"own": [Reaction], "peer": {emoji: native date}}
        self.reaction_state = {}
        self.reaction_calls = []
        self.reaction_failure = None
        self.palette = {"emoji": list(PALETTE), "limit": 1, "premium": False}
        self.unread = {}
        self.unread_reads = []
        self.catalog_changed = lambda: None
        self.catalog_data = CatalogLibrary(premium=False)
        self.catalog_packs = {}
        self.catalog_searches = {}
        self.catalog_calls = []
        self.catalog_failure = None
        self.catalog_media = {}
        self.catalog_downloads = []
        self.group_send_reviews = []
        self.group_send_review_failure = None
        self.group_send_review_hook = None
        self.group_send_rights_version = 0

    def catalog_ready(self):
        if not self.connected:
            raise Unavailable("Authored account is offline")
        if self.catalog_failure:
            raise self.catalog_failure

    async def catalog_entitlement(self):
        self.catalog_ready()
        self.catalog_calls.append(("entitlement",))
        return self.catalog_data.premium is True

    async def catalog_library(self):
        self.catalog_ready()
        self.catalog_calls.append(("library",))
        return self.catalog_data.model_copy(deep=True)

    async def catalog_pack(self, pack):
        self.catalog_ready()
        self.catalog_calls.append(("pack", pack.id))
        if pack.id not in self.catalog_packs:
            raise Unavailable("Authored sticker pack unavailable")
        return self.catalog_packs[pack.id].model_copy(deep=True)

    async def catalog_search(self, query, offset=""):
        self.catalog_ready()
        self.catalog_calls.append(("search", query, offset))
        return self.catalog_searches.get(
            (query, offset), CatalogSearchData(items=())
        ).model_copy(deep=True)

    async def catalog_refresh_document(self, source):
        self.catalog_ready()
        self.catalog_calls.append(("document", source.document_id))
        found = (
            self.catalog_data.gifs
            + self.catalog_data.recent
            + self.catalog_data.favorites
        )
        found += tuple(
            item for pack in self.catalog_packs.values() for item in pack.items
        )
        fresh = next(
            (
                item
                for item in found
                if item.account_id == source.account_id == self.account_id
                and item.document_id == source.document_id
            ),
            None,
        )
        if fresh is None:
            raise Unavailable("Authored exact media unavailable")
        return fresh.model_copy(deep=True)

    async def catalog_download(self, source, path, *, max_bytes, thumb=False):
        from .selected_media import new_private_file, remove_owned, source_location

        self.catalog_ready()
        _, declared = source_location(source, thumb=thumb, max_bytes=max_bytes)
        if source.account_id != self.account_id:
            raise Refused("Authored media belongs to another account")
        self.catalog_downloads.append((source.document_id, thumb))
        data = self.catalog_media.get((source.document_id, thumb))
        if data is None:
            raise Unavailable("Authored exact media is unavailable")
        if len(data) > max_bytes:
            raise MediaTooLarge("Authored exact media exceeds its limit")
        if not data or (declared is not None and len(data) != declared):
            raise Unavailable("Authored complete media is unavailable")
        path, descriptor, identity = new_private_file(path)
        try:
            import os

            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
        except BaseException:
            remove_owned(path, identity)
            raise
        return source.model_copy(deep=True)

    def reaction_snapshot(self, peer, identity, observed=None):
        """A complete authored snapshot: the owner's and the peer's reactions."""
        state = self.reaction_state.get((peer, identity), {"own": [], "peer": {}})
        chips, actors = {}, []
        for order, reaction in enumerate(state["own"]):
            chip = chips.setdefault(reaction.model_dump_json(), [reaction, 0, None])
            chip[1] += 1
            chip[2] = order
            actors.append(
                ReactionActor(actor="own", reaction=reaction, date=time.time())
            )
        for emoji, date in state["peer"].items():
            reaction = Reaction(emoji=emoji)
            chip = chips.setdefault(reaction.model_dump_json(), [reaction, 0, None])
            chip[1] += 1
            actors.append(ReactionActor(actor="peer", reaction=reaction, date=date))
        return ReactionSnapshot(
            chips=[
                ReactionChip(
                    reaction=reaction, count=count, mine=order is not None, order=order
                )
                for reaction, count, order in chips.values()
            ],
            actors=actors,
            complete=True,
            observed=time.time() if observed is None else observed,
        )

    async def reaction_options(self):
        if not self.connected:
            raise Unavailable("Offline; reactions unavailable")
        return dict(self.palette, emoji=list(self.palette["emoji"]))

    async def profile(self, peer):
        self.profile_reads.append(peer)
        if not self.connected:
            raise Unavailable("Offline; the profile is unavailable")
        if peer not in self.profiles:
            raise Unavailable("Authored profile unavailable")
        return dict(self.profiles[peer])

    async def contacts(self):
        if not self.connected:
            raise Unavailable("Offline; contacts are unavailable")
        return [dict(value) for value in self.people.get("contacts", [])]

    async def search_people(self, query, *, limit=20):
        if not self.connected:
            raise Unavailable("Offline; the search is unavailable")
        needle = query.strip().lstrip("@").lower()
        return [
            dict(value)
            for value in self.people.get("search", [])
            if needle in value["name"].lower()
            or needle in (value.get("username") or "").lower()
        ][:limit]

    async def resolve_person(self, peer):
        if not self.connected:
            raise Unavailable("Offline; the chat is unavailable")
        for values in self.people.values():
            for value in values:
                if value["peer"] == peer:
                    return dict(value)
        if peer in self.profiles:
            # An authored profile is a person too, as a member's is.
            profile = self.profiles[peer]
            name = " ".join(
                part
                for part in (profile.get("first_name"), profile.get("last_name"))
                if part
            )
            return {
                "peer": peer,
                "name": name or "Telegram user",
                "username": profile.get("username"),
                "photo_id": None,
            }
        raise Refused("Only a person's private chat opens here")

    async def group_profile(self, peer):
        self.group_profile_reads.append(peer)
        if not self.connected:
            raise Unavailable("Offline; the profile is unavailable")
        if peer not in self.group_profiles:
            raise Unavailable("Authored group profile unavailable")
        return dict(self.group_profiles[peer])

    async def post_saved(self, *, path=None, caption="", text="", monospace=False):
        if not self.connected:
            raise Unavailable("Offline; nothing posted")
        if self.saved_failure:
            raise Unavailable(self.saved_failure)
        identity = 5000 + len(self.saved_posts)
        self.saved_posts.append(
            {
                "id": identity,
                "file": path.name if path is not None else None,
                "bytes": path.read_bytes() if path is not None else None,
                "caption": caption,
                "text": text,
                "monospace": monospace,
            }
        )
        self.saved_post_times[identity] = time.time()
        return identity

    async def edit_saved(self, identity, *, path, caption):
        if not self.connected:
            raise Unavailable("Offline; nothing changed")
        if self.saved_failure:
            raise Unavailable(self.saved_failure)
        for post in self.saved_posts:
            if post["id"] == identity:
                post.update(file=path.name, bytes=path.read_bytes(), caption=caption)
                self.saved_edits.append(identity)
                return True
        return False

    async def pin_saved(self, identity):
        if not self.connected:
            raise Unavailable("Offline; nothing pinned")
        if self.pin_failure:
            raise Unavailable(self.pin_failure)
        self.saved_pins.append(identity)

    async def latest_saved_image(self, *, scan, max_bytes):
        if not self.connected:
            raise Unavailable("Offline; nothing read")
        for image in reversed(self.saved_images[-scan:]):
            if len(image["data"]) > max_bytes:
                raise MediaTooLarge("The newest image in Saved Messages is too large")
            return dict(image)
        raise Refused(f"No image among the last {scan} Saved Messages")

    async def saved_notes(self, *, count, scan, max_bytes, build=None):
        if not self.connected:
            raise Unavailable("Offline; nothing read")
        notes = []
        for note in reversed(self.saved_owner_notes[-scan:]):
            if note["message_id"] == build:
                continue
            note = {"media": None, "mime": None, "data": None} | dict(note)
            if note["data"] is not None and len(note["data"]) > max_bytes:
                note.update(mime=None, data=None)
            notes.append(note)
            if len(notes) == count:
                break
        return notes

    async def latest_saved_code(self, *, scan, max_age):
        if not self.connected:
            raise Unavailable("Offline; nothing read")
        oldest = time.time() - max_age
        for post in reversed(self.saved_posts[-scan:]):
            posted = self.saved_post_times.get(post["id"], 0)
            if posted < oldest:
                break
            text = post["text"] or ""
            if post["monospace"] and len(text) == 6 and text.isdigit():
                return {
                    "message_id": post["id"],
                    "date": datetime.fromtimestamp(posted, UTC),
                    "code": text,
                }
        raise Refused(
            f"No pairing code posted to Saved Messages in the last {max_age} seconds"
        )

    async def send_reaction(self, peer, identity, reactions):
        if peer.startswith(("chat:", "channel:")):
            raise Refused("Group reactions are display-only")
        if not self.connected:
            raise Unavailable("Offline; nothing sent")
        if self.reaction_failure:
            raise self.reaction_failure
        self.reaction_calls.append((peer, identity, tuple(reactions)))
        state = self.reaction_state.setdefault(
            (peer, identity), {"own": [], "peer": {}}
        )
        state["own"] = list(reactions)
        return self.reaction_snapshot(peer, identity)

    async def unread_reactions(self, peer):
        self.unread_reads.append(peer)
        if not self.connected:
            raise Unavailable("Offline; nothing read")
        return [
            (identity, self.reaction_snapshot(peer, identity))
            for identity in self.unread.get(peer, ())
        ]

    async def download_avatar(self, peer, photo_id, path, *, max_bytes):
        self.avatar_reads.append((peer, photo_id))
        data = self.avatars.get((peer, photo_id))
        if not self.connected or not data or len(data) > max_bytes:
            raise Unavailable("Authored photo unavailable")
        path.write_bytes(data)

    def bind_receipts(self, sink: ReceiptSink):
        self._receipt_sink = sink

    async def review_group_send(self, description: SendDescription, guard):
        from .group_sending import validate_review_media

        guard()
        if (
            description.account_id != self.account_id
            or not description.peer.startswith(("chat:", "channel:"))
            or not self.connected
        ):
            raise Refused("Authored connected group review is unavailable")
        if self.group_send_review_failure:
            raise self.group_send_review_failure
        await validate_review_media(description, guard)
        guard()
        if description.media and description.media.requires_premium:
            if not await self.catalog_entitlement():
                raise DeliveryRefused(
                    "Telegram Premium is required to send this sticker"
                )
        self.group_send_reviews.append(description)
        proof = _GroupSendReview(
            self, self.account_id, self.group_send_rights_version, guard
        )
        if self.group_send_review_hook:
            self.group_send_review_hook()
        proof.guard(fresh=True)
        return proof

    async def send(
        self, description: SendDescription, evidence: SendEvidence
    ) -> ReceiptBatch:
        if description.account_id != self.account_id:
            raise Refused("Wrong connected account")
        if not self.connected:
            raise Unavailable("Offline; nothing dispatched")
        selected = description.media
        if selected is not None:
            from .selected_media import validate_send_media

            await validate_send_media(selected)
            if (
                selected.requires_premium or selected.source.metadata.requires_premium
            ) and not await self.catalog_entitlement():
                raise DeliveryRefused(
                    "Telegram Premium is required to send this sticker"
                )
            if self.upload_failure:
                raise DeliveryRefused(
                    "Authored selected media upload failed; nothing sent"
                )
        if description.images and self.upload_failure:
            raise DeliveryRefused("Image upload failed; nothing sent")
        if self.held:
            if not description.images:
                evidence.calling()
                evidence.watching()
                evidence.holding(self.held_until(description))
            raise TimeoutError("Synthetic hold outlasted the send deadline")
        evidence.calling()
        evidence.watching()
        evidence.guard()
        if selected is not None:
            from .selected_media import validate_retained

            validate_retained(selected)
            if (
                selected.requires_premium or selected.source.metadata.requires_premium
            ) and self.catalog_data.premium is not True:
                raise DeliveryRefused(
                    "Telegram Premium is required to send this sticker"
                )
        evidence.handing_off()
        result = []
        for index, identity in enumerate(description.random_ids):
            if identity not in self.deliveries:
                native = 1000 + len(self.deliveries)
                self.deliveries[identity] = {
                    "description": description,
                    "message_id": native,
                }
                if description.images:
                    self.media[description.peer, native] = description.images[
                        index
                    ].path.read_bytes()
                elif selected is not None:
                    self.media[description.peer, native] = selected.path.read_bytes()
                    self.messages.append(
                        Message(
                            account_id=self.account_id,
                            peer=description.peer,
                            message_id=native,
                            revision=1,
                            date=time.time(),
                            outgoing=True,
                            text=description.text,
                            reply_to=description.reply_to,
                            media=selected.kind,
                            media_info=MediaInfo(
                                mime=selected.mime,
                                file_name="animation." + selected.format
                                if selected.kind == "gif"
                                else "sticker." + selected.format,
                                size=selected.size,
                                width=selected.width,
                                height=selected.height,
                                duration=selected.duration,
                                sticker=selected.sticker,
                                emoji=selected.emoji,
                            ),
                        )
                    )
            result.append((identity, self.deliveries[identity]["message_id"]))
        if self.lose_receipt:
            raise TimeoutError("Receipt unavailable")
        batch = ReceiptBatch(
            tuple(result[: self.partial_receipts] if description.images else result)
        )
        self._receipt_sink(batch)
        return batch

    def held_until(self, description):
        return time.time() + self.held if self.held else 0

    async def refresh_delivery(self):
        if self.check_failure:
            raise self.check_failure
        if not self.connected:
            raise Unavailable("Offline; nothing checked")
        self._receipt_sink(
            ReceiptBatch(
                tuple(
                    (identity, value["message_id"])
                    for identity, value in self.deliveries.items()
                )
            )
        )

    async def history(self, peer: str, before: int, limit: int):
        return sorted(
            [
                m
                for m in self.messages
                if m.peer == peer and (not before or m.message_id < before)
            ],
            key=lambda m: m.message_id,
            reverse=True,
        )[: min(limit, 100)]

    async def stream(
        self,
        peer,
        identity,
        *,
        max_bytes,
        expected_revision=None,
        priority=1,
        thumb=False,
    ):
        self.downloads.append((peer, identity, priority, thumb))
        data = self.media.get((peer, identity, "poster") if thumb else (peer, identity))
        if data is None:
            raise Unavailable("Synthetic media unavailable")
        if len(data) > max_bytes:
            raise MediaTooLarge("Synthetic media over limit")
        for offset in range(0, len(data), 1024 * 1024):
            yield data[offset : offset + 1024 * 1024]

    async def download(
        self,
        peer,
        identity,
        path,
        *,
        max_bytes,
        expected_revision=None,
        timeout=120,
        priority=1,
        thumb=False,
    ):
        with path.open("wb") as output:
            async for chunk in self.stream(
                peer,
                identity,
                max_bytes=max_bytes,
                expected_revision=expected_revision,
                priority=priority,
                thumb=thumb,
            ):
                output.write(chunk)

    async def exact(self, peer, identity):
        return next(
            (m for m in self.messages if m.peer == peer and m.message_id == identity),
            None,
        )

    async def exact_many(self, peer, identities):
        self.exact_reads.append((peer, tuple(identities)))
        wanted = set(identities)
        return [m for m in self.messages if m.peer == peer and m.message_id in wanted]

    async def delete(
        self, description: DeleteDescription, evidence: DeleteEvidence
    ) -> DeleteReceipt:
        if not isinstance(description, DeleteDescription):
            raise Refused("A captured native deletion description is required")
        if description.account_id != self.account_id:
            raise Refused("Wrong connected deletion account")
        if not self.connected:
            raise Unavailable("Offline; nothing deleted")
        evidence.calling()
        evidence.watching()
        evidence.guard()
        evidence.handing_off()
        self.deletion_calls.append(description)
        answer = (
            self.deletion_answers.pop(0)
            if self.deletion_answers
            else self.deletion_failure
        )
        if isinstance(answer, BaseException):
            if isinstance(answer, Refused):
                evidence.refused(type(answer).__name__)
            raise answer
        self.messages = [
            message
            for message in self.messages
            if not (
                message.account_id == description.account_id
                and message.peer == description.peer
                and message.message_id in description.identities
            )
        ]
        if self.lose_delete_receipt:
            raise TimeoutError("Synthetic deletion acknowledgement unavailable")
        if answer is not None and not isinstance(answer, DeleteReceipt):
            raise TimeoutError("Synthetic deletion acknowledgement unusable")
        return answer or DeleteReceipt(1, len(description.identities))

    async def inspect_deletion(self, peer, identities):
        deletion_peer(peer)
        identities = deletion_ids(identities)
        present, absent, unavailable = [], [], []
        for offset in range(0, len(identities), 100):
            batch = identities[offset : offset + 100]
            self.deletion_reads.append((peer, batch))
            for identity in batch:
                if (
                    not self.connected
                    or self.deletion_check_failure
                    or identity in self.deletion_unavailable
                ):
                    unavailable.append(identity)
                    continue
                found = [
                    message
                    for message in self.messages
                    if message.account_id == self.account_id
                    and message.peer == peer
                    and message.message_id == identity
                    and not message.deleted
                ]
                if len(found) == 1:
                    present.extend(found)
                elif found:
                    unavailable.append(identity)
                elif any(
                    message.account_id == self.account_id
                    and message.message_id == identity
                    and not message.deleted
                    for message in self.messages
                ):
                    unavailable.append(identity)
                else:
                    absent.append(identity)
        return DeletionRead(
            self.account_id,
            peer,
            identities,
            tuple(present),
            tuple(absent),
            tuple(unavailable),
        )

    async def read(self, peer: str, through: int):
        self.reads.append((peer, through))

    async def typing(self, peer: str):
        self.typing_events.append(peer)

    async def close(self):
        self.connected = False
