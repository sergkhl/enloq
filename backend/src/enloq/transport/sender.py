"""Select the connected account without applying channel-only flags to DMs."""

from telethon import utils
from telethon.tl import functions, types

from ..domain.models import Refused


async def account_sender(client, peer, account_id, *, observe=None):
    if not isinstance(peer, types.InputPeerChannel):
        return None
    request = functions.channels.GetSendAsRequest(peer)
    captured = bytes(request)

    def exact_sender_query(native):
        if type(native) is not type(request) or bytes(native) != captured:
            raise Refused("Connected group sender query changed")

    request._enloq_final_guard = exact_sender_query
    choices = await client(request)
    if observe:
        for entity in getattr(choices, "chats", ()):
            observe(entity)
    if not any(
        isinstance(choice.peer, types.PeerUser) and choice.peer.user_id == account_id
        for choice in getattr(choices, "peers", ())
    ):
        raise Refused("The connected account cannot send as itself in this group")
    me = await client.get_me()
    if not isinstance(me, types.User) or me.id != account_id or me.bot:
        raise Refused("Connected sender changed")
    return utils.get_input_peer(me, allow_self=False)
