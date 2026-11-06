---
status: accepted
---

# Default-on account processing and an independent reading view

## Context

Paying for model work and choosing how to read a conversation are different
decisions. Enabling chats one by one was tedious, and tying spending to a
display choice would make reading cost money.

## Decision

**Account AI Processing.** Each Telegram account has one durable AI
processing switch, On by default for existing and new accounts. A member's
accounts start On once the owner approves the member, and new AI stages wait
while the member has not accepted the current Member Agreement
([ADR 48](0048-members-on-the-shared-service.md)). The switch is stored
against the account's stable identity, shared by every trusted client and
kept across refreshes, reconnects and restarts. It sits in Manage accounts in
both clients ([ADR 46](0046-the-apps-catch-up-units.md)); while it is Off the
account sheet marks the account, and its chats' AI Menus say so, with the
header's robot outlined ([ADR 62](0062-the-ai-menu-and-private-chat-ai-off-by-default.md) Q14). It is
separate from login state, from a group's own Group AI processing
([ADR 34](0034-group-chats-and-daily-briefings.md)) and from each private
chat's switches in its AI Menu (ADR 62 Q2), and the master over both.

**What Off stops.** Every new AI stage for that account: transcription,
rewriting, image understanding, decisions, suggestions, memory, briefings and
manual suggestion generation and Retry. Notification delivery takes ADR 20's
original fallback without screening. Sync, originals, cached results, drafts and
sending continue. Work already started may finish and save a still-valid
result and its receipt but starts no next stage, so admission and
publication validity are separate checks. Toggling alone never discards a
valid paid result or turns its cost into an unknown. Pending revisions wait
while Off and resume within the normal age and quiet bounds when On, without
rescanning read history, repeating successful work or retrying failed or
unknown calls automatically.

**The Reading View.** One global choice between AI and Originals: AI view,
the first row of the AI Menu that the robot at the conversation header's
right end opens ([ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md),
ADR 62 Q1). AI shows
combined blocks and rewrites, falling back to originals; Originals shows
native messages in order. The choice is remembered per member across chats,
accounts, clients and restarts (ADR 48), independently of processing and of
Suggestion Style, and changing it starts no model work. A message's own choice is in
its long-press sheet ([ADR 20](0020-minimal-reading-and-ready-notifications.md)),
and conversation previews follow the global one
([ADR 23](0023-local-unseen-messages-and-conversation-previews.md)). Saved
Messages has no AI Menu.

## Consequences

- An account can pause new spending while its cached rewrites stay readable,
  or be read in Originals while processing continues in the background.
- The switch neither adds budget nor splits it: an account's work counts in
  its member's Daily Budget
  ([ADR 15](0015-owner-controlled-processing-memory-and-prompts.md)).
- Each private chat's AI Menu has five switches, Off by default, for its
  automatic suggestions, emotion, softening, voice and images, and memory
  (ADR 62 Q2); this account switch stays their master.
