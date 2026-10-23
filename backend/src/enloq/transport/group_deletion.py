"""Fresh own-user group deletion proofs through the native packer boundary.

One-peer metadata and known exact originals supply the proof. No moderation,
member lookup, sender mutation or history scan belongs to this operation.
"""

from dataclasses import dataclass

from telethon import functions, types, utils

from ..domain.models import Message, Refused, same_content
from . import group_chats, group_sending
from .mtproto import native_request_guard, observation_cycle


def same_source(left, right, *, native=False):
    """Native material equality, with a separate local revision witness."""
    return bool(
        isinstance(left, Message)
        and isinstance(right, Message)
        and same_content(left, right)
        and all(
            getattr(left, field) == getattr(right, field)
            for field in (
                "account_id",
                "peer",
                "message_id",
                "author_peer",
                "author_signature",
                "date",
                "service",
                "topic_id",
                "native_edited",
            )
        )
        and (native or left.revision == right.revision)
        and (
            left.native_revision if left.native_revision is not None else left.revision
        )
        == (
            right.native_revision
            if right.native_revision is not None
            else right.revision
        )
    )


def own_source(source, account_id):
    return bool(
        isinstance(source, Message)
        and source.author_peer == f"user:{account_id}"
        and not source.deleted
        and not source.service
        and source.topic_id is None
    )


def access_lost(owner, peer, expected=None):
    key = f"{owner.account_id}/{peer}"
    state = owner.store.groups.get(key)
    owner.store.groups.observe(
        key,
        access="lost",
        supported=state.supported,
        reason="native_deletion_access_lost",
        observed_at=owner.observation(),
        expected_revision=expected[0] if expected else state.access_revision,
    )
    group_sending.invalidate(owner, peer)


@dataclass(frozen=True)
class GroupDeleteProof:
    owner: object
    description: object
    evidence: object
    expected: tuple
    version: int
    checked_at: float
    sources: tuple
    peer_bytes: bytes
    revoke_limit: int | None

    def guard(self, request=None, *, fresh=False):
        owner, description = self.owner, self.description
        owner._group_read(description.peer, expected=self.expected)
        if (
            description.account_id != owner.account_id
            or description.access_revision != self.expected[0]
            or tuple(description.identities)
            != tuple(source.message_id for source in self.sources)
            or len(description.sources) != len(self.sources)
        ):
            raise Refused("Captured group deletion identity changed")
        if group_sending.metadata_version(owner, description.peer) != self.version:
            raise Refused("Native group deletion rights changed; review again")
        me = getattr(owner, "identity", None)
        if (
            not isinstance(me, types.User)
            or me.id != description.account_id
            or me.bot
            or me.deleted
        ):
            raise Refused("Connected personal group deletion identity changed")
        if fresh:
            age = owner.store.clock() - self.checked_at
            identity_age = owner.store.clock() - owner.identity_checked_at
            if not (
                0 <= age <= group_sending.PROOF_SECONDS
                and 0 <= identity_age <= group_sending.PROOF_SECONDS
            ):
                raise Refused("Native group deletion rights expired; review again")
        key = f"{description.account_id}/{description.peer}"
        for captured, described in zip(self.sources, description.sources, strict=True):
            current = owner.store.message(key, captured.message_id)
            if (
                not own_source(captured, description.account_id)
                or not same_source(captured, described)
                or not same_source(captured, current)
                or not owner.store.groups.current(current, admission=False)
            ):
                raise Refused("Exact own-user group deletion source changed")
            if self.revoke_limit is not None:
                age = owner.store.clock() - captured.date
                if not 0 <= age < self.revoke_limit:
                    raise Refused("Native basic-group revoke time limit reached")
        if request is not None:
            if description.peer.startswith("channel:"):
                valid = bool(
                    isinstance(request, functions.channels.DeleteMessagesRequest)
                    and bytes(request.channel) == self.peer_bytes
                )
            else:
                valid = bool(
                    isinstance(request, functions.messages.DeleteMessagesRequest)
                    and request.revoke is True
                )
            if not valid or tuple(request.id) != description.identities:
                raise Refused("Captured native group deletion request changed")
        self.evidence.guard()


