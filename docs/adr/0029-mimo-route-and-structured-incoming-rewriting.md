---
status: accepted
---

# MiMo V2.6 Flash route and structured incoming rewriting

## Context

Enloq's text-generating roles need one exact model route each, with known
terms, rates and reasoning behavior, and no selector or silent fallback.
Incoming rewrites exist to lower the effort of reading a fragmented burst, a
rambling voice note or an image the sender didn't explain, so their shape
matters as much as their wording.

## Decision

**The MiMo route (Q1, Q3).** MiMo V2.6 Flash, a mixture-of-experts model with
15 billion of its 309 billion parameters active, is the generation route,
adopted at $0.14 per million input and $0.28 per million output tokens. Since
[ADR 39](0039-litellm-proxy-and-vps-speech.md) it is the owner's LiteLLM
proxy's `xiaomi_mimo/mimo-v2.6-flash`, which calls Xiaomi's own API. It
serves:

- incoming rewriting with hidden image context, for blocks with images, voice
  notes or video circles that aren't softened
  ([ADR 54](0054-incoming-text-combined-not-rewritten.md));
- Person Memory, Account Memory and trait notes
  ([ADR 55](0055-person-and-account-memory-and-traits.md));
- Friendly and Professional Response Suggestions, manual and automatic
  ([ADR 25](0025-emotion-response-suggestions.md));
- Daily Briefings ([ADR 34](0034-group-chats-and-daily-briefings.md)).

Every role has one exact route, with no selector, no fallback route or
provider and no automatic resampling. The other routes are the decision model
for every decision role ([ADR 30](0030-decider-emotion-classification.md),
[ADR 49](0049-useful-response-suggestions.md)) and Muse Spark 1.3 for softened
rewrites ([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md))
and idiom sets ([ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md)).
MiMo-V2.6-Pro and Pro-UltraSpeed are not adopted; a later upgrade needs its
own decision and check. MiMo accepts audio, but no voice audio is ever sent to
it; speech-to-text is ADR 39's.

**Provider and data terms (Q2, Q12).** The owner accepted Xiaomi's listed
terms: no prompt training, 30-day retention, processing in Singapore and the
Netherlands, and a required user identity. They are not a privacy or
zero-retention guarantee, and they don't settle the Telegram AI-content
boundary in the source reuse note. Every request carries one fixed, random,
app-level `user` pseudonym, generated once and kept privately; it says nothing
about the owner, the keys or the Telegram accounts, and it reaches Xiaomi as
sent. Every request asks the proxy to redact it from its spend log. Xiaomi
can't cancel a stream: outgoing Stop and Undo restore the draft at once and
detach late output, but the provider may finish and bill. Xiaomi rejects
`tool_choice: none`, so the adapter, which offers no tools, never sends it.

**Verification.** Before dispatch, the proxy's `/v1/model/info` must list
exactly one entry of that name, in chat mode, with response-schema and vision
support, at per-token rates within the adopted ceiling, re-read when the
cached listing is over 24 hours old (ADR 39). The response must name the
route as its `model`.

**Reasoning (Q4).** MiMo's reasoning is only on or off, set by Xiaomi's
`thinking` switch. Incoming, memory, suggestion and group roles run with
reasoning On and excluded from the result; manual suggestions use the same
role and route as automatic ones. Outgoing Rewrite is removed (ADR 25).
Output caps and attempt deadlines are raised so reasoning can't truncate a result.
Reasoning text is discarded unread.

**Accounting and continuity (Q5, Q6).** Every call counts, from the proxy's
cost receipt, in the budget of the member whose account it serves: the Daily
Budget, or the Group Briefing Budget for briefings
([ADR 48](0048-members-on-the-shared-service.md)). An unknown outcome counts
the flat $0.01 estimate. Cached results from an earlier
route stay valid and are never regenerated; new and changed blocks use the
current route. A route change restarts each role's consecutive format-failure
count, because that count measures one route and guidance pairing, and makes
group briefings updateable once.

