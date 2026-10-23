"""One-peer native send rights and immutable personal-sender wire proofs.

Metadata reads use GetChats/GetChannels, never member lists or reader queries.
The synchronous proof expires rather than trusting rights across a long paced
hold; every native packing/recovery handoff uses it again.
"""

import asyncio
from dataclasses import dataclass

from telethon import errors, functions, types, utils

from ..domain.deletion import deletion_fingerprint
from ..domain.models import DeliveryRefused, Message, Refused, same_content
from . import group_chats
from .mtproto import native_request_guard, observation_cycle
from .sender import account_sender

PROOF_SECONDS = 15
ACCESS_ERRORS = (
    errors.ChannelPrivateError,
    errors.ChannelPublicGroupNaError,
    errors.UserBannedInChannelError,
    errors.ChatForbiddenError,
)
SEND_REFUSALS = (
    *ACCESS_ERRORS,
    errors.SendAsPeerInvalidError,
    errors.ChatWriteForbiddenError,
    errors.ChatRestrictedError,
    errors.ChatGuestSendForbiddenError,
    errors.ChatSendPlainForbiddenError,
    errors.ChatSendMediaForbiddenError,
    errors.ChatSendPhotosForbiddenError,
    errors.ChatSendGifsForbiddenError,
    errors.ChatSendStickersForbiddenError,
    errors.SlowModeMultiMsgsDisabledError,
    errors.MessageIdInvalidError,
    errors.MsgIdInvalidError,
)


def group_peer(peer):
    return isinstance(peer, str) and peer.startswith(("chat:", "channel:"))


def captured_peer(peer):
    try:
        kind, raw = peer.split(":")
        identity = int(raw)
        if str(identity) != raw or not 0 < identity < 2**63:
            raise ValueError
        return {
            "user": types.PeerUser,
            "chat": types.PeerChat,
            "channel": types.PeerChannel,
        }[kind](identity)
    except (ValueError, KeyError, AttributeError):
        raise DeliveryRefused("Captured Telegram send peer is invalid") from None


def identity_of(peer):
    for field, kind in (
        ("user_id", "user"),
        ("chat_id", "chat"),
        ("channel_id", "channel"),
    ):
        value = getattr(peer, field, None)
        if value:
            return f"{kind}:{value}"
    raise DeliveryRefused("Native send peer is invalid")


def metadata_version(owner, peer):
    return getattr(owner, "_group_send_versions", {}).get(peer, 0)


def invalidate(owner, peer):
    versions = getattr(owner, "_group_send_versions", None)
    if versions is None:
        versions = owner._group_send_versions = {}
    versions[peer] = versions.get(peer, 0) + 1


def observe_metadata(owner, entity):
    description = group_chats.describe(entity)
    if description is None:
        return
    if (
        getattr(entity, "min", False)
        and description.supported
        and description.access == "available"
        and not any(
            getattr(entity, field, None)
            for field in (
                "admin_rights",
                "banned_rights",
                "default_banned_rights",
                "send_paid_messages_stars",
            )
        )
    ):
        # Minimal source-response entities omit current account rights. They
        # cannot replace the preceding complete one-peer metadata proof.
        return
    values = getattr(owner, "_group_send_metadata", None)
    if values is None:
        values = owner._group_send_metadata = {}
    fingerprint = (
        type(entity),
        description.peer,
        description.supported,
        description.access,
        description.reason,
        description.migrated_to,
        getattr(entity, "min", False),
        getattr(entity, "creator", False),
        getattr(entity, "send_paid_messages_stars", None),
        *(
            bytes(value) if value is not None else None
            for value in (
                getattr(entity, "admin_rights", None),
                getattr(entity, "banned_rights", None),
                getattr(entity, "default_banned_rights", None),
            )
        ),
    )
    if values.get(description.peer) != fingerprint:
        values[description.peer] = fingerprint
        invalidate(owner, description.peer)


def lost(owner, peer, expected=None):
    if not group_peer(peer):
        return
    key = f"{owner.account_id}/{peer}"
    state = owner.store.groups.get(key)
    owner.store.groups.observe(
        key,
        access="lost",
        supported=state.supported,
        reason="native_send_access_lost",
        observed_at=owner.observation(),
        expected_revision=expected[0] if expected else state.access_revision,
    )
    invalidate(owner, peer)


def refusal(owner, description, evidence, error):
    if group_peer(description.peer) and isinstance(error, ACCESS_ERRORS):
        lost(owner, description.peer)
    return owner._refused(evidence, error)


def paid_refusal(error):
    return (
        isinstance(error, errors.RPCError)
        and error.code in (400, 403)
        and (
            str(getattr(error, "message", "")).startswith("ALLOW_PAYMENT_REQUIRED_")
            or getattr(error, "message", "")
            in {"BALANCE_TOO_LOW", "STARS_BALANCE_TOO_LOW"}
        )
    )


