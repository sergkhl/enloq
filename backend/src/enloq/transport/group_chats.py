"""Pure projections of supplied native group metadata; never performs a read."""

from dataclasses import dataclass

from telethon import types


@dataclass(frozen=True)
class GroupDescription:
    peer: str
    title: str
    supported: bool
    access: str
    reason: str
    migrated_to: str | None = None


class GroupHistoryPage(list):
    """Eligible originals plus raw page progress, including withheld posts."""

    def __init__(self, values, *, raw_count, before, oldest):
        super().__init__(values)
        self.raw_count, self.before, self.oldest = raw_count, before, oldest


def entity_map(values):
    result = {}
    for entity in values:
        kind = (
            "user"
            if isinstance(entity, (types.User, types.UserEmpty))
            else "chat"
            if isinstance(entity, (types.Chat, types.ChatForbidden, types.ChatEmpty))
            else "channel"
            if isinstance(entity, (types.Channel, types.ChannelForbidden))
            else None
        )
        if kind:
            result[f"{kind}:{entity.id}"] = entity
    return result


def describe(entity):
    if isinstance(entity, (types.Chat, types.ChatForbidden)):
        peer = f"chat:{entity.id}"
    elif isinstance(entity, (types.Channel, types.ChannelForbidden)):
        peer = f"channel:{entity.id}"
    else:
        return None
    title = (getattr(entity, "title", None) or peer)[:256]
    migrated = getattr(entity, "migrated_to", None)
    migrated_to = (
        f"channel:{migrated.channel_id}"
        if isinstance(migrated, types.InputChannel)
        else None
    )
    forbidden = isinstance(entity, (types.ChatForbidden, types.ChannelForbidden))
    left = bool(getattr(entity, "left", False) or getattr(entity, "deactivated", False))
    access = "lost" if forbidden or left or migrated_to else "available"
    reason = (
        "forbidden"
        if forbidden
        else "migrated"
        if migrated_to
        else "left"
        if left
        else "native_metadata"
    )
    supported = True
    if getattr(entity, "noforwards", False):
        supported, reason = False, "protected"
    elif isinstance(entity, (types.Channel, types.ChannelForbidden)):
        if getattr(entity, "broadcast", False):
            supported, reason = False, "broadcast"
        elif (
            getattr(entity, "forum", False)
            or getattr(entity, "monoforum", False)
            or getattr(entity, "forum_tabs", False)
        ):
            supported, reason = False, "forum"
        elif (
            not getattr(entity, "megagroup", False)
            and not getattr(entity, "min", False)
        ) or getattr(entity, "gigagroup", False):
            supported, reason = False, "unsupported_group"
    return GroupDescription(peer, title, supported, access, reason, migrated_to)


METADATA_UPDATES = (
    types.UpdateChat,
    types.UpdateChannel,
    types.UpdateChatParticipants,
    types.UpdateChatParticipant,
    types.UpdateChatParticipantAdd,
    types.UpdateChatParticipantDelete,
    types.UpdateChannelParticipant,
)


def update_peer(update):
    if isinstance(update, types.UpdateChatParticipants):
        identity = update.participants.chat_id
        return f"chat:{identity}"
    for attribute, kind in (("chat_id", "chat"), ("channel_id", "channel")):
        if getattr(update, attribute, None):
            return f"{kind}:{getattr(update, attribute)}"
    return None


def member_lost(update, account_id):
    if isinstance(update, types.UpdateChatParticipantDelete):
        return update.user_id == account_id
    if isinstance(update, types.UpdateChatParticipant):
        return (
            update.user_id == account_id
            and update.prev_participant is not None
            and update.new_participant is None
        )
    if isinstance(update, types.UpdateChatParticipants) and isinstance(
        update.participants, types.ChatParticipants
    ):
        return not any(
            participant.user_id == account_id
            for participant in update.participants.participants
        )
    if (
        isinstance(update, types.UpdateChannelParticipant)
        and update.user_id == account_id
    ):
        participant = update.new_participant
        return isinstance(participant, types.ChannelParticipantLeft) or (
            isinstance(participant, types.ChannelParticipantBanned)
            and bool(participant.left or participant.banned_rights.view_messages)
        )
    return False


def migration(message):
    action = getattr(message, "action", None)
    if isinstance(action, types.MessageActionChatMigrateTo) and isinstance(
        message.peer_id, types.PeerChat
    ):
        return f"chat:{message.peer_id.chat_id}", f"channel:{action.channel_id}"
    if isinstance(action, types.MessageActionChannelMigrateFrom) and isinstance(
        message.peer_id, types.PeerChannel
    ):
        return f"chat:{action.chat_id}", f"channel:{message.peer_id.channel_id}"
    return None
