---
status: accepted
---

# Rebuild around message rewriting without legacy data or qualification obligations

## Context

The first application tracked attention, topics and commitments and drafted
answers. Inferring what a conversation required proved less dependable than
narrow transformations of what was actually written, and its data and
qualification machinery made every change heavy. The owner asked for a
simpler rebuild and allowed retiring all existing application data and
qualification.

## Decision

**A communication translator.** Enloq is a small standalone Telegram client
whose model work reduces the effort of reading and writing correspondence.
Its core is two flows:

- **Incoming.** Consecutive incoming messages read as one Incoming Block
  ([ADR 24](0024-coherent-message-groups-and-focused-viewing.md)). A block of
  text shows its originals together in one bubble
  ([ADR 54](0054-incoming-text-combined-not-rewritten.md)). A block with an
  image, a voice note or a Video Circle is rewritten into clear text, with
  speech made readable and image meaning kept as hidden model context
  ([ADR 29](0029-mimo-route-and-structured-incoming-rewriting.md)), and a
  frustrated or angry block is calmed
  ([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)).
  Rewriting keeps substance, uncertainty and language under the Faithfulness
  Rules ([ADR 15](0015-owner-controlled-processing-memory-and-prompts.md)), in
  the sender's voice, and the originals stay one tap away.
- **Outgoing.** Rewrite improves the owner's own draft in Neutral,
  Professional or Friendly casual. It never invents an answer to an empty
  composer and never sends.

**What it does not do.** No attention or commitment tracking, topic
classification or "needs reply" marks. Two later decisions are narrow
exceptions: Response Suggestions offer short editable replies
([ADR 25](0025-emotion-response-suggestions.md),
[ADR 49](0049-useful-response-suggestions.md),
[ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md)), and
may draw on what a person and the owner care about
([ADR 55](0055-person-and-account-memory-and-traits.md)), the only
profile-based advice; and groups get Daily Briefings
([ADR 34](0034-group-chats-and-daily-briefings.md)).

**Scope.**

- **Accounts.** Several per member, switched in the account sheet
  ([ADR 10](0010-multiple-personal-accounts.md)).
- **Recent chats.** Private human chats whose latest sent or received message
  is within seven days, muted and archived ones included. Groups have their
  own tab (ADR 34) and Saved Messages its own, without AI
  ([ADR 50](0050-icon-tabs-saved-messages-and-one-saved-build.md)). Bots,
  service chats, broadcast channels and Secret Chats are outside automatic
  processing.
- **Any private chat.** New Message and chat links open any of the account's
  private chats to read and write; such a chat gets no AI until a message
  makes it recent ([ADR 51](0051-telegram-phone-look-and-new-messages.md)).
- **Background work.** New and changed blocks and unread catch-up within the
  seven days, after 30 quiet seconds. Opening, refreshing and restarting reuse
  unchanged results and start nothing.
- **Context.** Up to 20 preceding same-chat messages within seven days, plus
  the one message a block directly replies to, even when older
  ([ADR 41](0041-reply-quotes.md) excludes replies to another chat or a
  story). No history scan and no reply chains. A deliberate action may reach
  further back in the cache (ADR 15).
- **Retention.** A 30-day local cache of messages and results; drafts and
  their attachments are kept until sent or discarded. Older originals are
  read from Telegram only on demand, and the cache never widens what
  background work may read.

**Composer.** Send and Rewrite are separate, always-visible buttons; the tone
choice sits with Rewrite and is remembered once for all of a member's chats
and clients ([ADR 48](0048-members-on-the-shared-service.md)). There is no
Send/Rewrite mode preference. Rewrite starts on a click, streams into the
draft ([ADR 19](0019-streamed-rewriting-and-observed-message-history.md)) and
groups compactly with its status, Stop and Undo; Send transmits exactly the
draft shown. Small thumbnails sit above one optional shared caption; rewriting
keeps the attachments, and an image-only send invents no caption. Enter adds a
new line ([ADR 44](0044-mobile-only-web-in-the-apps-design.md)). A send is
unquoted unless the owner picks a reply target. No sparkles icon appears
anywhere. The authored [composer sketch](../design/compact-composer-mobile-dark.png)
shows the grouping.

**Implementation.** One backend, the web and the Enloq app, SQLite state with
a persisted work queue, the exact model routes (ADR 29,
[ADR 30](0030-decider-emotion-classification.md),
[ADR 39](0039-litellm-proxy-and-vps-speech.md)) and no extension framework.
Features are added gradually and each is tested on its own.

## Consequences

- The rebuild kept no message caches, drafts, generated results, feedback or
  qualification records from the earlier application, and the app has no
  qualification subsystem; finite checks follow
  [ADR 4](0004-acceptance-quality-feedback.md). Earlier failed or unknown
  outcomes were never turned into passes.
- Until the owner declares production, wiping application data (stores,
  caches, memory, drafts) is an acceptable migration or recovery path, with no
  backup required. Telegram sessions and correspondence, credentials and
  spending records stay protected.
- Narrow transformations are dependable but leave deciding what to answer to
  the owner, apart from the suggestion exceptions above.
