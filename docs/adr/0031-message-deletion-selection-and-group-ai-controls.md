---
status: accepted
---

# Message deletion, selection and group AI controls

## Context

Enloq shows each private chat's native originals and, for blocks the AI
rewrites, a combined AI message. The owner needs to delete messages for real,
one or several at a time, and to control one message group's AI reading
without changing the global Reading View. Telegram deletes private messages
for both participants by message ID alone, so the app must establish each
target's account and chat itself and must report partial or uncertain
outcomes truthfully.

## Decision

**Deletion scope (Q1-Q4).** Delete performs native Telegram deletion for both
participants in a private chat; every request uses `revoke=True`. There is no
Delete for me and no local hiding of current messages (a message already
deleted can be hidden, below). Current ordinary incoming and outgoing native
originals are eligible, including messages sent from another client and every
supported text and attachment type, within the recent-chat and 30-day cache
scope. Historical versions, deleted messages and service messages are not.
In groups only the owner's verified own originals can be deleted, for
everyone ([ADR 34](0034-group-chats-and-daily-briefings.md) Q50). Local
pending, failed, cancelled or unknown sends are not native originals: they
keep [ADR 26](0026-mobile-message-actions-and-send-status.md)'s Cancel send
and Check delivery and their authored content.

Select and Delete are offered only in Originals view. Delete is the last item
of an original's long-press sheet (right-click or the menu key on the web),
with Select directly above it. AI wording has no
native deletion identity.

**Deleted messages (Q5).** Native deletion follows
[ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md): a message
deleted elsewhere keeps what it last said, with a trash icon, and its
observed versions, transcripts and cached media keep their original 30-day
expiry; a message whose deletion went through Enloq is no longer shown. A
deleted message can be hidden for good from its sheet or a selection.
Deletion extends no retention, fetches no older versions, downloads no
archived media and makes nothing eligible for new model work.

**One sheet per original (Q7, Q16, Q17).** Every original, outgoing ones
included, has its own long-press sheet. Its native actions (Reply, reactions,
Share or Download, Select and Delete) target exactly that original without a
chooser. Copy takes the wording shown: the viewed original, observed version
or transcript, without metadata or files. An earlier or
deleted version's sheet is read-only, keeping Copy and access to cached
media and versions. In AI view a block has one sheet, and its native actions
keep the exact-source rules: an AI message with several sources asks which
message.

**Selection (Q6, Q8, Q19).** Select starts Message Selection with that
original, in Originals view; leaving Originals view ends it. While it runs,
a tap on an eligible original adds or removes it, links and interactive media
keep their own behavior, and a bar shows the count with Delete and Cancel;
Delete is off at zero. A selection holds one kind, set by its first pick:
current originals to delete, or deleted messages to hide (ADR 52 Q11). A
message showing an earlier version is not selectable. The composer stays
usable. Delete and Hide are the only bulk actions.

**Membership and lifetime (Q9, Q10, Q14).** A selection is the explicitly
picked loaded originals of one account and chat, across groups and loaded
pages. More than 100 may be selected; deletion runs as sequential chunks of
at most 100. There is no Select all, range selection, automatic selection of
unloaded messages or whole-chat clearing. Refreshes and live updates keep the
selection, and new arrivals stay unpicked. An observed external deletion
removes that message; an observed edit requires reviewing the updated
original before unstarted dispatch; neither edits nor regrouping add
messages. Completed deletion stays completed. Cancel (or Escape), an accepted
deletion, leaving the chat or account, or a reload clears the selection.
Selection and an accepted Deletion Operation have separate lifetimes.

**Confirmation and execution (Q11-Q13).** Every deletion, single or bulk, is
confirmed in a review showing the account, chat, exact count and previews;
its action is Delete for both participants (Delete for everyone in a group),
and Cancel returns to the selection. The review captures the exact account,
chat, native IDs and reviewed revisions; account ownership, eligibility and
currentness are checked again before each unstarted chunk reaches Telegram.
The RPC carries no chat, so a displayed chat or a client library's chat
argument cannot replace those checks.

Dispatch follows confirmation at once, with no Undo delay and no automatic
recreation or resending. Originals stay visible with Deleting until confirmed.
The service records the captured intent and each chunk's outcome, applies
confirmed successes, stops later chunks on failure or uncertainty and shows
completed and unresolved counts. A missing acknowledgement is never treated
as success or repeated automatically. Accepted work continues if the client
closes. After a service restart, recorded work is reconciled without new
delete requests. Check result makes bounded exact reads only; Retry remaining
needs a fresh review of still-present unresolved originals and never adds
completed or absent ones. Account removal and cache expiry keep truthful
unresolved evidence and reply holds instead of inventing an outcome.