def rights(entity, description):
    admin = getattr(entity, "admin_rights", None)
    if getattr(admin, "anonymous", False):
        raise DeliveryRefused("Anonymous group sending is unavailable")
    individual = getattr(entity, "banned_rights", None)
    default = (
        None
        if (getattr(entity, "creator", False) or admin)
        else getattr(entity, "default_banned_rights", None)
    )
    flags = ["view_messages", "send_messages"]
    if description.images:
        flags += ["send_media", "send_photos"]
    elif description.media:
        flags += [
            "send_media",
            "send_gifs" if description.media.kind == "gif" else "send_stickers",
        ]
    else:
        flags += ["send_plain"]
    if any(
        getattr(value, flag, False) for value in (individual, default) for flag in flags
    ):
        raise DeliveryRefused("Current native group rights do not permit this send")
    if getattr(entity, "send_paid_messages_stars", 0):
        raise DeliveryRefused("This group requires a Telegram Stars payment")


@dataclass(frozen=True)
class SendTarget:
    peer: object
    send_as: object = None
    proof: object = None


def preparation_guard(target, evidence):
    peer_bytes = bytes(target.peer)

    def guard(request):
        if target.proof:
            target.proof.guard()
        if (
            isinstance(request, functions.messages.UploadMediaRequest)
            and bytes(request.peer) != peer_bytes
        ):
            raise Refused("Captured native media preparation peer changed")
        evidence.guard()

    return guard


@dataclass(frozen=True)
class GroupSendProof:
    owner: object
    description: object
    evidence: object
    expected: tuple
    version: int
    checked_at: float
    peer_bytes: bytes
    sender_bytes: bytes | None
    reply_revision: int | None

    def guard(self, request=None, *, fresh=False):
        owner, description = self.owner, self.description
        owner._group_read(description.peer, expected=self.expected)
        if metadata_version(owner, description.peer) != self.version:
            raise Refused("Native group send rights changed; review the send again")
        me = getattr(owner, "identity", None)
        if (
            not isinstance(me, types.User)
            or me.id != description.account_id
            or me.bot
            or me.deleted
        ):
            raise Refused("Connected personal group sender changed")
        if fresh:
            age = owner.store.clock() - self.checked_at
            identity_age = owner.store.clock() - owner.identity_checked_at
            if not 0 <= age <= PROOF_SECONDS or not 0 <= identity_age <= PROOF_SECONDS:
                raise Refused("Native group send rights expired; review the send again")
        if self.reply_revision is not None:
            message = owner.store.message(
                f"{description.account_id}/{description.peer}", description.reply_to
            )
            if (
                not message
                or message.revision != self.reply_revision
                or not owner.store.groups.current(message, admission=False)
            ):
                raise Refused("Exact group reply source changed")
        if request is not None and (
            bytes(request.peer) != self.peer_bytes
            or (bytes(request.send_as) if request.send_as is not None else None)
            != self.sender_bytes
        ):
            raise Refused("Connected personal group send identity changed")
        self.evidence.guard()


