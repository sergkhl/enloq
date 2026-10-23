"""Account-local Send Operation state, durable evidence and retention policy.

The current SQLite/JSON formats stay private to this owner. Recovery and expiry
participate in their caller's transaction and never need a Telegram connection.

ADR 26: a submission is immutable once accepted. Its exact text, images and
reply target are saved and the composer is cleared in one transaction; each
chat's submissions then leave in order, one native send at a time. A failed or
uncertain one pauses its chat until a deliberate Retry, Cancel or Check
delivery resolves it.
"""

from __future__ import annotations

import json
import secrets
import uuid
from copy import deepcopy
from pathlib import Path

from ..domain.delivery import (
    Confirmation,
    GroupSendBlocked,
    ReceiptBatch,
    ReceiptCheck,
    ReplySendBlocked,
    SendDescription,
    SendImage,
)
from ..domain.models import (
    Chat,
    Conflict,
    DeliveryRefused,
    MediaInfo,
    Message,
    Refused,
    Unavailable,
)

KEPT = " Nothing was sent; your draft is kept."
NOTHING = " Nothing was sent."
# A queued send waits out ordinary pacing; anything longer needs a Retry.
PACING_LIMIT = 10
_UNCAPTURED = object()
# Submissions waiting before their chat's head; refused ones pause it until a
# deliberate Retry or Cancel. Older pre-queue refusals kept their drafts.
ACTIVE = frozenset({"queued", "prepared", "dispatching", "unknown"})

# Hand-off ledger outcomes that prove a send's final request was not executed:
# the request policy saw it but never handed it to the sender, the sender
# refused it before writing, or Telegram answered only with a flood wait.
NOT_EXECUTED = frozenset({"watched", "not_handed", "flood_refused"})


def not_sent(operation):
    """Whether a Send Operation provably never reached Telegram. Operations
    recorded before the ledger fall back to the image-only wire flag, so a
    text send from then stays unknown."""
    ledger = operation.get("wire")
    if isinstance(ledger, list):
        return all(entry.get("outcome") in NOT_EXECUTED for entry in ledger)
    return operation.get("wire_started") is False


def resumable(operation):
    """Only retained group work with a definitive non-delivery verdict."""
    return bool(
        operation.get("submitted")
        and operation.get("state") == "refused"
        and operation.get("cause") == "group_access_loss"
        and operation.get("peer", "").startswith(("chat:", "channel:"))
        and (not_sent(operation) or operation.get("native_refused") is True)
        and not operation.get("message_id")
        and not any((operation.get("receipts") or {}).values())
    )


def blocking(operation):
    """Whether this operation holds back later submissions in its chat."""
    return operation["state"] in ACTIVE or (
        operation["state"] == "refused" and bool(operation.get("submitted"))
    )


def kept(operation):
    return NOTHING if operation.get("submitted") else KEPT


class _AttemptEvidence:
    """A live attempt's independent in-memory and durable hand-off evidence."""

    def __init__(self, owner, operation):
        self._owner, self._identity = owner, operation["id"]
        self._ledger = deepcopy(operation.get("wire") or [])

    def _change(self, change, *, handoff=False):
        self._owner._wire(self._identity, change, guard_reply=handoff)
        change(self._ledger)

    def _mark(self, outcome, *, replace=None, handoff=False, **fields):
        entry = {"outcome": outcome, "at": self._owner.store.clock(), **fields}

        def change(ledger):
            if replace and ledger and ledger[-1]["outcome"] == replace:
                ledger[-1] = entry
            else:
                ledger.append(entry)

        self._change(change, handoff=handoff)

    def guard(self):
        self._owner.guard(self._identity)

    def calling(self):
        self._mark("calling")

    def watching(self):
        self._mark("watched", replace="calling")

    def holding(self, until):
        def change(ledger):
            if ledger:
                ledger[-1] = {**ledger[-1], "until": until}

        self._change(change)

    def handing_off(self):
        self._mark("exposed", handoff=True)

    def not_handed(self):
        self._mark("not_handed", replace="exposed")

    def flood_refused(self, error, until):
        self._mark("flood_refused", replace="exposed", error=error, until=until)

    def refused(self, reason):
        self._owner.record_refusal(self._identity, reason)


