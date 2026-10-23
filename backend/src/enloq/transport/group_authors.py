"""Bounded exact cached-author metadata through the existing native owner."""

import re

from telethon import errors, functions, types, utils

from ..domain.models import Refused, Unavailable

WINDOW = 7 * 86400
RETENTION = 30 * 86400
MAX_AUTHORS = 32


def bot_flag(entity, author_peer):
    """Only a full, living native User bound to the exact message author."""
    if (
        not isinstance(entity, types.User)
        or author_peer != f"user:{entity.id}"
        or entity.min
        or entity.deleted
    ):
        return None
    # TL optional booleans are None when the flag is absent; a full User's
    # absent bot bit is native evidence of false, not an unknown entity.
    return bool(entity.bot)


async def refresh(transport, peer, authors):
    authors = tuple(authors)
    if (
        not 1 <= len(authors) <= MAX_AUTHORS
        or len(set(authors)) != len(authors)
        or any(
            not isinstance(a, str) or not re.fullmatch(r"user:[1-9][0-9]*", a)
            for a in authors
        )
    ):
        raise Refused("A bounded set of exact cached native authors is required")
    expected = transport._group_read(peer)
    store, key = transport.store, f"{transport.account_id}/{peer}"

    def known():
        transport._group_read(peer, expected=expected)
        for author in authors:
            if not store.rows(
                "SELECT 1 FROM messages WHERE chat_key=? AND date>=? AND date<=? "
                "AND (deleted=1 OR date>=?) "
                "AND json_extract(body,'$.author_peer')=? LIMIT 1",
                (
                    key,
                    store.clock() - RETENTION,
                    store.clock(),
                    store.clock() - WINDOW,
                    author,
                ),
            ):
                raise Refused("Native author is no longer in the cached group window")

    known()
    session = getattr(transport.client, "session", None)
    if session is None:
        return 0
    users = []
    for author in authors:
        identity = int(author.split(":")[1])
        try:
            # Session lookup is local. Never call client.get_input_entity: its
            # fallback may resolve a user or username through additional RPCs.
            cached = session.get_input_entity(types.PeerUser(identity))
            if (
                not isinstance(cached, types.InputPeerUser)
                or cached.user_id != identity
            ):
                continue
            users.append(utils.get_input_user(cached))
        except (ValueError, TypeError, KeyError):
            continue
    if not users:
        return 0
    captured = tuple((u.user_id, u.access_hash) for u in users)
    request = functions.users.GetUsersRequest(users)

    def guard(native):
        known()
        if (
            not isinstance(native, functions.users.GetUsersRequest)
            or tuple(
                (getattr(u, "user_id", None), getattr(u, "access_hash", None))
                for u in native.id
            )
            != captured
        ):
            raise Refused("Exact cached native author request changed")

    request._enloq_final_guard = guard
    guard(request)
    try:
        response = await transport.client(request)
    except (errors.RPCError, TimeoutError, OSError):
        # The caller rechecks ordinary group access after this optional read.
        # Missing profile evidence keeps authors unknown, never guessed.
        raise Unavailable("Native author metadata is temporarily unavailable") from None
    guard(request)
    identities = {identity for identity, _ in captured}
    if (
        not isinstance(response, (list, tuple))
        or len(response) != len(users)
        or any(
            not isinstance(u, (types.User, types.UserEmpty)) or u.id not in identities
            for u in response
        )
        or len({u.id for u in response}) != len(response)
    ):
        raise Unavailable("Native author metadata identity mismatch")
    changed = 0
    with store.transaction() as db:
        state, intake = store.groups.get(key, db=db), store.groups.intake(key, db=db)
        if (
            (state.access_revision, intake.epoch) != expected
            or state.access != "available"
            or not state.supported
            or not transport.connected
        ):
            raise Refused("Native group changed before bot metadata publication")
        for author in authors:
            if not db.execute(
                "SELECT 1 FROM messages WHERE chat_key=? AND date>=? AND date<=? AND (deleted=1 OR date>=?) AND json_extract(body,'$.author_peer')=? LIMIT 1",
                (
                    key,
                    store.clock() - RETENTION,
                    store.clock(),
                    store.clock() - WINDOW,
                    author,
                ),
            ).fetchone():
                raise Refused("Native author left the cached group window")
        for user in response:
            author = f"user:{user.id}"
            value = bot_flag(user, author)
            if value is not None:
                changed += store.group_filters.native_bot(key, author, value, db=db)
    return changed
