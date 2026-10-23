import asyncio
import json
import logging

from ..domain.models import Chat, Message, Refused
from ..storage.store import METADATA, RETENTION, WINDOW
from .blocks import reclassify

DELETION_EFFECTS = "deletion-effects:"
GROUP_EFFECTS = "group-effects:"
_LOG = logging.getLogger("enloq.sync")


def coverage(store, key):
    """The lowest message ID from which every newer message of the chat is
    stored locally, or None when no contiguous read established one."""
    rows = store.rows("SELECT value FROM cursors WHERE key=?", ("coverage:" + key,))
    return int(rows[0]["value"]) if rows else None


def covered(store, key, floor):
    current = coverage(store, key)
    if current is None or floor < current:
        with store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                ("coverage:" + key, str(floor)),
            )


class Sync:
    def __init__(self, store, transport, *, changed=None):
        self.store, self.transport, self.changed = store, transport, changed
        self.arrived = None
        self.new_incoming = None

    def ingest(
        self,
        message: Message,
        *,
        cursor=None,
        target=True,
        arrival=True,
        live=False,
        intake=None,
        group_epoch=None,
    ):
        """``arrival`` marks live updates and gap catch-up, not history scans."""
        key = f"{message.account_id}/{message.peer}"
        group = self.store.chat(key).kind == "group"
        observed = message.reactions
        if observed is not None:
            message = message.model_copy(update={"reactions": None})
        unseen = live and not self.store.message(
            f"{message.account_id}/{message.peer}", message.message_id
        )
        changed = self.store.apply_message(
            message,
            cursor=cursor,
            intake=intake
            or ("arrival" if (target or group) and arrival else "history"),
            group_epoch=group_epoch,
        )
        if group:
            self._group_effects(message.key, arrival=arrival)
            return bool(changed and changed != METADATA)
        if observed is not None:
            # Metadata of the stored message: never a source edit (ADR 26).
            self.store.reactions.apply(message.key, observed)
        if changed == METADATA:
            reclassify(
                self.store,
                f"{message.account_id}/{message.peer}",
                message.message_id,
            )
            return False
        if changed:
            message = self.store.message(
                f"{message.account_id}/{message.peer}", message.message_id
            )
        if changed and self.changed:
            self.changed(message, target=target, arrival=arrival)
        if changed and target and arrival and self.arrived:
            self.arrived(message)
        if changed and unseen and target and self.new_incoming:
            self.new_incoming(message)
        return changed

    async def scan(self, key):
        """Bounded recent unread catch-up. Never model already-read initial history."""
        chat = self.store.chat(key)
        if chat.kind == "group":
            return await self._scan_group(chat)
        saved = chat.kind == "saved"
        if not saved and not any(c["key"] == key for c in self.store.recent_chats()):
            return
        # Saved Messages shows its newest messages whatever their age, within
        # the 30-day content rule, and none of them is a target (ADR 50).
        window = RETENTION if saved else WINDOW
        cursor = "recent:" + key
        rows = self.store.rows("SELECT value FROM cursors WHERE key=?", (cursor,))
        progress = (
            json.loads(rows[0]["value"]) if rows else {"before": 0, "complete": False}
        )
        if progress["complete"]:
            return progress
        async with asyncio.timeout(120):
            page = await self.transport.history(chat.peer, progress["before"], 100)
        recent = [m for m in page if m.date >= self.store.clock() - window]
        for message in sorted(recent, key=lambda m: m.message_id):
            self.ingest(
                message,
                target=not saved and message.message_id > chat.read_through,
                arrival=False,
                intake="initial",
            )
        if recent and (progress["before"] == 0 or coverage(self.store, key)):
            # Pages from the newest message down are stored without gaps.
            covered(self.store, key, min(m.message_id for m in recent))
        progress["count"] = progress.get("count", 0) + len(recent)
        progress.update(
            before=min((m.message_id for m in page), default=0),
            complete=len(page) < 100 or len(recent) < len(page),
        )
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                (cursor, json.dumps(progress)),
            )

        return progress

    async def _scan_group(self, chat):
        if not any(row["key"] == chat.key for row in self.store.sync_targets()):
            return
        state, intake = (
            self.store.groups.get(chat.key),
            self.store.groups.intake(chat.key),
        )
        if intake.complete:
            return intake.model_dump()
        cutoff = max(intake.cutoff or 0, self.store.clock() - WINDOW)
        async with asyncio.timeout(120):
            page = await self.transport.history(chat.peer, intake.before, 100)
        raw_count = getattr(page, "raw_count", len(page))
        raw_before = getattr(
            page, "before", min((message.message_id for message in page), default=0)
        )
        raw_oldest = getattr(
            page, "oldest", min((message.date for message in page), default=None)
        )
        current_state, current_intake = (
            self.store.groups.get(chat.key),
            self.store.groups.intake(chat.key),
        )
        if (
            current_state.access_revision != state.access_revision
            or current_intake.epoch != intake.epoch
            or not current_state.supported
            or current_state.access != "available"
        ):
            raise Refused("Group changed during recent read")
        if (
            raw_count > 100
            or raw_count < len(page)
            or (
                raw_count
                and (raw_before <= 0 or (intake.before and raw_before >= intake.before))
            )
            or len({message.message_id for message in page}) != len(page)
            or any(
                message.account_id != self.store.account_id
                or message.peer != chat.peer
                or (intake.before and message.message_id >= intake.before)
                for message in page
            )
        ):
            raise Refused("Recent group source identity or cursor mismatch")
        recent = [message for message in page if message.date >= cutoff]
        for message in sorted(recent, key=lambda message: message.message_id):
            self.ingest(
                message,
                target=False,
                arrival=False,
                intake="initial",
                group_epoch=intake.epoch,
            )
        before = raw_before
        oldest = min(
            [message.date for message in recent]
            + ([intake.oldest] if intake.oldest is not None else []),
            default=None,
        )
        return self.store.groups.scanned(
            chat.key,
            epoch=intake.epoch,
            before=before,
            top=intake.top
            or chat.latest_message_id
            or max((message.message_id for message in page), default=0),
            cutoff=cutoff,
            oldest=oldest,
            count=intake.count + len(recent),
            omitted=intake.omitted + raw_count - len(page),
            complete=raw_count < 100
            or (raw_oldest is not None and raw_oldest < cutoff),
        ).model_dump()

    async def reaction_catch_up(self, key):
        """One bounded read of messages with unread reactions after Telegram's
        count changed; reaction updates themselves are not replayed."""
        if self.store.chat(key).kind != "private":
            return
        cursor = "unread_reactions:" + key
        rows = self.store.rows("SELECT value FROM cursors WHERE key=?", (cursor,))
        if not rows or not hasattr(self.transport, "unread_reactions"):
            return
        value = json.loads(rows[0]["value"])
        if not value["count"] or value["count"] == value.get("read"):
            return
        chat = self.store.chat(key)
        async with asyncio.timeout(120):
            found = await self.transport.unread_reactions(chat.peer)
        for identity, snapshot in found:
            self.reactions(chat.peer, identity, snapshot)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT value FROM cursors WHERE key=?", (cursor,)
            ).fetchone()
            current = json.loads(row[0])
            current["read"] = value["count"]
            db.execute(
                "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                (cursor, json.dumps(current)),
            )

    def reactions(self, peer, identity, snapshot):
        """A reaction update for a cached message; others are not admitted."""
        return self.store.reactions.apply(
            f"{self.store.account_id}/{peer}/{identity}", snapshot, record_empty=True
        )

    def read_outbox(self, peer, through):
        """The recipient's read cursor for an existing private chat."""
        return self.store.read_outbox(f"{self.store.account_id}/{peer}", through)

    def deleted(self, peer, identities):
        # Resolve the whole native update before effects can remove a shared
        # retained citation. One failed hook must not lose later observed IDs.
        targets, failure = [], None
        for identity in identities:
            try:
                source_peer = (
                    peer if peer is not None else self._peerless_peer(identity)
                )
                if source_peer is not None:
                    targets.append((source_peer, identity))
            except Exception as error:
                if failure is None:
                    failure = error
        for source_peer, identity in targets:
            try:
                self._deleted_one(source_peer, identity)
            except Exception as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure

    def _deleted_one(self, peer, identity):
        rows = self.store.rows(
            "SELECT body FROM messages WHERE message_id=? AND chat_key=? LIMIT 2",
            (identity, f"{self.store.account_id}/{peer}"),
        )
        if not rows:
            if not self.store.rows(
                "SELECT 1 FROM chats WHERE key=?", (f"{self.store.account_id}/{peer}",)
            ):
                return
            # Exact native identity remains useful after content expiry:
            # remove cited generated memory and invalidate dependencies,
            # without recreating a message, body, media or history entry.
            message = self._deleted_identity(
                f"{self.store.account_id}/{peer}/{identity}"
            )
            marker = DELETION_EFFECTS + message.key
            with self.store.transaction() as db:
                db.execute(
                    "INSERT OR REPLACE INTO cursors VALUES (?,?)", (marker, "pending")
                )
            self._deleted_effects(message, marker)
            return
        for row in rows:
            message = Message.model_validate_json(row["body"])
            marker = DELETION_EFFECTS + message.key
            if message.deleted:
                if self.store.rows(
                    "SELECT 1 FROM cursors WHERE key=?", (marker,)
                ) or self._receipt_pending(message):
                    self._deleted_effects(message, marker)
                continue
            self.ingest(
                message.model_copy(
                    update={
                        "deleted": True,
                        "text": "",
                        "links": [],
                        "media": None,
                        "media_info": None,
                        "attachment_id": None,
                    }
                ),
                # This marker and the tombstone commit together. A hook
                # failure cannot make their saved state look completed.
                cursor=(marker, "pending"),
            )
            self._clear_deleted_effects(marker)

    def _peerless_peer(self, identity):
        """Resolve only a unique retained private identity, never source text.

        Native private deletions may omit the peer after the source's content
        has expired. Generated citations and reviewed/native membership remain
        exact local identity evidence. Owner-written citations do not establish
        that evidence; any collision, unavailable chat or other peer stays out.
        """
        if type(identity) is not int or not 0 < identity < 2**31:
            return None
        cached = self.store.rows(
            "SELECT json_extract(body,'$.service') AS service FROM messages WHERE message_id=? LIMIT 2",
            (identity,),
        )
        if len(cached) > 1 or (cached and cached[0]["service"]):
            return None
        suffix = f"/{identity}"
        candidates = self.store.rows(
            "SELECT DISTINCT chat_key FROM ("
            "SELECT chat_key FROM messages WHERE message_id=? "
            "UNION ALL SELECT e.chat_key FROM memory_entries e,json_each(e.citations) i "
            "WHERE e.source='model' AND i.type='integer' AND i.value=? "
            "UNION ALL SELECT e.chat_key FROM memory_entries e,json_each(e.outdated,'$.citations') i "
            "WHERE i.type='integer' AND i.value=? "
            "UNION ALL SELECT d.chat_key FROM deletion_operations d,json_each(d.body,'$.targets') t "
            "WHERE json_type(t.value,'$.message_id')='integer' AND json_extract(t.value,'$.message_id')=? "
            "UNION ALL SELECT b.chat_key FROM blocks b,json_each(b.sources) i "
            "WHERE i.type='integer' AND i.value=? "
            "UNION ALL SELECT substr(key,?,length(key)-?-?) AS chat_key FROM cursors "
            "WHERE value='pending' AND key LIKE ? AND substr(key,-?)=?"
            ") LIMIT 2",
            (
                identity,
                identity,
                identity,
                identity,
                identity,
                len(DELETION_EFFECTS) + 1,
                len(DELETION_EFFECTS),
                len(suffix),
                DELETION_EFFECTS + "%",
                len(suffix),
                suffix,
            ),
        )
        if len(candidates) != 1:
            return None
        key = candidates[0]["chat_key"]
        rows = self.store.rows("SELECT body FROM chats WHERE key=?", (key,))
        if not rows:
            return None
        chat = Chat.model_validate_json(rows[0]["body"])
        private = chat.kind == "private" and chat.peer.startswith("user:")
        # The account's own Saved Messages takes deletions made elsewhere,
        # such as from a phone, as a private chat does (ADR 50).
        saved = chat.kind == "saved" and chat.peer == f"user:{self.store.account_id}"
        basic = (
            chat.kind == "group"
            and chat.peer.startswith("chat:")
            and self.store.groups.get(key).access != "unknown"
        )
        if (
            chat.key != key
            or chat.account_id != self.store.account_id
            or not (private or saved or basic)
        ):
            return None
        return chat.peer

    def _receipt_pending(self, message):
        return bool(
            self.store.rows(
                "SELECT 1 FROM deletion_operations d,json_each(d.body,'$.chunks') c,json_each(c.value,'$.identities') i "
                "WHERE d.chat_key=? AND i.value=? AND json_extract(c.value,'$.state')='acknowledged' "
                "AND coalesce(json_extract(c.value,'$.local_applied'),0)=0 LIMIT 1",
                (f"{message.account_id}/{message.peer}", message.message_id),
            )
        )

    def _clear_deleted_effects(self, marker):
        with self.store.transaction() as db:
            db.execute("DELETE FROM cursors WHERE key=?", (marker,))

    def _deleted_identity(self, source):
        account_id, peer, identity = source.split("/")
        if int(account_id) != self.store.account_id:
            raise ValueError("Deletion effects belong to another account")
        self.store.chat(f"{account_id}/{peer}")
        # This content-free value is a hook argument only, never native source
        # content, an observed version or an application cache admission.
        return Message(
            account_id=int(account_id),
            peer=peer,
            message_id=int(identity),
            revision=0,
            date=0,
            deleted=True,
        )

    def _deleted_effects(self, message, marker):
        if self.changed:
            self.changed(message, target=True, arrival=False)
        self._clear_deleted_effects(marker)

    def recover_deletions(self):
        """Finish locally saved native deletion effects without native reads,
        mutation, history backfill or new provider calls. Successful repeats
        keep unchanged group identities/jobs and already-withdrawn results."""
        self.recover_group_changes()
        for row in self.store.rows(
            "SELECT key FROM cursors WHERE key LIKE ?", (DELETION_EFFECTS + "%",)
        ):
            try:
                source = row["key"][len(DELETION_EFFECTS) :]
                messages = self.store.rows(
                    "SELECT body FROM messages WHERE key=?", (source,)
                )
                if not messages:
                    # The pending marker proves a deletion was already observed;
                    # source expiry cannot erase its still-unfinished effects.
                    self._deleted_effects(self._deleted_identity(source), row["key"])
                    continue
                message = Message.model_validate_json(messages[0]["body"])
                if message.deleted:
                    self._deleted_effects(message, row["key"])
            except Exception:
                # One incomplete source must not stop other accounts' ordinary
                # startup/retention; retain its marker for a later local pass.
                _LOG.warning("deletion_effects_unavailable", exc_info=True)

    def _group_effects(self, source, *, arrival=False):
        marker = GROUP_EFFECTS + source
        rows = self.store.rows("SELECT value FROM cursors WHERE key=?", (marker,))
        if not rows:
            return
        messages = self.store.rows("SELECT body FROM messages WHERE key=?", (source,))
        if messages and self.changed:
            message = Message.model_validate_json(messages[0]["body"])
            self.changed(message, target=False, arrival=arrival)
        with self.store.transaction() as db:
            db.execute(
                "DELETE FROM cursors WHERE key=? AND value=?",
                (marker, rows[0]["value"]),
            )

    def recover_group_changes(self):
        """Finish saved group-source invalidation; never fetch or generate."""
        for row in self.store.rows(
            "SELECT key FROM cursors WHERE key LIKE ?", (GROUP_EFFECTS + "%",)
        ):
            try:
                self._group_effects(row["key"][len(GROUP_EFFECTS) :])
            except Exception:
                _LOG.warning("group_effects_incomplete", exc_info=True)
