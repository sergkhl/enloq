---
status: accepted
---

# Local unseen messages and compact conversation previews

## Context

Enloq never sends Telegram read receipts, so Telegram's own unread state
can't tell the owner where new content waits. Content is also not ready the
moment it arrives: incoming work waits 30 quiet seconds and may then be
decided, rewritten or calmed. A notification can be muted, suppressed, fail or
expire, and the owner may read an original before its rewrite finishes. The
app therefore keeps its own durable viewing state, separate from readiness,
from Telegram's read position and from notification delivery, and shows it as
one quiet dot on the conversation row.

This builds on [ADR 14](0014-account-ai-processing-and-reading-view.md)'s
separation of processing from presentation and on
[ADR 20](0020-minimal-reading-and-ready-notifications.md)'s content readiness
and original fallback. It changes no notification policy, AI admission,
model, budget or Telegram read state, and none of the seven-day discovery and
catch-up limits. These rules describe private chats; groups follow
[ADR 34](0034-group-chats-and-daily-briefings.md), and Saved Messages
([ADR 50](0050-icon-tabs-saved-messages-and-one-saved-build.md)) is never
unseen.

## Decision

**Local viewing state.** An unseen source is an incoming native message that
is ready to read and has not been viewed in Enloq. Viewing is stored apart
from readiness, keyed by the stable account, chat and message, and shared by
every trusted browser and phone. It survives refreshes and restarts and
follows the source cache's 30-day expiry, logout and local-removal rules,
without extending that cache. Deleted or expired sources stop counting, and
historical versions cannot restore an unseen state. Viewing is independent of
Telegram's read position, inference catch-up and notification state: reading
in another Telegram client clears nothing here, viewing here sends no read
acknowledgement, and viewing neither starts nor cancels AI work.

**One-time baseline.** For an existing cached chat, or a newly added account's
chat, Telegram's incoming read position is used once, within the seven-day
catch-up window, to pick the initially unseen candidates; older and
already-read sources start viewed. The baseline is frozen: later changes to
Telegram's read position don't touch local viewing. New incoming messages
after it, including bounded offline recovery, are candidates whatever
Telegram says. Loading older history creates no candidates and widens no
fetch.

**Readiness, independent of alerts.** A candidate becomes ready after its 30
quiet seconds, under the same content decision that notifications use:

- when its validated result publishes: a rewrite of a block with media, or a
  calmed rewrite ([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md));
- when the original fallback is known: text that stays combined
  ([ADR 54](0054-incoming-text-combined-not-rewritten.md)), AI Off, the
  budget, a failure or nothing to rewrite. In a chat with calming on, a block
  still waiting for its mood decision is not yet known, so it waits for that
  decision.

Native arrival and partial streaming are never enough. Readiness applies in
muted chats, with notifications off or denied, while the owner reads history
in the open chat, and to initial unread and catch-up sources that raise no
alert; delivering a sound or push is not a prerequisite. Mute, active-chat
suppression and the no-alert rule for catch-up stay as they are, and a
notification's expiry doesn't expire the dot. Readiness is latched once per
source: later edits or reprocessing don't hide a source that has not been
viewed, and it remains until it is viewed, deleted or expired. Readiness
starts no inference, retry or transcription.

**What counts as viewed.** One focused second in the open conversation
acknowledges every ready source in it
([ADR 24](0024-coherent-message-groups-and-focused-viewing.md)). Before
readiness, a current original whose end stays visible for one continuous
second also counts as viewed, and stays viewed when its rewrite finishes; a
media card needs no playback or download. Partial streams, historical
versions, row previews, hidden or background pages and sending a reply
acknowledge nothing. Edits, retries and replacement rewrites never mark a
viewed source unseen again. A new source appended to a block stays
independently unseen; an older rendering cannot acknowledge it.
Acknowledgements are bound to the account, chat and content actually shown.

**The dot and the opening position.** A conversation row shows one dot while
it holds at least one ready, unviewed incoming source or reaction event
([ADR 26](0026-mobile-message-actions-and-send-status.md)). The row's
accessible name says "new messages". There is no count, no per-message badge
and no "New messages" text; inside the conversation the one marker is
[ADR 28](0028-unread-separator-and-compact-conversation-header.md)'s Unread
messages line. Opening such a chat goes to the earliest ready unseen message
(its block in AI reading) or reaction target; otherwise it opens at the
latest message. Sources still waiting for readiness don't move the opening
target. The target is resolved from bounded local cache reads, with no extra
Telegram history fetch. After opening, deliberate history reading is kept,
the view follows new messages while at the bottom, and Scroll to latest
stays; readiness arriving in an open chat never pulls the owner away from
where they are reading.

**Compact rows.** A row shows the avatar, the name, a small mute icon when
muted, the latest message's time, one truncated preview line and the dot.
There is no type, Archived label or AI processing status on the row;
processing status stays inside the conversation. Muted and archived eligible
chats stay in the seven-day list in activity order. A draft never replaces
the preview.

**The preview line.** It shows the latest sent or received native message,
with "You: " before the owner's own, and follows the global Reading View:

- In AI reading, the latest incoming block's validated rewrite, calmed
  rewrites included. A block that stays combined, or one still pending or
  streaming, shows its original.
- In Originals, the original; outgoing messages always show the sent text.
- Per-message reading choices don't change the row. Cached results are
  reused: no summary is generated, and hidden image context never appears.
- Without usable text, a short media label (Image, Voice message, Video
  message, Video, GIF, Sticker, File, Poll, Location, Contact or Link), with a
  caption or file name on the same line when there is one. No thumbnails.
- A deleted newest message previews what it last said, with a small trash
  icon; once it is hidden or left out, the message before it gives the
  preview ([ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md)
  Q10).
- If the actual newest message isn't cached, the preview is blank rather
  than an older message presented as the latest.

The time is the newest native message's own: a short local time today, a
short day or date before that.

## Consequences

- The dot marks content that is ready to read, while Enloq keeps sending no
  read receipts. Readiness and viewing need their own durable state, so
  suppressed or failed notifications, and originals read before their rewrite
  finished, neither lose nor duplicate unseen content.
- No attention or task tracking, recipient-read ticks for incoming messages,
  new notification channel or model work is added.
- [History](../history.md#local-viewing) owns the storage, readiness and
  acknowledgement mechanics.
