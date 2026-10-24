---
status: accepted
---

# Separate group chats with default-off AI daily briefings

## Context

Enloq's private-chat rewriting turns one correspondent's messages into clearer
wording, preserving every point in their voice. A group has many speakers,
much routine noise, bots and advertising, and far more messages, so the same
treatment does not fit. Groups therefore get their own tab and a different
reading aid: one **Daily Briefing** per group and day, a cited topic summary.
Group AI is off by default, so no newly discovered group content reaches a
model until the owner opts that group in, and briefings have their own budget
so they cannot crowd out private work.

A Telegram **Group Chat** has multiple participants; it is distinct from an
Incoming Block or Message Group, which combines a private chat's native
messages for one rewrite.

## Decision

### Groups tab and processing

Existing basic groups and ordinary supergroups appear in a separate Groups
tab, under the same seven-day native-message activity rule and muted and
archived treatment as private chats. Opening, reading and editing drafts do
not count as activity. Forum (topic) groups and broadcast channels are not
included, and Enloq does not create, join or administer groups.

The account header's icon-only switch shows Private, Groups and Saved
([ADR 50](0050-icon-tabs-saved-messages-and-one-saved-build.md)). The app opens
on Private and remembers each account's tab while it runs; it remembers no
chat per tab ([ADR 44](0044-mobile-only-web-in-the-apps-design.md)).
Private-chat alerts open the Private tab.

Each account and group has a durable AI opt-in, initially Off, under the
account's AI switch in Manage accounts. Processing and the Reading View stay
separate ([ADR 14](0014-account-ai-processing-and-reading-view.md)): choosing
AI reading starts no work and opts no group in. Group Off pauses automatic
briefings and their media stages. The composer offers ordinary sends, with
neither outgoing Rewrite nor suggestions (ADR 25).
Account Off blocks all new AI. A valid started stage may finish, but no next
automatic stage starts while paused. The Group AI processing switch sits in
the group's ⋮ actions sheet, beside Hide Bots
([ADR 42](0042-groups-and-briefings-in-the-enloq-app.md) Q3). A passive AI off
tag reflects either switch, and a factual status names any other pause.

### Daily briefings

AI reading of a group shows one briefing per group and calendar day in the
server's configured timezone (`Asia/Ho_Chi_Minh`). Originals stay available,
and Originals reading keeps their chronology. Groups get no individual
incoming rewrites.

A briefing summarizes substantive participant messages, the owner's
included, rather than only mentions of or replies to the owner, and omits
routine service events and the bots and ads excluded below. It preserves
speaker identity, forwarding attribution and uncertainty about unknown
speakers. Inputs are text and captions, image meanings, and voice and Video
Circle transcripts under the existing media boundaries; Non-AI Media stays
non-AI, and ordinary videos get no analysis or transcription.

It is a concise topic-based account of substantive discussion, explicit
decisions, relevant dates and unresolved questions, with material speaker
attribution and source links. It compresses repetition without inventing
agreement, facts, commitments or task tracking. Each topic has a bold title,
normally one paragraph of one to three sentences (more when material numbers,
conditions, disagreement or open outcomes need it) and a separate participant
list. Participants are native authors who substantively contributed,
questioners and informative bots included; mentions, forwarded origins, empty
acknowledgements and unrelated jokes do not count. Three names show, with an
inline expand for the rest. Topics follow their first substantive appearance
in the day, merging later fragments and keeping corrections and the latest
stated outcome.

Only the final assembled briefing streams, in the day's position; chunk notes
and reasoning stay hidden. Headings and summary bodies appear as they arrive,
the first usable prefix replaces the previous wording, Analyzing shows while
it is provisional, and participants and source links activate after final
validation. Each attempt freezes its source cutoff: later posts stay
uncovered originals, and changed or deleted dependencies withdraw its output.
A failed or interrupted stream is discarded and the last still-valid final
returns at its earlier cutoff, otherwise the originals. Failure and Retry
state and spending evidence stay. A prefix is never kept as completed, and
counted or uncertain failed work is never repeated automatically. Reading,
refreshing or restarting reuses unchanged results.

