"""Exact native group access for deliberate actions, independent of AI."""

from ..domain.models import Refused


def native_access(store, key, revision=None):
    """Capture or recheck access without enabling processing or changing peers."""
    chat = store.chat(key)
    if chat.account_id != store.account_id:
        raise Refused("Conversation belongs to another account")
    # Saved Messages acts like a private chat with the account itself (ADR 50).
    saved = chat.kind == "saved" and chat.peer == f"user:{store.account_id}"
    if (chat.kind == "private" or saved) and chat.peer.startswith("user:"):
        if revision is not None:
            raise Refused("Conversation access changed")
        return None
    if chat.kind != "group" or not chat.peer.startswith(("chat:", "channel:")):
        raise Refused("Only existing private conversations and groups are supported")
    state = store.groups.get(key)
    if (
        not state.supported
        or state.access != "available"
        or state.migrated_to is not None
        or (revision is not None and state.access_revision != revision)
    ):
        raise Refused("Native group access changed or is unavailable")
    return state.access_revision
