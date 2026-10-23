---
status: accepted
---

# Deleted messages, hiding them, and the header's AI toggle

## Context

Messages sent through Enloq showed an edited pencil and extra versions they
never had; [ADR 19](0019-streamed-rewriting-and-observed-message-history.md)
owns that fix (Q1-Q5 below). A deleted message showed "Deleted message",
though Enloq had captured what it last said, and the owner could not put
deleted messages away. The AI | Originals bar took a row of its own; the
owner wanted it in the header as an icon-only toggle.

## Decision

**Q1-Q5: owned by ADR 19.** Telegram's first copy of a sent message replaces
the record built from the send receipt, with no version or edited mark
(Q1); an edit Telegram marks `edit_hide` sets no edited mark (Q2); new sends
take Telegram's date (Q3); store schema 34 repaired the records already
wrong in every chat (Q4); Q5 was delivery scope.

**Hidden edits (Q6).** An edit Telegram marks `edit_hide` keeps an earlier
version only when its wording or a link target changed; a change in media
details or other metadata replaces the stored message quietly.

**A deleted message keeps what it last said (Q7).** The version captured when
Enloq saw the deletion is what its bubble shows, with a trash icon in the
footer where the pencil goes, in both reading views; its rewrite was already
withdrawn, so the AI view shows the original. Earlier edits stay one step
back with the Originals arrows; the capture itself is no step. Its media,
and a voice note's transcript, come from Enloq's cache only, like any earlier
version; without a cached copy it reads "Photo · no longer available", since
Telegram no longer serves it. Copy works; Reply and reactions are off. A
message deleted before Enloq saw its content, and a deleted service notice,
are not shown at all. "Deleted message" never appears. Not adopted: also
greying or striking the text.

**Deletions made in Enloq leave at once (Q8).** A message in a Deletion
Operation chunk that reached Telegram (dispatching, acknowledged or
uncertain) is not shown once deleted. Telegram does not say who made any
other deletion, so those show with the trash.

**Groups as private chats; Saved Messages drops them (Q9, "for now").**
Groups follow Q7, although most of their deletions are moderation. Deleted
notes leave Saved Messages, where only the owner deletes.

**Previews and quotes (Q10).** A chat whose newest message was deleted is
previewed by what it last said, with a small trash icon, never by its
rewrite; once that message is hidden or left out, the message before it
gives the preview. An uncached newest message still previews nothing. A Reply
Quote of a deleted message shows its captured content with the trash, hidden
or not, since it is the reply's context; without captured content it is an
unknown target.

**Hide (Q11).** A deleted message's sheet has Hide. Select works for several:
a selection holds one kind, set by its first pick, and a selection of deleted
messages has Hide (an eye with a line through it) in place of Delete. There is
no confirmation. The service keeps hidden messages per account and message
(`hidden_messages`, `POST …/chats/{peer}/hidden`, deleted messages only), so
both clients agree; a row expires with its message.

**Hiding is final (Q12).** There is no unhide.

**The Reading View as one robot (Q13).** The AI | Originals bar leaves both clients for
one icon-only toggle at the header's right end: after the AI off tag in
private chats, before ⋮ in groups. A robot, pressed for AI and unpressed for
Originals, named "AI view" with its pressed state for screen readers. The
choice stays global, remembered and independent of processing. Saved Messages
has no toggle. The "Copied" note sits under the header.

**Q14:** superseded by Q13 (which view the icon shows fell away).

**No sparkles (Q15).** The sheet's Show original / Show AI text item uses the
robot, as ADR 13 and ADR 20 require.

**Both clients (Q16).** Every change here ships in the app and the web.

**Older sends take Telegram's date (Q17).** Store schema 35 gives a message
sent before schema 34, still dated by the moment Send was pressed, Telegram's
date from its native revision while it is unedited, its captured versions
too. Own-message deletion in groups compares that date with Telegram's.
Telegram dates in whole seconds, so schema 36 also takes a Telegram date up
to five seconds before the press.

**An outgoing flag alone is never an edit (Q18).** Telegram's update stream
(`updates.getDifference`, live updates) reports the owner's own notes in
Saved Messages with `out` unset while `messages.getHistory` and `getMessages`
set it. Intake treats a message in Saved Messages whose sender is the account
itself as outgoing, and a change of the outgoing flag alone is metadata.
Schema 35 restored the flipped notes and removed versions whose content their
successor repeats, outgoing flag aside, with the pencil nothing else shows.

## Consequences

- Deleted messages stay readable, with their media from the cache, until the
  owner hides them or their 30 days end.
- This amends ADR 19,
  [ADR 31](0031-message-deletion-selection-and-group-ai-controls.md),
  [ADR 44](0044-mobile-only-web-in-the-apps-design.md),
  [ADR 46](0046-the-apps-catch-up-units.md) and
  [ADR 50](0050-icon-tabs-saved-messages-and-one-saved-build.md) where they
  describe deleted messages, selection and the reading control.