A validated no-substance outcome reads "No substantive updates in the covered
messages.", with coverage and missing-content status and access to the
originals. Blank, truncated or malformed output is a failure. Empty,
service-only or wholly Non-AI input makes no model call.

A valid briefing with an earlier cutoff stays beside newer uncovered
originals; a validated update folds its covered sources into that day's
briefing. A seen edit or deletion of a covered original withdraws the
affected day's briefing at once and rejects late output; the originals show
until an eligible replacement. Source changes do not bypass the automatic
four-hour interval.

A source link reveals that day's originals in place and scrolls to and
highlights the cited message, with Return to briefing at the top of the day;
the global Reading View is unchanged, and missing or changed sources are
shown truthfully. Summarized attachments are reached through the originals,
without a day gallery; uncovered originals keep their attachments and players.

Supporting Context stays within the same group: up to 20 preceding recent
cached messages plus the one directly replied-to message per bounded request.
It only resolves references, with no reply-chain or history scan, no other
chat and no memory, and never presents earlier days' events as new.

### Today's Briefing Slot, the date band and the new-message spoiler

Each day starts with a pinned, green-tinted full-width date band with a 2px
accent rule, in AI, Originals and revealed sources, staying at the top of the
stream while its day scrolls past and distinct from the Unread line. Labels
read Today, Yesterday, then weekday and date, with the year only outside the
current one. Cited sources and the opening position land below the band.

In AI reading, the right end of today's band holds the **Briefing Slot**: an
icon and a word at most, with no tooltip or popup.

- `↻ N new` when N messages are not covered yet, or `↻ Update` when only
  edits, media evidence, guidance or bot visibility changed. A tap starts an
  update.
- `Waiting` while today's briefing runs.
- Amber `Retry` for a failed attempt; a tap retries it. The error text is not
  shown.
- `Budget` when the day's Group Briefing Budget is spent; a tap opens the
  budget in Settings ([ADR 45](0045-settings-in-the-enloq-app.md) Q8).
- `Syncing` while Telegram messages are verified again or the account is
  connecting. A transient catch-up keeps the last settled state instead of
  flickering.
- A grey check when today is up to date; nothing while account or group AI is
  Off, or when today has nothing eligible.

Every Retry refusal maps to one of these, so there is no disabled Retry, and
a refused tap only rereads the slot. Up to date outranks Budget, which
outranks Retry.

A tap on `↻` updates today only, first generation included. Unchanged valid
results are reused and the slot answers up to date without a paid call; new
or changed sources, newly available media evidence or changed filtering or
summary rules qualify for work. A deliberate update bypasses the four-hour
interval, the 30 quiet seconds and the character minimum, but needs account
and group AI On, native access, source and role eligibility, the Group
Briefing Budget, concurrency and the work limits. One run per group is
active; repeated taps coalesce; the automatic clock is unchanged. The day and
source cutoff freeze when the tap is accepted, and the run finishes that day
after midnight; later sources wait for later work.

Earlier days have no slot, no Retry and no Analyzing status. Their automatic
work, yesterday's final pass and rebuilding after an edit or deletion,
continues unseen, and a failed background run leaves the last briefing or the
originals.

On a day with a briefing, messages it does not cover yet fold under one
`N new messages` spoiler, closed by default; days without a briefing show
their messages as before. The chat opens on the folded spoiler, with the
Unread line above it, when unseen messages start there. Once opened it stays
open until the chat closes, and a send from the composer opens today's.
Folded messages are not counted as viewed.

### Update admission and resource sharing

Automatic updates run at most every four hours per account and group,
measured from the start of the last automatic update. The clock persists
across restarts and midnight, and Off/On toggles do not reset it. The first
generation can begin as soon as content is ready; finalizing yesterday waits
for the next eligible update. Unchanged input starts no work. One eligible
update forms one batch under a shared work cap, handling pending days newest
first, with no separate bypass for older days and no parallel batches for one
group.

