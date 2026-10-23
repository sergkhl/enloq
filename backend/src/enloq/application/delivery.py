"""Service-owned ordered sending with persistent native correlation (ADR 26).

A submission is accepted into its chat's queue once its exact content is saved;
the browser may then close or navigate. Each chat sends one native message at a
time, in order; chats are independent within the account's connection and
flood limits. Nothing is ever resent automatically.
"""

from __future__ import annotations

import asyncio
import logging

from ..domain.delivery import (
    GroupSendBlocked,
    ReplySendBlocked,
    StickerReselectionRequired,
)
from ..domain.models import (
    DeliveryRefused,
    Refused,
)
from ..domain.selected_media import public_media
from ..storage.send_operations import KEPT, PACING_LIMIT, blocking, resumable
from .attachments import Attachments
from .group_access import native_access

_LOG = logging.getLogger("enloq.delivery")
TEXT_DEADLINE, MEDIA_DEADLINE = 60, 180
# Queue workers look for accepted work at least this often between wakeups.
IDLE = 5


def operation_view(operation, now):
    """What a browser may show for one submission: no local file paths."""
    receipts = operation.get("receipts") or {}
    native = (
        [receipts.get(str(i)) for i in operation.get("random_ids", [])]
        if operation.get("attachments")
        else [operation.get("message_id")]
    )
    state = operation["state"]
    submitted = bool(operation.get("submitted"))
    return {
        "id": operation["id"],
        "state": state,
        "reason": operation.get("reason"),
        "cause": operation.get("cause"),
        "retry_after": operation.get("retry_after"),
        "check": operation.get("check"),
        "text": operation.get("text", ""),
        "images": [image["id"] for image in operation.get("attachments", [])],
        "media": public_media(
            operation["media"],
            base=f"/api/accounts/{operation['account_id']}/chats/{operation['peer']}/selected-media/operations/{operation['id']}/{operation['media']['id']}",
        )
        if operation.get("media")
        else None,
        "reply_to": operation.get("reply_to"),
        "created": operation.get("created"),
        "native_ids": [i for i in native if i],
        "submitted": submitted,
        "retryable": submitted
        and state == "refused"
        and operation.get("cause")
        not in (
            "reply_deleted",
            "reply_unavailable",
            "sticker_unavailable",
            "group_access_loss",
            "target_changed",
        )
        and (operation.get("retry_after") or 0) <= now,
        "cancellable": submitted and state in ("queued", "refused"),
        "checkable": state == "unknown",
        "resumable": resumable(operation),
    }


