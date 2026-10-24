---
status: accepted
---

# Owner-controlled processing, chat memory, prompts, budget and concurrency

## Context

The owner wants direct control over the reach, cost and behavior of model
work: which earlier messages may be processed, what the model is told, how
much a day may cost and how many calls run at once. That control comes with
weaker built-in guarantees, which this ADR accepts. The Q numbers below are
this decision's own, as the code cites them.

## Decision

**Explicit processing of earlier messages.** A deliberate action may target
blocks in the local 30-day cache of an eligible recent chat:

- **Rewrite earlier messages**, at the top of the loaded history, with its run
  progress and Stop ([ADR 28](0028-unread-separator-and-compact-conversation-header.md)).
  It offers only blocks with an image, a voice note or a Video Circle, since
  text stays combined ([ADR 54](0054-incoming-text-combined-not-rewritten.md)).
  It runs newest first and never resumes on its own. Stop, AI Off and lost
  eligibility end it, and so does the budget reserve (Q25): it starts no call
  once less than a quarter of the day's budget remains, showing how many
  blocks remain, "keeping budget for new messages".
- **Retry** of a failed block, from its long-press sheet
  ([ADR 46](0046-the-apps-catch-up-units.md)). Retry of a text block that
  isn't softened decides it again and ends combined (ADR 54).

There is no first-time Rewrite now per block
([ADR 31](0031-message-deletion-selection-and-group-ai-controls.md)) and no
Rewrite again of a completed result
([ADR 26](0026-mobile-message-actions-and-send-status.md)). Background
admission, unread catch-up and automatic re-queueing keep the seven-day
window. An explicit target older than seven days uses up to 20 preceding
messages within seven days of its own date, from the local store only, plus
its direct reply. Explicit work never fetches or scans Telegram history.

**Memory.** Memory is a bounded list of short cited entries, framed to the
model as unverified reference data and applied only to targets newer than its
coverage. It is never a rewrite target.
The owner sees, edits, adds to and clears it, and it is a reference list, not
a task list. It is kept until cleared or until the account's local data is
removed, an exception to the 30-day rule for derived content. A cited list
rather than a rolling summary lets an edited or deleted source remove its
entries without a model call and limits drift. There is no semantic
retrieval, vector store or recurring sweep of every chat. What memory holds,
who reads it and its start from the cache are
[ADR 55](0055-person-and-account-memory-and-traits.md)'s (Person Memory and
Account Memory); when updates run is
[ADR 21](0021-chat-memory-takeaways-and-idle-processing.md)'s; Remember this
chat starts Off ([ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md)).

**Processing Guidance.** The owner edits global guidance in Settings → AI →
Guidance ([ADR 47](0047-pairing-settings-menus-and-profiles.md)), each field up
to 4,000 characters with Save, Reset and a version:

- **Rewriting:** Faithfulness rules, Incoming rewriting and Soften hostile
  messages;
- **Suggestion styles:** Friendly, Professional and Idioms;
- **Other work:** Emotion extraction, Person memory, Account memory, Response
  suggestions, When to suggest and Group briefings.

A fixed core the owner cannot edit keeps message and memory content framed as
untrusted data and holds the output contracts, the hidden image-context
contract, the plain-text rule and structural validation. Endpoints accept no
prompt text, model name or context packet. A change applies to new work only;
each result records its guidance version, and nothing re-runs because
guidance changed. The Faithfulness Rules are a shipped default the owner may
weaken. Guidance and the other AI settings are server-wide and only the owner
changes them ([ADR 48](0048-members-on-the-shared-service.md)).

**Format-failure pause.** After three consecutive format failures, automatic
incoming rewriting or memory updates pause until the owner
keeps or resets that role's guidance from the paused-role notice; saving
guidance restarts the counts, and so does a change of the role's Model Route
([ADR 29](0029-mimo-route-and-structured-incoming-rewriting.md)). Suggestions
never pause.

**Daily Budget.** Each member has one daily limit from $0 to $10, which only
the owner sets: $1 by default for the owner and $0.50 for a friend (ADR 48). A
change is logged; the limit never changes automatically.

- **Admission.** A new call starts only while the day's counted spending is
  below the limit; running calls may finish, so a small overshoot is possible.
- **Counting.** Counted spending is the receipted costs, taking the larger of
  the charged and upstream cost, plus a flat $0.01 for each call whose outcome
  is unknown, on that call's own day only. A refusal where the provider did no
  work counts nothing. There are no in-flight holds.
- **The ledger.** It counts integer nanodollars per accounting day in the
  server's timezone (Asia/Ho_Chi_Minh), behind a persisted day watermark that
  never moves backwards; the timezone cannot change on reopen. An admitted job
  is never dispatched twice after a restart.

The Group Briefing Budget is separate and independent
([ADR 34](0034-group-chats-and-daily-briefings.md)).

**Refusals and waits.** When the provider refuses with no work done, or the
pre-dispatch check finds the exact route missing or not reported OK,
background work re-queues after the provider's Retry-After or 30, 60 and 120
seconds, at most three times, then fails visibly with Retry (Q31). It never
falls back to another route or provider. A block that failed before dispatch
only because of Non-AI Media is re-queued once while still inside the
seven-day window, since nothing reached a provider (Q21). A changed route contract, such as missing
output parameters or unbounded rates, fails at once. Notification screening
and delivery instead follow ADR 20's bounded original-text fallback. Work waiting for budget shows why and resumes
when spending allows. Receipts, attempt identities and the
no-automatic-resampling rule apply throughout.

**Concurrency Limit.** One global limit on concurrent model calls, 1 to 8 and
4 by default, across accounts and members. Classes are served highest first:
new incoming work (a Retry click included), unread catch-up,
Reaction Assessment, the earlier-messages run, Response Suggestions, group
briefings and memory. With a limit of 2 or more, the four lowest classes never
take the last free slot; at 1, work runs strictly by priority. Members take
turns within each class (ADR 48), memory runs only while communication is
idle (ADR 21), and transcription runs one note at a time.

## Consequences

- Faithfulness and output quality depend partly on the owner's guidance.
- Memory keeps derived content beyond the 30-day cache until cleared.
- Spending can slightly exceed the limit, by the calls already running when it
  is reached.
- Earlier messages are processed only on a deliberate click, never by a
  history scan.
