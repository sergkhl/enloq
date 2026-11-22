---
status: accepted
---

# The Terra route and structured incoming rewriting

## Context

Enloq's text-generating roles need one exact model route each, with known
terms, costs and reasoning behavior, and no selector or silent fallback.
Incoming rewrites exist to lower the effort of reading a fragmented burst, a
rambling voice note or an image the sender didn't explain, so their shape
matters as much as their wording.

MiMo V2.6 Flash, through Xiaomi's own API, held the route until the proxy's
Xiaomi account ran out of credit and refused every call, which stopped every
role on it. The owner moved the roles to gpt-6.1-terra on the ChatGPT
subscription that already writes Ask this group's answers
([ADR 59](0059-ask-this-group.md) Q5).

## Decision

**The Terra route (Q1, Q3).** gpt-6.1-terra is the generation route, on the
owner's ChatGPT subscription at the proxy route's default medium reasoning
effort. Since [ADR 39](0039-litellm-proxy-and-vps-speech.md) it is the owner's
LiteLLM proxy's `chatgpt/gpt-6.1-terra`, LiteLLM's ChatGPT provider reaching
the subscription through the Codex backend. It serves:

- incoming rewriting with hidden image context, for blocks with images, voice
  notes or video circles that aren't softened
  ([ADR 54](0054-incoming-text-combined-not-rewritten.md));
- Person Memory, Account Memory and trait notes
  ([ADR 55](0055-person-and-account-memory-and-traits.md));
- Response Suggestions in every Style, Friendly, Professional and Idioms,
  manual and automatic ([ADR 25](0025-emotion-response-suggestions.md),
  [ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md)
  Q20);
- Daily Briefings ([ADR 34](0034-group-chats-and-daily-briefings.md)).

Every role has one exact route, with no selector, no fallback route or
provider and no automatic resampling. The other routes are the decision model
for every decision role ([ADR 30](0030-decider-emotion-classification.md),
[ADR 49](0049-useful-response-suggestions.md)) and Muse Spark 1.3 for softened
rewrites ([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)).
MiMo V2.6 Flash, the earlier route, and the subscription's other models are
not adopted for these roles; a change needs its own decision and check. No
voice audio is ever sent to the route; speech-to-text is ADR 39's.

**Provider and data terms (Q2, Q12).** Requests reach OpenAI under the
owner's ChatGPT plan and its data settings, as Ask this group's do; the owner
accepted this for these roles on 2026-11-22. They are not a privacy or
zero-retention guarantee, and they don't settle the Telegram
AI-content boundary in the source reuse note. Every request carries one fixed,
random, app-level `user` pseudonym, generated once and kept privately; it says
nothing about the owner, the keys or the Telegram accounts. LiteLLM's ChatGPT
provider forwards a fixed set of fields that leaves it out. Every request asks
the proxy to redact it from its spend log. Outgoing Stop and Undo restore the
draft at once and detach late output; the backend may still finish the reply.
The adapter offers no tools and sends no `tool_choice`.

**Verification.** Before dispatch, the proxy's `/v1/model/info` must list
exactly one entry of that name, with vision support and no per-token rate,
re-read when the cached listing is over 24 hours old (ADR 39). The response
must name the route as its `model`.

**Reasoning (Q4).** The route reasons at the proxy's default medium effort,
which the app never overrides, for incoming, memory, suggestion and group
roles alike; manual suggestions use the same role and route as automatic ones.
Outgoing Rewrite is removed (ADR 25). The route takes no token cap, and
attempt deadlines leave room for hidden reasoning. Reasoning text is
discarded unread.

**Structured output (Q15).** The ChatGPT route drops `response_format`, so a
request cannot make the backend follow a schema. The fixed instructions end
with the role's JSON Schema, built per request with its allowed IDs and
bounds as before, and ask for that one object alone. Text around the reply's
outermost JSON object is dropped, and the object is validated as strictly as
a schema-bound reply was; anything else is a format failure (Q6). Ask this group's writer asks for
its answer the same way (ADR 59 Q5).

**Accounting and continuity (Q5, Q6).** Every call is receipted in the budget
of the member whose account it serves: the Daily Budget, or the Group
Briefing Budget for briefings
([ADR 48](0048-members-on-the-shared-service.md)). The subscription's receipts
record $0, so these roles spend nothing and no budget limits them, which the
owner accepted on 2026-11-22; an unknown outcome still counts the flat $0.01
estimate. The service's one concurrency
limit (ADR 15) bounds how many run at once. An expired login, a model the key
may not call and the backend's own rate limit (HTTP 401, 403 and 429) are
no-work refusals, retried with backoff like any other. Cached results from an
earlier route stay valid and are never regenerated; new and changed blocks use
the current route. A route change restarts each role's consecutive
format-failure count, because that count measures one route and guidance
pairing, and makes group briefings updateable once.

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

- **Q1:** `gpt-6.1-terra` on the owner's ChatGPT subscription; MiMo V2.6
  Flash, the earlier route, and the subscription's other models not adopted.
- **Q2:** the proxy's `chatgpt/gpt-6.1-terra` route, pinned with no fallback;
  OpenAI under the owner's plan and its data settings, accepted by the owner
  on 2026-11-22.
- **Q3:** one route for the generation roles, with no selector or fallback.
  Replaced in part: the decision roles moved to the decision model (ADR 30),
  and softened text to Muse Spark 1.3 (ADR 53); idiom sets are this route's
  (ADR 56 Q20). Speech is ADR 39's. One Daily Budget continues.
- **Q4:** hidden reasoning at the route's default medium effort for the
  retained roles, including manual suggestions, with no token cap; outgoing
  Rewrite is removed.
- **Q5:** cached results stay; no regeneration or data wipe. No budget limits
  the route's $0 receipts, accepted by the owner on 2026-11-22.
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
- **Q12:** one fixed, random, private app-level `user` pseudonym, which the
  ChatGPT provider does not forward.
- **Q13:** a route or guidance switch is gated by a finite check on authored
  synthetic content with the service stopped, no Telegram reads or sends and
  first attempts only. Malformed output or a route failure blocks; weak but
  well-formed output is recorded as Low quality.
- **Q14:** the earlier route's pending memory evidence closed as superseded; a
  memory format failure is ordinary work with Retry.
- **Q15:** the role's JSON Schema in the fixed instructions, since the route
  drops `response_format`, and the reply's outermost object validated
  strictly.

## Consequences

- These roles cost nothing from any budget. Every member's rewrites, memory,
  suggestions and briefings use the owner's subscription allowance, shared
  with Ask this group and the owner's other callers; the backend answers bursts of about 8 to 12 parallel calls with its
  own HTTP 429. A failed subscription call is never retried elsewhere.
- Prompts reach OpenAI under the owner's plan and its data settings, not
  Xiaomi's 30-day terms.
- No provider enforces the schema, so a reply's shape rests on the
  instructions and on strict validation; a malformed reply is a format
  failure like any other.
- Hidden reasoning favours faithful restructuring in background work, at the
  cost of later first words and readiness. Suggestion sets publish complete.
- Plain-text structure needs no rendering change and keeps previews and Copy
  simple, at the cost of no emphasis. Grouping by topic changes the order of
  fragments, so the originals keep the exact sequence; rewriting speech
  departs from the verbatim transcript, so doubt is marked and the transcript
  stays available.
- Finite checks on MiMo kept Low quality outcomes for media rewrites, such as
  an unsolicited description of an image's contents in the text and a "(?)"
  after the sender's own hedge, in
  [inference](../inference.md#retained-acceptance-limits) for the owner's
  quality review.
