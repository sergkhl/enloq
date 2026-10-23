# Telegram sync and original history

How each account's Telegram client keeps the local conversation cache current.
[Operations](operations.md) owns what the owner sees, [inference](inference.md)
the model bounds, [media delivery](media-delivery.md) file parts and sends, and
[source reuse](extraction.md) the origin of the request policy.

## Intake

This runbook covers each account's private chats with people and its own Saved
Messages. Group Chats, basic groups and ordinary supergroups, have their own
intake ([ADR 34](adr/0034-group-chats-and-daily-briefings.md),
[operations](operations.md#group-native-intake)). Bots, Telegram's service
notifications, channels and forums are skipped both at dialog discovery and
when a live update arrives. Each account's own Saved Messages is admitted as
kind `saved` for its Saved tab
([ADR 50](adr/0050-icon-tabs-saved-messages-and-one-saved-build.md)), whatever
its activity: its newest pages within the 30-day content rule sync like a
recent chat's, live updates reach it, and Load earlier reads older ones.
Nothing in it is a target, so it takes no AI work, unseen state, alert or
prefetch. Each signed-in account has one app-owned session, held under its
owner lock, and one client. Load originals, exact reads and sends reuse that
client; no request opens another. New, edited and deleted messages are
ingested as their live updates arrive.

Each account's sync loop starts a new cycle 30 seconds after the previous one
ends. A cycle runs, in order:

1. dialog discovery: up to 100 dialogs per cycle, continuing from the previous
   page until the list is complete, then starting again from the newest. It
   refreshes titles, mute, archive and photo metadata and mirrors Telegram's
   read position;
2. gap catch-up through `updates.getDifference`: at most five slices per cycle,
   saving the recovery state after each. A backlog that needs more continues in
   the next cycle, and history pages still run meanwhile. The first run only
   records Telegram's current state, so nothing before it is recovered this way;
3. one history page for each recent chat whose seven-day catch-up is not yet
   complete. Messages newer than the chat's read position become unread
   catch-up targets; older ones are stored as context only. Until a chat's
   first page is stored (a new chat, every chat after a gap restart, or a first
   page that failed) its current read position is fetched with
   `messages.getPeerDialogs` before that page, up to 100 chats per request. A
   chat Telegram cannot resolve waits for a later cycle without holding back the
   others, and a chat Telegram answers without a dialog has nothing to catch
   up.

Discovery and catch-up each have a 120-second limit, and each history page has
its own. The steps fail independently: a chat whose page fails is tried again
next cycle without holding back the chats after it. A failed discovery, or a
failed read-position fetch, defers the pages that depend on it to a later cycle,
because pages choose targets by read position. Among step failures, only a lost
connection, a flood hold, an exhausted recovery allowance, a timeout, a server
error or an authorization error ends a cycle early, because every later request
would fail the same way; a lost authorization stops the account instead. Manage
accounts shows *Sync incomplete; retrying bounded recovery* while the latest
cycle had a failed step, a catch-up backlog, a chat still waiting for its read
position or, after a gap restart, pages deferred to the next cycle, and
*Connected* once a cycle completes. Ordinary paging of a long dialog list or of
a chat's seven-day catch-up does not count.

When Telegram reports a gap as too long to replay, the sync loop follows
Telegram's update guide: it takes the latest update state and, in the same store
transaction, clears every chat's coverage floor and seven-day catch-up progress
and restarts discovery from the newest dialog. History pages resume in the next
cycle whose discovery succeeds; since every chat's catch-up starts over, each
chat's read position is fetched first, and the gap's messages return through the
ordinary seven-day catch-up of recent chats: unread targets above the read
position, context at or below it. Older messages come back only when Load
originals reads Telegram. Deletions and send receipts inside the gap are not
recovered; an unknown send stays unknown and is never resent. While catch-up
cannot reach Telegram, **Check delivery** says so and leaves the send unknown;
it never restarts recovery itself.

A revoked or duplicated authorization, a different identity or a bot identity
stops the account and asks for a new login, whether found at connect, by the
five-minute recheck during discovery (Telethon turns a 401 error during that
recheck into a missing identity) or by Telethon's own update loop, which
disconnects for good when Telegram refuses it as not logged in or with a broken
authorization key; the next cycle then stops the account. Sends and live
intake stop as soon as the recheck finds it, and live updates are ingested only
while the identity is verified; a failed recheck request changes nothing and is
retried. Transient network failures at startup or after a dropped connection
(including Telegram closing it cleanly) keep the account *Reconnecting*. Each
connection attempt has a 120-second limit. Delays double from 2 seconds to a
five-minute cap and continue until connectivity returns; a successful recovery
resets the delay for the next outage. After a transport-level flood refusal,
every attempt waits at least five minutes. Longer durable account/flood holds,
including a hold extended while recovery sleeps, take precedence and survive
manual Reconnect and service restarts. Individual RPC attempts and recovery
allowances remain bounded as described below; reconnecting never replays an
uncertain send.

An unknown authorization key, an unexpected failure or incoming handlers that
cannot reach their durable boundary stop recovery with *Reconnect required*.
Another process holding the session also requires intervention. Revoked or
duplicated authorization stops recovery and asks to *Sign in again*. Connection
status is read from the recovery task, so a stopped task never keeps claiming
to retry just because the sync loop still runs. A connection Telethon drops
mid-request only ends that cycle; a later cycle resumes after recovery.

## Cursors

Per account, the `dialogs` cursor resumes discovery from the last dialog with a
dated top message and `global_difference` holds the gap-recovery state. Neither
advances past data that was not stored, except when Telegram reports a gap too
long to replay: that restart moves `global_difference` to Telegram's latest
state and removes `dialogs` in the same transaction. Per chat, `recent:<chat>`
records the seven-day catch-up. The read-position fetch opens it (or closes it
for a chat without a dialog), and it advances only after each page is stored, so
an interrupted page is read again and unchanged rows are no-ops. The
`coverage:<chat>` floor is the lowest message ID from which contiguous catch-up
pages and Load originals stored every newer message of the chat. They only lower
it, except that a too-long gap clears every floor together with the seven-day
progress. Until a catch-up page from the newest message sets a floor again, Load
originals reads Telegram; for a chat that is no longer recent, that lasts until
it becomes recent again. Load originals lowers a floor only if it still exists
after its Telegram read.

## Incoming groups and the cached transition

Consecutive incoming sources form one Incoming Block until an outgoing/service
message or a calendar-day boundary in the configured application timezone. A
pause alone does not split the target. Grouping uses native identities and
original sources; requests keep their text, packet, image and output limits.
Oversized blocks keep originals with the preparation failure instead of being
split or truncated. The API supplies the same timezone for message and preview
times. A text-only block is combined rather than rewritten
([ADR 54](adr/0054-incoming-text-combined-not-rewritten.md)); blocks with an
image, a voice note or a video circle are rewritten.

Two versioned startup passes run once per store, each with its own marker.
`calendar-day-v1` converts cached blocks to calendar-day grouping after
recovery/expiry: exact unchanged blocks keep their identity, current result and
failure state; changed memberships withdraw the combined result and coalesce
one replacement attempt for eligible recent blocks that had AI work or normal
new/unread admission. Read never-processed and older blocks remain click-only.
Pending quiet/backoff deadlines are preserved; normal account, role,
eligibility, concurrency and daily-budget gates still control dispatch. The
conversion touches no native history, arrivals/alerts, viewing, memory,
drafts, credentials or spending, and an interrupted pass resumes without
repeating converted jobs. `local-revisions-v1` hydrates the
`blocks.source_revisions` map of native IDs to local revisions for all cached
blocks, including read, excluded and inactive chats, using bounded local body
reads to verify the existing fingerprint. It changes no output, jobs or
admission. A stale block keeps a null snapshot until ordinary rebuilding;
per-block commits make interruption safe to resume, and hydration cannot rerun
grouping or generation.

A displayed block intersecting a loaded native page expands its complete local
sources through `/blocks/{anchor}/sources`, with the current revision and an
offset cursor. Each read returns at most 100 native bodies, the full count,
missing slots and the next cursor. Indexed revision/deletion metadata across the
block rejects stale snapshots, even before a separate rebuild or outside that
page. Missing or expired local sources stay explicit; no history read, media
preparation or inference starts. Available sources remain usable while a failed
read falls back, and Refresh retries. The web reads these pages; it merges
monotonic local revisions so older history cannot restore stale sources, and
anchors deliberate Originals reading to the native source when delayed pages
arrive.
An absent or incomplete source-revision map cannot establish that an AI result
is current, even when all source IDs are already loaded. The web keeps
available originals and the source-read status while bounded verification fails
or waits for ordinary rebuilding; nothing regenerates to repair metadata.
Completed AI text must also match the block's current rendering revision.
An unchanged explicit retry can retain that applicable prior result; a changed
source or supporting dependency keeps originals until the current provisional
stream or validated result is available. Keeping an old stored result does not
make it current.

An exact older reply may be used transiently before it is cached. New attempts
retain a bounded digest of that reference's model-relevant fields and attachment
identity. Cache admission with the same revision and proven-equal digest keeps
the completed result without a job, call or alert. A content change sharing the
same native timestamp still invalidates it. Legacy attempts without the digest
and attachments without a provable identity stay conservative. Publication also
checks a transient reference if it becomes cached during generation. No history
scan, stored source copy or new generation is used to fill missing digests.
Authored restart, reconnect, gap replay and dependency regressions are in
`backend/tests/test_replay_reuse.py`.

## Original history

**Load originals** asks for the 50 messages before the oldest one shown. When
all 50 lie above the coverage floor it serves them from the local store.
Otherwise it reads one page from Telegram through the account's client and
lowers the floor when that page continues the covered range. Messages up to 30
days old are stored; older ones are shown but not kept. The server keeps no
paging cursor, because the client passes the oldest loaded ID. Loaded messages
are ordinary cached rows that can serve as Supporting Context within its usual
bounds. They are not targets when loaded, but the unread reconciliation that
runs whenever the account starts (service start, sign-in or Reconnect) or its AI
processing is switched On can queue a loaded message that is newer than the
chat's read position and less than seven days old.

New Message, a group member's profile and a chat link can open any of the
account's private chats with a person
([ADR 51](adr/0051-telegram-phone-look-and-new-messages.md)). Opening one that
isn't cached admits it with no activity and reads its latest page from
Telegram; Load earlier reads further. Such a chat is not a Recent Chat: its
messages are originals only, with no rewrite, emotion, suggestions or memory,
and it stays out of the Private list until a message is sent or received
there. From then on the Recent Chat rules apply.

## Reply parents and senders

Intake never fetches reply parents. A parent in the same page or the local store
marks the reply available; otherwise it stays unknown until the message itself
changes. Incoming rewriting and sending use the stored direct parent, or read it
by exact ID when it is not stored, and refuse one that is missing or deleted.
Outgoing Rewrite does not read it.

For Reply Quotes ([ADR 41](adr/0041-reply-quotes.md)) intake also keeps the
reply header's own details: `reply_quote` (a quoted fragment),
`reply_elsewhere` (another chat's peer, the original sender's peer and name,
and its date, from `reply_to_peer_id` and `reply_from`) and `reply_story`, and
marks each capture `reply_read`. `reply_to` stays the header's number;
`reply_target()` names the same-chat message only when the reply is neither to
another chat nor to a story, and every parent lookup uses it, so another chat's
number is never read here. A sender name for another chat comes from the
header, an entity Telegram returned or a chat already stored; nothing is
fetched. A capture without the details never erases them.

Reply Quotes resolve through `POST …/chats/{peer}/originals/replies`, given at
most 100 loaded replies. The cache answers first. In private chats one exact
read fetches the uncached replied-to messages and rereads replies captured
before `reply_read` existed, under a lock shared by clients, a 60-second
timeout, a one-minute retry after a failure and a remembered absence after a
read that did not return the message. A reread only learns the header's
details (`learn_reply`), never content, history or derived work. Messages from
the last 30 days join the cache through ordinary non-target intake; older ones
are returned without being stored and read again on a later open. Groups read
nothing and report uncached messages missing.

Author labels come from sender entities that Telegram already returned, with no
extra request. Private-history records may omit `from_id`. In a one-to-one
dialog the sender is then the dialog peer for an incoming message and the
account itself for an outgoing one. An explicit `from_id` still wins, and group
or channel authors stay unknown. Live updates carry `from_id` while history
pages may not, and re-reading unchanged content keeps the author it was first
stored with, so without this rule one message revision could record different
senders depending on which encoding arrived first.

## Pacing and read state

Forward attribution is read from Telegram's forward header and already-returned
source entities, without resolving extra peers. `forwarded: null` is legacy
unknown metadata; false is a confirmed non-forward. Intake also keeps the
header's original date (`forward_date`), channel post signature
(`forward_signature`) and imported flag (`imported`); they are immutable header
fields, so a later read only fills them in
([ADR 27](adr/0027-header-message-menu-forwarding-details-and-voice-playback.md)).
Opening a chat or loading originals requests missing attribution in batches of
at most 100 known cached IDs, through the account owner's read lane: legacy
unknown rows and confirmed forwards lacking these details or a source name. A
successful exact read records `forward_read`, after which a still-missing name
is final. Each batch has a 60-second deadline; concurrent clients share its
work and unavailable/missing records wait at least 60 seconds before another
attempt. Reads never fetch uncached IDs or scan a forward chain/history. Only
missing forwarding fields are merged into the latest stored record, never
replacing a known value and preserving concurrent edits, revisions, versions
and derived AI state. A normal update with less attribution does not erase a
known name or detail.

A live incoming forward that arrives without a source name (typically a short
update without entities) gets one exact read of that message within its quiet
seconds, only when AI processing is admitted for the account and a queued
incoming job covers it; a failed read never delays processing and waits 60
seconds before the display repair may retry.

Recorded rewrites, emotion, suggestions and reaction assessments keep the
`[forwarded, forwarded_from]` pair they used. Learning a previously unknown
value later is a fill-in that keeps them current, including in-flight work and
transient-reply digests (which are compared under the recorded pair); a
conflicting known value still makes them non-current. Coverage is in
`backend/tests/test_forwarding.py` and `web/journeys/details.spec.ts`.

Message, dialog, history and gap reads share one lane per account: one read in
flight, at least a second apart, each with a 60-second timeout and at most six
attempts with backoff on transient errors, within 900 seconds of recovery
waiting. A flood wait holds the whole account, a slow-mode wait holds its peer,
and transient-error backoff on a read holds that method; holds are durable and
survive a restart. A send waits for them only within its own deadline (60
seconds, or 180 with images), and a hold longer than the recovery allowance
stops it at once. A flood refusal is retried with the same random ID while the
wait fits. Before each hand-off of a send's final request to Telethon's sender,
the app durably records that it may have reached Telegram
([ADR 17](adr/0017-send-hand-off-boundary.md)). A send is refused as not sent,
keeping its draft, images and an open chat, when that record proves nothing was
handed off (the deadline, a hold that outlasted it or the allowance, a failed
chat lookup, a disconnect other than while preparing or uploading images, or an
interruption) or every hand-off got only a flood or slow-mode answer. While a
hold lasts it says when the send can go again, and sending the same revision
again reuses its random IDs. An error while preparing or uploading images, and
Telegram's definitive refusal, refuse a send outright. Any other failure after
a hand-off stays unknown, and a send whose outcome is uncertain is never
retried. File parts have their own lane.

The app sends no read acknowledgements and never marks a voice note or circle
as played. Opening chats, loading originals, playback, prefetch and
transcription all leave Telegram's read state to the owner's own clients.
Telegram's read position is only read, by discovery and by the fetch before a
chat's first catch-up page, to choose catch-up targets.

## Reaction metadata and recipient read state

[ADR 26](adr/0026-mobile-message-actions-and-send-status.md) keeps two kinds of
native metadata. Neither is source content: applying them never changes a
message's revision, derivatives, block, observed edit history, viewing token or
rewrite work, so no transcription or image preparation repeats.

The recipient's outgoing read cursor (`read_outbox` on the chat) comes from
dialog discovery, the read-position fetch, live `updateReadHistoryOutbox` and
replayed gap updates. It only moves forward. Outgoing messages through it read
as Read; later native outgoing messages read as Sent. Telegram has no separate
delivered-to-device state. Incoming read acknowledgements remain unsent.

Each native read or update carries a reaction snapshot of a full message
(`domain/models.py`), stored per cached message (`storage/reactions.py`). A
snapshot keeps Telegram's evidence explicit: chips and counts, the owner's
choices (unknown in Telegram's `min` form), attribution of each reaction to the
owner or the correspondent, and native addition times. An unknown third party
is never attributed, and local observation time never stands in for addition
time. Observation time only orders snapshots of one message: a read that started
before a newer update arrived cannot replace it. A reaction update that leaves
none is recorded too, so an older read cannot restore a reaction removed live.
Service, deleted and uncached messages get none.

The correspondent's dated 👍/🤝/👌/👎 reactions form a durable ledger
(`reaction_events`). Reactions dated before the store first recorded reactions
(`reactions_introduced` metadata) are a display-only baseline. Repeated
observations are idempotent. A replacement or re-addition has a new native time
and identity; a removal is recorded only when a snapshot proves it: the emoji
is no longer counted, complete attribution omits it, or the only counted one is
the owner's. Partial and minimal snapshots prove nothing. The owner's own
reactions, other emoji and undated evidence never enter the ledger. Rows expire
with their cached message.

A private chat also keeps, per cached message, the first local observation of
an own reaction (`own_reactions`): a chip marked the owner's or an `own` actor,
never a minimal snapshot. It answers Response Suggestions for a block holding
that message ([ADR 49](adr/0049-useful-response-suggestions.md)), and it stays
after the reaction is removed; it expires with its cached message.

Reaction updates have no replayable update sequence, so a gap cannot recover
them. Discovery and the read-position fetch record Telegram's count of unread
reactions to the owner's messages; when that count changes, the sync cycle does
one bounded `messages.getUnreadReactions` read (at most 100 messages), applies
snapshots only to already-cached messages and caches nothing new. It never marks
reactions read. Reactions on the correspondent's own messages missed while the
service was offline are learned only when those messages are read again.

The owner chooses or removes ordinary emoji per exact native message through the
session owner (`messages.sendReaction` with the complete own list). The palette
is Telegram's active available reactions (premium ones only for a premium
account) and the limit is `reactions_user_max_default`/`_premium` from the app
config; both are cached for a day and never guessed. At the limit a new choice
replaces the oldest, as native clients do. When the owner's current choices are
unknown, one exact read of that message precedes a removal. Custom and paid
reactions are displayed but never offered.

## Tests

`backend/tests/test_transport.py` covers pacing, the read and file lanes, flood
holds, unknown-send handling, gap-cursor safety, the too-long-gap restart,
identity withdrawal, bounded sync continuation, session ownership and the
private-DM sender rule. `backend/tests/test_sync_cycle.py` covers step isolation,
account-wide halts, the deferred rescan and its seven-day bounds, and
`backend/tests/test_older.py` local-first Load originals and its floor race.
`backend/tests/test_reactions.py` covers reaction snapshots, the dated ledger,
the read cursor, unread-reaction catch-up and reaction choices.
They use fake clients and authored content, so they are not live Telegram
evidence. The running service serves new transport behaviour only after it
restarts on the new code.

## Attachment identity and native versions

The store records stable photo/document IDs without access hashes or file
references. A native timestamp orders intake; a separate local revision advances
for meaningful content changes within that timestamp. Blocks, context guards,
derivatives and cached media use the local revision. Timestamp-only updates
with proven identical text, link destinations and attachments keep results and
record the native edited flag without another visible history step. Media
captured before attachment identity was recorded has no prior identity, so its
observed versions are retained and an attachment change among them can be
neither confirmed nor excluded. The store collapses only proven identical
adjacent versions, preserves native edit evidence and keeps original-date
expiry. History and already-cached media remain reading-only; no history read
downloads an old attachment or enters AI context. Focused invariants live in
`test_observed_history.py`.

Natively observed so far: a distinct-text edit navigates between its versions
and restores exactly; a link-to-image edit shows its cached current image, then
the prior link without reusing that image; deletion navigation works. Two
cached, distinct image assets of one message have not been observed natively.

A confirmed send writes a provisional record from Telegram's receipt: revision
0, no native revision, the send's own media identity and facts, and the moment
Send was pressed. Every native capture has a positive revision, so that record
is exact. When Telegram's copy is first read it replaces the record whole: no
observed version, Telegram's date, and an edited flag only if the copy itself
carries a shown edit date. A receipt record arriving after the copy is older
and ignored. Telegram's `edit_hide` makes an edit date not an edit: it never
sets the flag, though a content change under it is still an observed version.
A store upgraded from before schema 34 drops receipt records kept as `edited`
versions and clears the flag where nothing else shows an edit and the native
date is within five minutes of the record's. The live check
`web/live/sent-not-edited.spec.ts` sends a text, a saved GIF and an image in
the Maya test chat and checks that none has the pencil or a version, that each
carries Telegram's date, and that the GIF's stored facts match its retained
bytes.

## Deleted messages and hidden edits

Under [ADR 52](adr/0052-deleted-messages-hiding-and-the-header-ai-toggle.md)
a deleted message is presented by the version captured when its deletion was
observed: `ObservedHistory.shown()` serves that content with `deleted`,
`deleted_version` and `media_available`, and leaves out deletions made through
Enloq (a Deletion Operation chunk past dispatch), hidden ones
(`hidden_messages`), Saved Messages' notes, service notices and deletions seen
before any content. Media, preview and transcript reads of a deleted message
use its capture from the cache only, and the versions route skips it. Reply
Quotes keep any capture. Hidden rows expire with their messages.

An edit Telegram marks `edit_hide` keeps an observed version only when its
wording or a link target changed. A change of the outgoing flag alone is
metadata: Telegram's update stream reports the owner's own Saved notes with
`out` unset while history reads set it, so intake treats a Saved message sent
by the account itself as outgoing. A store upgraded from before schema 35
makes those notes outgoing again and drops each version whose content its
successor repeats, outgoing flag aside. Upgrades to schemas 35 and 36 also
give an unedited message sent before schema 34 Telegram's own date instead of
the moment Send was pressed, accepting a Telegram date up to five seconds
before that moment, since Telegram dates in whole seconds.

## Incoming notification bursts

Only an unseen live NewMessage event dated after this runtime started may open
an incoming notification burst. Native edited events (including Telethon's
NewMessage subclass), gap recovery, history reads, outgoing work and retries do
not. Burst identities are account/chat scoped and distinct from Incoming Blocks;
30 quiet seconds coalesce arrivals, while a later burst can extend the same
block. Once current output validates, or rewriting is known unavailable, one
pending outcome references the current source revisions and completed blocks.
A text-only block, combined rather than rewritten, is ready as its originals;
one held for the calming decision
([ADR 53](adr/0053-calming-hostile-messages-and-removing-adaptive-tone.md))
waits until it is decided. Fallback consumes that burst even if a later
rewrite succeeds. No notification operation starts inference or adjusts
spending. Pending delivery expires five minutes after eligibility; a newer
burst replaces the prior pending candidate. Every dispatch rechecks current
source, output, account eligibility and mute. The operational rows contain
IDs/revisions, not previews; previews are read from current sources/results
only when needed. Completed rows are pruned after a day. Push display, sound
and activation are in [operations](operations.md#desktop-web-push).

## Local viewing

Local viewing is source-specific and kept apart from Telegram's read position,
model work and notification delivery. Ordinary native dialog discovery freezes
each chat's initial read position, top message and seven-day cutoff once.
Cached initial sources wait for this evidence; initial paging and restarts reuse
it. Later arrivals and bounded recovery above that top remain candidates despite
subsequent Telegram reads. Explicit older/exact history loads create no
candidates.

The account-scoped viewed endpoint accepts at most 100 rendering identities per
request. Each token identifies a current original or a completed validated rewrite
and its exact source revisions. Validation and acknowledgement are atomic; stale
renderings, partial streams and history versions cannot acknowledge new content.
Viewing is saved before readiness, persists across edits/retries, and updates all
trusted clients through committed change events. It sends no Telegram read
receipt. Source deletion/30-day expiry removes state; logout hides it with the
account's cache, and local removal removes that store without affecting
spending.

The normal scheduler evaluates local readiness once per second using the shared
notification content decision after 30 quiet seconds. Candidate bursts wait for
validated completion or the original fallback. Mute, notification settings,
attention and notification expiry affect alert delivery only. Catch-up gets no
new alerts. Readiness is latched once per source and cannot be revoked by
reprocessing; a later appended source waits separately. Concurrent source/output
changes defer the latch until the next tick. No inference is started by readiness.

Conversation rows carry original/AI previews and the first ready unseen source.
Ordinary dialog metadata tracks the actual latest native ID; an uncached or
unknown top has a blank preview. Incoming AI previews require a validated current
source fingerprint, while outgoing previews always use sent text. Deletion never
exposes historical text and private image context is never a preview. Native
message time supplies the row date. The cached-window endpoint reads at most 25
predecessors and 75 sources starting at an anchor, using the conversation-order
index. It performs no history fetch, media preparation or viewing acknowledgement.

Each client snapshots all currently ready, unviewed incoming sources through
`/ready-sources?after={message_id}&limit=100`. Each indexed local page returns at
most 100 current original tokens and a native-ID cursor. It starts no history,
media, inference or readiness work. After one continuous focused, visible,
active second the conversation acknowledges those exact tokens, including
offscreen sources, in batches of at most 100. Newly ready tokens start their own
dwell; their arrival cannot reset older tokens' deadlines. Focus loss, hidden
screens, covering sheets and navigation guards cancel observation. Exact tokens
reject changed sources and cannot acknowledge later arrivals or another chat.
Before readiness, current original ends still qualify for one continuous visible
second; media cards require no playback. Historical versions and partial text
manufacture no readiness. Server committed events refresh all trusted clients.

Both paths retry a failed acknowledgement at most three times per token and
continuous qualifying observation; a later focus observation may retry.
Readiness-page loading is serialized/coalesced across content refreshes, with
three attempts per read round and renewed-focus recovery. Chat-list reads are
also serialized/coalesced, so pre-viewing snapshots precede the newer cleared
state instead of arriving last and restoring its dot.
Opening chooses the first unseen original or containing AI block once, loads a
local anchor window if needed, and thereafter preserves deliberate history
reading and bottom-follow. Row previews follow the global reading preference;
per-bubble choices stay local.

The Unread Separator ([ADR 28](adr/0028-unread-separator-and-compact-conversation-header.md))
is presentation only. The conversation response carries `unread_sources`, at
most 100 ready unseen incoming message IDs (no reactions), read in the same
transaction as `opening_source`; the client captures them once per opened
conversation and places the separator above the earliest that is not deleted:
above its whole block in AI view, or above the native message in originals. A
capture that is not loaded shows none rather than marking a later message.
Opening puts the separator at the top when it marks the opening message; scroll
anchoring still uses source rows. The focused readiness snapshot detects
departures (blur, a hidden page or app, an open sheet). On return it holds
every acknowledgement in the conversation, both the snapshot's and visible
original ends', until a ready-source read started after the return completes,
then moves the separator above the earliest still unacknowledged message, if
any; an exhausted read releases the hold without a move. It stores nothing,
sends no read receipt and changes no viewing, dot or notification rule. Both
clients take the rule from `shared/unread.ts`.

Reaction dots ([ADR 26](adr/0026-mobile-message-actions-and-send-status.md)) use
the dated correspondent ledger, not source viewing, so an earlier view of the
target never acknowledges a later reaction. A non-baseline, active 👍/🤝/👌/👎 on
a current cached target within seven days of it, in an eligible recent chat,
becomes ready after 30 quiet seconds from the chat's latest such observation,
once its Reaction Assessment has settled or cannot run (AI Off, budget, an
ineligible chat or a failure); suggestions are never awaited. Mute and push
settings do not suppress it, and readiness sends no alert or sound and starts
no work. The row dot and opening position include the earliest ready unseen
reaction target alongside ready unseen sources; the client opens its
containing bubble. The first readiness page also carries up to 100 reaction
tokens, so one focused second acknowledges ready messages and reactions
together, shared by trusted clients, without Telegram read state. A dot lasts
beyond the 12-hour AI expiry until viewed, withdrawn, its target deleted or its
cache expired; withdrawal removes only its contribution and repeated
observations never recreate it. Coverage: `backend/tests/test_reaction_dots.py`.

Native use confirms that reopening starts at the first ready unseen source,
that one focused second clears every ready identity, offscreen ones included,
while other conversations keep their dots, that a restart keeps the
acknowledgements, and that a dot appears when new content becomes ready in a
chat without one. Readiness regressions (`backend/tests/test_unseen_readiness.py`,
`test_local_viewing.py`, `test_focused_viewing.py`) cover the timing, including
no dot from arrival or streaming alone.
