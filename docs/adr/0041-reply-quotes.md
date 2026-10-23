---
status: accepted
---

# Reply quotes

## Context

Every native message stores the number of the message it replies to, but the
message stream did not show it: only the composer named a reply target, by
number, and pending sends showed a bare message number. A reply was hard to
recognise, and its sender unknown. The existing message cache already holds
most replied-to messages.

## Decision

**A quote inside the reply (Q1).** Every reply, incoming or outgoing, shows a
Reply Quote above its own content: a tinted box with an accent bar, as in
Telegram, naming the replied-to message's sender and showing how it starts. It
appears even when the replied-to message sits directly above. A reply does not
start a new bubble or AI group:
[ADR 24](0024-coherent-message-groups-and-focused-viewing.md)'s group
boundaries stay.

**Sender line (Q2).** The name follows the message-header rule: You for the
owner's messages, the contact in a private chat, the author with any channel
signature in a group. The replied-to message's date follows the name when it
was sent on an earlier calendar day than the reply, in the application's
timezone ("You · Oct 12"). The message number is not shown.

**Body (Q3).** One line, cut with "…": the replied-to message's original
wording in its current version, never AI text. A message without text shows
an icon and its media label, such as "Image", "Voice message · 0:12", "Video
message", "Sticker" or a file name; a caption follows its label ("Image ·
caption…"). There are no thumbnails. A deleted replied-to message shows what
it last said with a trash icon
([ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md) Q10), hidden
or not; without captured content it is an unknown target (Q6).

**Composer and sends (Q4).** The composer's reply target ("Reply to Maya" with
the same one-line body, keeping its ✕), Pending Sends and the send-resume
review show the same quote, in both clients.

**Jumping (Q5).** Tapping a quote scrolls to the replied-to message and briefly
highlights it (in AI view, the bubble that holds it), loading earlier messages
the way scrolling up does. A quote with nothing to show cannot be tapped.
Scroll to latest brings the owner back.

**Messages that are not cached (Q6).** The replied-to message comes from the
message cache, which keeps every synced message for 30 days after its own
date. In a private chat, when it opens, the replied-to messages of its loaded
replies that are not cached are read from Telegram in one batched read by
number. This is the same bounded exact read the composer's reply target and
[ADR 27](0027-header-message-menu-forwarding-details-and-voice-playback.md)'s
forwarded-details repair make; it starts no AI work. Messages from the last 30
days join the cache, so the read happens once; older ones are read again on
later opens. Group chats use only cached evidence. A replied-to message that
stays unknown shows "Reply to an earlier message", with no name or text.

**Quoted fragments (Q7).** When a correspondent replies to a selected part of a
message, Telegram's reply header carries that fragment. Intake captures it,
and the quote shows the fragment as its body. Rewrite inputs do not change;
giving the fragment to the model would be a separate decision.

**Replies to another chat or a story (Q8).** For a reply to a message in
another chat, intake captures what Telegram's reply header supplies (the
original sender, date and any quoted text), and the quote shows it, for
example "Maya · in another chat" with the quoted text or "Message". The app
never looks that number up in this chat, so such a reply has no same-chat
target: it is not the direct reply in Supporting Context, and the block's
rewrite proceeds without it. A reply to a story shows "Reply to a story".

**Surfaces (Q9).** The same rules cover every chat view in both clients:
private chats, groups wherever originals show (including sources a briefing
citation reveals) and Saved Messages. Both clients take their wording from
`shared/replyQuote.ts`.

**Q10:** implementation scope; no lasting rule.

**Merged AI bubbles (Q11).** A merged AI bubble, or a combined text block
([ADR 54](0054-incoming-text-combined-not-rewritten.md)), shows its quotes
under its header, one per replied-to message outside the bubble, in the order
its replies appear; replies to the same message share one quote. A reply to a
message inside the same bubble gets none, because the merged text already
contains it. The rewrite's wording does not change.

**Group messages out of sight (Q12).** In a group, a quote shows normally even
when its message is hidden, including a hidden bot's name and text. Tapping
brings a hidden or folded message into view the way a briefing citation does:
its day switches to Originals, or its "N new messages" fold opens, with the
message highlighted and a way back. Only that post is unhidden; Hide Bots and
the reading view stay as they are.

**Small cases (Q13).**

- A deleted reply keeps its quote with what it last said (ADR 52 Q7).
- During Message Selection, tapping a quote selects its message instead of
  jumping.
- Copy leaves quotes out, like other message details.
- Chat-list previews and notifications do not mark replies.
- Replies captured before reply details existed: in private chats the Q6 read
  rereads them once, which corrects a reply to another chat; in groups they
  keep their earlier reading until they leave the 30-day cache.

The direct-reply exception of [ADR 13](0013-clean-slate-rewriting-rebuild.md)
and ADR 24 holds for same-chat replies; replies to another chat or a story
were never same-chat messages, so it does not apply to them. No model call or
Telegram send is added.

## Mechanics

`reply_to` keeps Telegram's number, `reply_target()` names the same-chat
message, `reply_read` marks captures with the reply details and `learn_reply`
fills them in from a reread without touching content. `POST
…/originals/replies` resolves quotes; the
[history runbook](../history.md#reply-parents-and-senders) owns its mechanics.
The web jumps within the conversation, loading at most 40 earlier pages, and a
group reveals through the citation path or shows one hidden post. The app
outlines a jumped-to row for 1.5 s; to reach a far row its list moves toward
the row's estimated offset before retrying, at most five times, a handler the
Unread line's opening also uses.

## Consequences

- A quote on every reply costs two lines of height, even when the replied-to
  message is directly above, in exchange for Telegram's familiar cue and one
  consistent rule.
- Keeping groups intact preserves the merged AI reading and avoids paying
  again for regrouped rewrites.
- Reading uncached messages costs at most one batched Telegram request when a
  private chat opens, and only while such replies are loaded.
- The iPhone shows quotes from its next build on the Mac.
