---
status: accepted
---

# Person and account memory, and traits

## Context

Chat Memory ([ADR 15](0015-owner-controlled-processing-memory-and-prompts.md),
[ADR 21](0021-chat-memory-takeaways-and-idle-processing.md)) held cited
takeaways about both people in a chat, mixing the other person's facts with
the owner's, and since [ADR 54](0054-incoming-text-combined-not-rewritten.md)
only media and softened rewrites read it. The requirement: "memories should be
collected per private chat to understand what recipient care about and per
account to understand what owner cares".

## Decision

### Two memories

**Who reads them (Q1).** The owner reads both. Response Suggestions read the
cares-about entries of both memories (Q15-Q17), reversing ADR 25's exclusion
of memory. Media and softened rewrites keep reading Person Memory as reference.
Nothing else reads them: not group briefings, notifications or the mood
decision. Manual suggestions may read enabled memory under the same rules;
an unfinished draft is not a memory-learning source.

**Person Memory holds the other person's side (Q2).** It belongs to one
private chat and holds only what the other person cares about: their
priorities, plans, worries, interests, important people and events, likes,
dislikes and boundaries. An agreement stays when it matters to them; facts
about the owner leave it.

**Inferred traits too (Q3).** Traits are kept apart from the memories
(Q10-Q13, Q22, Q23).

**Account Memory per Telegram account (Q4).** It holds what the account's
holder, the owner or a member for their own accounts, cares about.

**What Account Memory learns from (Q5).** The holder's own messages in that
account's private chats, read with the conversation around them, and entries
they write. An Account Memory entry that cites none of the holder's own
messages is dropped, since it would describe someone else; a Person Memory
entry cites at least one of the person's own. Groups and Saved Messages teach
neither memory.

**Chat Memory's rules carry over to both (Q6).**

- **Entries:** each is cited, and an edited or deleted cited message removes
  it with no model call.
- **Control:** the owner sees, adds, edits, removes and clears entries; the
  model never changes theirs.
- **Size:** at most 40 entries and about 3,000 bytes per memory, 150
  characters per generated entry, in the language it was said in.
- **Retention:** kept until cleared or the account's local data is removed.
- **Writing:** MiMo writes them, one memory call at a time across the server,
  charged to the member's Daily Budget. A failed update keeps the previous
  memory, with Retry.

**Switches (Q7).** One **Memory** switch in Settings covers both memories; it
is the owner's and server-wide
([ADR 48](0048-members-on-the-shared-service.md)). Each Correspondent Profile
has **Remember this chat**, Off by default
([ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md)
Q9).

**A quality gate (Q8).** See Q26.

**Q9:** delivery scope; no lasting rule.

### Traits

**Collected now, used later (Q10).** Traits are collected for each chat's
person and each account's holder and kept in their profiles; how to use or
improve them is decided later.

**Anything the evidence supports (Q11).** Notes may record sensitive guesses;
the starter list of scales holds none.

**Notes (Q12).** MiMo writes them freely: each a pattern across at least three
of the person's own messages on at least two days, cited and worded as a
tendency, at most 10, each with a confidence. A deleted or edited cited
message drops only its citation; the note goes when fewer than three remain.
Notes are written at most once a day per chat and per account, at a memory
update.

**Only the owner sees traits (Q13).** No model reads them for now.

### Suggestions and memory

**Account Memory holds the same kinds of things (Q14)** as a person's, through
the same lens.

**Said only where it was said (Q15).** Account Memory may shape which stances
and words fit in any chat, but an option states something from it only in the
chat it came from: an entry may be stated only when every one of its
citations is in that chat, and an entry merged across chats only shapes.

**Judged against today (Q16).** From Person Memory an option may ask about,
wish luck with or acknowledge what the person cares about, judged against
today's date, never claiming it is still true; no answer rests on memory
alone.

**ADR 49's gate stays (Q17).** Memory shapes the sets that appear and adds
none.

### Learning and controls

**Remember this chat Off takes the chat out both ways (Q18).** Off, the chat
teaches neither memory, nothing in it reads either, and it collects no
traits.

**Sooner (Q19).** Memories learn from every message already sent to a model,
the latest 20 included, after 30 quiet minutes in the chat. This reverses
ADR 21's wait for messages to leave Supporting Context.

**A start from the cache (Q20).** Chat Memory's takeaways were cleared at
launch, and each memory starts once from the local 30-day cache, without a
Telegram fetch; so does a newly added account's, and a chat switched on that
was Off only by default (ADR 56 Q11). This reverses ADR 21's rule that
caching a message doesn't make it eligible for a model. Messages that arrive
while Memory, or a chat's holder-chosen Remember this chat, is Off are never
learned.