First opt-in starts with today's cached messages, read or unread. Later
updates catch up missed or changed days since opt-in within seven days,
newest first; history before opt-in is never back-filled. Incomplete source or
media coverage may give a clearly labelled partial briefing with its covered
range and missing content, never a claim of completeness, and later input
enters a later update.

A busy day is summarized in bounded chunks plus a final briefing, reusing
unchanged chunks; sources are never truncated silently. One automatic update
admits at most five generative requests (chunk, image and final calls,
refused and failed ones included), separately up to 12 classifier requests
(below), 200 newly covered or changed native messages and 20 minutes of new
speech preparation. Reused chunks and media need no fresh calls. Per-call and
per-file limits and global concurrency stay, and there is no work-limit
setting. Within a day an update advances oldest to newest through uncovered
or changed sources, keeps room for a final partial briefing when it can, and
leaves the rest readable for later updates. Completed unchanged work is never
repeated.

The [Processing Character Threshold](0035-processing-character-threshold-and-empty-rewrites.md)
gates automatic updates. It counts the day's cached eligible original text
and captions (the owner's, informative bots' and ads included) in Unicode code
points of the trimmed originals, excluding service text, metadata, Supporting
Context and generated wording, and does not reset after a briefing: a short
addition qualifies once the whole day meets the minimum. New or changed
analysable images, voice notes and Video Circles exempt an update, media-only
days included, reusing cached meanings and transcripts; already-covered
unchanged media does not. Filtering adds no second threshold. The latest
threshold is checked before automatic preparation and every dispatch: newly
ineligible unstarted stages are held, valid cached chunks stay and valid
in-flight calls finish. Lowering it releases pending work only when quiet and
the four-hour clock permit. Changes reset no clock, invalidate no valid final,
scan no history and retry no failure, and there is no below-threshold status.

Briefing work runs below private-chat work, manual suggestions included, and
above idle memory work, taking turns across members, accounts and groups. At
a concurrency of two or more, group work never takes the last free model slot.
Started work is not interrupted.

### Bots and advertising

**Hide Bots** is each member's own durable preference
([ADR 48](0048-members-on-the-shared-service.md)), On by default, in the
group's ⋮ sheet. While it is on, every original from a native-verified bot
leaves group reading and briefings, old and new, with no model judgment.
Hidden bots are handled exclusions: never model input, classifier targets,
participants or uncovered sources, and a day of only bot posts completes
locally without a provider request. **Stop ignoring this bot**, in the
long-press sheet of a bot's original, exempts that one verified bot in that
group: it is shown and read while Hide Bots stays on, and there is no action
yet to hide it again. Off shows the cached originals and tombstones again
without starting model work. Exact source navigation reveals its target
without changing the preference. Unknown provenance stays visible and
eligible; a name proves nothing. Changing the preference changes the briefing
rules like edited guidance: today's briefing becomes updateable and nothing
published is withdrawn.

Deleted bot-authored messages are hidden using native bot provenance, while
deleted human messages follow
[ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md); day
sections that filtering leaves empty are not drawn. Bot status comes from
trusted native metadata and, when needed, bounded exact sender-profile lookups
for cached authors in accessible groups; Unknown stays visible until verified,
and no history is scanned.

Obvious advertising is excluded from briefings by a dedicated role on the
decision model (below). Each eligible source is evaluated once with one yes/no
question; an ad probability of 0.9 or more excludes the whole source, useful
mixed promotional posts included. Requested recommendations, discussion of ads
or promotion bugs and personal cost-sharing or requests for help are not ads.
An uncertain or failed classification keeps the source eligible without a
label, adds no standalone filter Retry and is not resampled automatically. The
probability is not a calibrated guarantee. Classification reads readable text
and captions with bounded same-chat context, trusted native metadata and
already-cached meanings and transcripts, before any fresh media preparation;
insufficient evidence keeps normal media handling, no media work is added
solely for filtering, and there is no second pass in the same update.

Classifier requests charge the Group Briefing Budget and share capacity below
private work: up to 12 per automatic update, separate from the five generative
requests, with at most 32 targets per call and earlier splits for size or cost
bounds. The 200-source cap is shared, counters survive pauses, restarts and
Retry, refused and failed attempts count, and route, rates, switches, access
and budget are checked before every stage. Unclassified or capped sources stay
eligible.