**Native evidence limits.** Telegram documents both-participant deletion of
sent and received private messages; its method takes IDs and `revoke`, with
no peer field ([Telegram FAQ](https://telegram.org/faq#q-can-i-delete-my-messages),
[messages.deleteMessages](https://core.telegram.org/method/messages.deleteMessages)).
Telethon's helper chunks at 100 IDs and warns that its chat argument does not
validate private-message ownership
([Telethon deletion API](https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.messages.MessageMethods.delete_messages));
the 100-message chunk is an execution rule, not a selection cap or a claimed
server limit. An acknowledged result shows that Telegram accepted the
for-both request; it has update counters, no per-ID verdict and no echoed
audience. Neither it nor absence from the connected account observes the
correspondent's state. A lost acknowledgement followed by local absence stays
a for-both outcome unconfirmed, and a cache miss is not native absence.
Deletion updates do not name the deleting actor. Each chunk is accounted
separately; one success or error says nothing about the whole run
([messages.affectedMessages](https://core.telegram.org/constructor/messages.affectedMessages),
[messages.getMessages](https://core.telegram.org/method/messages.getMessages),
[updateDeleteMessages](https://core.telegram.org/constructor/updateDeleteMessages)).

**Reply targets and dependent work (Q15).** A draft or queued reply keeps its
text, images and reply reference. While its target's deletion is pending or
unresolved, Send to that target is blocked and queued replies not yet handed
to Telegram are held, checked again at the final hand-off. After confirmed
deletion the composer shows Reply target deleted until the owner removes the
link, and an unhanded queued reply is refused without erasing its content. A
reply is never sent silently without its reference or out of queue order. A
send that may already have reached Telegram keeps ADR 26's delivery rules.

Confirmed or observed deletion goes through the normal ingestion pipeline:
history is kept as above; affected rewrites, emotion, reaction assessments,
suggestions and in-flight work are invalidated; memory entries citing the
message are removed while owner entries stay; unseen and preview state
updates. Removing an outgoing boundary can regroup incoming messages, and any
resulting processing keeps the normal quiet, route and budget gates. Deleting
an outgoing answer does not revive old suggestions. Late streaming output or
media downloads cannot resurrect deleted messages or overwrite authored text.

**A block's AI controls (Q18, Q20-Q22).** There is no separate AI button. A
block's long-press sheet holds its AI controls
([ADR 46](0046-the-apps-catch-up-units.md) Q9): Show AI text or Show original
for that block alone, marked with the robot icon, and Retry. The choice is
independent of the global Reading View, starts no generation and lasts while
the chat stays open and the Reading View is unchanged. Show AI text needs usable AI text. Retry appears for a
failed or unknown block without current AI text, is off while the account's
AI is Off, and starts exactly one attempt under the existing account,
currentness, target and budget gates. No sparkles anywhere.

**No Rewrite now (Q23).** There is no per-block first-time processing
control. Automatic initial, new or changed and unread catch-up processing,
the chat-level Rewrite earlier messages action remain. The private composer
offers manual suggestions instead of outgoing Rewrite (ADR 25).
Retry never becomes first-time processing or regeneration of a completed
result. Automatic suggestion triggers and explicit manual generation follow
ADR 25; neither is an incoming-result regeneration control.

[Operations](../operations.md#native-deletion-operations) owns the
mechanics and proof limits.

## Consequences

- Native deletion is consequential and may partially succeed. Explicit
  confirmation, durable exact membership and deliberate recovery make it
  reviewable without promising undo, whole-run atomicity or a receipt of the
  correspondent's state.
- Group reading controls live with the block they act on, so the header keeps
  only the global Reading View toggle.
- Simulated tests, native Telegram observation and the owner's ordinary use
  are separate evidence.

## Questions

- Q1: native deletion for both participants (`revoke=True`); no Delete for me.
- Q2: current ordinary incoming and outgoing originals of any supported type,
  within the recent-chat and 30-day scope.
- Q3: Delete only in Originals view, with Select for several.
- Q4: Delete is the sheet's last item, Select directly above it.
- Q5: deleted messages keep what they last said with a trash icon; Enloq's own
  deletions leave at once; Hide removes a deleted one for good (ADR 52 Q7-Q12).
- Q6: Select starts in Originals view; leaving that view ends the selection.
- Q7: every original has its own sheet.
- Q8: taps add and remove; a bar with the count, Delete and Cancel; the
  composer stays usable.
- Q9: explicit picks across loaded pages; over 100 allowed, sent in chunks of
  at most 100.
- Q10: refreshes keep the selection; Cancel, an accepted deletion, leaving
  or a reload clears it.
- Q11: always confirm with account, chat, count and previews.
- Q12: dispatch at once, no Undo delay.
- Q13: per-chunk durable outcomes, stop on failure or uncertainty, Check
  result and reviewed Retry remaining.
- Q14: external deletions leave the selection; edits need review; nothing is
  added automatically.
- Q15: replies to a target being deleted are held, then refused without losing
  content.
- Q16: one sheet per original; one per block in AI view.
- Q17: native actions target exactly that original; earlier and deleted
  versions are read-only.
- Q18: a block's AI controls sit in its long-press sheet (ADR 46 Q9).
- Q19: a message showing an earlier version is not selectable.
- Q20: no AI button; the robot-marked item in the block's sheet replaces it.
- Q21: Show AI text or Show original per block, independent of the Reading
  View; Retry starts one gated attempt.
- Q22: Show AI text needs usable AI text; Retry only for a failed or unknown
  block, off while AI is Off.
- Q23: no per-block Rewrite now; automatic work, Rewrite earlier messages and
  composer Rewrite remain.
