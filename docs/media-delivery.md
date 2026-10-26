# Media delivery and private drafts

A deliberate send carries one to ten images with one optional shared caption.
An image-only send is valid. The local per-image ceiling is 5 MiB, 4096 pixels
per dimension, 16,777,216 pixels and a 20:1 aspect ratio. JPEG, PNG and WebP
bytes must fully decode; client filenames and MIME claims are not validation.
The local caption ceiling is 1,024 UTF-16 units, further bounded by Telegram's
current `caption_length_max` before upload. Text-only sends use 4,096 UTF-16
units. Every native send keeps the account's own identity and refuses paid
delivery (`allow_paid_stars=0`, no paid flood skip); text sends also disable
link previews. Oversized drafts remain editable without splitting, truncation
or automatic attachment removal.

Draft images live in the account's private `draft-images` directory with mode
600, referenced by its `rewriting.sqlite3`. Saving, removing and choosing a
suggestion check the draft revision. A chosen suggestion and Undo change only
the text and preserve the attachment selection. An unknown operation pins its
image bytes even when the user edits the newer draft; a prepared one does so
only until housekeeping, within about a minute, refuses it because its
revision can no longer be sent.
Confirmed sends clear only their exact revision; a collector deletes images
only when no draft or unresolved operation references them. Temporary incoming
media belongs to a separate bounded lease.

Native sends persist one random ID per image before transmission. One image
uses `messages.sendMedia`; an album first uploads every image with
`messages.uploadMedia`, then issues one `messages.sendMultiMedia` request with
its caption on the first item only. There is no high-level automatic split into
multiple albums. Every request still crosses the MTProto request policy; the
aggregate upload/send operation is bounded to 180 seconds. Uploaded bytes are
associated to a chat before sending. Failure before the final request is
handed to Telegram's sender is a definitive local refusal that keeps the
images ([ADR 17](adr/0017-send-hand-off-boundary.md)). A failed image check, or
an error while preparing or uploading (including a disconnect), refuses it
outright. A hold, the send deadline, an interruption, a failed chat lookup, or a
disconnect before preparation starts or at the final request refuses it as not
sent, as does a final request answered only with a flood or slow-mode wait.
Sending the same revision again reuses the random IDs of its latest not-sent
attempt, if there is one, and otherwise gets new ones. Telegram's definitive
refusal of the final request is also refused outright and stored for that
attempt's Check delivery. Once that request may have run, missing/partial native
receipts remain unknown. Reconciliation uses native random-ID mappings, never
text matching, and never re-uploads/re-sends a possibly delivered item.