Excluded ads leave AI reading and participant lists instead of reappearing as
uncovered originals. In Originals they keep a passive `(ad)` label in the
header metadata when a validated classification matches the displayed
revision, with exact wording and Copy; human-authored ads stay visible there
with the label. Decisions, labels and dependent work bind to exact source and
evidence revisions, and nothing historical is processed only to label it. When
every intended source is a validated ad, the day completes locally without a
summary request as a quiet Ads skipped day with cached original access and an
opening anchor; unfinished or capped sources prevent that claim.

While Hide Bots is off, the decision model also judges native-verified bots:
one judgment over the latest ten distinct surviving current sources from that
bot in that group within seven cached days, waiting while there are fewer and
fetching no history. A probability of 0.90 or more that all ten are
advertising, spam or routine notices without substantive contribution
remembers that bot as ignored for that exact account, group and bot, until the
owner corrects it, with no automatic reassessment or expiry. Missing image or
speech meaning that the sample needs holds it before dispatch, whatever a
model might score. An ignore applies from today on; earlier days keep their
results. Ad classification of bot posts likewise applies only while Hide Bots
is off. Native service records need no model call.

When the shipped filtering rules change, one durable pass rebuilds today's
briefings across processing-enabled groups, including days without a result
and today's cached sources from before opt-in. It freezes the calendar date
and the eligible account and group set when it starts, finishes that day after
a restart or midnight while ordinary gates permit, and never expands to a new
day or newly enabled groups. It bypasses the four-hour timer once, keeps every
other gate (Off pauses its remaining work) and leaves the automatic clock
alone. Earlier days are not regenerated and no history is fetched.

Critical briefing defects must be fixed: fabricated or lost substantive
information, wrong material attribution, false substantive exclusions, broken
source access and budget or access violations. Minor wording and style issues
are accepted, and a permitted mixed-post exclusion is not content loss.

### Intake epochs

A busy group can produce more changes than Telegram will replay. Enloq then
cannot prove its cached originals are current, opens a new intake epoch and
reads the recent seven-day window again. There is one briefing per day, and
it is not regenerated when no original changed:

- A new epoch alone withdraws and regenerates nothing. A published briefing,
  its reusable chunks, consumed exclusion decisions and running work stand
  until an original is seen to change.
- Seen changes are an edit, a deletion, a changed author or signature, a
  withdrawn proof, lost or changed group access, and an original that the
  epoch's read passed without finding. Each withdraws its dependent work.
- An earlier proof of the exact revision stands until the read reaches that
  original, and for originals older than the window the read covers.
- A new stage is admitted only on originals proven in the current epoch. A
  running update waits for the read to prove its originals again and then
  continues.
- New work for a day starts only after the epoch's read has passed that day,
  so it never freezes a partial day or drops saved summaries; meanwhile a
  deliberate update answers that messages are being verified again.
- An unchanged day answers up to date and gives the automatic clock nothing
  to do, during and after the read.

Native actions keep the stricter rule: sending, replying, deleting and media
access need proof in the current epoch.

### Budget and failed-attempt Retry

Each member has one durable **Group Briefing Budget** across all their
accounts and groups (ADR 48). The owner's defaults to $2 and can be set from
$0 to $10; a friend's defaults to $0.50, is set only by the owner and is
read-only to them. Settings → Budget shows it with today's spending beside the
Daily Budget, with an explicit Save.

Briefing image understanding, chunk summaries, final synthesis, classifier
requests and briefing Retry charge only the group budget. Private-chat
roles, including notification screening and both kinds of suggestions,
charge the Daily Budget. Reused
cached work costs nothing, and VPS transcription has no provider charge
([ADR 39](0039-litellm-proxy-and-vps-speech.md) owns the hosted fallback). Each
request's budget is fixed at dispatch, so late receipts are never reassigned.

