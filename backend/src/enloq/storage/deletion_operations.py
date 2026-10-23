"""Durable reviewed deletion intent and per-chunk native evidence (ADR 31).

The ledger survives source expiry. A connected-account absence never substitutes
for an acknowledgement of the submitted native removal request.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy

from ..domain.deletion import (
    DeleteDescription,
    DeleteReceipt,
    DeletionRead,
    deletion_fingerprint,
    deletion_peer,
    private_peer,
)
from ..domain.models import Chat, Conflict, Message, Refused

DDL = "CREATE TABLE IF NOT EXISTS deletion_operations (id TEXT PRIMARY KEY, chat_key TEXT NOT NULL REFERENCES chats(key), state TEXT NOT NULL, body TEXT NOT NULL)"
CHUNK = 100
NOT_EXECUTED = frozenset({"watched", "not_handed", "flood_refused", "refused"})
ACTIVE = frozenset({"queued", "deleting"})


class _DeletionEvidence:
    def __init__(self, owner, identity, index, guard):
        self.owner, self.identity, self.index = owner, identity, index
        self.current = guard
        self.ledger = deepcopy(owner.get(identity)["chunks"][index]["wire"])

    def guard(self):
        self.current()
        self.owner.validate(self.identity, self.index)

    def _change(self, change):
        self.owner.wire(self.identity, self.index, change)
        change(self.ledger)

    def _mark(self, outcome, *, replace=None, **fields):
        entry = {"outcome": outcome, "at": self.owner.store.clock(), **fields}

        def change(ledger):
            if replace and ledger and ledger[-1]["outcome"] == replace:
                ledger[-1] = entry
            else:
                ledger.append(entry)

        self._change(change)

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
        self._mark("exposed")

    def not_handed(self):
        self._mark("not_handed", replace="exposed")

    def flood_refused(self, error, until):
        self._mark("flood_refused", replace="exposed", error=error, until=until)

    def refused(self, reason):
        self._mark("refused", replace="exposed", reason=reason)
        self.owner.record_reason(self.identity, self.index, reason)


class DeletionOperations:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def targets(values):
        result = []
        for value in values:
            if (
                not isinstance(value, dict)
                or not {"message_id", "revision"} <= set(value)
                or not set(value) <= {"message_id", "revision", "source_fingerprint"}
            ):
                raise Refused("Review current native originals before deleting")
            identity, revision = value["message_id"], value["revision"]
            if (
                type(identity) is not int
                or not 0 < identity < 2**31
                or type(revision) is not int
                or revision < 0
            ):
                raise Refused("Invalid reviewed native original")
            reviewed = {"message_id": identity, "revision": revision}
            if "source_fingerprint" in value:
                fingerprint = value["source_fingerprint"]
                if not isinstance(fingerprint, str) or not re.fullmatch(
                    r"[a-f0-9]{64}", fingerprint
                ):
                    raise Refused("Invalid reviewed native source fingerprint")
                reviewed["source_fingerprint"] = fingerprint
            result.append(reviewed)
        if not result or len({v["message_id"] for v in result}) != len(result):
            raise Refused("Review an explicit set of different native originals")
        return sorted(result, key=lambda target: target["message_id"])

    def _chat(self, db, key, *, active=True, operation=None):
        from .store import WINDOW

        row = db.execute(
            "SELECT body,activity FROM chats WHERE key=?", (key,)
        ).fetchone()
        if not row:
            raise Refused("Conversation unavailable for this account")
        chat = Chat.model_validate_json(row["body"])
        if (
            key != chat.key
            or chat.account_id != self.store.account_id
            or chat.kind not in ("private", "group", "saved")
        ):
            raise Refused("Eligible account and native conversation do not match")
        if chat.kind in ("private", "saved"):
            private_peer(chat.peer)
        else:
            deletion_peer(chat.peer)
            if not chat.peer.startswith(("chat:", "channel:")):
                raise Refused("An exact native group is required for deletion")
            state = self.store.groups.get(key, db=db)
            intake = self.store.groups.intake(key, db=db)
            if (
                not state.supported
                or state.access != "available"
                or state.migrated_to is not None
                or (
                    operation is not None
                    and (
                        operation.get("kind") != "group"
                        or operation.get("access_revision") != state.access_revision
                        or operation.get("intake_epoch") != intake.epoch
                    )
                )
            ):
                raise Refused(
                    "Native group access changed or is unavailable for deletion"
                )
        if operation is not None and (
            operation["peer"] != chat.peer
            or operation["account_id"] != chat.account_id
            or operation.get("kind", "private") != chat.kind
        ):
            raise Refused("Reviewed deletion conversation changed")
        # Saved Messages is always shown, whatever its activity (ADR 50).
        if (
            active
            and chat.kind != "saved"
            and row["activity"] < self.store.clock() - WINDOW
        ):
            raise Refused("This conversation is outside the recent activity window")
        return chat

    def _review(self, db, key, targets, *, operation=None, retained_retry=False):
        from .store import RETENTION

        chat = self._chat(db, key, active=not retained_retry, operation=operation)
        if retained_retry and chat.kind != "group":
            raise Refused(
                "Inactive deletion recovery requires the original native group"
            )
        sources = []
        for target in targets:
            row = db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND message_id=?",
                (key, target["message_id"]),
            ).fetchone()
            if not row:
                raise Refused(
                    "A reviewed original is unavailable; review the remaining messages"
                )
            source = Message.model_validate_json(row["body"])
            if (
                source.account_id != chat.account_id
                or source.peer != chat.peer
                or source.message_id != target["message_id"]
                or source.deleted
                or source.service
                or source.date < self.store.clock() - RETENTION
            ):
                raise Refused("Only current ordinary native originals can be deleted")
            if source.revision != target["revision"]:
                raise Conflict(
                    "An original changed; review its current version before deleting"
                )
            if chat.kind == "group" and (
                source.author_peer != f"user:{self.store.account_id}"
                or not self.store.groups.current(source, admission=False, db=db)
            ):
                raise Refused(
                    "Only verified own-user current group originals can be deleted"
                )
            if chat.kind == "group" and target.get("source_fingerprint") is None:
                raise Refused(
                    "Review the full current group wording and media before deleting"
                )
            if (
                target.get("source_fingerprint") is not None
                and deletion_fingerprint(source) != target["source_fingerprint"]
            ):
                raise Conflict(
                    "A reviewed original changed; review its current wording and media"
                )
            sources.append(source)
        return chat, sources

    def eligible(self, source):
        if not isinstance(source, Message):
            return False
        try:
            with self.store.transaction() as db:
                self._review(
                    db,
                    f"{source.account_id}/{source.peer}",
                    [
                        {
                            "message_id": source.message_id,
                            "revision": source.revision,
                            "source_fingerprint": deletion_fingerprint(source),
                        }
                    ],
                )
            return True
        except (Refused, Conflict):
            return False

    @staticmethod
    def _save(db, operation):
        db.execute(
            "UPDATE deletion_operations SET state=?,body=? WHERE id=?",
            (operation["state"], json.dumps(operation), operation["id"]),
        )

    @staticmethod
    def _read(db, identity):
        row = db.execute(
            "SELECT body FROM deletion_operations WHERE id=?", (identity,)
        ).fetchone()
        if not row:
            raise Refused("Deletion operation unavailable for this account")
        return json.loads(row["body"])

    def prepare(self, key, identity, targets, *, browser, retry_of=None):
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"[a-f0-9]{32}", identity)
            or not isinstance(browser, str)
            or not browser
        ):
            raise Refused("A browser-owned deletion identity is required")
        targets = self.targets(targets)
        with self.store.transaction() as db:
            existing = db.execute(
                "SELECT body FROM deletion_operations WHERE id=?", (identity,)
            ).fetchone()
            if existing:
                operation = json.loads(existing["body"])
                captured = operation.get("reviewed_targets") or [
                    {"message_id": t["message_id"], "revision": t["revision"]}
                    for t in operation["targets"]
                ]
                if (
                    operation["chat_key"] != key
                    or operation["browser"] != browser
                    or captured != targets
                    or operation.get("retry_of") != retry_of
                ):
                    raise Conflict(
                        "This deletion identity belongs to a different reviewed intent"
                    )
                return operation
            ancestry = set()
            retained_retry = False
            if retry_of:
                previous = self._read(db, retry_of)
                if (
                    previous["chat_key"] != key
                    or previous["account_id"] != self.store.account_id
                    or previous["state"] in ACTIVE
                    or retry_of == identity
                ):
                    raise Refused(
                        "Review a stopped deletion before retrying remaining originals"
                    )
                retained_retry = previous.get("kind") == "group"
                chat, sources = self._review(
                    db, key, targets, retained_retry=retained_retry
                )
                allowed = {
                    t["message_id"]: t["revision"]
                    for t in self._retry_targets(db, previous)
                }
                if any(allowed.get(t["message_id"]) != t["revision"] for t in targets):
                    raise Conflict(
                        "Retry includes an original that was not checked as still present"
                    )
                cursor = retry_of
                while cursor and cursor not in ancestry:
                    ancestry.add(cursor)
                    ancestor = self._read(db, cursor)
                    if ancestor["chat_key"] != key or ancestor["state"] in ACTIVE:
                        raise Conflict(
                            "A deletion in this retry chain is still pending"
                        )
                    cursor = ancestor.get("retry_of")
            else:
                chat, sources = self._review(db, key, targets)
            selected = {t["message_id"] for t in targets}
            for row in db.execute(
                "SELECT body FROM deletion_operations WHERE chat_key=?", (key,)
            ):
                previous = json.loads(row[0])
                if previous["id"] in ancestry:
                    continue
                unresolved = {
                    t["message_id"]
                    for t in previous["targets"]
                    if t["observation"] != "absent"
                    and not t.get("absent_at")
                    and not t.get("resolved_by")
                    and any(
                        t["message_id"] in c["identities"]
                        and c["state"] != "acknowledged"
                        for c in previous["chunks"]
                    )
                }
                if unresolved & selected:
                    raise Conflict(
                        "A deletion of one of these originals is pending or unresolved; check it and review Retry remaining"
                    )
            operation = dict(
                id=identity,
                account_id=chat.account_id,
                peer=chat.peer,
                chat_key=key,
                kind=chat.kind,
                access_revision=self.store.groups.get(key, db=db).access_revision
                if chat.kind == "group"
                else None,
                intake_epoch=self.store.groups.intake(key, db=db).epoch
                if chat.kind == "group"
                else None,
                browser=browser,
                reviewed_targets=deepcopy(targets),
                retry_of=retry_of,
                retained_retry=retained_retry,
                state="queued",
                created=self.store.clock(),
                targets=[
                    {
                        **target,
                        "preview": source.text[:180],
                        "media": source.media,
                        "outgoing": source.outgoing,
                        "date": source.date,
                        "author_peer": source.author_peer,
                        "native_revision": source.native_revision,
                        "source_fingerprint": deletion_fingerprint(source),
                        "observation": None,
                    }
                    for target, source in zip(targets, sources, strict=True)
                ],
                chunks=[
                    {
                        "index": index // CHUNK,
                        "identities": [
                            t["message_id"] for t in targets[index : index + CHUNK]
                        ],
                        "state": "unstarted",
                        "wire": [],
                    }
                    for index in range(0, len(targets), CHUNK)
                ],
                reason=None,
                check=None,
                needs_reconcile=False,
            )
            db.execute(
                "INSERT INTO deletion_operations VALUES (?,?,?,?)",
                (identity, key, "queued", json.dumps(operation)),
            )
        return operation

    def get(self, identity):
        with self.store.transaction() as db:
            return self._read(db, identity)

    def recent(self, key, limit=100):
        with self.store.transaction() as db:
            result, terminal = [], 0
            for row in db.execute(
                "SELECT body FROM deletion_operations WHERE chat_key=? ORDER BY rowid DESC",
                (key,),
            ).fetchall():
                operation = json.loads(row[0])
                unresolved = (
                    operation["state"] in ACTIVE
                    or self._uncertain(operation)
                    or any(
                        self._target_hold(db, operation, t) in ("pending", "unresolved")
                        for t in operation["targets"]
                    )
                )
                if unresolved or terminal < limit:
                    result.append(operation)
                    terminal += not unresolved
            return result

    def waiting(self):
        return [
            row["id"]
            for row in self.store.rows(
                "SELECT id FROM deletion_operations WHERE state='queued' ORDER BY rowid"
            )
        ]

    def reconciliation(self):
        return [
            row["id"]
            for row in self.store.rows(
                "SELECT id FROM deletion_operations WHERE json_extract(body,'$.needs_reconcile')=1 ORDER BY rowid"
            )
        ]

    def stranded(self):
        return [
            row["id"]
            for row in self.store.rows(
                "SELECT id FROM deletion_operations WHERE state='deleting'"
            )
        ]

    def interrupt(self, identity):
        with self.store.transaction() as db:
            operation = self._read(db, identity)
            if operation["state"] == "deleting":
                self._recover_operation(operation)
                self._save(db, operation)

    def claim(self, identity):
        with self.store.transaction() as db:
            operation = self._read(db, identity)
            if operation["state"] != "queued":
                return False
            operation["state"] = "deleting"
            self._save(db, operation)
        return True

    def _validate(self, db, identity, index):
        operation = self._read(db, identity)
        if operation["state"] != "deleting" or operation["chunks"][index][
            "state"
        ] not in ("unstarted", "dispatching"):
            raise Refused("Deletion is no longer current")
        remaining = {
            i
            for chunk in operation["chunks"]
            if chunk["state"] in ("unstarted", "dispatching")
            for i in chunk["identities"]
        }
        chat, sources = self._review(
            db,
            operation["chat_key"],
            [t for t in operation["targets"] if t["message_id"] in remaining],
            operation=operation,
            retained_retry=bool(operation.get("retained_retry")),
        )
        return operation, chat, sources

    def validate(self, identity, index):
        with self.store.transaction() as db:
            self._validate(db, identity, index)

    def start_chunk(self, identity, index, guard):
        guard()
        with self.store.transaction() as db:
            operation, chat, sources = self._validate(db, identity, index)
            chunk = operation["chunks"][index]
            if chunk["state"] != "unstarted" or any(
                c["state"] != "acknowledged" for c in operation["chunks"][:index]
            ):
                raise Refused("Deletion chunks must be accounted for in order")
            chunk["state"] = "dispatching"
            self._save(db, operation)
        return DeleteDescription(
            identity,
            operation["account_id"],
            operation["peer"],
            tuple(chunk["identities"]),
            operation.get("access_revision"),
            tuple(
                source for source in sources if source.message_id in chunk["identities"]
            )
            if chat.kind == "group"
            else (),
        ), _DeletionEvidence(self, identity, index, guard)

    def wire(self, identity, index, change):
        with self.store.transaction() as db:
            operation = self._read(db, identity)
            chunk = operation["chunks"][index]
            if operation["state"] != "deleting" or chunk["state"] != "dispatching":
                raise RuntimeError("Deletion hand-off evidence is not recorded")
            change(chunk["wire"])
            self._save(db, operation)

    def record_reason(self, identity, index, reason):
        with self.store.transaction() as db:
            operation = self._read(db, identity)
            operation["chunks"][index]["reason"] = reason
            self._save(db, operation)

    def acknowledge(self, identity, index, receipt):
        if not isinstance(receipt, DeleteReceipt):
            raise Refused("Invalid native deletion acknowledgement")
        with self.store.transaction() as db:
            operation = self._read(db, identity)
            chunk = operation["chunks"][index]
            if chunk["state"] == "acknowledged":
                return
            if chunk["state"] != "dispatching":
                raise Refused("Deletion acknowledgement has no matching dispatch")
            chunk.update(
                state="acknowledged",
                receipt={"pts": receipt.pts, "pts_count": receipt.pts_count},
            )
            operation["needs_reconcile"] = True
            operation["state"] = (
                "completed"
                if all(c["state"] == "acknowledged" for c in operation["chunks"])
                else "deleting"
            )
            self._save(db, operation)

            # A separately reviewed acknowledgement resolves an earlier target
            # without changing the old operation's historical native outcome.
            previous_id = operation.get("retry_of")
            completed_ids = set(chunk["identities"])
            visited = set()
            while previous_id and previous_id not in visited:
                visited.add(previous_id)
                previous = self._read(db, previous_id)
                for target in previous["targets"]:
                    if target["message_id"] in completed_ids:
                        target["resolved_by"] = identity
                self._save(db, previous)
                previous_id = previous.get("retry_of")

    def applied(self, identity, index):
        with self.store.transaction() as db:
            operation = self._read(db, identity)
            operation["chunks"][index]["local_applied"] = True
            operation["needs_reconcile"] = any(
                c["state"] == "acknowledged" and not c.get("local_applied")
                for c in operation["chunks"]
            )
            self._save(db, operation)

    @staticmethod
    def _stop(operation, reason):
        states = {chunk["state"] for chunk in operation["chunks"]}
        operation["state"] = (
            "completed"
            if states == {"acknowledged"}
            else "uncertain"
            if "uncertain" in states
            else "partial"
            if "acknowledged" in states
            else "failed"
        )
        operation["reason"] = reason

    def settle(
        self,
        identity,
        index,
        evidence=None,
        *,
        reason="Deletion interrupted; check the result before retrying",
    ):
        with self.store.transaction() as db:
            operation = self._read(db, identity)
            chunk = operation["chunks"][index]
            if chunk["state"] == "acknowledged":
                # A later dependency failure cannot erase its receipt.
                self._stop(operation, reason)
                self._save(db, operation)
                return
            ledger = chunk["wire"] + (evidence.ledger if evidence else [])
            chunk["state"] = (
                "uncertain"
                if any(entry["outcome"] not in NOT_EXECUTED for entry in ledger)
                else "failed"
                if chunk["state"] == "dispatching"
                else "unstarted"
            )
            chunk["reason"] = reason
            self._stop(operation, reason)
            self._save(db, operation)

    def record_read(self, identity, checked):
        if not isinstance(checked, DeletionRead):
            raise Refused("Invalid exact deletion read")
        with self.store.transaction() as db:
            operation = self._read(db, identity)
            if (
                checked.account_id != operation["account_id"]
                or checked.peer != operation["peer"]
                or not set(checked.identities)
                <= {t["message_id"] for t in operation["targets"]}
            ):
                raise Refused(
                    "Exact deletion read belongs to another account or conversation"
                )
            present = {message.message_id: message for message in checked.present}
            for target in operation["targets"]:
                target_id = target["message_id"]
                if target_id not in checked.identities:
                    continue
                target["observation"] = (
                    "present"
                    if target_id in present
                    else "absent"
                    if target_id in checked.absent
                    else "unavailable"
                )
                target["checked"] = self.store.clock()
                if target_id in checked.absent:
                    target["absent_at"] = target.get("absent_at") or self.store.clock()
                target["checked_revision"] = (
                    present[target_id].revision if target_id in present else None
                )
                target["checked_fingerprint"] = (
                    deletion_fingerprint(present[target_id])
                    if target_id in present
                    else None
                )
            operation["check"] = (
                "Checked only the captured originals in this account; no deletion was repeated"
            )
            self._save(db, operation)

    def finish_read(self, identity):
        with self.store.transaction() as db:
            operation = self._read(db, identity)
            operation["needs_reconcile"] = False
            for chunk in operation["chunks"]:
                if chunk["state"] == "acknowledged":
                    chunk["local_applied"] = True
            self._save(db, operation)

    def _retry_targets(self, db, operation):
        from .store import RETENTION

        if operation["state"] in ACTIVE:
            return []
        try:
            chat = self._chat(
                db, operation["chat_key"], active=operation.get("kind") != "group"
            )
        except Refused:
            return []
        if (
            operation["account_id"] != chat.account_id
            or operation["peer"] != chat.peer
            or operation.get("kind", "private") != chat.kind
        ):
            return []
        acknowledged = {
            i
            for c in operation["chunks"]
            if c["state"] == "acknowledged"
            for i in c["identities"]
        }
        result = []
        for target in operation["targets"]:
            if (
                target["message_id"] in acknowledged
                or target.get("resolved_by")
                or target.get("absent_at")
                or target["observation"] != "present"
            ):
                continue
            row = db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND message_id=?",
                (operation["chat_key"], target["message_id"]),
            ).fetchone()
            if not row:
                continue
            source = Message.model_validate_json(row[0])
            if (
                not source.deleted
                and not source.service
                and source.date >= self.store.clock() - RETENTION
                and source.revision == target.get("checked_revision")
                and source.account_id == chat.account_id
                and source.peer == chat.peer
                and (
                    target.get("checked_fingerprint") is None
                    or deletion_fingerprint(source) == target["checked_fingerprint"]
                )
                and (
                    chat.kind != "group"
                    or (
                        source.author_peer == f"user:{self.store.account_id}"
                        and self.store.groups.current(source, admission=False, db=db)
                    )
                )
            ):
                result.append(
                    {"message_id": source.message_id, "revision": source.revision}
                    | (
                        {"source_fingerprint": deletion_fingerprint(source)}
                        if chat.kind == "group"
                        else {}
                    )
                )
        return result

    def retry_targets(self, identity):
        with self.store.transaction() as db:
            return self._retry_targets(db, self._read(db, identity))

    def reply_hold(self, key, message_id, *, db=None):
        if db is None:
            with self.store.transaction() as transaction:
                return self.reply_hold(key, message_id, db=transaction)
        held = None
        for row in db.execute(
            "SELECT body FROM deletion_operations WHERE chat_key=?", (key,)
        ):
            operation = json.loads(row[0])
            for target in operation["targets"]:
                if target["message_id"] != message_id:
                    continue
                current = self._target_hold(db, operation, target)
                if current == "deleted":
                    return "deleted"
                if current == "pending":
                    held = "pending"
                elif held != "pending" and current == "unresolved":
                    held = "unresolved"
        return held

    def reply_holds(self, key):
        """Safe current composer/action status, computed once per stored target."""
        held = {}
        with self.store.transaction() as db:
            for row in db.execute(
                "SELECT body FROM deletion_operations WHERE chat_key=?", (key,)
            ):
                operation = json.loads(row[0])
                for target in operation["targets"]:
                    identity = target["message_id"]
                    current = self._target_hold(db, operation, target)
                    previous = held.get(identity)
                    if current and previous != "deleted":
                        if current == "deleted" or previous != "pending":
                            held[identity] = current
        return held

    def _target_hold(self, db, operation, target):
        chunk = next(
            c for c in operation["chunks"] if target["message_id"] in c["identities"]
        )
        if (
            chunk["state"] == "acknowledged"
            or target.get("resolved_by")
            or target.get("absent_at")
            or target["observation"] == "absent"
        ):
            return "deleted"
        if operation["state"] in ACTIVE:
            return "pending"
        if (
            chunk["state"] == "uncertain"
            or target["observation"] != "present"
            or not self._present_current(db, operation, target)
        ):
            return "unresolved"
        return None

    def _present_current(self, db, operation, target):
        from .store import RETENTION

        row = db.execute(
            "SELECT body FROM messages WHERE chat_key=? AND message_id=?",
            (operation["chat_key"], target["message_id"]),
        ).fetchone()
        if not row:
            return False
        source = Message.model_validate_json(row[0])
        try:
            chat = self._chat(db, operation["chat_key"], active=False)
        except Refused:
            return False
        return (
            not source.deleted
            and not source.service
            and source.date >= self.store.clock() - RETENTION
            and source.revision == target.get("checked_revision")
            and source.account_id == chat.account_id
            and source.peer == chat.peer
            and (
                target.get("checked_fingerprint") is None
                or deletion_fingerprint(source) == target["checked_fingerprint"]
            )
            and (
                chat.kind != "group"
                or (
                    source.author_peer == f"user:{self.store.account_id}"
                    and self.store.groups.current(source, admission=False, db=db)
                )
            )
        )

    def blocks_removal(self):
        with self.store.transaction() as db:
            for row in db.execute("SELECT body FROM deletion_operations").fetchall():
                operation = json.loads(row[0])
                if self._uncertain(operation) or any(
                    self._target_hold(db, operation, t) in ("pending", "unresolved")
                    for t in operation["targets"]
                ):
                    return True
        return False

    @staticmethod
    def _uncertain(operation):
        uncertain = {
            i
            for c in operation["chunks"]
            if c["state"] == "uncertain"
            for i in c["identities"]
        }
        return any(
            t["message_id"] in uncertain and not t.get("resolved_by")
            for t in operation["targets"]
        )

    def recover(self, db):
        for row in db.execute(
            "SELECT body FROM deletion_operations WHERE state IN ('queued','deleting')"
        ).fetchall():
            operation = json.loads(row[0])
            self._recover_operation(operation)
            self._save(db, operation)

    def _recover_operation(self, operation):
        for chunk in operation["chunks"]:
            if chunk["state"] == "dispatching":
                chunk["state"] = (
                    "uncertain"
                    if any(
                        entry["outcome"] not in NOT_EXECUTED for entry in chunk["wire"]
                    )
                    else "failed"
                )
        self._stop(
            operation, "Service interrupted; only exact reads will resume automatically"
        )
        operation["needs_reconcile"] = True

    def expire(self, db, cutoff):
        # Unresolved evidence must survive source expiry and account draining.
        for row in db.execute("SELECT body FROM deletion_operations").fetchall():
            operation = json.loads(row[0])
            redacted = False
            for target in operation["targets"]:
                if target["date"] < cutoff and target["preview"]:
                    target["preview"] = ""
                    redacted = True
            if operation["created"] >= cutoff:
                if redacted:
                    self._save(db, operation)
                continue
            holds = [self._target_hold(db, operation, t) for t in operation["targets"]]
            if not self._uncertain(operation) and not any(
                hold in ("pending", "unresolved") for hold in holds
            ):
                db.execute(
                    "DELETE FROM deletion_operations WHERE id=?", (operation["id"],)
                )
            else:
                # Retain operational IDs/evidence, not message previews past expiry.
                for target in operation["targets"]:
                    target["preview"] = ""
                self._save(db, operation)