async def prepare(owner, description, evidence):
    """Re-read connected identity, native metadata and every exact chunk source."""
    evidence.guard()
    expected = owner._group_read(description.peer)
    if description.access_revision != expected[0]:
        raise Refused("Native group deletion access changed; review again")
    sources = tuple(source.model_copy(deep=True) for source in description.sources)
    if (
        len(sources) != len(description.identities)
        or tuple(source.message_id for source in sources) != description.identities
        or any(not own_source(source, description.account_id) for source in sources)
    ):
        raise Refused("Verified own-user group originals are required")
    key = f"{description.account_id}/{description.peer}"

    def guard(_=None):
        owner._group_read(description.peer, expected=expected)
        if (
            owner.account_id != description.account_id
            or description.access_revision != expected[0]
            or tuple(description.identities)
            != tuple(source.message_id for source in sources)
            or len(description.sources) != len(sources)
        ):
            raise Refused("Connected group deletion account changed")
        for captured, described in zip(sources, description.sources, strict=True):
            current = owner.store.message(key, captured.message_id)
            if (
                not same_source(captured, described)
                or not same_source(captured, current)
                or not owner.store.groups.current(current, admission=False)
            ):
                raise Refused("Exact own-user group deletion source changed")
        evidence.guard()

    guard()
    try:
        with observation_cycle(), native_request_guard(guard):
            me = await owner.client.get_me()
            guard()
            if not isinstance(me, types.User) or me.deleted:
                raise Refused("Verified personal group deletion identity unavailable")
            owner._accept_identity(me)
            peer = await owner.client.get_input_entity(
                group_sending.captured_peer(description.peer)
            )
            guard()
            required = (
                types.InputPeerChannel
                if description.peer.startswith("channel:")
                else types.InputPeerChat
            )
            if (
                not isinstance(peer, required)
                or group_sending.identity_of(peer) != description.peer
            ):
                raise Refused("Captured native group deletion peer changed")
            request = (
                functions.channels.GetChannelsRequest([utils.get_input_channel(peer)])
                if isinstance(peer, types.InputPeerChannel)
                else functions.messages.GetChatsRequest([peer.chat_id])
            )
            request_bytes = bytes(request)
            query_version = group_sending.metadata_version(owner, description.peer)

            def exact_metadata(native):
                guard()
                if (
                    group_sending.metadata_version(owner, description.peer)
                    != query_version
                ):
                    raise Refused("Native group deletion rights changed during read")
                if type(native) is not type(request) or bytes(native) != request_bytes:
                    raise Refused("Exact native group deletion rights request changed")

            request._enloq_final_guard = exact_metadata
            response = await owner.client(request)
            exact_metadata(request)
            entities = getattr(response, "chats", ())
            if len(entities) != 1:
                raise Refused("Exact native group deletion rights unavailable")
            entity = entities[0]
            description_native = group_chats.describe(entity)
            if (
                description_native is None
                or description_native.peer != description.peer
            ):
                raise Refused("Exact native group deletion rights peer changed")
            owner._observe_group(
                entity, observed=owner.observation(), expected_revision=expected[0]
            )
            guard()
            if (
                not description_native.supported
                or description_native.access != "available"
                or getattr(entity, "min", False)
                or getattr(
                    getattr(entity, "banned_rights", None), "view_messages", False
                )
            ):
                raise Refused("Native group deletion access unavailable")
            version = group_sending.metadata_version(owner, description.peer)
            checked_at = owner.store.clock()

            def checked_guard(_=None):
                guard()
                if group_sending.metadata_version(owner, description.peer) != version:
                    raise Refused(
                        "Native group deletion rights changed during preparation"
                    )

            revoke_limit = None
            with native_request_guard(checked_guard):
                if isinstance(peer, types.InputPeerChat):
                    # Telegram Desktop/TDLib apply revoke_time_limit to basic
                    # chats; deletable supergroup posts are always for everyone.
                    config = await owner.client(functions.help.GetConfigRequest())
                    checked_guard()
                    limit = getattr(config, "revoke_time_limit", None)
                    if (
                        not isinstance(config, types.Config)
                        or type(limit) is not int
                        or limit <= 0
                    ):
                        raise Refused(
                            "Native basic-group revoke time limit unavailable"
                        )
                    revoke_limit = limit
                checked = await owner.exact_many(
                    description.peer, description.identities
                )
                checked_guard()
                if len(checked) != len(sources) or len(
                    {source.message_id for source in checked}
                ) != len(checked):
                    raise Refused("Exact native group deletion originals unavailable")
                by_id = {source.message_id: source for source in checked}
                for captured in sources:
                    native = by_id.get(captured.message_id)
                    if not own_source(
                        native, description.account_id
                    ) or not same_source(captured, native, native=True):
                        raise Refused(
                            "Exact native own-user group deletion source changed"
                        )
                checked_guard()
        proof = GroupDeleteProof(
            owner,
            description,
            evidence,
            expected,
            version,
            checked_at,
            sources,
            bytes(utils.get_input_channel(peer))
            if isinstance(peer, types.InputPeerChannel)
            else bytes(peer),
            revoke_limit,
        )
        proof.guard(fresh=True)
        request = (
            functions.channels.DeleteMessagesRequest(
                utils.get_input_channel(peer), list(description.identities)
            )
            if isinstance(peer, types.InputPeerChannel)
            else functions.messages.DeleteMessagesRequest(
                list(description.identities), revoke=True
            )
        )
        return request, proof
    except group_sending.ACCESS_ERRORS as error:
        access_lost(owner, description.peer, expected)
        evidence.guard()
        raise Refused(type(error).__name__) from None
