# Rewrite and transcription boundaries

Enloq's model work runs in fixed roles, each on one exact Model Route with no
selector and no fallback to another model or provider; speech's hosted
fallback is the one adopted exception.

- **Text roles**, written by MiMo: `incoming_rewrite`,
  `chat_memory` (Person and Account Memory), `trait_notes`,
  `response_suggestions` and the group roles `group_briefing_image`,
  `group_briefing_chunk` and `group_briefing`. Muse writes calmed incoming
  rewrites ([Soften hostile messages](#soften-hostile-messages)) and idiom suggestion sets
  ([Response suggestions](#response-suggestions)) instead.
- **Decision roles**, answered by Perplexity's decision model, which writes no
  text: `hostility_screen`, `emotion_classification`, `reaction_assessment`, `suggestion_labels`,
  `trait_scales`, `group_ad_classification` and `group_bot_classification`
  ([Emotion Classification](#emotion-classification)).
- **Speech**, the `transcript` role ([Speech transcripts](#speech-transcripts)).

Incoming output contains combined wording plus image context keyed to target
native IDs; image understanding is hidden in the ordinary UI. Only
`response_suggestions` proposes new wording, through manual generation or
automatic eligibility. A nonblank manual draft supplies intent and the captured
cursor; generation leaves the draft intact until an option is selected.

## Model routes and the proxy

Every live call goes through the owner's LiteLLM proxy with one caller key
([ADR 39](adr/0039-litellm-proxy-and-vps-speech.md)); the proxy holds the
provider keys. The endpoint is configuration: `./scripts/setup proxy-key` asks
for the endpoint and the caller key and saves them as `MUSE_ENDPOINT_REF` and
`MUSE_API_KEY_REF`. The endpoint must be an https URL ending in `/v1`, with no
user info, query or fragment (the deployment's is
`https://llm.globesoul.com/v1`), and the key an `sk-` key that is not an
OpenRouter key; every live adapter checks both. The reference names predate the
current routes and are kept. Offline mode constructs no live adapter. The
decision, hosted-speech, embeddings and rerank pass-throughs replace the
endpoint's `/v1` with `/openrouter/api/alpha/decisions`,
`/openrouter/api/v1/audio/transcriptions`, `/openrouter/api/v1/embeddings` and
`/openrouter/api/v1/rerank`.

- **MiMo.** The proxy's `xiaomi_mimo/mimo-v2.6-flash`, which calls Xiaomi's own
  API ([ADR 29](adr/0029-mimo-route-and-structured-incoming-rewriting.md)).
  Verification reads the proxy's `/v1/model/info` for exactly one entry of that
  name, in chat mode, with response-schema and vision support, finite
  non-negative per-token rates and a context length of at most 1,048,576
  tokens. That whole-context ceiling is informational; budgets count receipts.
  The adopted rates are $0.14 per million input and $0.28 per million output
  tokens. Finite checks refuse higher rates, so their recorded bounds stay
  valid.
- **Muse.** The proxy's `openrouter/meta/muse-spark-1.3-contributor`, which
  pins `meta` with low reasoning hidden, writes calmed rewrites and idiom sets
  only. The proxy records no rates for it, so `muse_rates` takes them from
  OpenRouter's public listing: exactly one `meta` endpoint at status 0, within
  `MUSE_ADOPTED_RATES` ($0.10 and $0.20 per million input and output tokens),
  rechecked daily as for MiMo. A higher rate is a no-work refusal.
- **The decision model.** `perplexity/pplx-decider-v1.2` at checkpoint
  `-20261016`, through the proxy's pass-through to OpenRouter's Decisions
  endpoint ([Emotion Classification](#emotion-classification)).
- **Speech.** The proxy's `whisper-large-v3-turbo` route on the VPS, with
  ElevenLabs Scribe v2 as a paid hosted fallback
  ([Speech transcripts](#speech-transcripts)).
- **Embeddings and rerank.** [Ask this group](#ask-this-group)'s routes,
  through the proxy's pass-throughs to OpenRouter's routers:
  `perplexity/pplx-embed-v1-4b` at Perplexity ($0.03 per million tokens) and
  `qwen/qwen3-reranker-8b` at Fireworks ($0.20 per million tokens). Each
  request pins its provider with `allow_fallbacks: false` and `zdr: true`.
  Verification reads OpenRouter's public listing at most daily for exactly
  one up endpoint of that provider at no more than the adopted rate.

MiMo and Muse requests ask for strict structured output (`response_format`
`json_schema` with `strict`), send no `provider` block and send no
`tool_choice`: no tools are offered, and Xiaomi rejects `none`. Reasoning is
only on or off, through Xiaomi's `extra_body.thinking`, which replaces the
proxy route's own (thinking off). Muse requests carry the same pseudonym,
schema and limits but no `extra_body`, which would replace its route's.

Text generation uses hidden reasoning, 16,384 output tokens and a 540-second
attempt. Manual and automatic suggestions share those limits. Incoming text
starts streaming after hidden reasoning, and readiness waits for validation.
Arrival screening has its own five-second deadline, including queue and route
preparation, and never streams a preview.

The response must name the route as its `model`. Reasoning arrives beside the
content and is discarded unread; content keeps a 1 MiB capture limit, and a
stream may carry up to 16 MiB on the wire. Receipts keep usage counts only
(reasoning and cached-token counts when reported), the requested
`output_token_limit`, the finish reason and the cost: `usage.cost` from a
stream's final chunk, or the proxy's `x-litellm-response-cost` header
otherwise. Unknown or malformed responses keep their cost receipts when
present; failed format has unassessable semantics. Reasoning and request
bodies are excluded from operational logs and receipts.

Every request carries one fixed `user` pseudonym per member
([ADR 48](adr/0048-members-on-the-shared-service.md)): 32 random hexadecimal
characters generated once from a cryptographic source and kept in the shared
registry's member settings (`provider_user`). It is never logged or derived
from account, Telegram or key material, and it reaches Xiaomi as sent.
Xiaomi's listed terms are no prompt training, 30-day retention and processing
in Singapore and the Netherlands; they are not zero retention. Cancelling local work does not promise provider cancellation or a refund;
observed receipts and unknown outcomes still count.

The proxy keeps request and response bodies in its spend log. Every adapter
sends `x-litellm-enable-message-redaction: true`, which keeps MiMo prompts and
replies and VPS transcripts out of it. The pass-through routes ignore that
header, so decision requests, whose `state` carries message text, are stored
with their answers. The proxy deletes spend-log rows after 30 days.

Each role's consecutive format-failure count restarts once when that role's
Model Route changes. The registry records the route per role beside the counts
(`format_route`, a JSON object), bound when the service starts, so a restart on
the same routes keeps them. A route change also makes today's group briefings
updateable once. Finite checks neither read nor write these counts.

## Processing Guidance

Instructions are a fixed core per role plus owner-editable Processing Guidance
([ADR 15](adr/0015-owner-controlled-processing-memory-and-prompts.md)). The
fields are Faithfulness rules, Incoming rewriting, Soften hostile messages,
Emotion extraction, Friendly, Professional, Person memory, Account memory,
Response suggestions, Idioms, When to suggest and Group briefings. Each holds at most
4,000 characters, is global across accounts and members, and is edited only by
the owner through the authenticated Settings endpoint. Traits and the sarcasm
question have fixed questions and no field.

The core, which the owner cannot edit, frames messages, images and memory as
untrusted data, keeps the output contracts (one text with no suggested answer,
hidden image context, change-only memory updates) and delimits owner text so it
cannot pose as the core. Guidance travels as instructions, never as message
content, and has its own allowance outside the request byte caps. Rewrite
endpoints accept no prompt text.

The guidance version is fixed when an attempt is dispatched and recorded with
its result (block, draft, memory entry, emotion, suggestion and assessment
records) and receipt; a saved change applies from the next call. Each record
also keeps the shipped-defaults revision (`DEFAULTS_REVISION` in
`backend/src/enloq/inference/guidance.py`, now 15; no value means revision 1),
so evidence can tell results of different shipped defaults and cores apart.
Saved owner fields keep their wording and version when the defaults change;
Reset selects the current default.

After three consecutive format failures for a role since the last change, that
role's automatic dispatch pauses until the owner resets or keeps the guidance;
deliberate clicks still run. Response suggestions never pause and never count
toward another role, and the decision roles never count format failures. Tests
cover behavior and contracts, never prompt wording.

## Incoming admission

Private incoming text is combined, not rewritten
([ADR 54](adr/0054-incoming-text-combined-not-rewritten.md)).
`character_admitted` admits a block with an analysable image, voice note or
Video Circle and, while the registry's `text_rewriting` switch is off, no text
at any length. The switch is off by default and not in Settings;
`text_rewriting(store, db)` reads it. Every text block is held, decided and
shown as its originals together, and [Soften hostile messages](#soften-hostile-messages) is its one rewrite.

- **A text block in the rewrite path.** If a text block without analysed media
  still reaches `Rewriting.incoming` (a Retry) and its decision first doesn't
  calm it, `Rewriting.combined` ends the job `done` with the block `original`,
  publishing a decision made first as the rewrite would have.
- **Older runs.** Rewrite earlier messages (`Rewriting.older`) lists only blocks
  with analysed media.
- **Memory.** Memory records what a held or calm-only decision read, since text
  no longer reaches the rewrite that used to record it.
- **The switch on.** Text rewriting returns under the Processing Character
  Threshold. Tests and finite checks that exercise the rewrite path with text
  set `Store.rewrites_text = True` (the `text_rewriting` fixture), which
  overrides the switch without saving it.

The **Processing Character Threshold**
([ADR 35](adr/0035-processing-character-threshold-and-empty-rewrites.md)) is a
global owner setting, default 200, saved as an integer from 0 to 10,000. It
sums Unicode code points in each current original's trimmed text or caption
across the complete block; context, metadata, transcripts and generated wording
do not count. An analysable image, voice note or Video Circle exempts the
block, and zero removes only this gate. It governs group briefings, and private
text only while `text_rewriting` is on. Quiet, target, account, budget and
concurrency gates remain; manual suggestions, failure Retry, older runs,
independent reactions and memory keep their own admission.

Held blocks use durable `incoming_deferred` records, outside runnable jobs and
model reservations. They show originals without a threshold status or counter,
reach normal original readiness after quiet and leave idle memory available.
Startup, a shared Save and source changes reconcile cached memberships only.
Preparation and dispatch recheck the latest value; a Save may supersede a
preparing attempt, while already dispatched valid work may finish. Successful
unchanged results and unchanged failures are not regenerated by a setting
change. Readiness and notification identities stay source and burst based, so
later completion does not alert the same burst twice. A held block still gets
its decision ([ADR 49](adr/0049-useful-response-suggestions.md) Q3):
[Emotion Classification](#emotion-classification) from the cache only, once per
held revision, after its quiet period.

The provider's incoming `parts` schema and the independent final validator
share the target-only `has_target_text` predicate. Nonblank target originals,
captions or transcripts require at least one part. Textless image targets still
permit empty visible parts, even with textual Supporting Context. This does not
prove faithful or complete wording. An empty textual final records
`omitted_target_text`, and the UI says **The AI returned no rewrite text.
Showing originals.** Provisional output rolls back, counted receipts remain and
the failure waits for deliberate Retry.

## Structured incoming rewriting

Incoming rewrites have plain-text structure
([ADR 29](adr/0029-mimo-route-and-structured-incoming-rewriting.md)): one
sentence for a single point, short paragraphs per topic, "•" lines for three or
more parallel items (numbered when order matters), fragments and repeats
grouped by topic in order of first mention, in the sender's voice and
languages, never translated and with no summary line. Speech becomes clear
written text: fillers, false starts and repetition go, a self-correction keeps
its final version, an unmistakable recognition slip is corrected from context,
and a possibly misheard word, name or number keeps its best reading followed by
"(?)". Typed text is never marked. Forwarded content stays attributed to its
source; imported messages are the sender's own.

The incoming provider schema returns `parts` first: ordered `{kind, text}`
items, `paragraph`, `bullet` or `numbered`, each one line of plain text, at
most 80. No JSON string needs a line break.

- **Rendering.** `render` in `backend/src/enloq/inference/streaming.py` builds
  the stored text: a list follows its lead-in on the next line, other runs are
  divided by a blank line, numbering restarts per run and stray list markers
  in items are dropped. `PartsPreview` streams the rendered parts received so
  far. The bubble keeps line breaks; previews collapse them.
- **Completeness.** The fixed incoming core states the parts contract and a
  completeness rule: every distinct question, request, fact, condition,
  feeling, hedge and topic appears; only true repeats merge; no summary
  replaces items; nothing the sender did not express is added. The shared core
  says removing disfluencies is not invention, while doubtful transcript
  content stays marked.
- **Validation.** Parts are rendered into `text`; both or neither shape, blank
  parts and unknown kinds fail as `schema`. Offline authored engines may still
  return a single `text`. The validator and previews accept a raw line break or
  tab inside a string, while any other raw control character stays
  `invalid_json`.

## Concurrency, refusals and budgets

Model calls share one global Concurrency Limit, 1 to 8 and 4 by default, set by
the owner in Settings and applied without a restart. Check provider request
limits before raising it; Meta's Contributor route behind Muse may limit
requests to 100 a minute. Waiting calls are served by class:

1. new incoming work, including arrival screens and a per-block Retry;
2. unread catch-up;
3. Reaction Assessment;
4. the chat-level run over older messages;
5. manual or automatic response suggestions and their labels;
6. group briefings and their filtering;
7. memory updates and traits.

An Emotion Classification takes the class of the incoming work it follows.
Within a class the least recently served member goes first, then the least
recently served account. With a limit of 2 or more the four lowest classes
never take the last free slot; at 1, work runs strictly by priority. Memory is
admitted only while communication is idle.

Jobs are claimed atomically into a pre-dispatch state, so no job runs twice; a
restart returns claimed work to the queue and marks dispatched work unknown. At
most limit + 1 jobs prepare or call at once, and one job's unexpected error
fails only that job. Blocks in one chat may run together, while a shared voice
note is transcribed once.

For generation jobs, a missing or non-OK
exact route at preparation is a no-work refusal: the job is refused before any
reservation or receipt exists. Preparation re-reads the route only when the
running engine has no verification or it is over 24 hours old, so this catches
unavailability at that refresh (for example the first call after a restart).
An outage that begins between refreshes dispatches normally and fails as
`Provider unavailable`: an unknown outcome that counts and needs Retry. Whether
background work should re-check sooner is an open follow-up in the
[TODO](plans/TODO.md), not adopted behavior.

HTTP 429 and 402 and refused connections are also no-work refusals. They count
nothing and re-queue as a new attempt after the provider's requested backoff, or
30, 60 and 120 seconds, at most three times, then fail visibly with Retry.
Account AI Off and budget waits still prevent dispatch; an explicit memory Retry
keeps its permission through no-work backoff even when automatic work is
format-paused. Missing required output parameters or unbounded rates fail
immediately as a changed route contract. Arrival screens use the original
notification fallback immediately on refusal, failure or deadline; they never
retry or resample.

Each member has two budgets ([ADR 15](adr/0015-owner-controlled-processing-memory-and-prompts.md),
[ADR 34](adr/0034-group-chats-and-daily-briefings.md),
[ADR 48](adr/0048-members-on-the-shared-service.md)), counting only their own
accounts:

- **The Daily Budget** pays for every private role, including manual and
  automatic suggestions, arrival screening and hosted speech.
- **The Group Briefing Budget** pays for briefing image understanding, chunk
  summaries, final synthesis, group filtering and briefing Retry.

Each ranges from $0 to $10 and is set only by the owner, with an explicit
cent-valued Save; the owner's defaults are $1 and $2, a friend's $0.50 each, and
a member sees theirs read-only. Every change is logged and nothing changes a
budget automatically. The two are independent: no borrowing, combined ceiling
or group share of the Daily Budget. Older-message runs stop while less than a
quarter of the Daily Budget remains, keeping it for new messages.

A new call starts only while the member's counted spending for the budget day is
below the budget: receipted costs plus a flat $0.01 for each call whose outcome
is unknown, including calls still running. A refusal where the provider did no
work counts nothing.

- **Receipts.** A receipt counts the larger of its charged `cost` and its
  `cost_details.upstream_inference_cost`; Xiaomi's receipts have reported a
  zero charge beside a nonzero upstream cost. It keeps the content-free
  `is_byok` flag when reported. Counting spans every route and engine; only
  offline authored fixtures are exempt.
- **The flat estimate.** $0.01 is a rough upper estimate: at the adopted MiMo
  rates a cap-length 16,384-token background response costs about $0.0046 of
  output. Revisit it if rates change materially.
- **Overshoot.** There are no full-context reservations, so running calls may
  finish slightly past a budget, at most about one call per running slot.
- **The budget day.** It follows the server's configured timezone and a
  monotonic calendar-day watermark, so a rolled-back clock cannot move it
  backwards. An unknown outcome counts only toward its own day. Ledger rows keep
  their recorded amounts and status.

Reservations, receipts and budget changes carry a `family` (`daily` or
`group_briefing`) and the member. Dispatch fixes the family and the original
day; receipt settlement reads the stored family, so late, failed, invalid and
interrupted cost evidence settles where it was dispatched. Both families share
the watermark, the reset and the unknown estimate. New briefing preparation
checks its latest budget without recording a dispatch, and dispatch checks
again atomically; valid started work may finish. Settings protects each
family's unsaved edit during a shared refresh and reads current values after
Save, so a delayed read or an older POST snapshot cannot undo an accepted Save.

## Group briefings

[ADR 34](adr/0034-group-chats-and-daily-briefings.md) gives each opted-in group
one briefing per calendar day in the configured timezone, written by MiMo and
paid from the Group Briefing Budget.

### Group briefing inputs and contracts

Briefings keep their own daily coverage, frozen attempts, reusable chunks and
final results; they never reuse private blocks, emotion, suggestions or memory.
Targets preserve native speaker identity, unknown authors, direction and
forward provenance; service events and wholly Non-AI input start no call.
Supporting Context uses cached same-group originals only: up to 20 recent
predecessors and one direct replied-to source within retention, with no reply
chain or native history fetch.

Partitioning keeps whole sources oldest first, recording oversized or
work-bound omissions. The 24,000-byte target allowance, 6,000-byte Supporting
Context allowance and 32,000-byte request bound include source and dependency
metadata. Fresh transcripts are checked again after preparation and can be
repartitioned from cache without repeating recognition. Raw images keep the
independent four-image and per-file/combined limits. Small days can use a
direct final request; large days use validated reusable topic chunks, with
space retained for a final partial briefing. Native intake gaps, incomplete
ranges and anonymous omission counts remain coverage evidence; cached IDs alone
never establish completeness.

Bots and advertising leave briefings before any summary:

- **Hide Bots.** With Hide Bots on (the default; each member has their own),
  every native-verified bot's posts leave group reading and briefings with no
  model call: never model input, classifier targets, participants or
  uncovered sources. Stop ignoring this bot exempts one bot in one group.
  Unknown provenance stays visible and eligible; a name proves nothing.
- **Advertising.** `group_ad_classification` asks the decision model one
  yes/no question per source. A probability of 0.90 or more excludes the whole
  source, mixed useful and promotional posts included, and labels its original
  `(ad)` in Originals. Requested recommendations, discussion about ads and
  personal requests for help or cost sharing stay eligible.
- **Bot judgment.** Only while Hide Bots is off, `group_bot_classification`
  judges a native-verified bot's latest ten distinct current sources within
  seven cached days together. A yes at 0.90 remembers an ignore for that
  account, group and bot until the owner corrects it, with no reassessment or
  expiry. A sample missing a required image or speech meaning is held before
  dispatch, whatever the model would say.
- **Bounds.** Classification reads text, captions, trusted native metadata and
  already-cached meanings and transcripts, adding no media work of its own. A
  request packs at most 32 targets, split earlier when whole-source bytes or
  the repeated-state input bound require it. An update makes at most 12
  classifier requests, separate from its five generative requests, within the
  same 200-source cap. Uncertain, failed, capped or unclassified sources stay
  eligible.
- **Local completion.** A day of only validated ads or hidden bots completes
  locally without a summary request.

A separate owner-editable Group briefings field shapes the three generative
group roles. Fixed group instructions frame all inputs as untrusted, preserve
attribution and substantive discussion, and forbid invented agreement, facts,
commitments or task tracking. Each topic (contract version 2) has a title, a
concise body, citations and participant source IDs; participants are native
authors who substantively contributed, and topics follow their first
substantive appearance in the day. Only final topic text streams; image and
chunk notes, citation metadata and reasoning stay hidden. A substantive output
requires nonblank cited topics; citations are strict native IDs belonging to
its actual targets or validated chunk coverage, never supporting-only messages.
The explicit no-substance outcome renders "No substantive updates in the
covered messages." Blank, truncated and malformed output fails. Shape,
membership and currentness checks establish mechanics, not semantic
faithfulness.

Chunk reuse fixes the group guidance, core, role and route, native identities
and the actual prepared dependencies; unrelated private guidance Saves do not
invalidate it. Appending preserves an earlier cutoff. A seen change withdraws
dependent work and rejects late publication: an edit, a deletion, a changed
author or signature, a withdrawn proof, lost or changed access, or an original
that the intake read passed without finding. A new intake epoch alone
withdraws nothing. Published briefings, chunks, consumed exclusion decisions
and running work stand until an original is seen to change; a running update
waits for the read to prove its originals again, and new work for a day starts
only after the read has passed that day. Publication checks every target and
projected context dependency atomically; partial coverage can continue later
without regenerating completed targets. Provisional prefixes stay in memory and
never become completed results. Retained cached reading has a validity check
separate from native dispatch and reuse.

The group conversation projects each retained day without starting work. Only
the matching active final attempt's usable prefix replaces visible wording; its
citations stay inactive and only a validated final's covered native IDs fold
originals. Failure or interruption restores a still-current final or
Originals. Partial and no-substance outcomes keep explicit coverage, intake
gaps and omissions. Cached citation and day-original routes return at most 100
same-day originals within 30 days and revalidate the final after annotation,
with no native history read. Temporary day Originals and per-day return
positions leave the global Reading View untouched. Retained originals outside
the latest conversation page are rechecked through bounded cached reads on
refresh, so later edits or deletions cannot leave a withdrawn citation
highlighted as current.

Automatic selection waits for 30 quiet seconds and a durable rolling four-hour
start clock. First opt-in admits the current calendar day; later missed or
changed cached days since opt-in remain within seven days, newest first under
one update cap. Lookup creates no clock, attempt or reservation. A deliberate
Update, today only, bypasses the four-hour clock, quiet and the character
threshold, keeps every other gate and work cap, reuses unchanged results and
reports Up to date without a paid call. Groups share global capacity below
every private role and above idle memory, keeping the protected last slot and
fairness across members, accounts and groups. Each claim performs only one
bounded stage. Threshold-deferred group work has no waiting status or
memory-idle footprint.

Every fresh stage rechecks account and group processing, current native
dependencies, whole-day character and media admission and the member's Group
Briefing Budget. The reservation and the update's five-request and
200-fresh-source accounting share one transaction; local speech starts durably
charge their measured duration against 1,200 seconds. No-work route refusals
cost nothing but count toward the request cap, with bounded backoff. Counted or
uncertain unchanged outcomes stay Retry-only, including an append outside the
same bounded failed prefix. Valid started work keeps its captured guidance and
native proof; a pending stage uses one canonical guidance snapshot before any
await, with common and role fingerprints derived together.

Retry (today's only, from the Briefing Slot) uses only a failed or interrupted
frozen intent whose cached originals are current and within 30 days, and needs
both processing switches, native access and budget. It bypasses automatic
cadence and character admission but keeps the work caps, and never expands,
backfills or regenerates completed coverage. Earlier days have no Retry; their
automatic work continues unseen. Validated earlier finals and chunks and image
and transcript caches stay reusable; reused targets consume no fresh-source or
speech allowance. Successful transcripts survive speech-cap exhaustion, with
the unavailable remainder explicitly omitted. When combined earlier notes
exceed the request bound, final assembly keeps fresh progress and explicitly
omits earlier reusable coverage first. The union of still-current validated
finals prevents unchanged partial results from starting another automatic run.
Permanent material or automatic-window invalidation closes orphan continuations
without resetting the clock, caps, receipts or caches; ordinary switch, budget
and threshold holds stay resumable. A started local worker may finish caching
before an expired automatic continuation closes.

Group preparation takes per-call admission and currentness hooks. Fresh media
fails closed without its admission hook; fresh speech also needs its durable
charge hook. The validated ffprobe duration is charged immediately before
transcription starts, after the shared private/group lock; invalid probes and
paused unstarted work consume no speech allowance. Cached derivatives are free.
A started, materially valid transcription may finish while processing is Off,
with source and proof rechecked on its derivative-write transaction; no next
fresh stage starts while paused. Audio reaches a provider only through the
hosted fallback for long notes and VPS outages.

### Citation check

[ADR 34](adr/0034-group-chats-and-daily-briefings.md)'s citation check runs as
its own stage, `briefing_citation_check`, between a validated final and its
publication, one decision request per topic, in topic order:

- **Packet.** The topic's title and text, each cited original as the briefing
  read it (text or caption, cached transcript and image meaning, speaker name,
  date, forwarding), and the cached originals they reply to as context, up to
  6,000 bytes, leaving out ads, ignored or hidden bots and Non-AI Media.
  Packets are whole: at most 31 citations, 24,000 bytes of cited originals and
  32,000 bytes of state; the repeated-input bound is the ad filter's 240,000
  bytes, since the state is billed once per question. A larger topic stays
  unchecked.
- **Questions.** `cites_<id>` for each cited original, whether it supports at
  least part of the topic, and `claim`, whether together they support all of
  it. The rules are fixed text with their own fingerprint, not Processing
  Guidance.
- **Outcome.** A citation below 0.5 leaves the topic's links, and so does its
  participant unless another remaining citation is theirs, which then names
  them. The final is stored as written, with the check beside it; the view,
  participants and source links apply the removals, and a topic left with
  no citation is hidden (citation check Q4).
- **Unchecked.** A refused or unavailable route, a spent Group Briefing Budget
  or the update's twelve check requests leave that topic and the rest
  unchecked without further calls; a malformed answer or an oversized packet
  leaves only its topic. The final then publishes with its allowlist-checked
  citations. A spent budget never holds a finished final back: the stage's
  admission skips the budget gate, and each request still meets it.
- **Changes.** A source that changes during the stage withdraws the attempt
  as at any other stage.

### Finite authored group qualification

`scripts/check-group-briefings.py` runs the real image, reusable-chunk and
streamed-final pipeline in a temporary Store with FakeTransport. It uses wholly
authored multi-speaker messages, forwarding and direct-reply context, prepared
transcripts and one small drawn timetable. It reads an authorized, AI-enabled
account identity from the shared registry, without opening that account's
database, session or correspondence. Run it between service stop and start
under the owner lock; the [upgrade procedure](operations.md#start-stop-and-upgrade)
owns lifecycle commands. It dispatches no classifier requests.

The ceilings are 12 model requests, 1,200 seconds and $0.10 counted cost within
the current Group Briefing Budget; command-line options only lower them. A
fresh exact MiMo reading must satisfy the adopted rate ceiling. Before each
dispatch, a conservative bound covers the prepared instructions, packet,
schema, complete image URL, image expansion and the role's output cap. The
larger of that bound and the ordinary $0.01 unknown estimate must fit both the
remaining run allowance and the current briefing-day allowance. Normal shared
reservations and receipts keep account, purpose and dispatch day; the run total
does not reset at midnight.

Unknown, refused, malformed or failed first outcomes, an unexpectedly large
receipt and any run bound stop further stages. No Retry or resampling occurs.
Owner switches, threshold, guidance, budget settings and format counters stay
unchanged; only counted group-family reservations and receipts persist.
Private evidence under `.local/` keeps first outputs and source and format
checks, with manual semantic expectations recorded separately. Offline tests
prove the real staging, accounting and guard mechanics; the
[first semantic outcomes](#group-briefing-first-outcomes) are below.

## Ask this group

[ADR 59](adr/0059-ask-this-group.md)'s `POST /chats/{peer}/ask` answers one
question at a time per account, in the manual-suggestion class, paid from the
member's Group Briefing Budget. It needs account AI, the group's AI and verified
access.

- **Pool.** Eligible current originals from the group's first opt-in day,
  within the 30-day cache: text and captions, cached transcripts and image
  meanings, without ads, ignored or hidden bots, service events and Non-AI
  Media. No Telegram read and no media work. An empty pool answers not found
  with no model call.
- **Retrieval.** An in-memory SQLite FTS5 trigram index built for the
  question ranks by BM25 (each word of three or more letters, any of them); the
  question's embedding ranks by cosine similarity. Reciprocal rank fusion (k
  60) keeps 40, the reranker reorders them, and the writer reads the best 25
  in chronological order. A rerank or embedding refusal that did no work, such
  as a provider's per-second quota, is tried again after the wait it names, at
  most twice and five seconds each.
- **Vectors.** Computed when a question first needs them, in requests of at
  most 64 texts and 100,000 bytes (a text's first 8,000 characters), and kept
  per message revision and model with the 30-day content cache. An edit or
  deletion drops them.
- **Writer.** gpt-6.1-sol in the `group_question` role on the proxy's
  `chatgpt/gpt-6.1-sol` subscription route at its medium default, through the
  chat route, which drops `response_format`: the reply must hold one JSON
  object, at most four statements whose citations are among the retrieved
  IDs, none meaning not found, validated strictly. Receipts record $0. Its
  instructions are fixed and read no Processing Guidance.
- **Check.** Each statement goes through the citation check as
  `group_answer_check`. The answer returns each statement's kept and
  unsupported citations and whether the whole statement is supported; how the
  clients show an unsupported statement waits for ADR 59 Q6. A check that
  cannot run returns `unchecked`, and no answer shows.

Nothing about a question or an answer is stored beyond content-free receipts.

## Private processing and media

Automatic private rewriting, emotion, suggestions and memory targets belong to
eligible human private chats active in the last seven days; a chat opened or
written to outside that list starts no background AI until a message makes it
recent. Manual suggestions can instead use an authored draft alone, under
their ordinary account, target and budget guards. Background work and initial
catch-up admit sources within seven days,
unread ones only for catch-up; change-driven blocks wait 30 quiet seconds.
Explicit processing (the chat-level Rewrite earlier messages, or a failure
Retry; completed results have no repeat action under
[ADR 26](adr/0026-mobile-message-actions-and-send-status.md)) may target any
block in the local 30-day cache of such a chat
([ADR 15](adr/0015-owner-controlled-processing-memory-and-prompts.md)).

Context holds at most 20 preceding same-chat messages from the local store,
within seven days of now (or of the target's own date when the target is
older), plus the single direct reply, fetched by exact native ID even when
older. No recursive reply chain or older-history search occurs. Supporting
enriched text is bounded to 6,000 UTF-8 bytes (so no more than 6,000 tokens);
instructions and the current block have a separate allowance, within a
32,000-byte enriched request cap. Required target input is never silently
truncated.

Incoming image preparation permits four images, each at most 5 MiB, 4,096
pixels per dimension and 16 million pixels after decoding, and at most 20 MiB
per call. Cached unchanged image context is reused as input. Exactly the images
attached to this call need a new image-context record, supporting images
included; cached or placeholder images are not described again. The per-call
schema and final validator share this bound, so four cached images plus a new
image do not require five output records.

Failed output stores only a fixed `format_failure` receipt code:
`invalid_json`, `schema`, `incomplete_response`, `image_references` or
`omitted_target_text`; memory adds `memory_text_bytes`, `memory_text_length`
and `memory_text_empty`. Only these fixed codes are extracted from validation
failures; arbitrary fields, inputs and exception contexts are never kept, and
other schema failures stay `schema`. Failed output content is never stored,
and failures recorded without a code are not reclassified. Receipts and unknown
costs still count.

Non-AI media ([ADR 16](adr/0016-persistent-media-cache-and-non-ai-media.md))
never fails a block. Documents, stickers, GIFs, videos, polls, locations,
contacts and unknown items reach the model only as a placeholder with kind and
safe metadata: file name, size or duration, sticker emoji, venue or contact
name and poll question and options. Normalized metadata never stores phone
numbers, vCards or coordinates, and file contents are never read for the model.
A link preview is ordinary text.

- **Not analysed.** Target images over a bound or undecodable, a fifth image
  and audio beyond the transcription bounds become visible "not analysed"
  placeholders; a supporting-context item that cannot be prepared becomes a
  placeholder.
- **Failed.** A target image that cannot be downloaded, or a target voice note
  or Video Circle whose transcription fails, keeps the block failed with
  Retry, because its substance would be missing.
- **Nothing to rewrite.** A block with no text and no analysable media makes no
  model call, is never queued and is re-evaluated only when a source changes.

Outgoing image drafts use private durable files distinct from temporary leases.
[Native delivery bounds](media-delivery.md) document caption, album and receipt
handling.

Recipient Emotion ([ADR 22](adr/0022-recipient-emotion-and-adaptive-rewriting.md))
has a fixed bounded code contract, assigned by
[Emotion Classification](#emotion-classification); the rewrite's own output
carries no emotion, so an emotion field there is a `schema` failure. The store
keeps one assessment per account and chat, with its attempt, guidance and
native and derived source dependencies. The shared EmotionView uses native
chronology and a deletion-inclusive latest-message barrier, withdrawing changed
evidence and expiring 12 hours after the latest target source time. Reads and
expiry never queue inference. Stored assessments follow 30-day source
retention. The single Emotion extraction field instructs both decision roles
(incoming classification and Reaction Assessment); their protected core
excludes memory and history and facial or acoustic inference.

Native quote and forward attribution accompanies originals. Under
[ADR 27](adr/0027-header-message-menu-forwarding-details-and-voice-playback.md)
incoming, emotion, suggestion and reaction-assessment inputs also carry a
forward's original date and channel signature when known; an imported message
is projected as the sender's own (`forwarded: false`, `imported: true`) with
its original date. Absent details are omitted. The shipped faithfulness and
incoming defaults keep forwarded content explicitly attributed instead of in
the sender's voice. Filling in missing details invalidates no result; coverage
is `backend/tests/test_forward_attribution.py`. Incoming parts are requested
first, and the SSE parser exposes their incremental prefixes without waiting
for image metadata, regardless of field order.

An assessment's captured native and derived dependencies invalidate it on
source edits or deletions, a conflicting known quote or forward attribution
(filling in an unknown value does not) and transcript replacements; new
incoming messages and time or cache expiry after dispatch do not. The header
renders the same projection with local deadline expiry.

Transcription and image preparation read the
[media cache](media-delivery.md#incoming-media-cache-and-playback) through
short leases cleaned on exit and bounded orphan cleanup; draft images survive
until send or discard. The subprocess runner has fixed arguments, bounded
input, output and stderr, process-group cancellation, a parent-lifetime guard
and an OS mutex that survives parent failure. It runs the Mac's Whisper and the
ffprobe and ffmpeg steps around proxy transcription.

Offline and browser tests use authored synthetic cases. Actual speech, provider
responses and native Telegram observations are separate evidence, and none
establishes broad semantic quality or the owner's adoption decision.

## Response suggestions

[ADR 25](adr/0025-emotion-response-suggestions.md) and
[ADR 56](adr/0056-idioms-either-signal-hiding-and-memory-off-by-default.md)
own automatic and manual `response_suggestions`. Both share the account
member's Daily Budget, receipts, unknown-cost accounting, concurrency, Account
AI switch and enabled memory. MiMo writes Friendly and Professional; Muse
writes Idioms. There is no provider fallback.

Each private chat has independent **Automatic suggestions** (On by default)
and **Style** (Friendly by default; Friendly, Professional or Idioms).
`GET` and `POST /chats/{peer}/suggestions/settings` share the controls across
the member's clients. `suggestion_settings` replaces `suggestion_choice`:
Off becomes false/Friendly, On true/Friendly and Idioms true/Idioms. The
server-wide Automatic suggestions switch is preserved. Removed Rewrite tones
seed no style. A style change starts no work; queued and shown sets retain
their captured style. Either automation switch Off hides automatic sets and
cancels queued automatic generation only; manual sets stay usable.

**Automatic generation.** New or changed incoming work, after 30 quiet
seconds, qualifies through a current usable non-neutral emotion or usefulness
at least 0.7. A correspondent's reaction still needs both a non-neutral emotion
and usefulness at least 0.5. An unchanged source reuses its pending, ready,
empty or failed outcome. Reading, restarting, changing guidance or style and
switching automation On never backfill or resample. Later own messages or
reactions answer incoming sets; source changes, replacement and the 12-hour
source-time lifetime withdraw them. The shared projection checks validity for
reading, dispatch and insertion.

**Manual generation.** `POST /chats/{peer}/suggestions/generate` names the
exact saved draft revision; a cursor that earlier clients still send is
ignored. It bypasses incoming,
unanswered, emotion and usefulness gates, including after an own message or an
ended exchange. A draft alone suffices. A blank draft with no usable cached
conversation is refused. Input captures at most 20 same-chat messages from the
last seven days and one cached direct reply, prioritising the newest context
within 6,000 UTF-8 bytes including cached transcripts and image meanings.
There is no history fetch, media preparation or reply chain. Draft text has a
24,000-byte bound; the complete prepared packet has the existing 56,000-byte
bound. No draft, attachment, reply target or send changes during generation.

Every manual option is a complete message. A blank draft requests messages
grounded in the conversation; a nonblank draft is the starting idea, which
options develop beyond its own words, keeping its stance, feelings and facts
and inventing none. Square-bracketed draft text is the owner's instruction to
the writer: it never appears in an option and never chooses the language,
which the draft's plain wording supplies. Each explicit press requests fresh
alternatives; duplicate requests while the current manual request is pending
reuse it, and both clients keep Suggestions disabled while any set for the
chat is queued, running or waiting. A current manual set owns the suggestion
area, so finishing automatic work cannot overwrite it.
Manual sets expire 12 hours after the request and withdraw on a draft revision,
new message or used source, attribution or derivative change. Nothing
regenerates on withdrawal. Settings changed during a call apply next time.

**Output and selection.** The writer returns zero to three distinct nonblank
options with no character limit; the request's 16,384-token output bound is
the only bound, and a chosen option longer than Telegram's 4,096 characters
meets Send's visible refusal. When the manual draft holds square-bracketed
text, an option with any square-bracketed text fails format without
resampling. Draft literals protect copied exact values during typography
normalization. One decision-model `suggestion_labels` call labels a nonempty
set as acknowledge, support, clarify, celebrate,
accept, decline or later. Any answer stance may stand alone or repeat across
distinct options, for automatic and manual generation in every Style. A
nonblank manual draft supplies intent that its complete messages follow; all
factual constraints persist. With no supplied intent, options invent no facts,
feelings, fault or commitments.
Idioms retain ADR 56's adopted advice and common-expression exceptions.

An empty set is a completed outcome. Format and provider failures, unknown
outcomes and interrupted calls fail visibly; processed output is never
resampled. No-work generation refusals retain the bounded three retries after
provider backoff. Stale results keep their costs and never publish. Pending
records keep only the bounded input, removing it on completion or expiry;
content otherwise follows 30-day retention. Memory is read at dispatch under
Remember this chat and is never part of the source identity.

Old failed sets keep their failure: their discarded options cannot be recovered.
A deliberate Suggestions press requests a new attempt under the current rule;
deployment and reads never regenerate them.

Selection posts attempt, option index, draft revision and the caret to
`/suggestions/insert`. A manual option replaces the draft's text, with the
caret at its end; an automatic option is inserted at the caret, preserving
every existing draft character and adding separating spaces where words would
join. Both retain images, a GIF and the reply target and store the previous
text for **Undo suggestion**. Neither sends; Send asks first while the draft
still holds square-bracketed text. Later expiry cannot remove chosen text.
Folding remains shared through `/suggestions/fold` until the next set arrives.

Typography normalization applies only to new generated options: typographic
sentence dashes, quotes and apostrophes become keyboard forms while supplied
literals and uncertain values stay intact; literal recognition reads supplied
values and quotation windows of at most 280 characters. Emoji escapes of the
form `\u0001XXXX` are decoded before parsing; other control characters fail.
Duplicate checks apply after normalization. Drafts and cached reads remain
verbatim.

**Diagnostics.** DEBUG `enloq.suggestions` events use the fixed
`suggestion_decision` code. The privacy formatter allows only generated opaque
attempt IDs, manual/automatic trigger, fixed generate/skip/outcome reasons,
style, emotion code, bounded probabilities, thresholds and option count.
It rejects every other field. No native account/chat IDs, names, source text,
drafts, prompts or model reasoning enter logs. Scheduling savepoints isolate
suggestion failures from the already-published decision.

Coverage includes `test_manual_suggestions.py`, `test_suggestion_choice.py`,
the generation/state/control tests, both composers' tests and phone-size web
journeys. Cached sets remain readable while Account AI is Off; new manual
requests still require AI and budget admission.

## Reminder sets

[ADR 58](adr/0058-recurring-message-reminders.md) gives a private chat with
pending reminder occurrences one manual set covering all of them, which owns
its suggestion area: no automatic set is prepared while any occurrence of
the chat exists, and an older automatic or manual set is not shown. The set
is requested when the timing tick creates an occurrence, two minutes before
its alert; 30 quiet seconds after new content in the chat; when a client
opens the chat (`POST /chats/{peer}/reminders/open`); and by the Suggestions
button, which asks for the same kind of set. The first three prepare only
when the current set no longer fits: its occurrences, draft revision, cached
messages, their transcripts and image meanings, and Kept Messages are part
of its identity, and an unchanged identity is never prepared again, even
after a failure. Only a press asks afresh on unchanged input; duplicate
presses share the active attempt. Account AI Off, a signed-out account, a
busy composer or a draft over the input bound prepare nothing in the
background.

The writer packet's `reminders` list carries each occurrence's Message Idea,
or a `check_in` intent without one, and its fallback language. `draft` is
the chat's draft as further intent. Context is the 20 newest cached messages
within the 30-day cache, and the draft's cached reply target, within 12,000
UTF-8 bytes including transcripts and image meanings, split at the earliest
covered due time: later ones go in `messages`, which the options answer,
with accept, decline and later variants when they ask for something;
earlier ones in `context`, reference only. `kept` holds each schedule's Kept
Messages, and enabled memory is read at dispatch as for every set. The
fixed `REMINDERS_FRAME` follows the manual instructions; brackets in a draft
or a Message Idea instruct the writer, and an option with bracketed text
then fails format. Ideas and Kept Messages also supply typography literals.

The existing Style, MiMo/Muse writers, Decider labels, memory restrictions,
Daily Budget, capacity, twelve-hour validity, replacement, Undo and
pending-send guards apply. Choosing an option marks the draft with the set's
occurrences; edits keep the mark, Undo and clearing drop it, and Send
carries it to the Send Operation, whose Telegram confirmation completes the
occurrences with a Message Idea. Schedule edits and removal, completion and
occurrence replacement withdraw the set and reject late output without
changing drafts. Timing, list reads, previews and alerts make no model
requests and stay available with Account AI Off.

## Reaction Assessment

[ADR 26](adr/0026-mobile-message-actions-and-send-status.md) adds a background
role, `reaction_assessment`: one request on the decision route
([Emotion Classification](#emotion-classification)), with the member's Daily
Budget, global concurrency (class `reaction`, after unread catch-up) and
no-work refusal backoff. Only the correspondent's dated 👍, 🤝, 👌 or 👎 on a
cached target no more than seven days older than the reaction, in an eligible
recent chat, can trigger it; the owner's reactions, other emoji, reactions
dated before the feature's introduction and undated evidence never do (see
[history](history.md#reaction-metadata-and-recipient-read-state)).

Each confirmed change restarts the chat's 30 quiet seconds, so a burst makes at
most one call. At dispatch the subject is the chat's newest such reaction, by
native addition time; a reaction removed before its assessment began is
ignored, so a burst that returns to the last processed state makes no call. If
newer incoming communication exists, nothing is assessed. The input is the
reaction, its exact target (possibly the owner's message) and up to 20 cached
messages from the conversation as it stood when the reaction was added, with
cached transcripts and image meanings only: no Telegram read, media
preparation, memory or history. An unchanged reaction and context reuse their
outcome, including a failure; nothing is resampled. An edit of a message the
assessment used schedules a new pass.

The request asks the nine-code `emotion` choice and a `suggestions_useful`
yes/no question; usefulness is yes at a probability of 0.5 or more, a fixed
implementation default. The owner's When to suggest field follows that
question's fixed core. Only the emotion question carries each emoji's
conventional meaning toward its target (👍 approval, 🤝 agreement or a settled
deal, 👌 fine, 👎 disapproval; positive unless the context shows reluctance);
suggestions keep the rule that a reaction supplies no agreement, commitment or
fact. Invalid answers are a format failure with its fixed diagnostic that
leaves the signal unavailable and never pauses other roles.

The newest of the latest incoming communication and the latest such reaction
is the current emotion source. A reaction's result expires 12 hours after its
native addition, not its observation. New or changed evidence withdraws it,
late output cannot replace newer evidence, and removing the current reaction
withdraws its result without reviving an older estimate. A useful, usable
non-neutral result schedules one separate Response Suggestions request grounded
in the reaction and its target; later native outgoing text or images answer
it, the owner's own reactions do not, and an accepted send suspends it.
Authored coverage: `backend/tests/test_reaction_assessment.py`.

## Emotion Classification

[ADR 30](adr/0030-decider-emotion-classification.md) assigns Recipient Emotion
with Perplexity's decision model, which writes no text: it reads a `state` and
answers named questions with probabilities. The exact route is
`perplexity/pplx-decider-v1.2` through Perplexity only, on OpenRouter's
Decisions endpoint ([ADR 49](adr/0049-useful-response-suggestions.md) Q1), with
fallbacks disabled and no fallback to MiMo, to an earlier Decider or to a later
model. Requests go to the proxy's pass-through (the endpoint with `/v1`
replaced by `/openrouter/api/alpha/decisions`), which forwards the body
unchanged with the proxy's OpenRouter key; the proxy stores these request
bodies for 30 days. The engine (`backend/src/enloq/inference/decider.py`)
reuses the MiMo endpoint and key binding and sends the same member pseudonym.

- **Verification.** It re-reads OpenRouter's public
  `/models/perplexity/pplx-decider-v1.2/endpoints` under the same 24-hour rule
  and requires exactly one Perplexity endpoint at status 0 within the adopted
  rates ($0.02 per million input tokens, $0 output) and, when the endpoint
  names a dated checkpoint, the adopted `-20261016` one. Anything else,
  including a higher rate or a later checkpoint, is a no-work refusal.
- **Responses.** A response must echo the model or that checkpoint name (kept
  in the receipt as `checkpoint`) and provider Perplexity; an unqualified echo
  fails as unavailable but keeps its receipt.
- **Receipts and time.** Receipts count the larger of `usage.cost` and any
  upstream cost; the flat $0.01 covers an unknown outcome. Each attempt has a
  60-second deadline.
- **Terms.** Perplexity publishes no retention or training terms for this API.
  OpenRouter lists this endpoint as zero data retention (checked 27 October
  2026); that is OpenRouter's classification, not a Perplexity term.

Refusals and outages ([ADR 49](adr/0049-useful-response-suggestions.md) Q14):

- **No-work refusals.** HTTP 401 or 403 is a no-work refusal that counts
  nothing and backs off like 402 and 429. That is how the proxy answers a model
  missing from the caller's key. The service calls with its own key,
  `reply-assistant-vps`, which mirrors the `local` key's model list. The
  proxy's `manage.py provision` updates only `local`, so after it, copy the
  list to `reply-assistant-vps` with `/key/update` and prove it with a
  contentless decisions request on the service's key before a check.
- **Route state.** Every answer reports the route's state to the account
  manager, which keeps one state for every account in the registry's
  `decision_model` metadata, with when and how it began:
  - `listing` for a refused or unbounded listing;
  - `refused` for 401, 402 or 403;
  - `http` for any other non-200;
  - `connection` for an unreachable endpoint;
  - `route` for an unqualified echo.
- **Clearing.** A qualified answer clears it; 429 throttling and format
  failures leave it alone.
- **Owner view.** The owner's Settings carries `decision_model` (available, or
  since when not), and both clients show the outage above Settings → AI.
  Members get none.
- **Push.** Each start sends one content-free push to the owner's phones and
  browser destination (subject `decision-model`, kind `decider`). It opens
  Settings → AI and shows nothing once the route answers again.
- **Failures.** Recording failures log `decision_state_unavailable` and never
  change the call.

Coverage is `backend/tests/test_decision_status.py` and
`backend/tests/test_decider.py`.

Answers are validated by question name: the `emotion` choice must be one of the
nine supplied codes with finite probabilities in [0, 1] that include it.
Failures are fixed content-free diagnostics in the receipt and record:
`missing_answer`, `invalid_answer` (also an unexpected question name or a
malformed yes/no), `unknown_option` or `invalid_probabilities`. A selected
probability below 0.35 stores `unknown`; the decision (choice, probability,
per-code probabilities and self-reported confidence) is kept for diagnosis and
never displayed. The fixed core frames the conversation as untrusted data,
names the permitted sources and attribution, forbids facial and acoustic
inference and carries the code descriptions; the Emotion extraction field
follows it as owner guidance.

The incoming request also asks three yes/no questions, each read at 0.5 or more:

- **`hostility`**, fixed and independent of emotion. Only hostile wording
  permits softening. A missing or malformed answer fails the decision.

- **`sarcasm`**, fixed with no owner field
  ([ADR 53](adr/0053-calming-hostile-messages-and-removing-adaptive-tone.md)).
  Only a softened rewrite is told, and a missing answer means no hint.
- **`suggestions_useful`** ([ADR 49](adr/0049-useful-response-suggestions.md)
  Q2): whether short optional reply wording would help the owner with the
  latest block now. Its fixed core is followed by the owner's When to suggest
  field (`usefulness`), which also follows the reaction question's. A missing
  or malformed usefulness answer is the same format failure as a bad emotion.
  The published record keeps `useful` and the probability (`usefulness`). The
  answer only gates Response Suggestions: it is never displayed or used to
  mark, count, sort or notify.

In a chat with calming off, the rewrite's ready transaction queues one
`emotion_classification` job, inside a savepoint
(`emotion_schedule_unavailable`), only when the block can become the current
source (the deletion-inclusive barrier is unchanged and the latest live
incoming message is among its sources) and is still inside its 12-hour
lifetime, so no paid call produces a result that could never show. With
calming on, the decision comes first ([Soften hostile messages](#soften-hostile-messages)). The job takes the
followed work's class and carries a deliberate Retry flag.

Its pending record holds the prepared originals, transcripts, fresh image
meanings, attribution and bounded Supporting Context. The state
(`backend/src/enloq/application/decision_state.py`) is a compact
`conversation` with `from` owner/recipient, `text`, `transcript`,
`image_meaning`, `attachment`, `forwarded_from`, `imported`, `reply_to` and
`latest_block` flags. Raw images, memory and observed history never enter it.
The header shows pending until publication, which rechecks the barrier, derived
digests and chat eligibility; late output keeps its receipt and never
publishes. Work that stops being current before dispatch, or whose output
cannot publish, ends `superseded` rather than failed, and so does a queued
classification of an older-message run that Stop, Off or the budget reserve
ends. Publication then schedules Response Suggestions.

A failure leaves emotion unavailable (the `invalid` reason) with its
diagnostic, never reruns the rewrite or retries automatically, and pauses no
role. Refusals back off 30, 60 and 120 seconds, three times, then `refused`;
Account AI Off and budget waits hold the queued attempt; a restart fails a
dispatched attempt as `interrupted`. Memory waits while a classification is
queued or running. Coverage is `backend/tests/test_decider.py` and
`backend/tests/test_emotion.py`.

A held block (`incoming_deferred`) gets the same job without a rewrite
([ADR 49](adr/0049-useful-response-suggestions.md) Q3). Once its quiet `due`
has passed, the scheduler's admission pass (`Emotion.decide_held`, run with
`reconcile_admission`) builds the packet from the local cache only
(`context.cached`):

- **Inputs.** The block's originals, the bounded Supporting Context and a
  cached direct reply, with cached transcripts and image meanings.
- **Left out.** An uncached image or voice note stays an `attachment`, and an
  uncached direct reply is left out. Nothing reads Telegram or prepares
  media.

The job is marked `held` in the `new` or `catchup` class. Publication,
freshness, the 12-hour lifetime and the Response Suggestions that follow are as
above.

- **Once per revision.** Each held revision is decided once: a `decided`
  marker on the held record stops repeats, and a changed block is held anew.
- **Released blocks.** A block that the threshold later admits supersedes a
  still-queued held job and is classified after its rewrite.
- **No call.** A block of Non-AI Media only is never held, so it makes no
  call.
- **Failures.** A scheduling failure logs `held_decision_unavailable` and
  leaves that revision decided.

Coverage is `backend/tests/test_held_decisions.py`.

## Soften hostile messages

[ADR 53](adr/0053-calming-hostile-messages-and-removing-adaptive-tone.md)
uses an explicit decision-model `hostility` probability, at least 0.5,
independently of mood. Insults, aggressive blame, intimidation and hostile
sarcasm toward anyone qualify in context. Anger, frustration, emphatic
profanity, friendly teasing and neutral reporting of an insult do not qualify
alone. A missing or malformed answer authorizes no softening.

The profile switch is **Soften hostile messages**, On by default. Its existing
internal `calming` table, endpoint and snapshot key preserve per-chat choices;
`blocks.calmed` preserves the flag belonging to each validated rendering.
Both clients display **Softened**. Off starts no arrival screen and no new
softening; existing renderings stay. Groups and Saved Messages have neither.

Combined-block decisions retain 30 quiet seconds. Text is held and decided
from originals; a hostile decision releases one Muse rewrite at any length.
Media is prepared normally, then its decision comes before its one rewrite.
The hostility flag, with an optional sarcasm hint, adds the fixed softening
frame and owner guidance to incoming instructions, never to source content.
Every fact, request, question, condition, amount, date, deadline and threat
must survive; expressed feeling is named only when supported, never inferred
from hostility. Hostile sarcasm is rendered by its meaning.

A decision already made for a held block is not repeated by its rewrite.
Other blocks use their own current decision; no screening answer is reused
across changed or combined inputs. A held block's non-hostile result leaves
originals combined. A failed or unavailable decision leaves originals or the
ordinary media rewrite. Unseen readiness still waits for the block's decision
and validated full output, independently of notification timing.

The finite `scripts/check-calming.py` keeps its established filename and
private evidence layout. It covers 30 authored blocks, including third-party
hostility, non-hostile anger and frustration, teasing and reported insults.
It uses text checks and the authorized sarcasm judge, within 32 rewrite,
32 decision and 30 judge requests, 2,400 seconds and $0.20 counted. Low semantic
quality is recorded separately from transport and format results.

## Arrival notifications

[ADR 20](adr/0020-minimal-reading-and-ready-notifications.md) screens original
text or captions immediately through `hostility_screen` on the pinned decision
route, with bounded cached same-chat context and one cached direct reply.
It reads or teaches no memory, prepares no media and triggers no suggestions.
The five-second deadline starts at service arrival and includes queueing,
capacity and route preparation. Clean results expose the original preview
without waiting for quiet, mood, media or suggestions. Textless media uses a
typed label immediately.

Hostile sources wait for a current validated softened block. Account AI Off,
softening Off, unavailable budget/route/capacity, malformed answers, failures
or deadline use originals immediately when known. Required softening that
cannot proceed also falls back; a later non-hostile full-block decision may
release the original. There is no extra screen or unchanged resampling.

`incoming_notices` groups arrivals within 30 seconds of quiet; `notice_sources`
keeps only native source revision, deadline, state and opaque attempt. The
first eligible preview alerts. Each later update uses a fresh opaque event ID,
the same notification group and a silent flag. A later clean arrival can alert
while an earlier hostile source waits. Its later completion joins silently and
cannot replace a newer preview. Web notification tags and Android chat notice
identifiers replace the displayed notification. Per-destination Sound Off and
foreground/mute/authorization/target guards still apply. Expiry and revalidation
happen before dispatch; no source text is stored in opaque push payloads.

Authored coverage is in `test_arrival_notifications.py`, delivery/push tests,
`test_notifications.py` and `mobile/__tests__/pushTask.test.ts`. These checks
prove deterministic policy, not provider quality or native delivery.

## Memory and traits

[ADR 55](adr/0055-person-and-account-memory-and-traits.md) keeps two memories:
**Person Memory**, what one private chat's person cares about, and **Account
Memory**, what the account's holder cares about. The Settings **Memory**
switch (the owner's, server-wide) covers both, and each chat's **Remember this
chat** switch starts Off
([ADR 56](adr/0056-idioms-either-signal-hiding-and-memory-off-by-default.md)).
A chat that is Off teaches neither memory, reads neither and collects no
traits; what memory already holds stays visible, editable and clearable.

- **One fold, two change sets.** A chat's update (`chat_memory`, MiMo) returns
  `person` and `account` changes after a fixed core that names whose list each
  note belongs to: cited additions, removals of superseded model entries and
  "possibly outdated" flags on owner entries. Account notes cite (chat,
  message) pairs; one that cites none of the holder's own messages is dropped.
  A fold sees the whole Account Memory but not other chats' originals
  (`cited_elsewhere`).
- **Sources.** Every message sent to a model while Memory and Remember this
  chat are on (`analyzed`, origin `sent`), the latest 20 included, and once,
  the cached messages dated before the store's `memory_start` (origin
  `start`). A chat that was Off only by default learns once from its 30-day
  cache when switched on; in a chat its holder switched Off, what arrives
  meanwhile is never learned. Remember this chat Off cancels the chat's
  waiting work, drops its unlearned sources and closes its start.
- **Timing.** An update waits for 30 quiet minutes in the chat, measured by
  the durable native activity timestamp (unchanged sync, reads and edits do
  not move it), and for idle communication: no queued or running rewrite,
  transcription, emotion classification, suggestion or group briefing work
  across accounts. Only one memory call runs across the server, inside the
  shared ceiling, and admission is rechecked after route preparation,
  immediately before spending. A started call may finish when communication
  arrives; waiting memory holds no slot.
- **Readers.** Media and calmed rewrites get Person Memory as unverified
  reference data, only for incoming targets newer than its coverage and within
  seven days, under their own allowance: memory is dropped rather than cutting
  the target when the request would exceed 35,000 bytes. Suggestions get both
  memories at dispatch ([Response suggestions](#response-suggestions)).
  Emotion Classification, arrival screening and group briefings get none.

A fold reads the local store only (text, cached transcripts and image context),
never Telegram history, and returns changes only. Missing originals are
labelled `not_cached`. The provider schema bounds each memory's retained IDs,
citation IDs and owner flags to the exact packet and the remaining
generated-entry allowance; publication still validates their relationships.

- **Size.** Each added note keeps a 300-UTF-8-byte limit and a 150-character
  ceiling (`TAKEAWAY_CHARACTERS`). JSON Schema's `maxLength` counts characters,
  so 150 keeps Latin and Cyrillic text within 300 bytes. The fixed contract,
  packet limits (`entry_characters`, `entry_bytes`) and field description state
  both, and local validation enforces both without truncation: bytes first
  (`memory_text_bytes`), then characters (`memory_text_length`). Owner-typed
  entries keep their 300-byte rule.
- **Rejection.** Unknown citations, owner replacement, malformed output or a
  result above 40 entries or 3,000 UTF-8 text bytes rejects the whole change
  set; no oldest-entry eviction occurs. Owner text is protected even when it
  alone exceeds the allowance.
- **Publication.** Exact processed and consumed source snapshots let a
  no-change result consume its batch across restarts. Refusals with no
  provider work do not admit their sources. Native revisions, transcript and
  image snapshots and the memory revision guard publication against concurrent
  changes, and coverage only moves forward. Budget waits keep the undispatched
  attempt; failed, unknown or interrupted publication keeps the previous memory
  and requires Retry. Editing or deleting a cited message removes its entries
  without a model call.
- **No waiting on memory.** Rewrites use the last published memory, and a
  memory change never re-runs published rewrites.

Traits are collected for each chat's person and each account's holder and
shown only to the owner; no model reads them.

- **Scales.** After each published update the chat's person and the account's
  holder are judged again. `trait_scales` asks the decision model the starter
  list (`domain/traits.py`) with an evidence question each; a value shows at
  evidence 0.7 or more and keeps its last level otherwise. The owner can set
  or clear a scale, and the model never changes one they set.
- **Notes.** `trait_notes` (MiMo) runs at most daily: each note cites three or
  more of the subject's own messages on two days, keeps their script and
  carries no IDs in its text.
- **Habits** are counted on reading, without a model.
- **Authorship.** Each message in this material says who wrote it (`recipient`
  or `owner`, `owner` or `other`), so a person's scales and notes describe that
  person, not the owner.

`scripts/check-memory.py` is the quality gate, rerun whenever the memory
guidance changes ([operations](operations.md)).

## Provisional streaming

Incoming rewrites and the group final synthesis use bounded SSE on
their exact route. The incremental parser holds incomplete escapes and
surrogate pairs and exposes only visible wording: rendered incoming parts or final topic text. It never exposes JSON framing,
reasoning, image context or citation metadata. Strict final validation still
owns publication. Receipts seen before an interruption count; otherwise the
normal unknown estimate remains. Provider comments, empty usage choices and
repeated finish frames are handled without starting another attempt. Framing
errors, route changes and missing termination fail safely. No failed raw
response is kept in operational diagnostics.

An account-local latest snapshot is bound to the job and the current block and
input revisions. Authenticated conversation reads reattach to it; content-free
change events notify clients immediately after commits. The web client
serializes conversation reads, presenting each snapshot before a queued
refresh, so partials are not discarded when chunks arrive faster than reads.
Tests cover fast increments in both directions; a provider that delivers all
text at once still yields one visible update. A changed source withdraws the
partial immediately. Failure or service interruption clears partials and restores a
prior completed rendering only when its revision still matches, otherwise
originals.

Manual suggestions publish complete validated options and never stream into
the draft. A restart restores the authored base of an interrupted legacy
outgoing attempt and cancels its removed job, retaining spending evidence.
Incoming rewrites have no Stop.

## Speech transcripts

Personal mode transcribes through the proxy
([ADR 39](adr/0039-litellm-proxy-and-vps-speech.md)):

- **On the VPS.** Notes up to two minutes go to the `whisper-large-v3-turbo`
  route: faster-whisper with an int8 conversion of Whisper large-v3-turbo on
  the VPS's CPU, at a pinned revision, each window decoded on its own with no
  voice-activity filter (automatic denoising off). That audio reaches no model
  provider. It runs at about half real time.
- **Hosted fallback.** Longer notes, and notes the VPS cannot answer
  (unreachable, a server error, or no answer within the note's length plus a
  minute), go to ElevenLabs Scribe v2 (`elevenlabs/scribe-v2`) through the
  proxy's OpenRouter pass-through. Each hosted call is admitted, reserved and
  receipted in the member's Daily Budget (`speech` engine, `transcript` role)
  and sends no audio while the listed rate is above $0.0000611 a second; a
  refusal that did no work counts nothing. A long note the hosted call cannot
  take (budget, rate, refusal or failure) falls back to the VPS. OpenRouter
  publishes no retention or training terms for that provider.
- **Offline.** Offline mode and `scripts/check-voice.py` use the Mac's
  `mlx-community/whisper-large-v3-turbo`, with no downloads at inference time.

Either way it covers voice notes, audio files and the audio track of Video
Circles ([ADR 16](adr/0016-persistent-media-cache-and-non-ai-media.md); circle
frames never reach a provider): one transcription at a time across the server,
members taking turns, at most ten minutes or 25 MB per audio source and a
650-second aggregate timeout. Each source revision keeps its transcript with
the model that produced it, its language and machine-generated provenance.

Ordinary processing transcribes a current incoming voice note or Video Circle
once. The owner's own voice notes and circles are transcribed too, once
Telegram confirms them, under the same rules; their transcript stays raw and is
never rewritten ([ADR 51](adr/0051-telegram-phone-look-and-new-messages.md)
Q8). Show transcript reads a stored transcript or starts a missing one with
progress (Q9); with the account's AI off it says so. There is no Re-transcribe:
no control, route or service action replaces a completed transcript
([ADR 26](adr/0026-mobile-message-actions-and-send-status.md)). A failed or
interrupted transcription waits for a deliberate Retry, which makes one new
attempt and still reuses any successful transcript. A changed native revision
is new evidence and is transcribed by normal processing. Playback and the raw
transcript stay readable under each current original, and versions keep their
captured transcript.

The proxy and the MLX wrapper both return the complete transcript with no
trustworthy incremental callback, so the UI shows transcription progress and
then the complete result; it does not replay completed text as a stream. The
incoming default guidance rewrites transcripts into clear written text,
corrects only unmistakable recognition slips from context and marks a possibly
misheard word, name or number with "(?)" after its best reading, so uncertainty
stays visible. These are implementation rules, not a claim of improved
natural-speech accuracy. No alternative recognizer has shown better accuracy on
representative natural speech with verified references; Turbo with automatic
denoising off stays adopted, and a further comparison needs a separately
bounded task.

Authored offline coverage: `backend/tests/test_message_actions.py` and
`backend/tests/test_speech_proxy.py`.

## Retained acceptance limits

- **Reminder check-ins: Low quality.** An empty-draft Friendly set answered
  an old request and included an unsupplied commitment instead of offering
  check-ins. Format and labels passed; Professional and Idioms additions
  preserved draft intent and insertion/Undo. Reminder packets now separate
  current cached reference context from reply targets and carry explicit
  check-in intent under defaults revision 19. A later bounded check does not
  re-score the earlier set; the owner's ordinary-use quality review remains
  open.

- **Complete manual messages: Low quality.** In ADR 25's live check under
  defaults revision 20, a draft set and a bracketed set in each Style and
  one long draft followed the draft's intent and its brackets, with no
  bracketed text in any option. The Friendly bracketed set began all three
  options with "not yet:", a stance word, so none could be sent as written;
  a Professional draft option kept a shortened form of the draft's test
  prefix, and an Idioms option used "falls through" for an unavailable day.
  The owner's ordinary-use quality review remains open.

- **Reminder sets: Low quality.** In ADR 58's live check under defaults
  revision 21, every set kept the idea's purpose, answered Maya's later
  question with accept, decline and later variants and followed the idea's
  bracketed instruction to keep it short, with no bracketed text. One later
  option offered to get back "on Friday", a time nobody supplied.

- **Hostile-wording detection: Low quality.** The pinned decision model can
  miss subtle hostile sarcasm, aggressive blame and hostility in a forwarded
  exchange. In the authored hostile-only pass it missed six of 18 positive
  cases; all 12 non-hostile controls stayed unchanged. The 12 softened outputs
  passed the automated language, literal/threat and sarcasm checks. This is
  not a zero-miss semantic pass or proof of complete faithfulness; the owner's
  reading remains open. The gate uses no unchanged resampling.

These Low quality and unverified outcomes of finite checks await the owner's
quality review ([TODO](plans/TODO.md)). Each stands as found: nothing
resamples it automatically, later passing checks don't re-score it, and a
repair needs an adopted changed candidate, an authored regression and a
separately bounded check. Passing format, source or browser
checks never establishes semantic acceptance.

- **Image rewrites with little text (ADR 35).** Two image cases are
  unverified: a text-plus-image block returned no usable text and ended
  `Provider unavailable`, and an image-only block with textual Supporting
  Context was never sent. The cause of the original empty response the owner
  reported is unknown.
- **Mixed-language incoming block.** An authored English/Russian block came
  back as one English affirmative statement, dropping a request to confirm a
  PDF and the need for train times.
- **Incoming rewriting on MiMo.** A Russian block once came back translated
  into English. Before the parts output, rewrites dropped questions and added
  an invented request or closing sentence. With parts, authored cases kept
  every point, but an image's line items leaked into the text, "(?)" followed
  the sender's own "I'm not sure", a self-correction note survived, one detail
  became its own paragraph instead of joining its topic, and a mixed-language
  block gained a generic lead-in question.
- **Memory.** MiMo can read figurative Russian idioms literally, and a
  memory-informed suggestion has asked about a past event as if it were still
  under way; Muse is the follow-up if literal readings show up in real chats. An update has kept a redundant generated note beside the
  one it merged into.
- **Response suggestions.** Options have asserted the owner's own feelings
  ("Really happy for you"), in Russian with masculine and feminine forms
  mixed; promised an owner action ("Let me take a quick look"); invented an
  offer (bringing boxes and tape) or an answer with an apology for fault; and
  given a bare "yes" as an acceptance. The decision model's labels once filed
  a calendar question as later. **Low quality:** a manual Friendly set made
  a firm refusal tentative and added an unsupplied promise to update; a
  Professional set supplied being busy as the reason for a refusal when the
  authored conversation gave no reason. One-sidedness is valid under ADR 49
  Q20 and does not excuse these grounding misses. Minor, not failures: "I'm
  here if you want to talk", "Sending you love" and an offer to ask around
  inside a decline.
- **Reaction usefulness.** On Decider V1.2 every authored reaction case was
  judged not useful, including three written to need a reply (a 👎 on a
  proposal, on the correspondent's own bad news and on a cancelled visit, at
  0.25-0.43 against the 0.5 cutoff), so reaction-derived suggestions rarely
  appear. The handshake misreadings of earlier routes (a stated deal read as
  unknown, a reluctant 🤝 as positive) are not repeated there: the stated deal
  reads positive and the reluctant one neutral.

### Group briefing first outcomes

The authored qualification ([above](#finite-authored-group-qualification))
passed format and current-source checks for image understanding, two reusable
chunks, a streamed final and a short no-substance final, with mechanical
coverage of every long-case target. Its transcripts were prepared text, not a
recognition run, and its image a small drawn timetable. The longer final is
**Low quality**:

- It omitted a topic supplied unchanged in a chunk with its citations (an
  exhibition's Tuesday proposal corrected to Wednesday afternoon, with the room
  unconfirmed): final-synthesis loss, not source clipping or a recorded
  work-cap omission.
- A clarification cites the right message and also an unrelated repeated
  planning note. The ID passes the covered-source allowlist while failing
  semantic grounding.
- A parenthesis draws on old direct-reply context: not invented, but beyond
  the needed clarification and not established by the final's citations.

Conditional instructions, a provisional booking, the absence of an agreed
purchase or invitation, planned versus completed work and forwarded attribution
were preserved, and the greeting case correctly returned `no_substance`.
Passing schema, full coverage and valid IDs do not make the long result
faithful.

A live update in one owner-selected group found no critical meaning,
attribution, citation or exclusion defect, and keeps these limits:

- **Classifier packing.** Replies to different earlier messages split packets,
  so twelve classifier requests settled only 46-58 of 200 sources; the rest
  stay eligible and unclassified.
- **Final fit.** Dependency metadata counts in the final request's
  32,000-byte bound, so the final fitted one of four chunk summaries.
- **Out-of-packet citations.** A chunk answer cited a source outside its packet
  and failed validation, one invalid answer in 16 chunk requests across a day:
  a format failure with unassessable semantics, not resampled.
- **Ad filtering.** On authored and cached sources the 0.90 rule excluded 12
  of 13 hand-labelled ads and none of 129 other cases. The one miss, a short
  real promotion at 0.87, is an accepted noncritical miss, with no tuning to
  the sample. Probability is not a calibrated reliability guarantee, and
  native bot provenance and full-media cases are not qualified by that check.

Owner quality and production acceptance remain open in the
[TODO](plans/TODO.md).