Both budgets reset at midnight in the configured timezone and count by
dispatch day; late receipts settle their original day. A request counts the
larger of its charged and upstream cost, $0.01 while running or unknown, and
nothing for a refusal where the provider did no work. Failed, invalid or
interrupted work keeps its spending evidence, and recorded spending never
moves between budgets.

The latest group limit is checked before each new briefing stage and model
dispatch. Started stages may finish, with the possible small overshoot.
Saving a limit keeps accumulated spending, caches and the four-hour clock and
retries nothing. At the ceiling, $0 included, new briefing AI stages wait,
while cached reading and ordinary non-AI actions continue.

The two budgets are independent: no borrowing, double charging, combined
ceiling or reserve, so briefings may use their whole allowance whatever the
Daily Budget has left. Shared concurrency, private-work priority and
last-slot protection still apply.

Retry of today's failed or interrupted attempt may bypass the four-hour
interval and the character minimum, but needs account and group AI On,
native access, the ordinary chat, target, role and currentness gates, the
group budget and concurrency. It retries only the previous attempt's intended
snapshot, whose required originals must still be current and cached within
30 days; it back-fills no history, expands no coverage and never generates a
first or a new completed result. Valid successful stages are reused, and the
same per-attempt work caps apply. Refresh starts no generation. Automatic
backoff exists only for refusals that did no work; counted, format and
uncertain failures wait for Retry. Refused requests consume the request cap
even at zero cost.

### Other group capabilities

**Sending.** Deliberate text and media sending and exact native replies work
under the existing account, target, draft, permission and queue guards, while
the group's access is verified. Groups have no outgoing Rewrite, tone
controls or suggestions. Sends show Sending, Sent, Not sent or Delivery unknown;
there is no group Read and no member-reader query. Every send keeps the
connected account's sending identity and the zero-Stars-payment boundary.

**Reactions.** Native aggregate reaction chips are display-only in groups:
no reaction writes or removals and no reaction-only dots, and Telegram's saved
sender identity is never changed as a side effect.

**Deletion.** Only the owner's own current originals can be deleted, for
everyone, where native rights permit; other people's messages and admin
moderation are out. An eligible original needs the connected account's
verified Telegram user authorship: anonymous, channel-authored and
unknown-author posts are excluded even with an outgoing flag or admin rights.
[ADR 31](0031-message-deletion-selection-and-group-ai-controls.md)'s
Originals-only selection, exact review of account, group, count, wording and
media, chunked execution and recovery apply; changed evidence needs a new
review, and source, access and native rights are checked again before each
chunk. A briefing is never a deletion target.

**Not in groups.** Recipient emotion, Response Suggestions, softening and memory
are private-chat features only; groups teach neither memory
([ADR 55](0055-person-and-account-memory-and-traits.md)).

**Unseen dots and opening.** Groups have local unseen dots, also while group
AI is Off, and no sound or push alerts. Dots follow native incoming messages
after 30 quiet seconds, independently of updates and AI state; a briefing
refresh alone creates no dot. In AI view the chat opens at the earliest ready
unseen message: its day's briefing or folded spoiler with the Unread line
above, or the exact original if it is uncovered; Originals view opens at the
exact message; otherwise at the latest. The Unread line's capture and
focus-return rules and the deliberate-history and follow-bottom rules stay; a
briefing update or a moved separator does not scroll by itself. One continuous
focused second acknowledges every ready incoming native message in the chat,
offscreen and uncovered ones included, except those folded under a closed
spoiler. The initial native-read and seven-day baseline, stable-ID
deduplication, current-content checks and durable viewing shared across
trusted clients stay, without Telegram read receipts. Briefing versions are
not unseen messages of their own.

