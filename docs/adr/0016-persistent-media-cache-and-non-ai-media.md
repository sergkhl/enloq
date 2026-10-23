---
status: accepted
---

# Persistent media cache, browser caching and non-AI media

## Context

Without a cache, every view, play or seek downloaded a whole file again
through the paced read lane, and media the model could not analyse could fail
the rewrite of its whole block. [Media delivery](../media-delivery.md#incoming-media-cache-and-playback)
owns the mechanics.

## Decision

**Media cache.** Downloaded message media is kept in a private per-account
cache with owner-only permissions. An entry expires 30 days after its
message's date, within one 2 GiB least-recently-used limit shared by all
accounts, and is cleared on logout and on local account removal.

- **Prefetch.** Voice notes and Video Circles arriving in recent chats are
  prefetched and shared with transcription. Prefetch is an ordinary read, so
  it continues while Account AI Processing is Off.
- **Videos.** An ordinary video or video document up to 50 MiB is fetched
  whole into the cache on its first Play
  ([ADR 33](0033-inline-video-playback.md)); larger ones show their details
  and point to Telegram.
- **Other files.** Documents stream to their download or share without being
  kept. GIFs and stickers chosen for sending keep their exact bytes outside
  the LRU ([ADR 32](0032-gif-and-sticker-sending.md)), and draft images have
  their own durable storage.
- **Variants.** A poster frame and an iPhone's AAC copy of a voice note are
  cached beside the original under the same rules
  ([ADR 46](0046-the-apps-catch-up-units.md)).
- **Earlier versions.** An edit or deletion invalidates work derived from the
  current source, but bytes already cached stay with the observed earlier
  version within the same limits
  ([ADR 19](0019-streamed-rewriting-and-observed-message-history.md)). A
  missing historical file never causes a Telegram read.

**Downloads.** File parts have their own lane: 1 MiB parts, one in flight per
account, no one-second gap, backing off on `FLOOD_WAIT`. Message and sync
reads keep the extracted pacing. Playback is served with HTTP byte ranges,
which Safari requires.

**Browser caching.** Browsers may keep media privately. Each use is
revalidated with an ETag, so logout, revocation and deletion still gate what
is shown. Logout, local account removal and responses to a revoked browser
send `Clear-Site-Data: "cache"`. The hashed static bundle may be cached
long-term; JSON and document downloads are not stored. A browser's copies
fall outside the 30-day expiry until it clears them.

**Non-AI Media.** Documents, stickers, GIFs, ordinary videos, polls,
locations, contacts and other kinds show as typed cards or players
([ADR 27](0027-header-message-menu-forwarding-details-and-voice-playback.md),
ADR 32, ADR 33, ADR 46). They never fail a block: the model receives only a
placeholder with the kind, caption, file name, size or duration and poll text,
never file contents. The service keeps no phone numbers, vCard data, exact
coordinates or link-preview descriptions in a message's details. The
rendering marks what was not analysed, and a block with nothing analysable
makes no model call. Link previews are ordinary text.

**Video Circles and GIFs.** A circle shows its poster in place and plays,
with sound, only on a tap; GIFs loop silently in place. A circle's audio is
transcribed like a voice note
([ADR 39](0039-litellm-proxy-and-vps-speech.md)); no video frame reaches a
model.

## Consequences

- Correspondence media is stored on the service host, and some copies in
  browsers, in exchange for immediate playback and fewer Telegram reads.
- Everyday stickers and files never block rewriting; visible placeholders
  replace the old all-or-nothing rule.
