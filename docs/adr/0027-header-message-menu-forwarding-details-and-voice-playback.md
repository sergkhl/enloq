---
status: accepted
---

# Message sheet, forwarded-message details and voice playback

## Context

Three things about reading a message needed settling. Where a message's
actions and its own reading choice live without cluttering every bubble. How a
forwarded message shows where it came from, so another person's words never
read as the sender's own. And how voice notes play, close to Telegram's own
player.

## Decision

**No button on the bubble (Q1).** Bubbles carry no action button. A message's
actions live in its long-press sheet
([ADR 26](0026-mobile-message-actions-and-send-status.md)), which right-click
and the menu key also open on the web
([ADR 44](0044-mobile-only-web-in-the-apps-design.md) Q4). Pending sends have
no inline actions either; their sheet carries them
([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q4).

**The reading choice in the sheet (Q2).** A rewritten block's sheet offers
Show original, and its originals' sheets Show AI text, with the robot icon and
never sparkles. The choice is local to that block, lasts until the global
Reading View changes or the chat is reopened, and starts no generation
([ADR 24](0024-coherent-message-groups-and-focused-viewing.md)). The global
Reading View and each account's AI processing are separate controls.

**The source count (Q3).** No other AI marker is added: the AI header always
shows the source count, including **1 message**. Originals carry no count.

**Forwarded and imported lines (Q4, Q5, Q17, Q18).** A forwarded message's
non-clickable line adds the original date and time from Telegram's forward
header and, for a channel post, the author signature: "Forwarded from News
Channel (Author) · Mar 4, 2024 · 10:04 PM", in the configured timezone, with
the year only outside the current year. A message Telegram marks as imported
from another app shows "Imported · <original date>" and counts as its sender's
own message, not as forwarded content. Names come only from the forward header
or entities returned by a native read; no profile, forward chain or history is
resolved. AI bubbles show the same lines under their header, one per forwarded
origin in order of first appearance ("Forwarded from Anna · 5 messages · Mar
4, 2024"), with a date range across days, the time only for a single message,
and nameless sources grouped as "Forwarded messages". Copy never includes
these lines.

**Filling in missing details (Q16).** Confirmed forwards that lack a source
name, original date, signature or imported state are repaired by exact reads
of opened or loaded cached messages only, in batches, without history scans,
media downloads or AI work. A successful read that still lacks a name is
final; a failed read is retried later. When a forward arrives live without a
name, one exact read of that message runs within its 30 quiet seconds so new
work can use the name; a failed read delays nothing. Filling in a missing
detail never invalidates validated rewrites, emotion, suggestions, reaction
assessments or work in flight; a conflicting known value still does.

**Attributed rewriting (Q6).** Incoming rewriting, and the roles that already
receive forwarding attribution, receive the original date, signature and
imported state too. The shipped incoming guidance keeps forwarded content
explicitly attributed in the rewritten text, for example "Forwarded from News
Channel (Mar 4): …", rather than in the sender's own voice. Existing results
are neither invalidated nor regenerated, and no extra call follows.

**The voice-note player (Q7-Q9).** A voice note has a round play and pause
button and Telegram's own waveform, which fills with the accent colour as it
plays; a tap or drag seeks, and on the web so do the arrow keys. The time shows
the length while idle and the elapsed time while playing, with truthful
loading and unavailable states, and the player grows with the note's length
between fixed bounds. The waveform is Telegram's voice-note metadata, captured
at intake for display only and never sent to a model; a note without one shows
flat bars, with no repair read or local computation. Capturing it is a
metadata-only update that keeps existing results. Music and other audio files
are cards with their name and size that download on the web and open Share on
the phones ([ADR 46](0046-the-apps-catch-up-units.md)). Video circles play in
place in their circle.

**Playback rules (Q10-Q12).** One voice note, circle or video plays at a time.
A 1×, 1.5× or 2× speed control appears once a voice note has started and is
remembered by each browser and phone. When a voice note ends, the next message
plays if it is also a voice note, within one AI block too; otherwise playback
stops. Playback never scrolls the conversation. It stops on leaving the chat,
switching account, changing the Reading View or closing the page; on the
phones it keeps playing with the screen locked or another app open until then.
There is no mini-player.

**Transcripts (Q13).** Voice notes and circles have a transcript button at the
end of the player. It expands the raw transcript below the player with a
machine-generated label, and when none exists yet it starts one with progress
(ADR 51 Q9). The owner's own voice notes and circles are transcribed too,
raw and never rewritten (ADR 51 Q8). The sheet's Copy transcript copies it.

**No listening state (Q14).** No listened or unplayed marks are shown, and
playing never sends Telegram a listen receipt.

**Share and Download (Q15, Q19).** The sheet offers Share on the phones and
Download on the web for photos, voice notes, circles, videos, documents and
music files ([ADR 33](0033-inline-video-playback.md),
[ADR 46](0046-the-apps-catch-up-units.md)). One file acts directly; with
several, as in an AI block, each item is named by its kind and time. Files are
named by kind and the message's date and time, like Telegram Desktop. An
earlier version's sheet offers only Copy, and a deleted message's none of
these; their cached media still shows in the bubble, since history never
causes a Telegram read.

## Consequences

- Bubbles stay uncluttered; the count and the sheet's reading item keep AI
  provenance discoverable.
- Attributed forwards make AI text slightly longer, but someone else's words
  no longer read as the sender's own.
- Telegram's captured waveform is exact and cheap; legacy and some third-party
  notes lack it and show flat bars until they expire. Telethon 1.44's
  `decode_waveform` misreads the final level, so the app decodes the 5-bit
  levels itself.
- A live forward that arrives without a name has not yet been observed in
  ordinary use; that remains the owner's check.
- The only Telegram reads added are the bounded exact forward reads. Model,
  account, target, content-eligibility, session-owner, privacy, local-viewing
  and budget boundaries are unchanged.