async def prepare(owner, description, evidence, *, review=False):
    evidence.guard()
    native_peer = captured_peer(description.peer)
    if not group_peer(description.peer):
        peer = await owner.client.get_input_entity(native_peer)
        evidence.guard()
        if (
            not isinstance(peer, types.InputPeerUser)
            or identity_of(peer) != description.peer
        ):
            raise DeliveryRefused("Captured private send peer changed")
        return SendTarget(peer)
    if description.topic_id is not None:
        raise DeliveryRefused("Forum/topic sending is unavailable")
    expected = owner._group_read(description.peer)

    def guard(_=None):
        owner._group_read(description.peer, expected=expected)
        evidence.guard()

    try:
        with observation_cycle(), native_request_guard(guard):
            me = await owner.client.get_me()
            guard()
            if not isinstance(me, types.User) or me.deleted:
                raise Refused("Connected personal group sender is unavailable")
            owner._accept_identity(me)
            peer = await owner.client.get_input_entity(native_peer)
            guard()
            required = (
                types.InputPeerChannel
                if description.peer.startswith("channel:")
                else types.InputPeerChat
            )
            if not isinstance(peer, required) or identity_of(peer) != description.peer:
                raise DeliveryRefused("Captured native group send peer changed")
            request = (
                functions.channels.GetChannelsRequest([utils.get_input_channel(peer)])
                if isinstance(peer, types.InputPeerChannel)
                else functions.messages.GetChatsRequest([peer.chat_id])
            )
            request_bytes = bytes(request)
            query_version = metadata_version(owner, description.peer)

            def exact_metadata(native):
                guard()
                if metadata_version(owner, description.peer) != query_version:
                    raise Refused(
                        "Native group send rights changed during metadata read"
                    )
                if type(native) is not type(request) or bytes(native) != request_bytes:
                    raise Refused("Exact native group rights request changed")

            request._enloq_final_guard = exact_metadata
            exact_metadata(request)
            response = await owner.client(request)
            exact_metadata(request)
            entities = getattr(response, "chats", ())
            if len(entities) != 1:
                raise DeliveryRefused("Exact native group rights are unavailable")
            entity = entities[0]
            description_native = group_chats.describe(entity)
            if (
                description_native is None
                or description_native.peer != description.peer
            ):
                raise DeliveryRefused("Exact native group rights peer changed")
            owner._observe_group(
                entity, observed=owner.observation(), expected_revision=expected[0]
            )
            evidence.guard()
            owner._group_read(description.peer, expected=expected)
            if (
                not description_native.supported
                or description_native.access != "available"
                or getattr(entity, "min", False)
            ):
                raise DeliveryRefused("Native group sending is unavailable")
            rights(entity, description)
            rights_version = metadata_version(owner, description.peer)

            def checked_guard(_=None):
                guard()
                if metadata_version(owner, description.peer) != rights_version:
                    raise Refused("Native group send rights changed during preparation")

            reply_revision = None

            def sender_metadata(entity):
                supplied = group_chats.describe(entity)
                if supplied is not None and supplied.peer == description.peer:
                    owner._observe_group(
                        entity,
                        observed=owner.observation(),
                        expected_revision=expected[0],
                    )

            with native_request_guard(checked_guard):
                sender = await account_sender(
                    owner.client, peer, owner.account_id, observe=sender_metadata
                )
                checked_guard()
                if description.reply_to is not None:
                    key = f"{description.account_id}/{description.peer}"
                    original = owner.store.message(key, description.reply_to)
                    current = original and owner.store.groups.current(
                        original, admission=False
                    )
                    if not original or (
                        not current
                        and (
                            not review
                            or original.deleted
                            or original.date < owner.store.clock() - 30 * 86400
                        )
                    ):
                        raise Refused("Exact group reply source is unavailable")
                    checked = await owner.exact(description.peer, description.reply_to)
                    checked_guard()
                    if (
                        not checked
                        or not same_content(original, checked)
                        or any(
                            getattr(original, field) != getattr(checked, field)
                            for field in (
                                "author_peer",
                                "author_signature",
                                "date",
                                "service",
                            )
                        )
                    ):
                        raise Refused("Exact native group reply source changed")
                    if not current:
                        # Restoration invalidates the old intake epoch. A
                        # reviewed retained reply can refresh only this cached
                        # unchanged original, never fetch uncached history.
                        checked_guard()
                        with owner.store.transaction() as db:
                            row = db.execute(
                                "SELECT body FROM messages WHERE chat_key=? AND message_id=?",
                                (key, description.reply_to),
                            ).fetchone()
                            retained = (
                                Message.model_validate_json(row[0]) if row else None
                            )
                            state = owner.store.groups.get(key, db=db)
                            intake = owner.store.groups.intake(key, db=db)
                            if (
                                retained is None
                                or deletion_fingerprint(retained)
                                != deletion_fingerprint(original)
                                or expected != (state.access_revision, intake.epoch)
                                or not state.supported
                                or state.access != "available"
                                or state.migrated_to is not None
                                or db.execute(
                                    "SELECT 1 FROM cursors WHERE key=?",
                                    ("group-effects:" + original.key,),
                                ).fetchone()
                            ):
                                raise Refused(
                                    "Exact retained group reply proof changed"
                                )
                            owner.store.groups.record_source(db, retained, intake.epoch)
                        checked_guard()
                    reply_revision = original.revision
                checked_guard()
        proof = GroupSendProof(
            owner,
            description,
            evidence,
            expected,
            rights_version,
            owner.store.clock(),
            bytes(peer),
            bytes(sender) if sender is not None else None,
            reply_revision,
        )
        proof.guard()
        return SendTarget(peer, sender, proof)
    except ACCESS_ERRORS as error:
        lost(owner, description.peer, expected)
        evidence.guard()
        raise DeliveryRefused(type(error).__name__) from None


async def validate_review_media(description, guard):
    """Prove retained bytes locally without uploading, changing or sending them."""
    from .media import MediaValidationError, validate_send_image
    from .selected_media import validate_send_media

    for image in description.images:
        guard()
        try:
            facts = await asyncio.to_thread(validate_send_image, image)
        except MediaValidationError:
            raise Refused("Exact retained group image bytes are unavailable") from None
        guard()
        if any(
            getattr(facts, actual) != getattr(image, captured)
            for actual, captured in (
                ("mime_type", "mime"),
                ("size_bytes", "size"),
                ("width", "width"),
                ("height", "height"),
            )
        ):
            raise Refused("Captured retained group image facts changed")
    if description.media is not None:
        guard()
        await validate_send_media(description.media)
        guard()


class _ReviewEvidence:
    def __init__(self, guard):
        self.guard = guard


async def review(owner, description, guard):
    """Refresh exact personal rights and reply proof while keeping the item paused."""
    guard()
    if description.account_id != owner.account_id or not group_peer(description.peer):
        raise Refused("An exact connected group item is required for review")
    await validate_review_media(description, guard)
    guard()
    target = await prepare(owner, description, _ReviewEvidence(guard), review=True)
    proof = target.proof
    if proof is None:
        raise Refused("Fresh exact native group send rights are unavailable")
    if description.media is not None and description.media.requires_premium:
        with native_request_guard(lambda _=None: proof.guard()):
            if not await owner.catalog_entitlement():
                raise DeliveryRefused(
                    "Telegram Premium is required to send this sticker"
                )
    guard()
    proof.guard(fresh=True)
    return proof
