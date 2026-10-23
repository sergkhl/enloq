---
status: accepted
---

# Chat memory takeaways and idle processing

## Context

Memory updates are background work that nobody waits for. They must never
take capacity from reading and writing, never resample unchanged material, and
never leave memory half-replaced. What memories hold, what they learn from
and who reads them are [ADR 55](0055-person-and-account-memory-and-traits.md)'s
(Person Memory and Account Memory), with Remember this chat Off by default
([ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md));
the memory rules themselves are
[ADR 15](0015-owner-controlled-processing-memory-and-prompts.md)'s. This ADR
owns when and how updates run and the size of an entry. ADR 55 cleared the
earlier takeaways rather than converting them.

## Decision

**Idle admission.** An update for a chat starts only after 30 minutes
without a new incoming or outgoing message in that chat, and while no
rewriting, transcription, emotion decision, suggestion, suggestion label or
group briefing work is waiting or running on any account, catch-up and the
earlier-messages run included. Telegram sync, reading and a closed client do
not hold it back while the service runs. The account's AI switch, the Memory
switch, Remember this chat, eligibility, the member's Daily Budget and a
format-failure pause still apply. Waiting long never overrides the idle
condition.

**Batching.** Once idle, any unconsumed eligible source is enough. Pending
material for a chat is coalesced into bounded batches. A validated no-change
result consumes its input like a useful update, so polling, reads, reloads and
restarts never resample it; a new revision of a source is considered after
its normal processing and invalidation.

**One call at a time.** At most one memory call runs on the server, within
the Concurrency Limit, rotating fairly among eligible chats, accounts and
members. Started work may finish when new communication arrives, but no next
call starts until all is idle again. At a limit of one, new communication may
wait for the running memory call. Memory therefore sits behind every waiting
private and group task.

**Sources.** Native text, cached transcripts and image meanings of messages
already sent to a model, plus ADR 55's one-time start from the 30-day cache.
Never observed earlier versions, new media analysis, a Telegram fetch or
reply-chain scanning.

**Entries.** A memory holds at most 40 entries and about 3,000 UTF-8 bytes.
An entry is at most 300 UTF-8 bytes; generated entries are capped at 150
characters, in the provider schema and in local validation alike, so Latin
and Cyrillic always fit. Owner entries are protected: the model never changes
them, even where they exceed the generated allowance, and the clients count
bytes as the owner types ([ADR 46](0046-the-apps-catch-up-units.md)). There
is no numerical importance score.

**Publication guards.** Malformed output, fabricated citations and stale
publication never replace memory, even partly. Source edits and deletions,
transcript changes, owner edits and authorization changes keep their guards.
A failed update keeps the previous memory and offers Retry; refusals with no
work and budget waits keep their bounded backoff. Readers use the last
published memory without waiting for an update, and a memory change re-runs
nothing.

## Consequences

- During sustained activity memory refreshes slowly, in exchange for
  predictable foreground capacity and restrained spending.
- No allowance, automatic quality resampling or recurring sweep of all history
  is added.
- Structural validity, semantic quality and the owner's adoption stay separate
  evidence ([ADR 4](0004-acceptance-quality-feedback.md)).
