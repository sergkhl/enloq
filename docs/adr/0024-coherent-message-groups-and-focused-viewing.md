---
status: accepted
---

# Coherent message groups and focused viewing

## Context

A correspondent often writes one thought across several messages, with pauses
between them. Splitting those messages by time gaps fragmented the AI reading,
and stitching earlier rewrites together gave text that read as several
answers. Reading one AI message must also show every attachment of its
sources. Clearing the unseen dot ([ADR 23](0023-local-unseen-messages-and-conversation-previews.md))
by requiring each message's end to scroll into view left dots on content the
owner had plainly seen. And an app reopened in a new tab should return to the
account last used.

## Decision

**One Incoming Block per turn and day.** Consecutive incoming native messages
form one Incoming Block until the owner's next outgoing message, a service
message or a calendar-day boundary; a pause alone never divides it. Days and
every displayed date and time use the application's configured timezone
(`ENLOQ_TIMEZONE`, default Asia/Ho_Chi_Minh) across accounts, browsers and
phones. A device's own timezone neither regroups sources nor causes model
work, and there is no timezone preference.

**One coherent result.** A block that is rewritten (one with an image, a voice
note or a video circle, or a calmed block,
[ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)) is
rewritten from its original sources into one coherent text. Text that is not
calmed shows as its originals together in one bubble
([ADR 54](0054-incoming-text-combined-not-rewritten.md)). Concatenating
earlier AI outputs never counts as a result, and a result for one set of
sources is never shown as the validated result for another. New or changed
sources wait the 30 quiet seconds, then the whole block is redone, with
[ADR 19](0019-streamed-rewriting-and-observed-message-history.md)'s
streaming, final validation, revision guards and failure recovery. Retry uses
the same complete target. Reply from a block, in its sheet or by swiping,
targets its newest source; each original keeps its own Reply and versions.

**Eligibility and context.** Every source still needs the seven-day
background or 30-day explicit eligibility. Context stays bounded: up to 20
preceding recent same-chat messages and the one message the block directly
replies to. No reply chains are followed and no history is scanned to make a
large block understandable; an external reference that isn't supplied stays
uncertain. Notification bursts keep their own 30 quiet seconds and the
readiness and original fallback rules.

**Changed membership.** When a block's membership changes, for example when
a deleted outgoing message joins two blocks, its combined result is
invalidated and the block is processed again only under normal admission:
blocks that already had AI processing or qualify as new or unread, within the
account, role, target, content, concurrency and budget gates. Read,
never-processed blocks and blocks with a source older than seven days stay
click-only within the 30-day cache, and a changed block may show its
originals until a replacement validates. Regrouping is not a new arrival:
it notifies nothing again, fetches no Telegram history and resets no viewing,
memory or spending. Unchanged blocks keep their valid results, and opening,
refreshing or restarting redoes nothing. Native identities and
source-dependent invalidation stay authoritative.

**Bounded rewriting.** A block that can't be rewritten within the current
limits stays one block: its originals and attachments stay visible with the
truthful failure reason. It is never silently truncated or split, the limits
aren't raised, and there is no recursive summarization. The native-text,
prepared-packet, Supporting Context, image and output bounds are owned by the
source configuration. Typed Non-AI Media and bounded image-preparation
placeholders apply as before; a retained file is no promise that the model
read it.

**Every attachment, once.** Each reading view shows every actual source
attachment once, even when the block spans more than the loaded page of
messages, using bounded reads of the local cache and never a Telegram history
scan. Generated text that mentions a file is never mistaken for an
attachment. File names, downloads, playback, transcripts, unavailable media
and native versions stay reachable. An AI bubble shows one Reply Quote for
each message outside it that its sources answer
([ADR 41](0041-reply-quotes.md)).

**Reading a block.** In AI reading a block is one bubble headed "AI · N
messages · time", the count shown even for 1 message; a combined text block
drops the "AI". In Originals each native message is its own bubble, in
[ADR 51](0051-telegram-phone-look-and-new-messages.md)'s Message Runs, with
its time, edit mark and versions. A block's long-press sheet offers Show
original, and its originals' sheets Show AI text, with the robot icon and
never sparkles. The choice holds for that block until the global Reading View
changes or the chat is reopened, and starts no generation. The global Reading
View is the header's robot toggle
([ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md) Q13).

**The neutral face for uncertainty.** An `unknown` estimate (mixed emotion or
too little evidence) shows 😐, named "Mixed or uncertain"; it is never
presented as a recognized Neutral. With no current estimate (pending,
expired, changed, invalid or unavailable) the header shows no emoji, and the
Correspondent Profile ([ADR 46](0046-the-apps-catch-up-units.md)) says why.
No model call is added for this.

**Remembered account.** Each browser and each phone remembers the account it
last opened successfully and starts there; tabs already open keep their own
choice. Opening a notification selects its exact account and chat under the
draft guard and, once that succeeds, becomes the remembered account; a failed
or cancelled navigation changes nothing. If the remembered account is no
longer authorized, the first authorized account opens, or sign-in when there
is none. No last chat is remembered, and no Telegram authorization is stored
in the client.

**Focused conversation viewing.** After one continuous second with the
conversation open, visible, active and focused, every currently ready
incoming source in it is acknowledged, offscreen ones included, and sources
that become ready while it stays focused qualify the same way. A background
tab or app, the phone's chat list and an open dialog or sheet don't qualify.
Scrolling to each source's end is no longer needed. Readiness and viewing
stay separate: native arrival and partial streams make nothing ready, viewing
before readiness still counts, a viewed source is never marked unseen again,
and replacement rewrites, regrouping or changed membership never erase
earlier viewing. No Telegram read receipt is sent, and catch-up, mute and
notification rules are unchanged.

**Binding and recovery.** An acknowledgement covers only the account, chat
and ready sources of the observation that qualified; stale work cannot
acknowledge a later source or another conversation. A failed acknowledgement
is retried a bounded number of times while the conversation stays focused,
without reopening it, and an older chat-list response can never replace newer
state and restore a cleared dot.

**Opening.** Opening goes to the earliest ready unseen source or its block,
otherwise to the latest message, and that target is resolved before the
focused acknowledgement clears it. Acknowledgements and readiness never move
the owner away from a deliberate reading position; following the latest
message and Scroll to latest stay.

## Consequences

- A long burst reads as one message. Larger blocks repeat more input and can
  hit the existing limits; the budget and the original fallback bound that.
- The dot tracks content that was ready while the conversation had the
  owner's attention, not proof that every message's end was reached.
- [History](../history.md#incoming-groups-and-the-cached-transition) owns
  the grouping and regrouping mechanics, and
  [history](../history.md#local-viewing) the viewing mechanics.