**Look.** Group messages follow Telegram's phone look, with each other
person's Message Run carrying their name and avatar
([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q13); the group's
photo and name open its Group Profile
([ADR 47](0047-pairing-settings-menus-and-profiles.md) Q8).

**Media.** There is no group-wide background media prefetch. AI media is
fetched only within an admitted update, reusing cached meanings, transcripts
and bytes. Deliberate playback and download work with AI Off under the shared
cache limits and access guards.

**Migration and lost access.** A basic group's migration to a supergroup
creates a separate typed peer with group AI Off. Cached content and drafts
stay with the old peer; accepted sends are never retargeted and opt-in and
briefings never merged. Lost access keeps cached sources and briefings
read-only within normal expiry and keeps drafts, opt-in and the clock, while
stopping new group AI and native actions. Restored access to the same peer
resumes under those settings; failed or unknown sends do not retry
automatically. Accepted sends never handed to Telegram before a confirmed
access loss stay paused until a deliberate Review and resume after same-peer
access returns: the review checks the exact content, media and reply, the
account and target and native rights again, and Resume acts within its
120-second token. Cancelling keeps the content. Ordinary connection
interruptions keep their existing behavior.

**Retained group work.** A folded section at the foot of the Groups list,
outside the seven-day list, holds peers with saved drafts or unresolved
operations, each with its reason line. Its history is cache-only with normal
expiry, and it offers draft view and copy, operation status and guarded
recovery when exact-peer access and the ordinary gates allow. It back-fills no
history, enables no group AI and merges or retargets no peer.

### Model roles

The daily-briefing role uses MiMo through the owner's LiteLLM proxy, as the
proxy's `xiaomi_mimo/mimo-v2.6-flash` route
([ADR 29](0029-mimo-route-and-structured-incoming-rewriting.md), ADR 39), with
no model or provider fallback, the global Concurrency Limit and the Group
Briefing Budget. Ad and bot filtering use the decision model,
`perplexity/pplx-decider-v1.2` at checkpoint `-20261016`
([ADR 49](0049-useful-response-suggestions.md) Q1). Speech follows ADR 39.

## Consequences

- Groups get a different reading aid from private chats, newly discovered
  group content is never processed until opted in, and existing private work
  keeps priority.
- Four-hour updates and the character minimum leave new originals uncovered
  for a while; partial coverage is explicit, and streamed wording stays
  provisional until validated.
- The independent budgets let both allowances be spent in full, and started
  calls may overshoot a limit slightly.
- With Hide Bots on, a useful bot notice goes unseen until the owner exempts
  that bot or turns the preference off.
- A failure's reason is not shown in the app, and an earlier day's failed
  background run has no visible recovery.
- After a missed deletion or edit, a briefing can show for a few minutes,
  until an epoch's read reaches that original.
- Known limits: retained **Low quality** briefing findings (dropped topics,
  wrong attribution, unrelated citations and weak compression) are in
  [inference](../inference.md#group-briefing-first-outcomes); the ad filter
  misses some short obvious promotions at its 0.9 cutoff, an accepted
  noncritical miss; native bot provenance, full-media and ordinary-use
  reliability are unproved.
- Open: each intake epoch's re-read and the planning pass still cost CPU in a
  busy group. The options await the owner's decision in the
  [TODO](../plans/TODO.md); native intake persistence does not change without
  it.

## Questions

Three interviews settled this ADR, each numbered from Q1. The rollout code's
"ADR 34 Q25" is Q25 of the readability list.

### Group chats (Q1-Q59)

- Q1: existing basic groups and ordinary supergroups; no forums or channels.
- Q2: the seven-day native-message activity rule, as for private chats.
- Q3: a durable per-account, per-group opt-in, initially Off, under the
  account's AI switch.
- Q4: group AI reading is briefing-only; no individual rewrites in groups.
- Q5: calendar days in the server's configured timezone.
- Q6: automatic updates at most every four hours (Q14).
- Q7: all substantive participants, the owner included; bots per Hide Bots.
- Q8: text, captions, image meanings and transcripts; Non-AI Media stays out.
- Q9: a concise topic-based account with attribution and source links that
  invents nothing.
- Q10: ordinary sending and exact replies in groups.
- Q11: no group emotion, suggestions, softening or memory.
- Q12: unseen dots without group sound or push.
- Q13: MiMo through the proxy, no fallback, shared concurrency; money from
  the Group Briefing Budget (Q54).
- Q14: a rolling four-hour clock per account and group from the last
  automatic start, kept across restarts and midnight.
- Q15: first opt-in covers today; catch-up within seven days since opt-in,
  newest first; nothing before opt-in.
- Q16: partial briefings name their covered range and missing content.
- Q17: chunk summaries plus a final briefing for busy days.
- Q18: below private work including manual suggestions, above idle memory; never the
  last free slot.
- Q19: replaced by Q58: no reserve of the Daily Budget.
- Q20: group Off pauses briefings; ordinary sending remains independent of AI.
- Q21: the final briefing streams (Q45, Q46).
- Q22: counted failures wait for deliberate Retry; automatic backoff only for
  no-work refusals (Q59).
- Q23: a seen edit or deletion withdraws that day's briefing until a
  replacement.
- Q24: bounded same-group Supporting Context, for references only.
- Q25: a source link reveals the day's originals in place and highlights the
  cited message.
- Q26: newer posts stay uncovered originals, folded under the spoiler on a
  briefed day.
- Q27: dots follow native incoming messages, not briefing versions.
- Q28: Sending, Sent, Not sent and Delivery unknown; no group Read.
- Q29: only the owner's verified own originals can be deleted (Q50).
- Q30: reaction chips are display-only in groups.
- Q31: a migrated supergroup is a new peer with AI Off; nothing retargeted or
  merged.
- Q32: lost access leaves cached reading read-only and stops new AI and
  native actions; restored access resumes.
- Q33: outgoing Rewrite and all tone controls are removed (ADR 25, ADR 53);
  groups gain no manual suggestions.
- Q34: no group media prefetch; AI media only inside an admitted update.
- Q35: attachments through the originals; no day gallery.
- Q36: Private, Groups and Saved as icon tabs in the account header (ADR 50);
  each account's tab remembered while the app runs; no remembered chat
  (ADR 44).
- Q37: Group AI processing in the group's ⋮ sheet (ADR 42 Q3); the account
  switch in Manage accounts.
- Q38: the Processing Character Threshold gates automatic briefings.
- Q39: the whole day's eligible original text counts, without reset after a
  briefing.
- Q40: new or changed analysable media exempts an update.
- Q41: the threshold is checked again before each stage; changes reset
  nothing.
- Q42: one update handles pending days newest first under one work cap.
- Q43: per update at most five generative requests, 200 new or changed
  sources and 20 minutes of speech preparation.
- Q44: within a day oldest to newest, keeping room for a final partial
  briefing; the rest continues later.
- Q45: headings and bodies stream; participants and links activate after
  validation.
- Q46: a failed stream is discarded and the last valid final, or the
  originals, returns.
- Q47: a validated "No substantive updates in the covered messages." outcome.
- Q48: opening lands at the earliest ready unseen message: its day's briefing
  or spoiler, or the exact original.
- Q49: a focused second acknowledges every ready message except folded ones.
- Q50: deletion needs verified own-user authorship and the right to delete
  for everyone.
- Q51: ADR 31's selection, exact review and recovery apply in groups.
- Q52: Retained group work at the foot of the Groups list, cache-only, with
  guarded recovery.
- Q53: a send paused by lost access needs a deliberate Review and resume
  after access returns.
- Q54: a separate Group Briefing Budget; the owner's defaults to $2.
- Q55: per member, durable, saved in Settings → Budget beside the Daily
  Budget (ADR 48).
- Q56: briefing requests charge only the group budget; the budget is fixed at
  dispatch.
- Q57: dispatch-day accounting; late receipts settle their day; the limit is
  checked before every stage.
- Q58: independent ceilings, with no borrowing, combined limit or reserve.
- Q59: Retry needs both switches and access, reuses the failed snapshot
  within 30 days and expands nothing.

### Readability, ads and bots (Q1-Q40)

- Q1: replaced by the Briefing Slot list's Q2 and Q9: a pinned, green-tinted
  date band.
- Q2: deleted bot posts are hidden; deleted human messages follow ADR 52.
- Q3: a bold topic title over normally one paragraph of one to three
  sentences.
- Q4: participants are native authors who substantively contributed.
- Q5: replaced by Q10.
- Q6: obvious ads are skipped; bots follow Hide Bots and, while it is off,
  the bot judgment.
- Q7: briefings are rebuilt once when the filtering rules change (Q11, Q12,
  Q25).
- Q8: headings and bodies stream; participants and links come after
  validation.
- Q9: ad filtering runs on the decision model (ADR 49 Q1).
- Q10: topics in order of first substantive appearance.
- Q11: the rules pass covers today only, across processing-enabled groups.
- Q12: the pass queues once and bypasses the four-hour timer once, under every
  other gate.
- Q13: bounded exact sender-profile lookups verify bots; Unknown stays
  visible.
- Q14: three participant names with an inline expand.
- Q15: excluded ads leave AI reading and participants; `(ad)` in Originals.
- Q16: critical defects must be fixed; noncritical issues are accepted.
- Q17: requested recommendations, ad discussion and personal cost-sharing are
  not ads.
- Q18: one yes/no ad question per source; 0.9 or more excludes it.
- Q19: an uncertain or failed classification keeps the source eligible.
- Q20: 12 classifier requests per update, 32 targets per call, on the Group
  Briefing Budget.
- Q21: ads count toward the character threshold; no second threshold.
- Q22: classify from text, metadata and cached meanings before fresh media
  work.
- Q23: an all-ads day completes locally as Ads skipped.
- Q24: a mixed promotional post is skipped whole and labelled `(ad)`.
- Q25: the rules pass freezes its day and group set and finishes that day
  after midnight or a restart.
- Q26: the deliberate update covers today only, from the Briefing Slot.
- Q27: an unchanged day answers up to date without a paid call.
- Q28: a deliberate update bypasses the four-hour wait, quiet and character
  minimum, keeping every other gate.
- Q29: while Hide Bots is off, ten messages are judged and a useless bot is
  remembered as ignored.
- Q30: the bot judgment covers native-verified bots; ads are judged for every
  author.
- Q31: excluded content disappears in both views; Hide Bots is On by default.
- Q32: the latest ten surviving current sources within seven cached days;
  wait below ten.
- Q33: 0.90 that all ten are useless, with missing media meaning holding the
  sample.
- Q34: an ignore binds to one account, group and bot.
- Q35: an ignore stays until the owner corrects it.
- Q36: replaced: with Hide Bots on, every verified bot leaves reading and
  briefings unless exempted.
- Q37: Hide Bots is one durable preference per member (ADR 48), initially On.
- Q38: human ads stay visible in Originals with `(ad)` and leave briefings.
- Q39: a remembered ignore applies from today; earlier days keep their
  results.
- Q40: one run per group; repeated taps coalesce; the day and cutoff freeze on
  the tap.

### Briefing Slot (Q1-Q19)

- Q1: the deliberate update stays today-only.
- Q2, Q9: a stronger date band, green-tinted, full width with a 2px accent
  rule, pinned while its day scrolls past.
- Q3: Today, Yesterday, then weekday and date, with the year only outside the
  current one.
- Q4, Q10: `↻ N new` for uncovered messages, `↻ Update` when only edits,
  media evidence, guidance or bot visibility changed.
- Q5, Q12, Q15: an icon and a word at most, with no tooltip or popup;
  `Budget` when the day's group budget is spent, opening it in Settings
  (ADR 45 Q8).
- Q6, Q7: the slot sits at the right end of today's band, in AI reading
  only; the bar at the top of the list is gone.
- Q8: Hide Bots is in the group's actions sheet.
- Q11: a grey check when today is up to date.
- Q13: earlier days have no slot, Retry or Analyzing; their automatic work
  continues unseen.
- Q14, Q17: uncovered messages on a briefed day fold under an `N new
  messages` spoiler, closed by default.
- Q16: `Syncing` while messages are verified again or the account connects.
- Q18: the chat opens on the folded spoiler, with the Unread line above it,
  when unseen messages start there.
- Q19: an opened spoiler stays open until the chat closes; a send opens
  today's; folded messages are not viewed.
