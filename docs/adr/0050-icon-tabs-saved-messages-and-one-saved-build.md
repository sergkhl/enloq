---
status: accepted
---

# Icon tabs, Saved Messages and one Saved Build

## Context

[ADR 34](0034-group-chats-and-daily-briefings.md)'s Private/Groups tabs were
a bordered switch on their own row under the account header, which the web
copies from the app ([ADR 44](0044-mobile-only-web-in-the-apps-design.md)).
Each account's own Saved Messages was left out of intake, the lists and
automatic analysis (ADR 2). Under
[ADR 43](0043-installable-android-builds-and-phone-setup.md) Q11 every Saved
Build was a new message the owner had to delete by hand.

Telegram lets an account edit its own Saved Messages with no time limit, and
`messages.editMessage` replaces a message's file and caption; an edited
message keeps its place and doesn't raise Saved Messages in the chat list.
Telegram's own Saved Messages tags need Telegram Premium, and Telegram never
lets a forwarded message be edited.

## Decision

**The tabs join the header, as icons (Q1).** The switch sits in the account
header, between the account name and the pencil's place
([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q19, Q20), about
26 dp tall. Its options are icons only, each keeping its name for screen
readers. The name drops from 22 to 18 sp and ends in "…" when long, and chat
rows keep their size. The app opens on Private and remembers each account's
tab while it runs. Not adopted: a slimmer switch on its own row, and an
avatar-only account button.

**A Saved tab (Q2).** A third tab shows the account's own Saved Messages
itself, as Telegram does: one timeline under the header, newest at the
bottom, with Load earlier. Not adopted: a Saved row at the top of Private, or
a header button.

**No AI in Saved (Q3).** Nothing in Saved goes to a model for now: no
rewrites, voice transcripts, emotion, suggestions, memory or Rewrite. Its
messages are the account's own, so they bring no push or unseen dot.

**Every account (Q4).** Every signed-in account has its own Saved tab,
members' accounts included. A member's own send there is their request;
Enloq still posts nothing to a member's Saved Messages on its own
([ADR 48](0048-members-on-the-shared-service.md) Q3).

**One Saved Build (Q5).** The service keeps one Saved Build message in the
Owner Account's Saved Messages. Its first post is pinned once, without a
notification, and each later publish replaces its file and caption in place.
It shows "edited", stays where it was, and the pinned bar opens it in one tap
however much follows it. If the owner unpins it, it stays unpinned; if it is
deleted, Telegram answers the edit with `MESSAGE_ID_INVALID` and the next
publish posts and pins a new one. The old build stays until Telegram confirms
the new one, so a failed publish changes nothing. Not adopted: editing
without the pin, which lets later codes and notes bury the build, and posting
each build anew and deleting the previous one.

**The service deletes nothing in Saved Messages (Q6).** The owner had already
deleted the older builds, so no clean-up was needed.

**Codes and notes stay (Q7).** Only the build's message changes. Pairing
codes, "was paired" notes and everything else in Saved Messages stay as they
are.

**Q8:** delivery scope; no lasting rule.

**Tab order (Q9).** Private, Groups, Saved. Not adopted: Saved first.

**Tab icons (Q10).** A person for Private, two people for Groups and a
bookmark for Saved (Ionicons `person`, `people` and `bookmark` in the app,
Lucide `User`, `Users` and `Bookmark` on the web), outlined, with the chosen
one filled on the accent. Not adopted: chat-bubble icons.

**No notes list (Q11-Q16).** The owner: "skip tag list saved messages, just
show saved messages in a separate tab". There are no tags, notes list, All
notes row or note editing. Saved content follows the chats' rules: 30 days in
Enloq, older messages through Load earlier.

**Writing in Saved (Q17).** The tab has the chat composer without Rewrite:
text, images, GIFs and stickers, sent to the account itself through the same
queue as a chat's, with Not sent, Retry and Cancel. The message menu offers
Copy, Reply, Select and Delete, and the media actions. There are no
reactions: Telegram treats them in Saved Messages as its Premium tags. Not
adopted: a tab without a composer.

**Deletions.** The Saved tab takes deletions made outside Enloq, as a private
chat does, and a deleted note leaves it
([ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md) Q9).

## Consequences

- Each account's own chat enters intake for its Saved tab, outside the Private
  list and the seven-day Recent Chat rule. ADR 2's exclusion from automatic
  analysis holds.
- The Owner Account's pairing codes, "was paired" notes and pinned Saved Build
  show in its Saved tab, which only the owner's trusted clients open.
- The host-only Saved Messages path gains edit and pin for its own build
  message. It still takes no peer and deletes nothing. This amends ADR 43
  Q11.
- The iPhone gets the tabs with its next build on the Mac.
