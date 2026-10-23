"""Service-owned, reviewed native deletion and bounded read-only recovery."""

from __future__ import annotations

import asyncio
import logging

from ..domain.deletion import DeletionRead
from ..domain.models import Conflict, Refused

_LOG = logging.getLogger("enloq.deletion")
DEADLINE = 60
IDLE = 5


class Deletion:
    def __init__(self, assistant):
        self.assistant = assistant
        self.store, self.transport = assistant.store, assistant.transport
        self.operations = self.store.deletion_operations
        self.signal = None
        self.processing = set()
        self.checking = set()

    def _guard(self):
        if (
            not self.assistant.authorized()
            or self.transport.account_id != self.store.account_id
        ):
            raise Refused("The connected account is no longer available for deletion")
        if not self.transport.connected:
            raise Refused("Telegram disconnected; check the result before retrying")

    def eligible(self, source):
        """Current Originals-only native action eligibility, independent of AI."""
        return bool(
            self.assistant.authorized()
            and self.transport.account_id == self.store.account_id
            and self.operations.eligible(source)
        )

    def _check_guard(self, key, revision=None):
        self._guard()
        chat = self.store.chat(key)
        if chat.kind == "group":
            from .group_access import native_access

            return native_access(self.store, key, revision)
        return None

    def _own(self, key, identity):
        self.store.chat(key)
        operation = self.operations.get(identity)
        if (
            operation["chat_key"] != key
            or operation["account_id"] != self.store.account_id
        ):
            raise Refused(
                "Deletion operation belongs to another account or conversation"
            )
        return operation

    def view(self, operation):
        from ..storage.store import RETENTION

        outcomes = {
            identity: chunk["state"]
            for chunk in operation["chunks"]
            for identity in chunk["identities"]
        }
        targets = [
            {
                name: target.get(name)
                for name in (
                    "message_id",
                    "revision",
                    "preview",
                    "media",
                    "outgoing",
                    "date",
                    "observation",
                    "checked",
                    "resolved_by",
                    "absent_at",
                )
            }
            | {
                "outcome": outcomes[target["message_id"]],
                "preview": target["preview"]
                if target["date"] >= self.store.clock() - RETENTION
                else "",
            }
            for target in operation["targets"]
        ]
        counts = {
            state: sum(t["outcome"] == state for t in targets)
            for state in (
                "acknowledged",
                "failed",
                "uncertain",
                "unstarted",
                "dispatching",
            )
        }
        counts.update(
            total=len(targets),
            resolved=sum(bool(t["resolved_by"]) for t in targets),
            absent=sum(
                bool(t["absent_at"] or t["observation"] == "absent")
                and t["outcome"] != "acknowledged"
                for t in targets
            ),
        )
        retry_targets = self.operations.retry_targets(operation["id"])
        return {
            "id": operation["id"],
            "account_id": operation["account_id"],
            "peer": operation["peer"],
            "state": operation["state"],
            "created": operation["created"],
            "retry_of": operation.get("retry_of"),
            "reason": operation.get("reason"),
            "check": operation.get("check"),
            "targets": targets,
            "counts": counts,
            "chunks": [
                {
                    name: chunk.get(name)
                    for name in ("index", "identities", "state", "receipt", "reason")
                }
                for chunk in operation["chunks"]
            ],
            "checkable": operation["state"] not in ("queued", "deleting"),
            "retryable": bool(retry_targets),
            "retry_targets": retry_targets,
        }

    def get(self, key, identity):
        return self.view(self._own(key, identity))

    def recent(self, key):
        self.store.chat(key)
        return [self.view(operation) for operation in self.operations.recent(key)]

    async def submit(self, key, identity, targets, *, browser, retry_of=None):
        # Returning a repeated accepted intent needs no fresh connection and
        # cannot requeue a stopped operation.
        try:
            existing = self.operations.get(identity)
        except Refused:
            existing = None
        if existing is None:
            self._guard()
        operation = self.operations.prepare(
            key, identity, targets, browser=browser, retry_of=retry_of
        )
        self.wake()
        return self.view(operation)

    def wake(self):
        if self.signal is not None:
            self.signal.set()

    async def dispatch(self, identity):
        if identity in self.processing:
            return self.view(self.operations.get(identity))
        self.processing.add(identity)
        try:
            if not self.operations.claim(identity):
                return self.view(self.operations.get(identity))
            operation = self.operations.get(identity)
            for chunk in operation["chunks"]:
                index, evidence = chunk["index"], None
                try:
                    description, evidence = self.operations.start_chunk(
                        identity, index, self._guard
                    )
                    async with asyncio.timeout(DEADLINE):
                        receipt = await self.transport.delete(description, evidence)
                    # Persistence precedes local deletion and the next chunk.
                    self.operations.acknowledge(identity, index, receipt)
                    self.assistant.sync.deleted(operation["peer"], chunk["identities"])
                    self.operations.applied(identity, index)
                except asyncio.CancelledError:
                    self.operations.settle(
                        identity,
                        index,
                        evidence,
                        reason="Deletion interrupted; check the result before retrying",
                    )
                    raise
                except Exception as error:
                    reason = (
                        str(error)
                        if isinstance(error, (Refused, Conflict))
                        else "Deletion did not complete; check the result before retrying"
                    )
                    self.operations.settle(identity, index, evidence, reason=reason)
                    break
            return self.view(self.operations.get(identity))
        finally:
            self.processing.discard(identity)

    async def check(self, key, identity):
        operation = self._own(key, identity)
        if operation["state"] in ("queued", "deleting") or identity in self.checking:
            return self.view(operation)
        self._guard()
        # A persisted receipt already authorizes these exact local effects.
        # Access loss can block further native observation, never receipt repair.
        for chunk in operation["chunks"]:
            if chunk["state"] == "acknowledged" and not chunk.get("local_applied"):
                self.assistant.sync.deleted(operation["peer"], chunk["identities"])
                self.operations.applied(identity, chunk["index"])
        operation = self.operations.get(identity)
        self._check_guard(key)
        self.checking.add(identity)
        try:
            identities = [target["message_id"] for target in operation["targets"]]
            for start in range(0, len(identities), 100):
                batch = tuple(identities[start : start + 100])
                try:
                    access_revision = self._check_guard(key)
                    requested = batch
                    if operation.get("kind") == "group":
                        from ..storage.store import RETENTION

                        requested = tuple(
                            target
                            for target in batch
                            if (source := self.store.message(key, target)) is not None
                            and source.account_id == operation["account_id"]
                            and source.peer == operation["peer"]
                            and source.date >= self.store.clock() - RETENTION
                        )
                    async with asyncio.timeout(DEADLINE):
                        checked = (
                            await self.transport.inspect_deletion(
                                operation["peer"], requested
                            )
                            if requested
                            else DeletionRead(
                                operation["account_id"], operation["peer"], ()
                            )
                        )
                    self._check_guard(key, access_revision)
                    if (
                        not isinstance(checked, DeletionRead)
                        or checked.account_id != operation["account_id"]
                        or checked.peer != operation["peer"]
                        or tuple(checked.identities) != requested
                    ):
                        raise Refused(
                            "Exact deletion read did not cover the captured originals"
                        )
                    if requested != batch:
                        checked = DeletionRead(
                            checked.account_id,
                            checked.peer,
                            batch,
                            checked.present,
                            checked.absent,
                            checked.unavailable
                            + tuple(
                                target for target in batch if target not in requested
                            ),
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    checked = DeletionRead(
                        operation["account_id"], operation["peer"], batch, (), (), batch
                    )
                # Existing cached current sources can learn an observed edit;
                # exact reads never back-fill expired application history.
                present = []
                unavailable = set(checked.unavailable)
                for source in checked.present:
                    current = self.store.message(key, source.message_id)
                    if current is not None:
                        if current.deleted:
                            unavailable.add(source.message_id)
                            continue
                        self.assistant.sync.ingest(source, target=False, arrival=False)
                        source = self.store.message(key, source.message_id)
                        if source.deleted:
                            unavailable.add(source.message_id)
                            continue
                    present.append(source)
                checked = DeletionRead(
                    checked.account_id,
                    checked.peer,
                    checked.identities,
                    tuple(present),
                    checked.absent,
                    tuple(sorted(unavailable)),
                )
                self.operations.record_read(identity, checked)
                # A receipt persisted before a crash must still apply its
                # local consequences even if this read is unavailable.
                acknowledged = {
                    i
                    for c in operation["chunks"]
                    if c["state"] == "acknowledged"
                    for i in c["identities"]
                }
                self.assistant.sync.deleted(
                    operation["peer"],
                    sorted(set(checked.absent) | (acknowledged & set(batch))),
                )
            self.operations.finish_read(identity)
            return self.view(self.operations.get(identity))
        finally:
            self.checking.discard(identity)

    async def run(self):
        self.signal = asyncio.Event()
        try:
            while True:
                self.signal.clear()
                try:
                    if self.transport.connected and self.assistant.authorized():
                        for identity in self.operations.stranded():
                            if identity not in self.processing:
                                self.operations.interrupt(identity)
                        for identity in self.operations.reconciliation():
                            operation = self.operations.get(identity)
                            try:
                                await self.check(operation["chat_key"], identity)
                            except (Refused, Conflict):
                                # One group's loss cannot hold unrelated private
                                # work. It grants no mutation/replay authority.
                                continue
                        for identity in self.operations.waiting():
                            await self.dispatch(identity)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOG.warning("deletion_queue_unavailable", exc_info=True)
                try:
                    async with asyncio.timeout(IDLE):
                        await self.signal.wait()
                except TimeoutError:
                    pass
        finally:
            self.signal = None
