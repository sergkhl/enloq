---
status: accepted
---

# Video playback in messages

## Context

Ordinary Telegram videos and video files sent as documents could only be
downloaded. The owner wants to watch them in Enloq without repeated Telegram
reads while seeking, and without turning video into AI input.

## Decision

**Scope and viewer (Q1-Q2).** Ordinary Telegram videos and eligible video
documents get a player, for incoming and outgoing messages in both reading
views. Tapping a video opens it in the chat's media carousel and plays it
there ([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q10-Q11), with
full screen available. Video Circles, GIFs and stickers stay inline. Sending
videos from the composer is outside this decision.

**Starting and coordination (Q3-Q4).** A poster shows first and playback
starts only on a deliberate tap, with sound. Videos never start by
themselves, loop or continue to the next one. They share one playback slot
with voice notes, audio files and circles. In a browser a hidden tab pauses
the video and a return needs Play. Leaving the conversation, switching
accounts or closing the page stops it; there is no mini-player.

**Loading and retention (Q5-Q6).** The first Play fetches the complete file
into the private media cache and waits with a loading state; later plays and
seeks read cached bytes. There is no background prefetch of ordinary videos.
Each file is capped at 50 MiB, in one shared 2 GiB LRU across accounts, and
expires 30 days after the original message's date. Larger items show safe
metadata and point the owner to Telegram. Eligible videos are thus kept,
unlike other documents, which keep
[ADR 16](0016-persistent-media-cache-and-non-ai-media.md)'s delivery. Cached
earlier versions keep [ADR 19](0019-streamed-rewriting-and-observed-message-history.md)'s
read-only access and expiry; missing historical bytes never cause a Telegram
read, and old video bytes are not back-filled.

Preparing and serving are distinct: only Play or Retry starts the first full
transfer, while posters, metadata, revalidation and range requests cannot
fetch a missing file. Share or Download before Play stays available and
reuses cached bytes. Reads are bound to the requested account, chat, message
and source revision or observed version, and cached history must exist
before a 304. A thumbnail keeps the 5 MiB poster bound, independent of its
video's 50 MiB bound.

**AI and failures (Q7-Q8).** Ordinary video contents stay Non-AI Media;
captions and safe metadata keep their existing contract. Playback adds no
transcription, frame interpretation, provider disclosure or model call and
does not depend on Account AI Processing. Circle audio keeps its
transcription. An unsupported or unavailable item shows a truthful
item-specific status, Share or Download when possible and a Telegram
fallback, without conversion or failure of the surrounding message or
composer.

**Controls and eligibility (Q9-Q10).** The platform's own player controls:
play and pause, seek and time, mute and volume, and full screen, by touch and
keyboard. Videos start at 1×; they do not take the voice-note speed and get
no custom control bar, app speed or volume memory, picture-in-picture or
casting buttons. A muted video still holds the shared playback slot;
silently looping GIFs do not. Native video sources and documents marked
`video/*` or named .mp4, .m4v, .mov or .webm are candidates, but names and
declared types prove nothing: only verified MP4 and WebM containers are
served for playback, and the platform's codec support decides the rest.
Anything else keeps the Share or Download fallback without conversion.
Eligibility is presentation only; the document's classification, identity and
AI results stay as they were.

**Posters and position (Q11-Q12).** An available Telegram thumbnail is
fetched when the item comes into view and kept under the private poster
bounds; otherwise a neutral Play tile shows duration or filename. The full
video is never fetched to make a poster. A paused position lasts only while
the chat stays open, bound to that exact source revision; leaving, reloading
or changed media resets it, and a finished video restarts on Play. There is
no watch history, watched mark or Telegram read receipt.

**Loading intent (Q13).** First loading shows Loading video with a spinner,
no percentage, Cancel, and Retry loading after a transient failure. Cancel
revokes pending playback; a download already started may finish into the
cache, but its late completion cannot start playback. A hidden tab,
navigation, a change of the source or starting other media also revokes the
intent. If the platform rejects delayed sound, the player returns to Play and
waits for a fresh tap. A failed load is never retried automatically.

**Reading changes and sharing (Q14-Q15).** An automatic rendering change waits
while a video plays; a deliberate reading-view, version or selection change
pauses it and never resumes it automatically. Videos and video files are in
the long-press sheet's Share (Download on the web) and the carousel's Share,
with retained filenames. Historical items need already-cached bytes.

**Currentness and eviction (Q16).** An edit or deletion of the source stops
playback or pending work; cached older revisions stay read-only until their
original expiry. If eviction interrupts access, the item shows unavailable
with Retry loading. Only a deliberate action may fetch a current source
again; range requests and refreshes never authorize a recovery transfer.
Expiry, account and trusted-client gates stay, and no older-history scan or
media back-fill is added.

**Checks (Q17).** Backend tests, Jest and the phone-size journeys of both
clients cover play and seek, cache reuse, shared playback, cancellation,
lifecycle, eligibility, size limits and format failure.

[Media delivery](../media-delivery.md#video-playback-delivery-and-proof-limits)
owns the mechanics and proof limits.

## Consequences

- The first play waits for the whole file, in exchange for bounded,
  authenticated range delivery and no repeated Telegram reads while seeking.
- Platform controls keep player work small but differ between platforms;
  uncommon codecs fall back to Share or Download.
- Position is not remembered, so no new history store exists.
- Simulated checks, native Telegram observation, physical-device playback
  and the owner's ordinary use are separate evidence.

## Questions

- Q1: ordinary videos and video documents, both directions and both views.
- Q2: a video plays in the chat's media carousel (ADR 51 Q11), with full
  screen.
- Q3: poster first; play only on a tap, with sound; no autoplay, loop or
  continuation.
- Q4: one shared playback slot; leaving or switching stops it; no
  mini-player.
- Q5: the whole file on first Play into the private cache; no prefetch.
- Q6: 50 MiB per file, a shared 2 GiB LRU, 30-day source-date expiry.
- Q7: Non-AI Media; no transcription or frame analysis.
- Q8: truthful item-specific failures with Share or Download and a Telegram
  fallback.
- Q9: platform controls; 1× start; no custom bar or speed memory.
- Q10: only verified MP4 and WebM play; names and declared types are hints.
- Q11: Telegram thumbnails as posters, else a neutral tile; never decode the
  video for a poster.
- Q12: position lasts only while the chat is open.
- Q13: Loading video with Cancel and Retry loading; late completion never
  starts playback.
- Q14: automatic rendering changes wait; deliberate changes pause.
- Q15: Share (Download on the web) in the sheet and the carousel.
- Q16: source changes stop playback; eviction shows Retry loading; no
  implicit recovery transfer.
- Q17: backend tests, Jest and both clients' phone-size journeys.
