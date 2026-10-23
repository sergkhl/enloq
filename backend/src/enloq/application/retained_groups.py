"""Cache-only access to exact inactive groups with durable owner work (ADR 34)."""

import json

from ..domain.models import Chat, Refused
from ..storage.store import WINDOW


class RetainedGroups:
    def __init__(self, app):
        self.app, self.store = app, app.store

    def _authorized(self):
        if not self.app.authorized():
            raise Refused("This account workspace is unavailable")

    def _chat(self, db, row):
        chat = Chat.model_validate_json(row["body"])
        if (
            chat.key != row["key"]
            or chat.account_id != self.store.account_id
            or chat.kind != "group"
            or not chat.peer.startswith(("chat:", "channel:"))
            or not self.store.groups.get(chat.key, db=db).supported
        ):
            raise Refused("Retained work requires an eligible exact native group")
        return chat

    def _entry(self, db, chat):
        draft_row = db.execute(
            "SELECT body FROM drafts WHERE chat_key=?", (chat.key,)
        ).fetchone()
        draft = json.loads(draft_row[0]) if draft_row else {}
        has_draft = bool(
            draft.get("text")
            or draft.get("attachments")
            or draft.get("reply_to") is not None
            or draft.get("undo")
        )
        selected_media = bool(draft.get("selected_media"))
        send_count, cancelled_count = db.execute(
            "SELECT coalesce(sum(state!='cancelled'),0),coalesce(sum(state='cancelled'),0) "
            "FROM operations WHERE chat_key=? AND state IN "
            "('queued','prepared','dispatching','unknown','refused','cancelled') "
            "AND json_extract(body,'$.submitted')=1 "
            "AND json_extract(body,'$.id')=id AND json_extract(body,'$.state')=state "
            "AND json_extract(body,'$.account_id')=? "
            "AND json_extract(body,'$.peer')=? AND json_extract(body,'$.chat_key')=?",
            (chat.key, chat.account_id, chat.peer, chat.key),
        ).fetchone()
        deletion_count = 0
        for operation_row in db.execute(
            "SELECT body FROM deletion_operations WHERE chat_key=?", (chat.key,)
        ):
            operation = json.loads(operation_row[0])
            if (
                operation["account_id"] != chat.account_id
                or operation["peer"] != chat.peer
                or operation["chat_key"] != chat.key
            ):
                raise Refused("Retained deletion evidence belongs to another group")
            acknowledged = {
                identity
                for chunk in operation["chunks"]
                if chunk["state"] == "acknowledged"
                for identity in chunk["identities"]
            }
            deletion_count += bool(
                operation["state"] in ("queued", "deleting")
                or any(
                    target["message_id"] not in acknowledged
                    and not target.get("resolved_by")
                    and not target.get("absent_at")
                    and target.get("observation") != "absent"
                    for target in operation["targets"]
                )
            )
        reasons = [
            reason
            for reason, present in (
                ("draft", has_draft),
                ("selected_media", selected_media),
                ("send", send_count),
                ("cancelled_send", cancelled_count),
                ("deletion", deletion_count),
            )
            if present
        ]
        return {
            "chat": {**chat.model_dump(), "key": chat.key},
            "reasons": reasons,
            "draft": has_draft,
            "selected_media": selected_media,
            "send_count": send_count,
            "cancelled_count": cancelled_count,
            "deletion_count": deletion_count,
        }

    def entries(self):
        """Safe navigation metadata only; opening never discovers native peers."""
        self._authorized()
        with self.store.transaction() as db:
            result = []
            for row in db.execute(
                "SELECT key,body,activity FROM chats WHERE activity<? "
                "AND json_extract(body,'$.kind')='group' ORDER BY activity DESC,key",
                (self.store.clock() - WINDOW,),
            ).fetchall():
                try:
                    chat = self._chat(db, row)
                except Refused:
                    continue
                entry = self._entry(db, chat)
                if entry["reasons"]:
                    result.append(entry)
            return result

    def _read(self, db, key):
        row = db.execute(
            "SELECT key,body,activity FROM chats WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            raise Refused("Retained group is unavailable for this account")
        return row, self._chat(db, row)

    def assert_open(self, key):
        """Admit only an inactive original peer with retained work, read-only."""
        self._authorized()
        with self.store.transaction() as db:
            row, chat = self._read(db, key)
            if (
                row["activity"] >= self.store.clock() - WINDOW
                or not self._entry(db, chat)["reasons"]
            ):
                raise Refused("This group has no inactive retained work")
            return chat

    def cache_read(self, key):
        """Recent or retained cache access survives loss/migration of this peer."""
        self._authorized()
        with self.store.transaction() as db:
            row, chat = self._read(db, key)
            if (
                row["activity"] < self.store.clock() - WINDOW
                and not self._entry(db, chat)["reasons"]
            ):
                raise Refused("This group is outside the recent and retained workspace")
            return chat
