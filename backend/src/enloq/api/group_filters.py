"""Deliberate owner correction of one account/group's remembered bot ignore."""

from fastapi import APIRouter, Path

from ..domain.models import ChatKey, Model, Refused
from .scope import MEMBER_ACCOUNT


class RestoreBot(Model):
    pass


class RestoredBot(Model):
    author_peer: str
    ignored: bool


def group_filter_routes(manager):
    router = APIRouter(
        prefix="/api/accounts/{account_id}/chats/{peer}/bots",
        dependencies=MEMBER_ACCOUNT,
    )

    @router.post("/{author_peer}/restore", response_model=RestoredBot)
    def restore(
        account_id: int,
        peer: str,
        body: RestoreBot,
        author_peer: str = Path(pattern=r"^user:[1-9][0-9]{0,18}$"),
    ):
        try:
            chat = ChatKey(account_id=account_id, peer=peer).key
        except ValueError:
            raise Refused("A valid conversation is required") from None
        manager.get(account_id).group_filtering.restore_bot(chat, author_peer)
        return {"author_peer": author_peer, "ignored": False}

    return router
