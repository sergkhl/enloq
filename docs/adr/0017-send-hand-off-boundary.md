---
status: accepted
---

# Refuse sends that Telegram provably did not execute

## Context

A deliberate send must never be repeated silently: a send whose outcome is
uncertain stays unknown until evidence settles it. Treating every failure as
unknown, though, let Telegram's flood and slow-mode waits block chats for
nothing. A flood wait holds the whole account, so a background read could keep
an ordinary Send waiting past its deadline (60 seconds, 180 with images); the
send was then recorded as unknown although nothing had reached Telegram, every
later send in that chat was held, and Check delivery could never resolve it
because there was no receipt to find.

## Decision

**Hand-off record.** Each Send Operation carries a small ledger for its final
request. The transport records that the request may reach Telegram before it
calls the client; the request policy narrows that record when it sees the
request, then records each hand-off to Telethon's sender immediately before it
happens. A record is narrowed back only on proof: the sender took nothing, or
it took exactly one transmission that Telegram answered with a flood or
slow-mode wait. Each record is durable before the step it describes: if the
transport's record or a hand-off record cannot be saved, that step does not
happen, and if a narrowing record cannot be saved, the broader record stands.
Evidence can therefore only move a verdict toward unknown.

**Verdict.** A send that fails without a definitive answer is refused as not
sent, keeping its content, only when both its stored and in-memory records
prove nothing was executed. An empty record proves it too: the send stopped
before its final request was handed off, for example at a hold met while
looking up the chat or uploading images, or at its deadline. While Telegram's
wait lasts, the refusal says when the send can go again. Telegram's
definitive refusal, a failed image check and an upload error still refuse a
send outright, and a definite answer met while media is prepared is named in
the Not sent line ([ADR 32](0032-gif-and-sticker-sending.md)). Anything else
is unknown, holds its chat's queue and is never resent. Restart recovery
applies the same rule.

**Check delivery** resolves an unknown send from a stored receipt, which
proves delivery, or from the definitive refusal Telegram gave that same
attempt; a refusal is stored per attempt because a resend shares its random
IDs. When Telegram cannot be checked, including while the account is
disconnected, it says so and leaves the send unknown instead of failing.

**Retry.** Sending the same submission again reuses the random IDs of its
latest not-sent attempt. If Telegram did execute the first request after all,
it answers `RANDOM_ID_DUPLICATE`, which is unknown, never a second message. An
edited draft is a new submission with new random IDs. A send record without a
hand-off ledger is never read as not sent.

## Consequences

- The premise that a flood or slow-mode reply means the request was not
  executed is Telegram's convention rather than a documented guarantee. The
  app already relies on it when it retries flood refusals with the same random
  ID, and random-ID reuse is the backstop for an unchanged resend.
- The proofs rely on Telethon 1.44.0 internals: its sender queues a request
  only in `send()`, which raises before queueing while disconnected; every
  protocol re-queue passes the app's counting wrapper; and with automatic
  reconnection off, a dropped connection re-queues nothing. Tests pin these
  against the installed sender; re-verify them before any Telethon upgrade.
- [ADR 18](0018-send-operations-ownership.md) owns the Send Operation state
  and [ADR 26](0026-mobile-message-actions-and-send-status.md) the per-chat
  queue, its Not sent and Delivery unknown states and Cancel send.
