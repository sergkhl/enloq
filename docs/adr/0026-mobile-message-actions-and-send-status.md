---
status: accepted
---

# Mobile message actions and asynchronous send status

## Context

Enloq is used mostly on phones, so a message's actions need a touch-first
home that keeps bubbles uncluttered. Sending must feel immediate while
Telegram delivery continues in the service, and the status shown must stay
truthful: a pending bubble is local presentation, not proof of a Telegram
message. A correspondent's reaction can also change what a conversation needs,
without being a new message. [ADR 18](0018-send-operations-ownership.md)'s
single Send Operations owner and native correlation, and
[ADR 17](0017-send-hand-off-boundary.md)'s distinction between proof that
nothing was sent and an uncertain hand-off, stay the base.

## Decision

**The message sheet.** A long-press opens a message's sheet in both clients;
on the web, right-click and the menu key do too, and on the phones so does
holding a play button, a photo or a file card. A tap on a pending send's red
"!" opens its sheet. Ordinary taps keep their links, playback and the media
carousel, and swiping a message from right to left replies to it
([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q5). The sheet holds
only what applies to that message: in a private chat a reaction row at the
top, then Reply and Copy, and, as their ADRs provide, Copy transcript, Share
on the phones or Download on the web
([ADR 27](0027-header-message-menu-forwarding-details-and-voice-playback.md)),
Show AI text or Show original and Retry
([ADR 46](0046-the-apps-catch-up-units.md)), Select
([ADR 31](0031-message-deletion-selection-and-group-ai-controls.md)) and Hide
([ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md)). Copy
takes the shown wording: the AI text in AI reading, the exact original or
version in Originals; Copy transcript takes a voice note's transcript. Copy
never includes metadata or files.

**Earlier and deleted versions.** An earlier version's sheet offers only Copy
of that exact version, beside its read-only version and cached-media
controls. A deleted message keeps Copy and Hide, with Reply and reactions off.
Reply, reactions and current-block processing act only on the current
original.

**No manual repeat processing.** A completed incoming result can't be
rewritten again, a voice note can't be re-transcribed on request, and there is
no first-time Rewrite now (ADR 31 Q23). First-time processing,
transcription, automatic handling of changed evidence, original playback, raw
transcripts and failure Retry stay. Show transcript may start a missing
transcript (ADR 51 Q9). Response Suggestions support automatic triggers and
manual generation; the manual button replaces outgoing Rewrite (ADR 25).

**Immediate local sending.** Send immediately shows the submitted text, images
and reply target as a Pending Send, drawn exactly as a sent message
(ADR 51 Q3). Once the service has safely saved that exact content, the
composer clears and the owner can write and navigate while upload and
Telegram delivery continue; if the save fails, the draft stays. The operation
is bound to its account, chat and content, and later status never clears or
overwrites a newer draft. Confirmation reconciles the local operation with the
native message without a duplicate display or send.

**Ordered sending and recovery.** Submissions are immutable and queue per
account and chat, one native send at a time in that chat. A failed or unknown
send pauses its chat's queue; other chats go on within the account-wide
connection and flood limits. A saved submission Telegram can't send becomes
Not sent with a deliberate Retry under the native identity and evidence
rules; there is no long-lived automatic offline queue. Delivery unknown offers
Check delivery and never resends. The exact content and Copy stay, and are
never restored over a newer draft. Closing the browser or the app doesn't stop
service-owned delivery; after a restart the service reconciles durable
evidence, definitely unsent work waits for Retry and an uncertain hand-off
stays unknown. Cancel send is offered only while durable evidence proves the
item was never handed off or definitely wasn't sent; the item stays as a
dimmed Cancelled message with Copy, stops blocking the queue, and later
accepted items continue in order. An uncertain hand-off can't be cancelled or
bypassed. The sheet carries Retry, Check delivery, Cancel send, Review for a
group send and Copy (ADR 51 Q4).

**Truthful status.** In the time line: a clock while Sending, one tick once
Telegram's cloud accepts the message, two once Telegram reports it Read; in
groups Sent, never Read ([ADR 34](0034-group-chats-and-daily-briefings.md)).
Not sent and Delivery unknown add a red "!" and their word, Send held keeps
the clock with "Held", and Cancelled is dimmed. Status shows on outgoing
messages sent from Enloq or any other Telegram client; there are no exact read
times. Telegram has no delivered-to-device state; its
[check-mark definition](https://telegram.org/faq#q-what-do-the-check-marks-mean)
owns the native meaning.

**Incoming viewing stays local.** [ADR 23](0023-local-unseen-messages-and-conversation-previews.md)
and [ADR 24](0024-coherent-message-groups-and-focused-viewing.md)'s local
viewing is unchanged: reading the recipient's status sends no incoming read
acknowledgement.

**Native reactions.** In private chats the sheet's reaction row offers the
owner's own reaction, six from the account's palette and + for the rest;
choosing the owner's own removes it, within Telegram's limits for the account
and chat. An AI message with several sources asks which message the reaction
is for; chips keep their source. Chips under a message show each emoji, ⭐ for
a paid reaction and ◇ for a custom one, the count above one and the owner's
own marked. There is no custom-emoji picker and no paid reaction. In groups
reactions are display-only. Which emojis trigger an assessment is fixed below,
independently of the palette.

**Reaction Assessment.** A correspondent's confirmed 👍, 🤝, 👌 or 👎 in a
private chat, including on the owner's own messages, triggers a Reaction
Assessment: one decision request for the emotion and whether suggestions
would be useful ([ADR 30](0030-decider-emotion-classification.md),
[ADR 49](0049-useful-response-suggestions.md)). The owner's own reactions,
from any Telegram client, and every other emoji are only displayed. The
assessment reads the reaction, its exact target and bounded recent
conversation. It is its own evidence with its own currentness, never a
fabricated incoming message, a source edit or a regeneration of an answered
block, and it keeps earlier rewritten wording and repeats no transcription or
image preparation. Confirmed qualifying changes invalidate dependent results;
stable net changes coalesce over 30 quiet seconds, unchanged outcomes are
reused, and a burst that returns to the last processed state makes no call.
Partial or unknown snapshots don't prove removal, and native actor,
completeness and addition time stay distinct from local observation time.

**Scope and freshness.** Automatic assessments cover only cached native
targets no older than seven days in eligible recent chats. Reactions present
when the feature started form a display-only baseline; later confirmed net
changes catch up against durable state, opening or restarting never resamples
unchanged reactions, no older history is scanned, and unknown actor or time
evidence never makes a fresh event. The newest eligible incoming
communication or qualifying reaction, by native source or addition time,
becomes the current source of emotion and suggestions. A reaction's results
expire 12 hours after its native addition; a removal doesn't renew the
deadline. New or changed evidence withdraws affected results, older late work
can't replace newer evidence, and removing the current reaction doesn't revive
an older estimate.

**Suggestions and answering.** A reaction alone is not a reply. A later
native outgoing message (text, images, GIFs or stickers,
[ADR 32](0032-gif-and-sticker-sending.md) Q23) answers a reaction's
suggestions without clearing emotion; the owner's own reactions don't answer
them, although an owner's reaction on the latest incoming block answers that
block's (ADR 49 Q12). Once a Pending Send is durably accepted, current options
are suspended: Telegram's confirmation withdraws them, a definite non-send may
restore still-current cached options without a call, and unknown delivery
keeps them suspended. Wording already inserted into the draft never changes.

**The actionable-reaction dot.** A correspondent's new or replaced 👍, 🤝, 👌
or 👎 on a cached target no older than seven days, in an eligible recent
private chat, puts a dot on the conversation row. It needs its own unseen
identity, since its target may be outgoing or already viewed. Baseline
reactions and repeated unchanged observations create no dot. After 30 quiet
seconds the reaction becomes ready once its assessment validates, or when the
raw reaction stands because AI is Off, the budget is reached, the route is
unavailable or the assessment failed; it never waits for suggestions. Mute
and push settings don't suppress the dot, reaction-only changes send no push
or sound, and readiness starts no work. Opening goes to the earliest ready
unseen message or reaction target, in its AI block when it has one, and one
focused second acknowledges every ready message and reaction event in the
chat, offscreen ones included, shared across trusted clients and without
Telegram read state. Viewing the target earlier doesn't acknowledge a later
reaction. The dot outlives the 12-hour AI expiry until the event is viewed,
the reaction is withdrawn, its target deleted or its cached source expired;
withdrawing removes only that reaction's contribution and creates no new dot.

### Questions

- **Q1:** a quiet message sheet for phones; now the long-press sheet above.
- **Q2:** immediate Pending Send; the composer clears once the service has
  saved the exact content.
- **Q3:** Sending, Sent and Read icons with Not sent and Delivery unknown;
  drawn in the time line (ADR 51 Q3).
- **Q4:** incoming viewing stays local, with no read acknowledgement.
- **Q5:** ordinary emoji reactions within Telegram's limits; no custom picker
  or paid reactions; groups display only.
- **Q6:** a reaction on a several-source AI message asks which message; AI
  Reply targets the newest source.
- **Q7:** some reactions trigger reprocessing, settled by Q12-Q15.
- **Q8:** a reaction alone leaves Response Suggestions available.
- **Q9:** no Rewrite again; failure Retry stays. The first-time Rewrite now
  it kept was removed by ADR 31 Q23.
- **Q10:** retired with manual Re-transcribe.
- **Q11:** phone-first actions with Copy in the sheet.
- **Q12:** only the correspondent's reactions trigger an assessment.
- **Q13:** the fixed trigger set 👍, 🤝, 👌 and 👎.
- **Q14:** a separate Reaction Assessment of emotion and usefulness, now on
  the decision model (ADR 30 Q3).
- **Q15:** invalidation, 30-second coalescing and reuse as above.
- **Q16:** replaced: a long-press, right-click or the menu key opens the
  sheet; bubbles carry no button (ADR 27 Q1).
- **Q17:** Copy takes the shown wording or a transcript, without metadata or
  files.
- **Q18:** an ordered per-chat queue, one native send at a time.
- **Q19:** a failed local save keeps the draft; Telegram failures become Not
  sent with Retry.
- **Q20:** exact content and Copy stay; Delivery unknown offers Check
  delivery.
- **Q21:** delivery continues without the client; restart reconciliation as
  above.
- **Q22:** seven-day cached targets and a display-only baseline.
- **Q23:** the newest communication is current; a reaction's 12-hour expiry.
- **Q24:** a dot for actionable reactions, without push or sound.
- **Q25:** replaced: Adaptive tone was removed
  ([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)).
- **Q26:** suggestions suspend on a durably accepted send.
- **Q27:** Cancel send only for proven unsent items.
- **Q28:** earlier and deleted versions keep Copy only for that version.
- **Q29:** a reaction's readiness after 30 quiet seconds.
- **Q30:** opening and focused viewing include reaction targets.
- **Q31:** which reaction changes make a dot, and when it leaves.

## Consequences

- Bubbles stay uncluttered and the composer stays responsive; a saved local
  send and native evidence stay distinct, and so do an observed emoji and its
  inferred meaning.
- Queued-send recovery after a real interruption or flood wait has not yet
  been observed in ordinary use; that remains the owner's check.
- The [history](../history.md#local-viewing), operations, media-delivery and
  inference runbooks own the mechanics.