**Where the owner sees them (Q21).** The Correspondent Profile shows the
person's Cares about and Traits; Manage accounts gives each account an About
you page with You care about and Your traits, in both clients.

**Three kinds of traits (Q22, Q23).**

- **Scales**, scored by the decision model as the `trait_scales` role, from a
  starter list of fixed questions:
  - how they write: message length, directness, formality, emoji and humor;
  - toward the other: warmth, sharing feelings and life, asking about the
    other, and, for a person only, closeness and the relationship;
  - personality, the Big Five: open to new things, organized and reliable,
    outgoing, accommodating and emotionally steady;
  - under friction: avoids, gives in, confronts, works it out, or none seen.

  Each is judged again at every memory update from the cached month (at most
  60,000 bytes). It is shown only while a yes/no evidence question says yes
  at 0.7 or more, as its likeliest level with that level's probability. A
  scale that loses its evidence keeps its last level and date. The owner can
  set or clear any scale, and the model never changes one they set; a cleared
  scale stays the owner's.
- **Notes** (Q12), from at most 24,000 bytes of the cached month.
- **Habits**, counted from the messages without a model once the person has
  at least 10 messages over at least two days in the month: typical reply
  time (the median gap to their next message within 12 hours), the busiest
  four active hours, the share of voice notes and who starts conversations
  (after six quiet hours).

The decision model was chosen for scales after a comparison with MiMo on
wholly authored chats: as accurate within one level, far faster and cheaper,
and with the per-trait evidence question catching thin evidence its own
confidence missed.

**Guidance (Q24).** Two owner-editable fields, Person memory and Account
memory. Traits have no switch or field yet: their questions are fixed, and
Memory and Remember this chat cover them.

**Names (Q25).** Chat Memory is renamed Person Memory; the profile shows Cares
about and Traits, and Manage accounts About you. Stores and receipts keep
`chat_memory`, `memory_entries` and `memory_state`.

### The quality gate

**The gate (Q26).** `scripts/check-memory.py` is the gate, rerun whenever the
memory guidance changes. Authored Russian and English chats go through the
real path.

- **Cares about:** every entry concerns the right person and cites messages
  that say it; no facts about the owner in a Person Memory; nothing
  invented; dates and languages kept.
- **Suggestions:** nothing said in a chat where it wasn't said (Q15), and
  nothing remembered claimed as still true (Q16).
- **Traits:** nothing shown for a three-message chat, and scales within one
  level on at least 9 in 10.

The first two allow zero misses. The owner reads every result.

### The Member Agreement

**A reduced agreement (Q27).** Enloq is not a paid service, so the agreement
is written once and not reworked for each feature. It promises no privacy
and names no provider or retention: Enloq is unofficial and
experimental and holds full sessions; anything it reads may be sent to any
service, kept for any time and read by the owner and their agents, and
contacts haven't agreed to that; data may be wiped, and leaving deletes what
it holds. A new provider, retention or feature no longer asks members again;
only changed words make a new version (ADR 48 Q3).

**Launched with two misses (Q28).** Memory launched although the gate's last
run still missed twice by Q26's zero-miss rule (see Known limits). Trying
Muse Spark 1.3 for the memory update is the follow-up if literal readings
show up in real chats.

**The test chat (Q29).** Remember this chat is On for the Maya test chat in
the Owner Account, so test messages there teach Maya's Person Memory and the
Owner Account's About you like any other chat's. A check that must not teach
memory switches it Off for its run and back On.

## Consequences

- Durable profiles of people are kept, as memory entries and traits for the
  owner's eyes, and suggestions read memory. Emotion Classification reads
  none. This amends ADR 13 (profile-based advice returns, for suggestions
  only and through memory), ADR 15 and ADR 21 (memory's scope, sources,
  start, readers and takeaway selection),
  [ADR 22](0022-recipient-emotion-and-adaptive-rewriting.md),
  [ADR 25](0025-emotion-response-suggestions.md) and ADR 48 Q3.
- Scales cost about $0.0001 per memory update on the decision route.

## Known limits

- **Figurative Russian.** MiMo sometimes reads a Russian idiom of worry
  literally in a memory update.
- **Stale events.** One gate suggestion asked how an event "is going" days
  after it, against Q16.
- **Account notes' chats.** An Account Memory note's sources name their chats,
  but neither client shows them yet.
- **A runaway first update.** A large first update can spend all its output
  tokens on hidden reasoning and fail as incomplete; it then waits for the
  owner's Retry in that person's profile. If it recurs, smaller first batches
  are the remedy.