class Delivery:
    def __init__(self, store, transport, *, authorized=lambda: True):
        self.store, self.transport = store, transport
        self.authorized = authorized
        self.attachments = Attachments(store)
        self.operations = store.send_operations
        self.selected_media = None
        transport.bind_receipts(self.operations.record_receipts)
        self.chats: dict[str, asyncio.Task] = {}
        self.signal: asyncio.Event | None = None
        from .group_send_resume import GroupSendResume

        self.group_resume = GroupSendResume(self)

    def _prepare(self, key, revision, *, exact_reply=None, group_revision=None):
        chat = self.store.chat(key)
        if chat.account_id != self.transport.account_id or chat.kind not in {
            "private",
            "group",
            "saved",
        }:
            raise Refused("Connected account or conversation does not match")
        if native_access(self.store, key) != group_revision:
            raise GroupSendBlocked("changed")
        if not self.transport.connected:
            raise Refused("Telegram disconnected; your draft stays editable")
        return self.operations.prepare(
            key,
            revision,
            snapshot=self.attachments.snapshot,
            exact_reply=exact_reply,
            media_snapshot=self.store.selected_media.snapshot,
            expected_group_revision=group_revision,
        )

    async def submit(self, key, revision, *, guard=None):
        """Save one exact submission and clear its composer, or refuse with the
        draft kept. Delivery continues in the service."""
        draft = self.store.draft(key)
        if guard:
            guard()
        if draft.get("state") == "rewriting":
            raise Refused("Stop or undo this rewrite before sending")
        chat = self.store.chat(key)
        if chat.account_id != self.transport.account_id:
            raise Refused("Connected account or conversation does not match")
        group_revision = native_access(self.store, key)
        if draft["revision"] == revision and draft["reply_to"]:
            hold = self.store.deletion_operations.reply_hold(key, draft["reply_to"])
            if hold:
                raise ReplySendBlocked(hold)
        exact_reply = None
        if (
            draft["revision"] == revision
            and draft["reply_to"]
            and self.store.message(key, draft["reply_to"]) is None
        ):
            if (
                chat.kind not in {"private", "group", "saved"}
                or chat.account_id != self.transport.account_id
                or not self.transport.connected
            ):
                raise Refused("Connected account or conversation does not match")
            try:
                async with asyncio.timeout(120):
                    exact_reply = await self.transport.exact(
                        chat.peer, draft["reply_to"]
                    )
            except (Refused, asyncio.CancelledError):
                raise
            except Exception:
                raise Refused(
                    "Could not check the reply target in Telegram." + KEPT
                ) from None
            if (
                not exact_reply
                or exact_reply.deleted
                or exact_reply.account_id != chat.account_id
                or exact_reply.peer != chat.peer
                or exact_reply.message_id != draft["reply_to"]
            ):
                raise Refused("Native reply target is unavailable")
        if draft["revision"] == revision and draft.get("selected_media"):
            if self.selected_media is None:
                raise Refused(
                    "Selected media preparation is unavailable; your draft is kept"
                )
            await self.selected_media.validate_send(key, revision, guard=guard)
        if guard:
            guard()
        operation = self._prepare(
            key,
            revision,
            exact_reply=exact_reply,
            group_revision=group_revision,
        )
        self.wake()
        return operation

    async def send(self, key, revision):
        """Submit, then run this chat's queue until the submission leaves it."""
        operation = await self.submit(key, revision)
        return await self.drain(operation["id"])

    async def drain(self, identity):
        key = self.operations.get(identity)["chat_key"]
        while True:
            operation = self.operations.get(identity)
            if operation["state"] != "queued":
                return operation
            head = self.operations.runnable(key)
            if head is None:
                # Queue admission may have just persisted a reply/access hold.
                return self.operations.get(identity)
            await self.dispatch(head["id"])

    async def dispatch(self, identity):
        """One attempt of the chat's queued head."""
        if self.operations.get(identity)["state"] != "queued":
            return self.operations.get(identity)
        delay = self.operations.pacing_delay(identity)
        if delay > PACING_LIMIT:
            self.operations.not_dispatched(
                identity, "Send pacing requires a later deliberate attempt."
            )
            return self.operations.get(identity)
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            description = self.operations.description(identity)
        except Refused:
            self.operations.not_dispatched(
                identity, "Exact retained send media is unavailable; review this item."
            )
            return self.operations.get(identity)
        if not self.operations.claim(identity):
            return self.operations.get(identity)
        evidence = self.operations.evidence(identity)
        try:
            async with asyncio.timeout(
                MEDIA_DEADLINE
                if description.images or description.media
                else TEXT_DEADLINE
            ):
                native_id = await self.transport.send(description, evidence)
            self._confirm(identity, native_id)
        except DeliveryRefused as error:
            self.operations.refuse(
                identity,
                str(error),
                cause="sticker_unavailable"
                if isinstance(error, StickerReselectionRequired)
                else None,
            )
        except asyncio.CancelledError:
            self._settle(description, evidence, cancelled=True)
            raise
        except Exception as error:
            self._settle(description, evidence, error=error)
        return self.operations.get(identity)

    def _settle(self, description, evidence, *, error=None, cancelled=False):
        try:
            held = self.transport.held_until(description)
        except Exception:
            held = 0
        self.operations.settle(
            evidence,
            held=held,
            error=error,
            cancelled=cancelled,
        )

    def _confirm(self, identity, receipt):
        confirmed = self.operations.confirm(identity, receipt)
        if confirmed is None:
            return
        for message in confirmed.messages:
            self.store.apply_message(message)
        if confirmed.complete:
            self.attachments.collect()
            self.store.selected_media.collect()

    # ---- the service-owned queue ------------------------------------------

    def wake(self):
        if self.signal is not None:
            self.signal.set()

    async def run(self):
        """Start one queue task per chat with accepted work. Draining the
        account cancels them; interrupted sends settle by their evidence."""
        self.signal = asyncio.Event()
        try:
            while True:
                self.signal.clear()
                try:
                    for key in self.operations.waiting():
                        # A chat paused behind a Not sent or unknown item waits
                        # for Retry, Cancel or Check delivery to wake it.
                        if key in self.chats or self.operations.runnable(key) is None:
                            continue
                        task = asyncio.create_task(self._chat(key))
                        self.chats[key] = task
                        task.add_done_callback(
                            lambda task, key=key: self._finished(key, task)
                        )
                except Exception:
                    # Queued work keeps its durable state; the next pass retries.
                    _LOG.warning("delivery_queue_unavailable", exc_info=True)
                try:
                    async with asyncio.timeout(IDLE):
                        await self.signal.wait()
                except TimeoutError:
                    pass
        finally:
            tasks = list(self.chats.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _finished(self, key, task):
        if self.chats.get(key) is task:
            self.chats.pop(key)
        if not task.cancelled() and task.exception():
            # The next pass retries; queued work keeps its durable state.
            _LOG.warning("delivery_queue_unavailable", exc_info=task.exception())
        else:
            self.wake()

    async def _chat(self, key):
        while (head := self.operations.runnable(key)) is not None:
            await self.dispatch(head["id"])

    # ---- deliberate resolution --------------------------------------------

    def _own(self, identity):
        operation = self.operations.get(identity)
        if operation["account_id"] != self.transport.account_id:
            raise Refused("Cannot change a send through another account")
        return operation

    def retry(self, identity):
        """A deliberate new attempt of a message that was not sent."""
        self._own(identity)
        operation = self.operations.retry(identity)
        self.wake()
        return operation

    def revalidate_group(self, key):
        """A native access change holds unhanded submissions without replay."""
        self.operations.revalidate_group(key)
        self.wake()

    async def review_resume(self, identity, *, browser, guard=None):
        return await self.group_resume.review(identity, browser=browser, guard=guard)

    async def resume(self, identity, token, *, browser, guard=None):
        return await self.group_resume.resume(
            identity, token, browser=browser, guard=guard
        )

    def cancel(self, identity):
        """Withdraw a message that provably never reached Telegram."""
        self._own(identity)
        operation = self.operations.cancel(identity)
        self.wake()
        return operation

    def paused(self, key):
        """Whether an earlier failed or uncertain message holds this chat."""
        return any(
            blocking(o)
            and (o["state"] != "queued" or o.get("cause") == "reply_deletion")
            for o in self.operations.recent(key)
        )

    async def recheck(self, identity):
        operation = self._own(identity)
        if operation["state"] != "unknown":
            return operation
        try:
            async with asyncio.timeout(60):
                checked = self.operations.check_receipts(identity)
                if checked.needs_refresh:
                    await self.transport.refresh_delivery()
                    checked = self.operations.check_receipts(identity)
                receipt = checked.receipts
        except DeliveryRefused as error:
            self.operations.refuse(operation["id"], str(error))
            self.wake()
            return self.operations.get(identity)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Telegram could not be checked; the send stays unknown and is
            # never resent.
            _LOG.warning("delivery_check_unavailable", exc_info=True)
            return {
                **self.operations.get(identity),
                "check": "Telegram could not be checked right now. Nothing was resent; try again later.",
            }
        if receipt:
            self._confirm(identity, receipt)
        current = self.operations.get(identity)
        if current["state"] == "unknown":
            current["check"] = (
                "No delivery receipt yet. Nothing was resent; check the chat in "
                "another Telegram app or try again later."
            )
        self.wake()
        return current
