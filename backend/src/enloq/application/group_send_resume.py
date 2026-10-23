"""Short-lived browser-owned review of exact retained group send work (ADR 34).

Only a deliberate Resume returns a proven unsent item to its original peer's
queue. Reviews and failed checks keep the saved item and newer composer intact.
Native preparation is read-only; ordinary dispatch repeats its native guards.
"""

import asyncio
import secrets
from dataclasses import dataclass

from ..domain.models import Refused

REVIEW_SECONDS = 120
REVIEW_LIMIT = 256
DEADLINE = 120


@dataclass(frozen=True)
class Review:
    browser: str
    operation: dict
    access_revision: int
    title: str
    reply_fingerprint: str | None
    proof: object
    created: float
    expires: float


class GroupSendResume:
    def __init__(self, delivery):
        self.delivery = delivery
        self.store, self.transport = delivery.store, delivery.transport
        self.operations = delivery.operations
        self.reviews: dict[str, Review] = {}

    def _guard(self, browser, guard):
        if guard:
            guard()
        if (
            not browser
            or not self.delivery.authorized()
            or self.transport.account_id != self.store.account_id
            or not self.transport.connected
        ):
            raise Refused(
                "A trusted browser and connected original account are required"
            )

    def _current(self, review, browser, guard):
        self._guard(browser, guard)
        if review.browser != browser:
            raise Refused(
                "Review this group item in the current browser before resuming"
            )
        if not review.created <= self.store.clock() < review.expires:
            raise Refused("Group send review expired; review this item again")
        self.operations.resume_guard(
            review.operation["id"],
            review.operation,
            review.access_revision,
            review.title,
            review.reply_fingerprint,
        )

    async def _native(self, description, guard):
        guard()
        try:
            async with asyncio.timeout(DEADLINE):
                proof = await self.transport.review_group_send(description, guard)
        except (TimeoutError, ConnectionError, OSError):
            raise Refused(
                "Native group send rights could not be checked; review again later"
            ) from None
        guard()
        if proof is None or not callable(getattr(proof, "guard", None)):
            raise Refused("Fresh exact native group send rights are unavailable")
        proof.guard(fresh=True)
        guard()
        return proof

    async def review(self, identity, *, browser, guard=None):
        from .delivery import operation_view

        self._guard(browser, guard)
        operation, revision, title, reply = self.operations.resume_snapshot(identity)
        created = self.store.clock()
        review = Review(
            browser,
            operation,
            revision,
            title,
            reply,
            None,
            created,
            created + REVIEW_SECONDS,
        )

        def current():
            self._current(review, browser, guard)

        description = self.operations.description(identity)
        proof = await self._native(description, current)
        current()
        now = self.store.clock()
        self.reviews = {
            token: item
            for token, item in self.reviews.items()
            if item.created <= now < item.expires
        }
        # New review replaces older tickets for this browser and saved item.
        self.reviews = {
            token: item
            for token, item in self.reviews.items()
            if (item.browser, item.operation["id"]) != (browser, identity)
        }
        while len(self.reviews) >= REVIEW_LIMIT:
            self.reviews.pop(next(iter(self.reviews)))
        token = secrets.token_hex(32)
        self.reviews[token] = Review(
            browser, operation, revision, title, reply, proof, created, review.expires
        )
        return {
            "token": token,
            "expires": review.expires,
            "account_id": operation["account_id"],
            "peer": operation["peer"],
            "title": title,
            "operation": operation_view(operation, now),
        }

    async def resume(self, identity, token, *, browser, guard=None):
        self._guard(browser, guard)
        review = self.reviews.get(token)
        if (
            review is None
            or review.browser != browser
            or review.operation["id"] != identity
        ):
            raise Refused(
                "Review this exact group item in this browser before resuming"
            )
        # A deliberate attempt consumes this ticket. Any failed recheck needs a
        # new review, and concurrent/repeated Resume cannot enqueue another item.
        self.reviews.pop(token)

        def current():
            self._current(review, browser, guard)

        current()
        review.proof.guard()
        description = self.operations.description(identity)
        proof = await self._native(description, current)
        # The fresh read must still agree with the rights shown at review; an
        # unobserved native rights change forces another visible review.
        review.proof.guard()
        current()

        def final_guard():
            current()
            review.proof.guard()
            proof.guard(fresh=True)

        operation = self.operations.resume(
            identity,
            review.operation,
            review.access_revision,
            review.title,
            review.reply_fingerprint,
            guard=final_guard,
        )
        self.delivery.wake()
        return operation
