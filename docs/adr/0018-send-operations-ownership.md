---
status: accepted
---

# One owner for Send Operation state and delivery evidence

## Context

A send's lifecycle once spread across delivery, store recovery and expiry,
account removal, attachment collection and the Telegram adapter, so a change
to prepared-send recovery meant reinterpreting states in several callers. A
send must also stay bound to what the owner actually submitted.

## Decision

**What a send is bound to.** A Send Operation is bound to its exact submitted
content (text, images, GIF or sticker), account, chat and native reply target,
independently of later draft edits. It has a stable identity, and its
receipts and refusals are retained, so restarts and repeated clicks are
inspectable and never produce a second message.

**One owner.** One account-scoped Send Operations module owns preparation and
state transitions, the hand-off ledger
([ADR 17](0017-send-hand-off-boundary.md)), receipt precedence, retry identity,
recovery and expiry policy, and the queries for removal blockers and retained
attachments. It has a local state owner usable without a connected Telegram
client and a live delivery coordinator. This is one responsibility boundary,
not a requirement for one class or a separate service.

**The Telegram adapter.** It consumes an immutable send description and
reports native facts through a narrow durable evidence interface. It keeps
native request construction, uploads, identity checks, pacing and connection
recovery, but never interprets the operation's storage or chooses its
lifecycle state. Facts needed before a native side effect are recorded
synchronously through the interface, at ADR 17's observation points.

**Presentation and queue.** The composer clears once the service has safely
saved a submission, and a Pending Send shows at once while upload and delivery
continue; one native send runs per chat at a time, and a failed or unknown
send holds its chat's queue
([ADR 26](0026-mobile-message-actions-and-send-status.md),
[ADR 51](0051-telegram-phone-look-and-new-messages.md)). A pending bubble is
presentation, not proof of a Telegram message: confirmation reconciles the
operation with the native message without a duplicate display or send.

**Storage.** The operations table and its JSON, the hand-off records and the
receipt and refusal cursor formats are kept, with conservative reading of
existing records; internal types hide those representations.
[Media delivery](../media-delivery.md#send-operations-ownership) describes the
interfaces.

## Consequences

- Lifecycle knowledge sits in one place, at the cost of a wider boundary
  between the module and its callers.
- This adds no automatic sends, automatic reconciliation, session owner or
  model work. [ADR 10](0010-multiple-personal-accounts.md),
  [ADR 13](0013-clean-slate-rewriting-rebuild.md), ADR 17 and ADR 26 own
  product and delivery policy.