class SendOperations:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _group_operation(operation):
        return "group_access_revision" in operation or operation.get(
            "peer", ""
        ).startswith(("chat:", "channel:"))

    def _target(self, db, key):
        row = db.execute("SELECT body FROM chats WHERE key=?", (key,)).fetchone()
        if not row:
            raise Refused("Native send target is unavailable for this account")
        chat = Chat.model_validate_json(row[0])
        if chat.account_id != self.store.account_id or chat.key != key:
            raise Refused("Connected account or conversation does not match")
        # Saved Messages sends to the account itself (ADR 50 Q17).
        saved = chat.kind == "saved" and chat.peer == f"user:{self.store.account_id}"
        if (chat.kind == "private" or saved) and chat.peer.startswith("user:"):
            return chat, None
        if chat.kind != "group" or not chat.peer.startswith(("chat:", "channel:")):
            raise Refused("This native conversation does not support sending")
        state = self.store.groups.get(key, db=db)
        if not state.supported:
            raise GroupSendBlocked("unsupported")
        if state.access != "available" or state.migrated_to:
            raise GroupSendBlocked("lost" if state.migrated_to else state.access)
        return chat, state.access_revision

    def _guard_target(self, db, operation):
        chat, revision = self._target(db, operation["chat_key"])
        if (
            operation["account_id"] != self.store.account_id
            or operation["account_id"] != chat.account_id
            or operation["peer"] != chat.peer
        ):
            raise Refused("Accepted send account or exact native peer changed")
        if self._group_operation(operation):
            if chat.kind != "group":
                raise GroupSendBlocked("target")
            if operation.get("group_access_revision") != revision:
                raise GroupSendBlocked("changed")
        elif revision is not None:
            raise GroupSendBlocked("target")

    @staticmethod
    def _block_target(operation, error):
        operation.update(
            state="refused",
            cause="group_access_loss"
            if isinstance(error, GroupSendBlocked)
            else "target_changed",
            reason=str(error) + kept(operation),
        )
        operation.pop("retry_after", None)

    def _queue_target(self, db, operation):
        try:
            self._guard_target(db, operation)
        except Refused as error:
            self._block_target(operation, error)
            db.execute(
                "UPDATE operations SET state=?,body=? WHERE id=?",
                (operation["state"], json.dumps(operation), operation["id"]),
            )
            return False
        return True

    def revalidate_group(self, key):
        """Hold every proven-unhanded accepted item after native access loss.

        A later restoration keeps the hold because the acceptance revision is
        immutable. A definitive native refusal also proves non-delivery even
        after handoff; exposed attempts with an unknown result stay unknown.
        """
        with self.store.transaction() as db:
            for row in db.execute(
                "SELECT body FROM operations WHERE chat_key=? AND state IN "
                "('queued','prepared','refused')",
                (key,),
            ).fetchall():
                operation = json.loads(row[0])
                if (
                    operation.get("submitted")
                    and self._group_operation(operation)
                    and (
                        not_sent(operation)
                        or (
                            operation["state"] == "refused"
                            and operation.get("native_refused") is True
                        )
                    )
                ):
                    self._queue_target(db, operation)

    def _reply_status(self, db, operation):
        target = operation.get("reply_to")
        if not target:
            return None
        held = self.store.deletion_operations.reply_hold(
            operation["chat_key"], target, db=db
        )
        source = db.execute(
            "SELECT deleted FROM messages WHERE chat_key=? AND message_id=?",
            (operation["chat_key"], target),
        ).fetchone()
        if held == "deleted" or (source and source["deleted"]):
            return "deleted"
        if held in ("pending", "unresolved"):
            return held
        if not source and not operation.get("reply_outside_cache"):
            return "unavailable"
        return None

    @staticmethod
    def _block_reply(operation, status):
        error = ReplySendBlocked(status)
        if status in ("pending", "unresolved"):
            operation.update(state="queued", cause="reply_deletion", reason=str(error))
        else:
            operation.update(
                state="refused",
                cause="reply_deleted" if status == "deleted" else "reply_unavailable",
                reason=str(error) + kept(operation),
            )
        operation.pop("retry_after", None)

    def _queue_reply(self, db, operation):
        """Persist an honest pause/refusal without changing authored content."""
        status = self._reply_status(db, operation)
        if status:
            self._block_reply(operation, status)
        elif operation.get("cause") == "reply_deletion":
            operation.pop("cause", None)
            operation.pop("reason", None)
        db.execute(
            "UPDATE operations SET state=?,body=? WHERE id=?",
            (operation["state"], json.dumps(operation), operation["id"]),
        )
        return status

    def guard(self, identity):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,body FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if not row or row["state"] != "dispatching":
                raise Refused("Send attempt is no longer current")
            operation = json.loads(row["body"])
            if operation["account_id"] != self.store.account_id:
                raise Refused("Send attempt belongs to another account")
            self._guard_target(db, operation)
            status = self._reply_status(db, operation)
            if status:
                raise ReplySendBlocked(status)

    def prepare(
        self,
        key,
        revision,
        *,
        snapshot,
        exact_reply=None,
        media_snapshot=None,
        expected_group_revision=_UNCAPTURED,
    ):
        """Accept one immutable submission and clear the submitted composer in
        the same transaction. The same composer revision returns its existing
        submission, so a repeated request never queues a second message."""
        with self.store.transaction() as db:
            chat, group_revision = self._target(db, key)
            if (
                expected_group_revision is not _UNCAPTURED
                and group_revision != expected_group_revision
            ):
                raise GroupSendBlocked("changed")
            for row in db.execute(
                "SELECT body FROM operations WHERE chat_key=? ORDER BY rowid DESC",
                (key,),
            ):
                operation = json.loads(row[0])
                if operation.get("submitted") and (
                    operation["composer_revision"] == revision
                ):
                    return operation
            row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (key,)
            ).fetchone()
            if not row:
                raise Refused("Write a message first")
            draft = json.loads(row[0])
            if draft["revision"] != revision:
                raise Conflict("Draft changed; review the current text before sending")
            if draft.get("state") == "rewriting":
                raise Refused("Stop or undo this rewrite before sending")
            images = snapshot(key, draft["attachments"], db=db)
            selected = media_snapshot(key, draft, db=db) if media_snapshot else None
            if draft.get("selected_media") and selected is None:
                raise Refused("Selected media must be prepared before sending")
            if selected and images:
                raise Refused("A GIF or sticker cannot be mixed with images")
            sticker = selected and selected["kind"] == "sticker"
            text = "" if sticker else draft["text"]
            try:
                length = len(text.encode("utf-16-le")) // 2
            except UnicodeEncodeError:
                raise Refused("Invalid Unicode; edit the draft") from None
            limit = 1024 if images or selected else 4096
            if (
                (not text.strip() and not images and not selected)
                or length > limit
                or len(images) > 10
            ):
                raise Refused(
                    f"Send allows up to {limit:,} UTF-16 caption/text units and ten images; edit the draft"
                )
            reply_outside_cache = False
            if draft["reply_to"]:
                hold = self.store.deletion_operations.reply_hold(
                    key, draft["reply_to"], db=db
                )
                if hold:
                    raise ReplySendBlocked(hold)
                source = db.execute(
                    "SELECT deleted FROM messages WHERE chat_key=? AND message_id=?",
                    (key, draft["reply_to"]),
                ).fetchone()
                if source and source["deleted"]:
                    raise ReplySendBlocked("deleted")
                if not source:
                    if (
                        not exact_reply
                        or exact_reply.deleted
                        or exact_reply.account_id != chat.account_id
                        or exact_reply.peer != chat.peer
                        or exact_reply.message_id != draft["reply_to"]
                    ):
                        raise Refused("Native reply target is unavailable")
                    reply_outside_cache = True
            operation = dict(
                id=uuid.uuid4().hex,
                account_id=chat.account_id,
                peer=chat.peer,
                chat_key=key,
                text=text,
                attachments=images,
                reply_to=draft["reply_to"],
                composer_revision=revision,
                random_id=secrets.randbits(63) or 1,
                state="queued",
                submitted=True,
                created=self.store.clock(),
                message_id=None,
                reply_outside_cache=reply_outside_cache,
                wire=[],
                attempts=[],
            )
            if group_revision is not None:
                operation["group_access_revision"] = group_revision
            if selected:
                operation["media"] = selected
            if images:
                operation.update(
                    random_ids=[operation["random_id"]]
                    + [secrets.randbits(63) or 1 for _ in images[1:]],
                    receipts={},
                )
            db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)",
                (operation["id"], key, "queued", json.dumps(operation)),
            )
            if selected:
                self.store.selected_media.transfer(
                    key, selected["id"], operation["id"], db=db
                )
            if sticker:
                draft.update(selected_media=None, revision=revision + 1)
                if not draft["text"]:
                    draft["reply_to"] = None
            else:
                draft.update(
                    text="",
                    attachments=[],
                    selected_media=None,
                    reply_to=None,
                    undo=None,
                    state="idle",
                    attempt=None,
                    error=None,
                    revision=revision + 1,
                )
            db.execute(
                "UPDATE drafts SET body=? WHERE chat_key=?", (json.dumps(draft), key)
            )
        return operation

    def runnable(self, key):
        """This chat's next submission to dispatch: the oldest blocking one when
        it is still queued. A dispatching, failed or uncertain head pauses the
        chat; other chats are independent."""
        with self.store.transaction() as db:
            for row in db.execute(
                "SELECT body FROM operations WHERE chat_key=? AND state IN "
                "('queued','prepared','dispatching','unknown','refused') ORDER BY rowid",
                (key,),
            ):
                operation = json.loads(row[0])
                if blocking(operation):
                    if operation["state"] != "queued":
                        return None
                    if not self._queue_target(db, operation):
                        return None
                    return operation if not self._queue_reply(db, operation) else None
        return None

    def waiting(self):
        """Chats with queued submissions."""
        return [
            r["chat_key"]
            for r in self.store.rows(
                "SELECT DISTINCT chat_key FROM operations WHERE state='queued'"
            )
        ]

    def retry(self, identity):
        """Queue a submission that was not sent again, as a new attempt with
        the same native random IDs: if Telegram did execute an earlier one, it
        answers RANDOM_ID_DUPLICATE instead of delivering a second message."""
        current = self.get(identity)
        if self._group_operation(current):
            self.revalidate_group(current["chat_key"])
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,body FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                raise Refused("Send operation unavailable for this account")
            operation = json.loads(row["body"])
            if row["state"] != "refused" or not operation.get("submitted"):
                raise Refused("Only a message that was not sent can be retried")
            if operation.get("cause") == "group_access_loss":
                raise Refused(
                    "Review this exact group item and deliberately Resume after native access returns"
                )
            self._guard_target(db, operation)
            if operation.get("cause") == "sticker_unavailable":
                raise Refused(
                    "The original sticker is unavailable; cancel this item and select a sticker again"
                )
            if (operation.get("retry_after") or 0) > self.store.clock():
                raise Refused("Telegram asked this account to wait before sending")
            status = self._reply_status(db, operation)
            if status:
                raise ReplySendBlocked(status)
            operation["attempts"] = [
                *operation.get("attempts", []),
                {
                    k: operation.get(k)
                    for k in (
                        "wire",
                        "reason",
                        "cause",
                        "retry_after",
                        "native_refused",
                    )
                },
            ][-10:]
            for field in ("reason", "cause", "retry_after", "native_refused"):
                operation.pop(field, None)
            operation.update(state="queued", wire=[])
            db.execute(
                "UPDATE operations SET state='queued',body=? WHERE id=?",
                (json.dumps(operation), identity),
            )
            # That refusal answered the earlier attempt only.
            db.execute("DELETE FROM cursors WHERE key=?", ("refusal:op:" + identity,))
        return operation

    def _resume_snapshot(self, db, identity):
        """Read a paused exact item without admitting new inactive-group sends."""
        from ..domain.deletion import deletion_fingerprint
        from .store import RETENTION

        row = db.execute(
            "SELECT state,body FROM operations WHERE id=?", (identity,)
        ).fetchone()
        if not row:
            raise Refused("Send operation unavailable for this account")
        operation = json.loads(row["body"])
        if row["state"] != operation.get("state") or not resumable(operation):
            raise Refused(
                "Only a proven unsent group item paused after access loss can resume"
            )
        chat, revision = self._target(db, operation["chat_key"])
        if (
            chat.kind != "group"
            or operation["account_id"] != self.store.account_id
            or operation["account_id"] != chat.account_id
            or operation["peer"] != chat.peer
            or revision is None
        ):
            raise Refused("Accepted send account or exact native peer changed")
        if (operation.get("retry_after") or 0) > self.store.clock():
            raise Refused("Telegram asked this account to wait before sending")
        identities = (
            operation.get("random_ids")
            if operation.get("attachments")
            else [operation.get("random_id")]
        )
        if (
            not isinstance(identities, list)
            or len(identities) != (len(operation.get("attachments") or []) or 1)
            or any(type(random) is not int or random <= 0 for random in identities)
            or len(set(identities)) != len(identities)
        ):
            raise Refused("Exact retained native send IDs are unavailable")
        if any(
            db.execute(
                "SELECT 1 FROM cursors WHERE key=?", (f"receipt:{random}",)
            ).fetchone()
            for random in identities
        ):
            raise Refused(
                "Native delivery evidence changed; check this item before resuming"
            )
        status = self._reply_status(db, operation)
        if status:
            raise ReplySendBlocked(status)
        reply_fingerprint = None
        if operation.get("reply_to"):
            source_row = db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND message_id=?",
                (operation["chat_key"], operation["reply_to"]),
            ).fetchone()
            source = Message.model_validate_json(source_row[0]) if source_row else None
            if (
                not source
                or source.deleted
                or source.account_id != chat.account_id
                or source.peer != chat.peer
                or source.date < self.store.clock() - RETENTION
            ):
                raise Refused("Exact retained group reply source is unavailable")
            reply_fingerprint = deletion_fingerprint(source)
        return operation, revision, chat.title, reply_fingerprint

    def resume_snapshot(self, identity):
        with self.store.transaction() as db:
            return self._resume_snapshot(db, identity)

    def _resume_guard(self, db, identity, snapshot, revision, title, reply_fingerprint):
        row = db.execute(
            "SELECT body FROM operations WHERE id=?", (identity,)
        ).fetchone()
        if not row or json.loads(row[0]) != snapshot:
            raise Conflict("Saved group item changed; review it again")
        current = self._resume_snapshot(db, identity)
        if current != (snapshot, revision, title, reply_fingerprint):
            raise Conflict("Saved group item or native access changed; review it again")

    def resume_guard(self, identity, snapshot, revision, title, reply_fingerprint):
        with self.store.transaction() as db:
            self._resume_guard(
                db, identity, snapshot, revision, title, reply_fingerprint
            )

    def resume(self, identity, snapshot, revision, title, reply_fingerprint, *, guard):
        """Atomically queue the reviewed payload using the original native IDs.

        Review and native reads cannot queue anything. Only this guarded commit
        accepts a fresh attempt, archiving every earlier non-delivery proof and
        leaving the current composer intact.
        """
        # Native/browser guards may read their own stores. Run synchronously
        # before this transaction; its exact local checks then admit the item
        # without another await or a nested SQLite write transaction.
        guard()
        with self.store.transaction() as db:
            self._resume_guard(
                db, identity, snapshot, revision, title, reply_fingerprint
            )
            operation = deepcopy(snapshot)
            operation["attempts"] = [
                *operation.get("attempts", []),
                {
                    field: operation.get(field)
                    for field in (
                        "wire",
                        "reason",
                        "cause",
                        "retry_after",
                        "native_refused",
                        "group_access_revision",
                    )
                },
            ][-10:]
            for field in ("reason", "cause", "retry_after", "native_refused", "check"):
                operation.pop(field, None)
            operation.update(state="queued", wire=[], group_access_revision=revision)
            db.execute(
                "UPDATE operations SET state='queued',body=? WHERE id=?",
                (json.dumps(operation), identity),
            )
            db.execute("DELETE FROM cursors WHERE key=?", ("refusal:op:" + identity,))
        return operation

    def cancel(self, identity):
        """Withdraw a submission that provably never reached Telegram. Its
        content stays as a cancelled item and later ones continue in order."""
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,body FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                raise Refused("Send operation unavailable for this account")
            operation = json.loads(row["body"])
            if row["state"] not in ("queued", "refused") or not operation.get(
                "submitted"
            ):
                raise Refused(
                    "Only a message that has not reached Telegram can be cancelled"
                )
            operation.update(state="cancelled", reason="Cancelled." + NOTHING)
            operation.pop("retry_after", None)
            db.execute(
                "UPDATE operations SET state='cancelled',body=? WHERE id=?",
                (json.dumps(operation), identity),
            )
        return operation

    def pacing_delay(self, identity):
        operation = self.get(identity)
        with self.store.transaction() as db:
            scopes = ("send_due:global", "send_due:" + operation["chat_key"])
            due = self.store.clock()
            for scope in scopes:
                row = db.execute(
                    "SELECT value FROM cursors WHERE key=?", (scope,)
                ).fetchone()
                if row:
                    due = max(due, float(row[0]))
            wait = due - self.store.clock()
            if wait > PACING_LIMIT:
                return wait
            for scope, gap in zip(scopes, (0.05, 1.0)):
                db.execute(
                    "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                    (scope, str(due + gap)),
                )
        return wait

    def not_dispatched(self, identity, reason):
        """A queued submission that will not be claimed now: nothing was sent."""
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,body FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if not row or row["state"] != "queued":
                return
            operation = json.loads(row["body"])
            operation.update(state="refused", cause="not_sent", reason=reason + NOTHING)
            db.execute(
                "UPDATE operations SET state='refused',body=? WHERE id=?",
                (json.dumps(operation), identity),
            )

    def refuse(self, identity, reason, *, cause=None):
        """Save a definitive native non-send answer, including its proof.

        Unlike an unhanded ledger, this answer can prove non-delivery after
        exposure. A later native-access observation must preserve that verdict.
        """
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT state,body FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if not current:
                raise Refused("Send operation unavailable for this account")
            if current["state"] != "confirmed":
                operation = json.loads(current["body"])
                operation.update(state="refused", reason=reason, native_refused=True)
                if cause:
                    operation["cause"] = cause
                if self._group_operation(operation):
                    try:
                        self._guard_target(db, operation)
                    except Refused as error:
                        self._block_target(operation, error)
                db.execute(
                    "UPDATE operations SET state=?,body=? WHERE id=?",
                    ("refused", json.dumps(operation), operation["id"]),
                )

    def claim(self, identity):
        """Move a queued submission to dispatching. It is bound to its own
        content, not to the composer; a deleted reply target refuses it."""
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,body FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if not row or row[0] != "queued":
                return False
            operation = json.loads(row["body"])
            if self._queue_target(db, operation) and not self._queue_reply(
                db, operation
            ):
                operation["state"] = "dispatching"
                operation.setdefault("wire", [])
            db.execute(
                "UPDATE operations SET state=?,body=? WHERE id=?",
                (operation["state"], json.dumps(operation), operation["id"]),
            )
        return operation["state"] == "dispatching"

    def settle(self, evidence, *, held=0, error=None, cancelled=False):
        """A send that failed without a definitive Telegram answer is refused
        only when both the stored and the in-memory hand-off ledgers prove its
        final request never reached Telegram; anything else is unknown."""
        if evidence._owner is not self:
            raise Refused("Send evidence belongs to another account store")
        identity, memory_wire = evidence._identity, evidence._ledger
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,body FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if not row or row["state"] != "dispatching":
                return
            operation = json.loads(row["body"])
            target_error = None
            if self._group_operation(operation):
                try:
                    self._guard_target(db, operation)
                except Refused as refusal:
                    # The verdict can hold only a proven-unhanded attempt.
                    # Its ledgers still win over this local access observation.
                    target_error = refusal
            if not (not_sent(operation) and not_sent({"wire": memory_wire})):
                operation["state"] = "unknown"
            elif target_error is not None or isinstance(error, GroupSendBlocked):
                self._block_target(operation, target_error or error)
            elif isinstance(error, ReplySendBlocked):
                self._block_reply(operation, error.status)
            else:
                ledger = memory_wire or []
                disconnected = isinstance(error, (Unavailable, ConnectionError)) or any(
                    e["outcome"] == "not_handed" for e in ledger
                )
                # While disconnected, the account is also held by the app's own
                # reconnect backoff; only holds this send met count then.
                until = max(
                    [0 if disconnected else held]
                    + [e.get("until") or 0 for e in ledger]
                )
                waiting = until > self.store.clock() or any(
                    e["outcome"] == "flood_refused" for e in ledger
                )
                if cancelled:
                    reason = "Interrupted before reaching Telegram."
                elif waiting:
                    reason = "Telegram asked this account to wait."
                elif disconnected:
                    reason = "Telegram disconnected before sending."
                else:
                    reason = "Stopped before reaching Telegram."
                operation.update(
                    state="refused", cause="not_sent", reason=reason + kept(operation)
                )
                if until > self.store.clock():
                    operation["retry_after"] = until
            db.execute(
                "UPDATE operations SET state=?,body=? WHERE id=?",
                (operation["state"], json.dumps(operation), operation["id"]),
            )

    def confirm(self, identity, batch: ReceiptBatch):
        operation = self.get(identity)
        if not isinstance(batch, ReceiptBatch):
            raise Refused("Invalid native receipts")
        identities = self._random_ids(operation)
        if not {i for i, _ in batch.pairs} <= set(identities):
            raise Refused("Invalid native receipts")
        native_id = (
            dict(batch.pairs)
            if operation.get("attachments")
            else dict(batch.pairs).get(identities[0])
        )
        media = bool(operation.get("attachments"))
        if media:
            if (
                not isinstance(native_id, dict)
                or not set(map(int, native_id)) <= set(operation["random_ids"])
                or any(type(i) is not int or i <= 0 for i in native_id.values())
            ):
                raise Refused("Invalid native media receipts")
            receipts = {
                **operation.get("receipts", {}),
                **{str(k): v for k, v in native_id.items()},
            }
            operation["receipts"] = receipts
            complete = len(receipts) == len(operation["random_ids"])
        else:
            if type(native_id) is not int or native_id <= 0:
                raise Refused("Invalid native receipt")
            complete = True
        with self.store.transaction() as db:
            existing = db.execute(
                "SELECT state FROM operations WHERE id=?", (operation["id"],)
            ).fetchone()
            if existing and existing[0] == "confirmed":
                return
            operation.update(
                state="confirmed" if complete else "unknown",
                message_id=None if media else native_id,
            )
            db.execute(
                "UPDATE operations SET state=?,body=? WHERE id=?",
                (operation["state"], json.dumps(operation), operation["id"]),
            )
            row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (operation["chat_key"],)
            ).fetchone()
            if complete and row and not operation.get("submitted"):
                # Before the queue, the composer cleared only on confirmation.
                draft = json.loads(row[0])
                if draft["revision"] == operation["composer_revision"]:
                    draft.update(
                        text="",
                        attachments=[],
                        reply_to=None,
                        undo=None,
                        state="idle",
                        revision=draft["revision"] + 1,
                    )
                    db.execute(
                        "UPDATE drafts SET body=? WHERE chat_key=?",
                        (json.dumps(draft), operation["chat_key"]),
                    )
        ids = (
            [receipts.get(str(i)) for i in operation["random_ids"]]
            if media
            else [native_id]
        )
        messages = []
        for index, native in enumerate(ids):
            if native:
                messages.append(
                    Message(
                        account_id=operation["account_id"],
                        peer=operation["peer"],
                        message_id=native,
                        revision=0,
                        date=operation["created"],
                        text=operation["text"] if index == 0 else "",
                        outgoing=True,
                        reply_to=operation["reply_to"],
                        media="image"
                        if media
                        else (operation.get("media") or {}).get("kind"),
                        attachment_id=(operation.get("media") or {}).get("id"),
                        media_info=self._media_info(operation.get("media")),
                    )
                )
        return Confirmation(complete, tuple(messages))

    @staticmethod
    def _media_info(media):
        if not media:
            return None
        return MediaInfo(
            **{
                k: media.get(k)
                for k in (
                    "mime",
                    "size",
                    "width",
                    "height",
                    "duration",
                    "sticker",
                    "emoji",
                )
            }
        )

    def get(self, identity):
        rows = self.store.rows("SELECT body FROM operations WHERE id=?", (identity,))
        if not rows:
            raise Refused("Send operation unavailable for this account")
        return json.loads(rows[0]["body"])

    def recent(self, key, limit=100):
        """Submissions a conversation shows: pending, failed, uncertain and
        cancelled ones, plus a confirmation whose native messages are not yet
        stored, so presentation never shows a message twice or not at all."""
        with self.store.transaction() as db:
            shown = []
            for row in db.execute(
                "SELECT body FROM operations WHERE chat_key=? AND ("
                "state IN ('queued','prepared','dispatching','unknown') OR "
                "(state IN ('refused','cancelled','confirmed') "
                "AND json_extract(body,'$.submitted')=1)) "
                "ORDER BY rowid DESC LIMIT ?",
                (key, limit),
            ).fetchall():
                operation = json.loads(row[0])
                if operation["state"] == "confirmed":
                    native = [
                        operation.get("message_id"),
                        *(operation.get("receipts") or {}).values(),
                    ]
                    if all(
                        db.execute(
                            "SELECT 1 FROM messages WHERE chat_key=? AND message_id=?",
                            (key, identity),
                        ).fetchone()
                        for identity in native
                        if identity
                    ):
                        continue
                shown.append(operation)
            return shown

    def blocks_removal(self):
        return bool(
            self.store.rows(
                "SELECT 1 FROM operations WHERE state IN ('dispatching','unknown') LIMIT 1"
            )
        )

    def retained_images(self, db):
        """Images of every unconfirmed submission: Retry needs them and failed
        or cancelled items keep showing exactly what was submitted."""
        return {
            image["id"]
            for row in db.execute(
                "SELECT body FROM operations WHERE state IN "
                "('queued','prepared','dispatching','unknown') OR (state IN "
                "('refused','cancelled') AND json_extract(body,'$.submitted')=1)"
            )
            for image in json.loads(row[0]).get("attachments", [])
        }

    def retained_media(self, db):
        """All retained operation rows own exact media, including terminal rows."""
        return {
            value["media"]["id"]
            for row in db.execute("SELECT body FROM operations")
            if (value := json.loads(row[0])).get("media")
        }

    def _wire(self, identity, change, *, guard_reply=False):
        """Apply one change to a dispatching Send Operation's hand-off ledger,
        durably before updating the attempt handle. Raises if the send is no longer
        dispatching, so nothing is handed off without its record."""
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,body FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if not row or row["state"] != "dispatching":
                raise RuntimeError("Send state not recorded; nothing sent")
            body = json.loads(row["body"])
            if guard_reply:
                self._guard_target(db, body)
                status = self._reply_status(db, body)
                if status:
                    raise ReplySendBlocked(status)
            ledger = list(body.get("wire") or [])
            change(ledger)
            body["wire"] = ledger
            db.execute(
                "UPDATE operations SET body=? WHERE id=?",
                (json.dumps(body), identity),
            )

    @staticmethod
    def _random_ids(operation):
        return tuple(
            operation["random_ids"]
            if operation.get("attachments")
            else [operation["random_id"]]
        )

    def description(self, identity):
        operation = self.get(identity)
        directory = self.store.path.parent / "draft-images"
        return SendDescription(
            attempt_id=operation["id"],
            account_id=operation["account_id"],
            peer=operation["peer"],
            text=operation["text"],
            reply_to=operation.get("reply_to"),
            topic_id=operation.get("topic_id"),
            random_ids=self._random_ids(operation),
            images=tuple(
                SendImage(
                    path=Path(image["path"]),
                    directory=directory,
                    sha256=image["sha256"],
                    mime=image["mime"],
                    size=image["size"],
                    width=image["width"],
                    height=image["height"],
                )
                for image in operation.get("attachments", [])
            ),
            media=self.store.selected_media.description(operation),
        )

    def evidence(self, identity):
        operation = self.get(identity)
        if operation["state"] != "dispatching":
            raise Refused("Send attempt has not been claimed")
        return _AttemptEvidence(self, operation)

    def record_receipts(self, batch: ReceiptBatch):
        if not isinstance(batch, ReceiptBatch):
            raise Refused("Invalid native receipts")
        with self.store.transaction() as db:
            db.executemany(
                "INSERT INTO cursors VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [
                    (f"receipt:{random_id}", str(message_id))
                    for random_id, message_id in batch.pairs
                ],
            )

    def record_refusal(self, identity, reason):
        self.get(identity)
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                ("refusal:op:" + identity, reason),
            )

    def check_receipts(self, identity):
        operation = self.get(identity)
        identities = self._random_ids(operation)
        with self.store.transaction() as db:
            found = []
            for random_id in identities:
                row = db.execute(
                    "SELECT value FROM cursors WHERE key=?", (f"receipt:{random_id}",)
                ).fetchone()
                if row:
                    found.append((random_id, int(row[0])))
            if not found:
                # Receipts win over refusals. Old attempts without a ledger
                # predate random-ID reuse and can use the historical key.
                keys = ["refusal:op:" + operation["id"]]
                if "wire" not in operation:
                    keys.append("refusal:" + str(operation["random_id"]))
                for key in keys:
                    row = db.execute(
                        "SELECT value FROM cursors WHERE key=?", (key,)
                    ).fetchone()
                    if row:
                        raise DeliveryRefused(row[0])
        return ReceiptCheck(ReceiptBatch(tuple(found)), len(found) < len(identities))

    def import_retired(self, db, old):
        """Compatibility entry point for the existing historical cutover tool."""
        if old["state"] == "prepared":
            return False
        if not old["peer"].startswith("user:"):
            raise Refused("Unresolved legacy non-DM delivery; source store retained")
        if old["account_id"] != self.store.account_id:
            raise Refused("Retired delivery belongs to another account")
        operation = {
            key: old[key]
            for key in (
                "id",
                "account_id",
                "peer",
                "chat_key",
                "text",
                "reply_to",
                "random_id",
                "created",
            )
        }
        operation.update(
            state="unknown",
            composer_revision=-1,
            attachments=[],
            message_id=None,
            retired=True,
        )
        db.execute(
            "INSERT OR IGNORE INTO operations VALUES (?,?,'unknown',?)",
            (old["id"], old["chat_key"], json.dumps(operation)),
        )
        return True

    def import_retired_evidence(self, db, key, value):
        if not key.startswith(("receipt:", "refusal:")):
            raise Refused("Not historical delivery evidence")
        db.execute("INSERT OR IGNORE INTO cursors VALUES (?,?)", (key, value))

    def recover(self, db):
        """Before an account serves, or after its requests drained: nothing of
        its own is in flight. Definitely unsent work waits for a deliberate
        Retry; an uncertain hand-off stays unknown."""
        for row in db.execute(
            "SELECT id,body FROM operations WHERE state='dispatching'"
        ).fetchall():
            body = json.loads(row["body"])
            if not_sent(body):
                body.update(
                    state="refused",
                    cause="not_sent",
                    reason="Interrupted before reaching Telegram." + kept(body),
                )
                if self._group_operation(body):
                    try:
                        self._guard_target(db, body)
                    except Refused as error:
                        self._block_target(body, error)
            else:
                body["state"] = "unknown"
            db.execute(
                "UPDATE operations SET state=?,body=? WHERE id=?",
                (body["state"], json.dumps(body), row["id"]),
            )
        # Queued or prepared work was never claimed: nothing reached the transport.
        for row in db.execute(
            "SELECT id,body FROM operations WHERE state IN ('queued','prepared')"
        ).fetchall():
            body = json.loads(row["body"])
            body.update(
                state="refused",
                cause="not_sent",
                reason="Interrupted before reaching Telegram." + kept(body),
            )
            if self._group_operation(body):
                try:
                    self._guard_target(db, body)
                except Refused as error:
                    self._block_target(body, error)
            db.execute(
                "UPDATE operations SET state='refused',body=? WHERE id=?",
                (json.dumps(body), row["id"]),
            )

    def expire(self, db, cutoff):
        # A pre-queue prepared send whose draft has changed since can never be
        # claimed. Submissions own their content and never depend on a draft.
        revisions = {
            r["chat_key"]: json.loads(r["body"])["revision"]
            for r in db.execute("SELECT chat_key,body FROM drafts")
        }
        for row in db.execute(
            "SELECT id,chat_key,body FROM operations WHERE state='prepared'"
        ).fetchall():
            body = json.loads(row["body"])
            if revisions.get(row["chat_key"]) != body["composer_revision"]:
                body.update(
                    state="refused",
                    reason="Draft or reply source changed before dispatch",
                )
                db.execute(
                    "UPDATE operations SET state='refused',body=? WHERE id=?",
                    (json.dumps(body), row["id"]),
                )
        for row in db.execute(
            "SELECT id,state,body FROM operations WHERE state IN ('confirmed','refused','cancelled')"
        ).fetchall():
            body = json.loads(row["body"])
            if row["state"] == "refused" and body.get("submitted"):
                # Not sent stays until a deliberate Retry or Cancel: expiring it
                # would lose its content and release the messages behind it.
                continue
            if body.get("created", self.store.clock()) < cutoff:
                db.execute("DELETE FROM operations WHERE id=?", (row["id"],))