The request shapes follow Telegram's documentation:
[album grouping](https://core.telegram.org/api/files#albums-grouped-media),
[sendMultiMedia](https://core.telegram.org/method/messages.sendMultiMedia),
[sendMedia](https://core.telegram.org/method/messages.sendMedia), and
[client limits](https://core.telegram.org/api/config).
`backend/tests/test_media_delivery.py` exercises actual native request types
against an in-memory RPC peer. It does not prove a live Telegram upload or send.

## Send Operations ownership

[ADR 18](adr/0018-send-operations-ownership.md) assigns the send lifecycle to the
account's `Store.send_operations`, implemented in
[`storage/send_operations.py`](../backend/src/enloq/storage/send_operations.py).
It owns submission, claims, pacing reservations, settlement, confirmation,
receipt/refusal precedence, Retry, Cancel, recovery, expiry and attachment pins.
It works without a live Telegram connection. Under
[ADR 26](adr/0026-mobile-message-actions-and-send-status.md) a submission is
immutable: saving its exact content and clearing the submitted composer commit
together, and its random IDs never change. A chat's next runnable submission is
its oldest blocking item while that item is still queued; a dispatching, Not
sent or unknown head pauses only that chat. Pre-queue refused sends keep their
drafts and never block. Retry archives the previous attempt's hand-off ledger,
starts a fresh one and removes that attempt's stored refusal, so an earlier
answer can never settle a later attempt. Cancel requires a queued (never
claimed) or provably unsent item. Not sent submissions never expire, so their
content and order survive until a deliberate Retry or Cancel; cancelled ones
follow ordinary retention. Images stay pinned for every unconfirmed submission.
The queue worker starts a chat's task only when its head is runnable, so a
paused chat costs nothing until it is woken, and a failed pass is retried.
Conversation reads and account removal use its detached snapshots and blocker
query. Store recovery/expiry and the image collector pass their current
transaction to the owner; the latter reads draft and operation references
before deleting images in that same transaction. Both clients draw a pending
send like a sent message, with its state in the time line and the allowed
actions in its sheet ([ADR 51](adr/0051-telegram-phone-look-and-new-messages.md)).

[`application/delivery.py`](../backend/src/enloq/application/delivery.py)
coordinates submission, the service-owned queue, Retry, Cancel and Check
delivery. It retains the account/connection checks, exact native reply lookup,
waits and deadlines. One queue task per chat with accepted work dispatches in
order; draining the account cancels them and settles an interrupted send by its
evidence. Ordinary pacing is waited out; a hold over ten seconds makes the item
Not sent. Delivery then materializes known outgoing messages and collects images
in the existing order; those later effects are not one larger transaction or an
automatic repair queue. Clients receive only an operation view without local
file paths.

The contracts in
[`domain/delivery.py`](../backend/src/enloq/domain/delivery.py) keep
native descriptions and nested image references immutable. Transports report
native random-ID mappings as ReceiptBatch values and use a synchronous,
attempt-bound evidence handle. Broad calling evidence must commit before the
native client call; exposure must commit before hand-off. Failed narrowing
leaves the broader record in place, and settlement checks both the stored and
in-memory copies. The native adapter retains request construction, uploads,
identity, pacing and reconnect behavior. Its image checks reuse the same pure
bounds, digest and full decoding validator as preparation, without looking up
operation or attachment rows.

Delivery binds account-local receipt intake before the transport connects.
Direct receipts, raw updates and gap replay use it, and gap replay cannot move
its checkpoint past a failed receipt write. Intake records evidence only: it
never clears a draft or automatically reconciles an unknown send. Live send
completion and explicit Check delivery apply the evidence. A receipt takes
precedence over a refusal; any partial album receipt prevents a total refusal.
Only incomplete evidence needs bounded native catch-up. A complete stored
receipt or applicable refusal can resolve a check while disconnected.

The operations table/JSON, hand-off ledger and receipt/refusal cursor keys keep
their format. Missing `wire` differs from an empty ledger; old `wire_started`
and refusal-key fallback remain conservative. The cutover reader
(`enloq.cutover`) uses explicit compatibility entries for its target writes.
Tests in
[`test_send_operations.py`](../backend/tests/test_send_operations.py),
[`test_send_handoff.py`](../backend/tests/test_send_handoff.py) and
[`test_media_delivery.py`](../backend/tests/test_media_delivery.py) exercise
transactions, evidence failures, old records and actual native request types
against authored peers. They do not establish live delivery or owner acceptance.

### Group sends and deliberate media

[ADR 34](adr/0034-group-chats-and-daily-briefings.md) extends ordinary composing
to supported basic groups and nonforum supergroups independently of both AI
switches, thresholds and model budgets. Text, image, album, GIF and sticker sends
retain exact account/typed-peer/reply/bytes/random-ID evidence. Channel-backed
targets explicitly select the connected personal user from current Send As
choices; basic groups verify that user without channel-only flags. Fresh one-peer
native metadata checks payload rights, and captured rights/access generations
reject stale responses and changes during preparation, pacing or MTProto packing.
Every final payload retains zero Stars and no paid flood skipping. No request
changes Telegram's saved default sender. Permission/slow-mode refusal and unknown
native receipts keep their distinct recovery.

Accepted group sends capture native access revision. Confirmed loss, including a
loss/restoration cycle before handoff, holds proven unhanded work and disables
ordinary Retry; cancellation keeps accepted content and media. Possible handoffs
stay unknown rather than acquiring permission to replay. Review leaves the send
paused; deliberate Resume rechecks the exact account, original peer, saved
content/media/reply and native rights with a short-lived client-bound token. It
preserves the operation's random IDs and newer draft, while uncertain delivery
stays in Check delivery. Groups never query member readers or show group Read.
An explicitly refused native request retains independent non-delivery proof, so
later loss also places it on the access hold. A fresh attempt archives and clears
that earlier proof; it cannot make an uncertain later handoff look unsent.

Deliberate original playback/download and GIF/sticker catalog selection do not
enable AI. Fresh transfers require exact current native source/access through
awaits and file parts; source edits/deletions, migration and access changes reject
late publication. After access loss, unchanged verified cached bytes remain
readable within 30-day source expiry, with no native fetch. Validators require the
actual variant's retained bytes; cached documents use the retained-file path too.
Historical versions stay cache-only. Catalog tickets and selections bind access
revision so a pre-loss ticket cannot authorize work after restoration. There is
no ordinary group prefetch; briefing media remains inside admitted stages.
Inactive retained groups may read verified source bytes within 30 days and
pinned ready selections, while uncached transfers and new selection/Send stay
refused. Authored source tests and journeys cover these mechanics; native
group rights, uploads and sends, physical playback and ordinary use are not
established by them.

## GIF and sticker selection and delivery

[ADR 32](adr/0032-gif-and-sticker-sending.md) owns the staged Media Picker,
optional GIF caption, exact kept sticker draft and native-library boundaries.
The account's Session Owner supplies its saved GIFs, recent/favorite stickers
and lazily loaded installed packs. Metadata catalogs are durable; client/chat
selection tickets and deliberately entered GIF query/results are transient. A
search scope is opened before issuing its catalog tickets, uses monotonic
requests and expires after 30 idle minutes. Normal picker reopen retains
results. Explicit Search again renews an expired scope and replaces its
tickets; leaving the chat/account, reload or authorization loss retires it.
Empty search reads saved items and starts no bot query. Catalog refreshes do
not download the library's full media or scan correspondence history.

Selecting one item persists intent before preparing bytes. The account's
private `selected-media/` directory (700, files 600) and `selected_media` table
retain its exact source, identity, digest and verified facts independently of
picker tickets. Bounds are 50 MiB per GIF and 5 MiB per sticker. Actual bytes
must validate: GIF frames and trailer, silent H264 MP4, static WebP, bounded
TGS, or silent VP9 WebM as appropriate. Names, MIME claims and native
attributes are insufficient. Preparing or failed items remain visible;
interrupted preparation becomes a truthful failed selection requiring
deliberate Retry. Media preparation and publication recheck draft identity,
account, chat and trusted client after asynchronous work.

Selected and retained Send Operation bytes are pinned outside the disposable
2 GiB LRU. The draft retains them until sent/discarded; retained operations
hold their own pin through the operation lifetime, including confirmed or
cancelled items. Publication and reads reject altered digests, symlinks,
hardlinks and unsafe private-file metadata. A received-cache copy has a distinct
inode and source-date expiry; historical cache misses never consult these pins
or trigger a native download. Account removal clears its selected media.

Web-only GIF results are fetched by the service through bounded HTTP(S)
handling: public DNS answers, a pinned destination with original-host TLS
verification for HTTPS, at most three redirects, no downgrade and no cookies,
authorization, proxy or referrer forwarding. Client previews use private
application routes rather than external page content. Preparation retains
exact chosen bytes; only deliberate Send may upload them. Uploading a GIF can
add it to Telegram's saved list and displace its oldest entry even if the
later send fails, as ADR 32 accepts. Transfer and upload alone never prove
delivery.

Durable queue acceptance captures the selected media, optional GIF caption,
reply and stable random ID in one immutable submission. A sticker payload has
no kept text. Acceptance restores its exact kept text/Undo/state and retains
the reply with nonempty text immediately; it does not wait for native
confirmation. Removing the unsent sticker restores the same draft. With a
sticker selected, Undo and suggestion insertion are unavailable. Both media
kinds use the paid-send refusal, per-chat ordering, receipt/refusal evidence
and the ordinary-reply suggestion lifecycle. Draft actions also bind clicked
content/selected identity across delayed autosaves so another trusted client's
replacement cannot be sent or amended silently.

A native GIF or sticker goes out in the final `messages.sendMedia` as the
chosen document itself: Telegram answers `messages.uploadMedia` with an
existing document with `MEDIA_INVALID`, so nothing is associated with the chat
first. Preparation reads the first 4 KB of that document from its own data
centre, which proves its file reference without sending anything, and can
renew the same exact document once through its known source. If that
reference remains unavailable, a GIF may upload its retained exact bytes; an
unrecoverable sticker requires reselection without conversion. Telegram's
definite answer while a GIF, sticker or image is prepared is named in the Not
sent line, for example "Telegram refused this GIF (MEDIA_INVALID). Nothing was
sent."; an unknown failure still reads "Stopped before reaching Telegram". A
definitive final send-reference refusal becomes Not sent and needs deliberate
Retry. There is no automatic second final send and no resend of an uncertain
hand-off. A later retry retires the earlier refusal evidence before exposure.
A Not sent GIF holds later sends in its chat until it is retried or cancelled.

Premium-required stickers use the account's actual current entitlement, checked
during selection/preparation and again immediately before native hand-off;
cached catalog entitlement alone does not authorize sending.

The source contracts and executable guards live in
[`application/selected_media.py`](../backend/src/enloq/application/selected_media.py),
[`storage/selected_media.py`](../backend/src/enloq/storage/selected_media.py),
[`transport/selected_media.py`](../backend/src/enloq/transport/selected_media.py),
[`domain/selection_formats.py`](../backend/src/enloq/domain/selection_formats.py)
and [`transport/web_media.py`](../backend/src/enloq/transport/web_media.py).
Authored selection, catalog, transport, cache and web-transfer tests establish
those local contracts; they do not establish live catalogs or Telegram delivery.

## GIF and sticker rendering

Current received GIF/sticker representation reads are cache-only. An in-view
missing current source may deliberately prepare a preview; an observed historical
version may only reuse retained bytes. Descriptor, file and animation reads bind
the exact message revision/version and recheck access. Disposable catalog assets
share the media LRU and expire 30 days after fetching; received versions keep
their original source-date expiry. Catalog still thumbnails are validated
JPEG/PNG/WebP within 5 MiB and 1024 pixels per dimension. Both clients' pickers
ask only for these thumbnails; the full `media` variant is prepared only when
explicitly requested. Width probes remain inert.

The two documented temporary preview/verification refusals from these received
media endpoints stay on the affected item with deliberate Retry. The conversation
and draft remain available. Network failures, authentication loss, other server
errors and unknown or malformed refusal bodies keep the ordinary connection
handling. Current-source Retry may prepare missing bytes; historical Retry only
rechecks retained bytes and cannot fetch a missing version.

The web's renderer displays WebP, GIF, silent MP4/WebM and TGS in picker,
selected, pending and received surfaces. Grid previews stay still; visible chat
and selected previews may loop while focused. Hidden tabs, offscreen items,
Back, account/chat changes, obscuring views and reduced motion stop or prevent
animation. A raw GIF's reduced-motion view uses its actual first frame. Silent
loops remain separate from the one-at-a-time voice/audio/video player.
Unsupported or unavailable media keeps an accessible Preview unavailable
fallback, independently of whether exact native sending is permitted. The app
shows video stickers still ([Playback in both clients](#playback-in-both-clients)).

A video-sticker preview in a browser first checks an authored, self-origin
VP9-alpha canary, not the owner's media or browser name. Its 718-byte, 32×32,
one-frame source has no audio. A five-second attempt caps accepted streamed
bytes at 16 KiB, verifies their SHA-256, then checks an opaque green marker and
transparent corners in the actual decoded frame. A loaded-data event can arrive
before the canvas can read that frame: an entirely blank sample stays pending
and is sampled on the next animation frame within the same five-second attempt.
A blank sample never proves support or refusal. Success, cancellation and the
deadline cancel pending sampling and release the decoder. The paused probe never
claims ordinary playback. Simultaneous previews share one attempt; only conclusive
support/refusal is cached for the document. Cancellation or temporary failure
is not a permanent negative cache. Deliberate preview Retry may try again
without native send or selection mutation. A private WebM source mounts only
after positive support; refusal keeps Preview unavailable while the validated
selection can still Send. Initial visible current-source preparation may still
populate the service cache; the capability gate withholds the browser's private
WebM GET/mount. A temporary capability failure's Retry remounts the
already-ready asset without another representation/preparation request.
Historical source reads stay cache-only. Abort, the deadline and loss of the
final viewer release the fetch, decoder, canvas and object URL; stale
completion cannot mount private media. The helper is
[`webmAlphaCapability.ts`](../web/src/webmAlphaCapability.ts).

TGS keeps the unchanged gzip source while producing bounded safe animation JSON:
4 MiB decompressed, depth 64 and 100,000 nodes, with additional expanded-layer,
geometry, mask, gradient and keyframe limits. External/raster assets, fonts,
expressions, effects and unsafe geometry are refused. Ordinary masks, gradients
and repeated precompositions are supported; precise unsupported SVG features
have a truthful fallback. The lazy SVG-light renderer consumes a fresh data clone,
accepts canonical empty metadata, strips optional CSS identifiers and starts
without autoplay, expressions or external segment loads. Its ten-second
asynchronous load/initialization deadline covers fetch/import and pending
initialization; failure aborts and destroys the continuation so late output
cannot paint. A browser timer cannot preempt synchronous JSON/Lottie/SVG work;
the structural and expanded-work limits bound that processing separately.
The strict CSP remains.
Raw selected/pending bytes and animation JSON are `no-store`; verified received
binary previews retain private ETag revalidation with actual bytes required
before 304. GIF decoding has a soft between-frame deadline, while FFprobe has
a hard killed/reaped deadline; these are different resource guarantees.

## GIF and sticker delivery and proof limits

Authored selection, catalog, transport, cache, web-transfer and rendering
tests, the web journeys and the app's media-sending journey establish the
local contracts above with fake sends. They make no native Telegram or model
calls.

Live sends in the Maya test chat have proven that a GIF from search with a
caption, a saved GIF and a static sticker each read 4 KB of their document and
go out in one `messages.sendMedia`, with no `messages.uploadMedia`, and arrive
as MP4 GIFs and a WebP sticker. Still unproven live: animated and video
stickers, a GIF that has to upload its retained bytes, and a Premium sticker.

Raw-GIF motion in WebKit is unverified. The web journeys run in Chromium
only; when they also ran in WebKit, two authored multi-frame GIFs stayed on
identical compositor snapshots on plain native-image pages, headless and
headed, while Chromium captured both in motion. This does not distinguish
native image motion from snapshot behavior, and no browser-name workaround or
renderer change was justified. Physical Safari behavior, live libraries and
entitlements on the owner's accounts, phone usability and production adoption
remain the owner's ordinary-use acceptance in [TODO](plans/TODO.md).

## Incoming media cache and playback

[ADR 16](adr/0016-persistent-media-cache-and-non-ai-media.md) keeps downloaded
voice notes, audio files, video circles, images, GIFs, static/animated/video
stickers, deliberately prepared ordinary videos/video files and poster frames
in each account's private `media-cache/` directory (mode 700, files 600),
indexed in its `rewriting.sqlite3`. Entries are bound to the message revision,
expire 30 days after the message date and share one 2 GiB least-recently-used
limit across accounts. Logout and local removal delete the cache. Under
[ADR 19](adr/0019-streamed-rewriting-and-observed-message-history.md),
already-cached older revisions remain for observed-history reading after an
edit/deletion, sharing the same expiry and size limit. Historical reads never
download missing bytes. Simultaneous permitted preparations of one item share
one download, and an item edited during its download is not kept. Other
documents and unplayed video Downloads keep attachment-only streaming; cached
video Downloads reuse retained bytes without requiring playback verification.
Newly arrived incoming voice notes and circles in recent private chats are
prefetched, including while Account AI is Off; history pages, recent-history
scans, groups and own messages are not prefetched. Image preparation and
transcription, including video-circle audio, read the same cached bytes. A
circle, GIF or eligible ordinary-video poster is the largest thumbnail
Telegram stores with the video document, fetched as its own cached variant; no
video is decoded locally for it. Its 5 MiB bound applies to the selected
thumbnail, independently of the parent video's size; the full video retains
50 MiB.

[ADR 33](adr/0033-inline-video-playback.md) owns ordinary-video and video-file
playback with first-Play caching.
`POST /api/accounts/{account}/chats/{peer}/originals/{id}/media/prepare-video`
with the exact `revision` and optional observed `version` grants ordinary
video's complete-file transfer. Current media must be locally observed and
unchanged; historical preparation only checks retained bytes. Full-file GET,
range and 304 requests require that exact revision and existing bytes, and
never recover a missing video. Poster requests grant only a thumbnail transfer.
Preparation and response completion recheck the source, expiry, chat, account
authorization and trusted client after asynchronous work.

Native videos and document MIME/name candidates remain their original source
kind and content identity. Inline serving requires bounded structural MP4/WebM
checks and restricted local ffprobe video-track/packet evidence; a declared MIME,
filename, generic header or older cached sniff label is insufficient. Verification
runs within 10 seconds, a 1 MiB probe setting, one second analysis, 64 packets,
64 KiB stdout and 8 KiB stderr, allowing only local-file protocols and the chosen
demuxer with external MOV references disabled. This confirms a container, not
client codec support. Source diagnostics and packet contents are not returned.

Complete bytes remain available as an opaque Download after a known format
rejection or temporary verifier failure. A verifier failure stays retryable;
deliberate preparation rechecks the retained bytes without another Telegram
transfer. Positive and definitive negative outcomes are reused only in the
current process for the exact source/revision and unchanged private file
identity; restarting revalidates old entries. This adds no video transcription,
frame interpretation, AI-result invalidation or provider call.

In a conversation a tapped video opens in the chat's Media Carousel
([ADR 51](adr/0051-telegram-phone-look-and-new-messages.md)) and plays there;
the player exposes the full-file source only after exact preparation succeeds.
Native controls supply play/pause, seeking, sound and fullscreen at 1x.
Pending Play owns the same revocable slot as active voice/audio/circle
playback. Cancel, another player, hidden tabs, used-source changes and
departure revoke that intent before late output can play. Delayed sound-policy
rejection returns to Play; ambiguous codec/HTTP failures keep explicit Retry,
whereas a verified unsupported preparation keeps Download/Telegram fallback.

Paused position stays in the open conversation's memory, keyed by exact
account/chat/source/revision/version. Deliberate reading/version/selection
changes pause playback and preserve that same-chat position; version navigation
pins Originals before releasing a held automatic AI rendering. Leaving,
reloading, authentication loss, page departure or changed media clears progress;
ended replay starts at zero. Working players use the message sheet's Download
or Share, with an exact-source chooser and retained names. Declared files over
50 MiB show Telegram fallback and are omitted from that chooser. Player
gestures keep their ordinary behavior rather than opening a message sheet or
selecting a message.

File parts use a separate lane from message and sync reads: 1 MiB
`upload.getFile` parts through the installed SDK iterator, one part in flight per
account, no one-second gap, and the flood/transient backoff. Waiting parts are
served playback first, then downloads, AI preparation and prefetch. Message,
dialog and history reads keep their one-second pacing.

Media responses support byte ranges (206), which Safari requires. Cached media is
`Cache-Control: private, no-cache` with a strong ETag derived from the message
revision, so a browser copy is revalidated on every use and a 304 needs no
Telegram read. Ordinary video and historical media require retained bytes before
304; ordinary video also needs actual-container proof. JSON, attachment downloads
and other documents are `no-store`; the hashed static bundle is cached for a
year as immutable. Logout, local removal, revoking the current browser and any
request from a revoked browser send `Clear-Site-Data: "cache"`; browser copies
otherwise sit outside the app's expiry, as the owner accepts. Browser caching
mainly helps images, stickers, posters and avatars: Safari does not keep
`<audio>`/`<video>` loads in its disk cache, Firefox bypasses its cache for
sub-range requests and Chrome keeps 206 responses only with strong validators,
so playback speed comes from the service cache. WebKit and Firefox ignore
`immutable` on `http://127.0.0.1`, and Chrome's `Clear-Site-Data: "cache"`
support is partial. `backend/tests/test_media_cache.py` and
`backend/tests/test_transport.py` cover these rules with authored fixtures.

### Playback in both clients

The Enloq app and the web play received media alike
([ADR 46](adr/0046-the-apps-catch-up-units.md)): voice notes with Telegram's
waveform, tap or drag to seek and 1×, 1.5× or 2× remembered on each phone or
browser; one recording at a time, so a circle or video pauses a voice note and
the other way round; a finished voice note playing on into the chat's next
one; circles in place with a tap to pause; videos with their poster, opening
in the Media Carousel where Play prepares them as above, with the platform's
controls and full screen, and their position kept while the chat is open;
GIFs looping silently on screen; static stickers as images and TGS stickers
through the service's Lottie JSON. Video (WebM) stickers stay still on the
phones and animate in a browser only after its VP9-alpha probe succeeds.
Motion stops off screen, in a background tab or app, and with reduced motion.
Leaving the chat or changing the reading view stops playback; on a phone a
voice note keeps playing with the screen locked or another app open until
then, with the lock screen's controls (expo-audio's background playback and
its Android media service). The long-press sheet offers Copy transcript for
voice notes and circles, and Share (the app) or Download (the web) for
photos, voice notes, circles, videos, documents and music files; documents and
music files are cards that open Share or Download. Show transcript starts a
missing transcript, the owner's own notes included.

iOS reads neither Ogg nor Opus, so an iPhone asks for a voice note's AAC
copy (`…/originals/{id}/media?revision=…&variant=aac`), as does a browser
that can't play Ogg Opus. The service makes it once from the cached source
with the image's ffmpeg (mono AAC at 64 kb/s in MP4 with the index first;
only the sniffed container's demuxer, local files only, no tags or chapters,
at most two at a time, two minutes each), keeps it in the Media Cache beside
the source under the same revision, 30-day expiry and size limit, and serves
it like the source (byte ranges, a strong ETag, `private, no-cache`). Only a
current voice note has one: Download, observed versions and other audio are
refused. After group access is lost a copy is still made from the cached
source, never from a new transfer. A failed conversion leaves nothing behind
and is retried on the next request.
`backend/tests/test_media_cache.py` and
`backend/tests/test_group_media_access.py` cover the copy;
`mobile/__tests__/media.test.tsx`, `web/journeys/media.spec.ts` and the app's
`mobile/maestro/flows/media.yaml` cover the players with the fixture's
authored media (`/api/test/voice-player`, `/api/test/video` with its VP9
`playable` case, `/api/test/media-kinds`). The journeys' Chromium has no
H.264, so the web's playable video and circle are VP9.

## Video playback delivery and proof limits

Authored coverage lives in `backend/tests/test_video_formats.py`,
`backend/tests/test_video_delivery.py`, `scripts/video_fixture.py`,
`web/journeys/media.spec.ts`, `mobile/__tests__/media.test.tsx` and the app's
`mobile/maestro/flows/media.yaml`.
The backend tests cover first-Play ranges, replay and revalidation from the
cache, unsupported bytes kept as Download, exact history versions, access
changes and failed transfers; the web journey prepares an authored VP9 video
on Play and checks that it plays in the carousel. None of them makes provider
calls, native Telegram requests or uses real correspondence, and lifecycle and
policy injections are separate from real decoding.

The web journeys run in Chromium without H.264, so H.264 and WebKit/Safari
playback in a browser are unverified by them. They do not establish physical
phone or Safari controls, native-source playback, ordinary-use usability or
production adoption; the owner keeps those observations in
[development acceptance](plans/TODO.md).

## Observed native history

The account store captures the previous native text/caption, link destinations
and existing transcript when an observed edit or deletion replaces it. This
includes both directions, excludes generated image context/rewrite history, and
never promises an intermediate revision missed while offline. All versions
expire 30 days after the original message date; changes do not extend expiry.
An edited message pages through its locally captured versions; a deleted one
shows the version captured at its deletion
([history](history.md#deleted-messages-and-hidden-edits)). Current source
derivatives and cited model memory still invalidate. Context and memory
preparation read current messages only, never `message_versions`.

Historical media uses authenticated account/chat/version routes and only bytes
already in the private revision-keyed cache. Eviction leaves a readable text
version with unavailable media; it cannot trigger a recovery download or a 304
that revives an evicted historical file. Logout hides text history and clears
media; account removal deletes the store. Store upgrades carry existing
versions and cache keys forward without new Telegram reads. Tests:
`test_observed_history.py`, `test_media_cache.py` and
`web/journeys/details.spec.ts`.
