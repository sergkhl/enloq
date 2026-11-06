---
status: accepted
---

# Unread separator and compact conversation header

## Context

Opening a chat at its earliest unseen message
([ADR 23](0023-local-unseen-messages-and-conversation-previews.md)) didn't show
where the new messages began, and one focused second clears their unseen state
([ADR 24](0024-coherent-message-groups-and-focused-viewing.md)), so the cue
has to survive that acknowledgement. The conversation header had also grown
to several rows of controls, taking reading space on a phone.

## Decision

**The Unread messages line (Q1, Q4, Q5, Q12).** An open conversation shows at
most one separator: a thin accent line with "Unread messages" centred, the
same accessible name and no count. It sits directly above the earliest ready
unseen incoming message, under the dot's readiness rule, so a message still in
its quiet seconds or being processed doesn't place it. Opening puts it at the
top of the view with that message just below. In Originals it sits directly
above the first unseen native message; in AI reading, above that message's
whole block, since one coherent result can't be split. View changes keep it
attached to the same source. Ready Unseen Reactions place no separator;
opening still goes to the earliest unseen message or reaction target
([ADR 26](0026-mobile-message-actions-and-send-status.md)). In groups,
[ADR 34](0034-group-chats-and-daily-briefings.md) places it by its day bands
and briefings.

**Captured on opening (Q2).** The line is captured when the conversation
opens and stays put while it remains open: through the one-second
acknowledgement, scrolling, sending and view changes. Reopening or reloading
recalculates it. When content becomes ready while the open conversation is not
focused and visible (a background tab or app, another window, an open dialog
or sheet, the phone's chat list), the line moves above the first such message
once focused viewing returns. It never scrolls the view; only opening
positions it. A notification that brings the owner back to an open
conversation therefore marks where the new messages begin.

**Local wording (Q3).** "Unread messages" is Telegram's familiar label for
Enloq's local unseen state: it neither mirrors nor changes Telegram's read
state and sends no read receipt. The line is presentation only; it stores
nothing, starts no work and changes no readiness, viewing, dot or notification
rule, and each open view keeps its own.

**One-row header (Q6, Q8, Q9, Q11).** The conversation header is one row:
Back, the avatar, the name (in a private chat with the emotion emoji right
after it, [ADR 47](0047-pairing-settings-menus-and-profiles.md) Q7) and the
robot that opens the chat's AI Menu, filled while AI work runs there
([ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md) Q13,
[ADR 62](0062-the-ai-menu-and-private-chat-ai-off-by-default.md) Q1, Q14). The AI Menu holds the
Reading View, the chat's AI controls and, in a group, Group AI processing and
Hide bots ([ADR 42](0042-groups-and-briefings-in-the-enloq-app.md) Q3,
ADR 62 Q13). Saved Messages has no robot. The photo and name open the
Correspondent Profile or Group Profile, and both hold Copy chat link
([ADR 46](0046-the-apps-catch-up-units.md), ADR 47); the emotion details and
Person Memory are in the AI Menu (ADR 62 Q12). Settings opens from the
account sheet's last row
([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q19). No chat has
another conversation menu, and there is no Refresh item; the app refreshes
by pulling down.

**AI processing per account (Q10, Q13, Q14).** Each signed-in account's AI
processing switch is in Manage accounts (ADR 46), with
[ADR 14](0014-account-ai-processing-and-reading-view.md)'s per-account
meaning, shared by every trusted client. While it is Off, the account sheet
shows "AI off" under the account's name, and each chat's robot is outlined
with its AI Menu saying why; no header tag shows (ADR 62 Q14).

**Earlier messages in the stream (Q7).** The chat-level Rewrite earlier
messages sits at the top of the loaded history, where those messages are: "N
earlier blocks are not rewritten" with Rewrite. During a run it reads
"Analyzing earlier messages · n of m" with a bar and Stop; afterwards a notice
such as "12 remaining — keeping budget for new messages". Since
[ADR 54](0054-incoming-text-combined-not-rewritten.md) it offers only blocks
with media. The action stays off, with its reason, while account AI
processing is Off, whatever the chat's own switches say (ADR 62 Q3), and
[ADR 15](0015-owner-controlled-processing-memory-and-prompts.md)'s
click-only, newest-first, budget-reserve and eligibility rules are unchanged.

### Questions

- **Q1:** at most one separator, above the earliest ready unseen message;
  opening puts it at the top.
- **Q2:** captured on opening, kept while open, moved on returning to focus.
- **Q3:** labelled "Unread messages", meaning the local unseen state.
- **Q4:** above the whole block in AI reading, the native message in
  Originals.
- **Q5:** messages only; reactions place none.
- **Q6:** a one-row header. The ⋮ that held everything else, the Show AI text
  switch among it, was replaced by the robot and its AI Menu (ADR 52 Q13,
  ADR 62), the profiles and the account sheet; no chat keeps a ⋮.
- **Q7:** earlier-message processing at the top of the loaded history.
- **Q8:** the account switch is labelled "AI processing". The "Show AI text"
  switch was replaced by AI view in the AI Menu (ADR 62 Q1).
- **Q9:** replaced: Stop lives in the stream, Chat memory (now Person Memory)
  in the chat's AI Menu (ADR 62 Q12) and Settings in the account sheet.
- **Q10:** replaced: the AI processing switch moved from the account picker to
  Manage accounts (ADR 46); there is no header tag (ADR 62 Q14).
- **Q11:** replaced: Settings opens from the account sheet (ADR 51 Q19).
- **Q12:** a thin accent line with centred text.
- **Q13:** replaced: Manage accounts shows every signed-in account's switch.
- **Q14:** the account sheet marks an account with AI processing Off.

## Consequences

- New messages are easy to find, and the header leaves most of a phone's
  height to the conversation.
- Keeping the line per open view survives the one-second acknowledgement
  without stored state, but each tab or phone keeps its own position.
- [History](../history.md#local-viewing) owns the separator's mechanics.