**Structured incoming rewriting (Q7-Q9).** Incoming rewrites get plain-text
structure to reduce reading effort. The model returns ordered paragraph,
bullet and numbered parts, rendered as plain text with line breaks; it needs
at least one part when the target has text or a transcript
([ADR 35](0035-processing-character-threshold-and-empty-rewrites.md) Q8), and
the fixed core adds a completeness rule. A single point is one natural
sentence; separate topics are short paragraphs; three or more parallel items
(questions, options, things to do) are "•" lines, numbered when their order
matters. Scattered fragments and repeated points across the block's messages
and voice notes are grouped by topic, in the order the sender first raised
them, in the sender's voice, with no summary line and no reordering by
importance; the originals keep the exact sequence. The text has no Markdown.
Copy takes the same text, and previews and notifications collapse it to one
line. The structure rules live in the owner-editable incoming guidance; the
plain-text rule lives in the fixed core, because rendering depends on it. The
Faithfulness Rules, forwarded-content attribution and the empty text of
image-only blocks are unchanged. A chosen manual suggestion replaces the
draft and an automatic one is added at its cursor, with Undo (ADR 25).

**Speech (Q10, Q11).** Voice notes and video-circle audio are rewritten into
clear written text, not stitched in. Fillers, false starts, repetition and
verbal habits go, and a self-correction keeps only its final version ("Friday,
no, Saturday" becomes Saturday). Spoken content merges with the block's text
by topic, keeping every request, fact, feeling, hedge and the sender's voice.
Where the recognizer may have misheard a word, name or number, the rewrite
keeps its best reading followed by "(?)" and drops nothing silently; an
unmistakable recognition slip may be corrected from context without a marker.
The sender's own hedges stay words, typed text never gets a marker, and the
raw transcript stays one tap away. These rules live in the incoming guidance.
This makes [ADR 19](0019-streamed-rewriting-and-observed-message-history.md)'s
explicit-uncertainty rule visible.

### Questions

- **Q1:** `mimo-v2.6-flash`; the Pro variants not adopted.
- **Q2:** Xiaomi's own endpoint, pinned with no fallback, its listed terms
  accepted; reached through the proxy since ADR 39.
- **Q3:** one route for the generation roles, with no selector or fallback.
  Replaced in part: the decision roles moved to the decision model (ADR 30),
  and softened text and idiom sets to Muse Spark 1.3 (ADR 53, ADR 56). Speech
  is ADR 39's. One Daily Budget continues.
- **Q4:** reasoning On and hidden for the retained roles, including manual
  suggestions, with raised caps; outgoing Rewrite is removed.
- **Q5:** cached results stay; no regeneration or data wipe.
- **Q6:** a route change restarts each role's format-failure count; nothing
  reruns automatically.
- **Q7:** structure for incoming rewrites only; manual suggestions add to
  the authored draft without replacing it (ADR 25). Since ADR 54 text that
  isn't softened is combined, not rewritten.
- **Q8:** plain-text structure as above, returned as ordered parts.
- **Q9:** grouped by topic in order of first mention, in the sender's voice.
- **Q10:** speech becomes clear written text, merged by topic.
- **Q11:** "(?)" after a possibly misheard word; the raw transcript stays. The
  app has no Help to explain it ([ADR 45](0045-settings-in-the-enloq-app.md)
  Q7).
- **Q12:** one fixed, random, private app-level `user` pseudonym.
- **Q13:** a route or guidance switch is gated by a finite check on authored
  synthetic content with the service stopped, no Telegram reads or sends and
  first attempts only. Malformed output or a route failure blocks; weak but
  well-formed output is recorded as Low quality.
- **Q14:** the earlier route's pending memory evidence closed as superseded; a
  memory format failure on MiMo is ordinary work with Retry.

## Consequences

- MiMo costs about 1.4 times the Muse route it replaced for these roles and
  drops Muse's Contributor training terms. Xiaomi keeps prompts for 30 days
  and can't cancel a stream.
- Hidden reasoning favours faithful restructuring in background work, at the
  cost of later first words and readiness. Suggestion sets publish complete.
- Plain-text structure needs no rendering change and keeps previews and Copy
  simple, at the cost of no emphasis. Grouping by topic changes the order of
  fragments, so the originals keep the exact sequence; rewriting speech
  departs from the verbatim transcript, so doubt is marked and the transcript
  stays available.
- Finite checks kept Low quality outcomes for media rewrites, such as an
  unsolicited description of an image's contents in the text and a "(?)"
  after the sender's own hedge, in
  [inference](../inference.md#retained-acceptance-limits) for the owner's
  quality review.
