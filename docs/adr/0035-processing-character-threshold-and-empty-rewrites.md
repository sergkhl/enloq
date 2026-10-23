---
status: accepted
---

# Processing character threshold and empty incoming rewrites

## Context

Automatic AI work on short fragments costs money and adds little, so AI work
waits until the text it would read reaches a minimum length, set in Settings.
Separately, a rewrite could come back blank: the failure code
`omitted_target_text` means the final rendered incoming rewrite is empty
although its target has nonblank text or a transcript. That is a format
failure with unassessable semantics, not a detector of a missing point in an
otherwise nonempty rewrite. The provider schema then allowed an empty `parts`
array that final validation rejected. Image-only targets may legitimately have
empty visible wording, since image understanding is hidden. A length gate
reduces work on fragments; it cannot guarantee nonempty or faithful output.

Since [ADR 54](0054-incoming-text-combined-not-rewritten.md), private text is
no longer rewritten automatically, so the threshold now matters mainly for
group briefings.

## Decision

**The setting (Q7).** The **Processing Character Threshold** is one durable
global number, default 200, accepting integers from 0 to 10,000 with an
explicit Save. Zero turns off only the character gate. A saved change applies
across accounts and clients without a restart. Only the owner sets it
([ADR 48](0048-members-on-the-shared-service.md) Q13).

**Counting (Q1, Q3).** The count covers the whole unit the AI would read: a
private Incoming Block under its local-day, outgoing and service-message
boundaries, or a group's calendar day ([ADR 34](0034-group-chats-and-daily-briefings.md)).
It sums Unicode code points of each source's trimmed original text or
caption, counting internal whitespace, punctuation and line breaks. Added
separators, metadata, Supporting Context, generated wording and memory do not
count. It is not a per-message minimum and does not reset after a success: a
short addition to an already long unit qualifies after quiet.

**What it governs (Q2).** Automatic group briefing updates (ADR 34). In
private chats, text blocks are not rewritten automatically (ADR 54); the
threshold gates their rewriting only while the registry's `text_rewriting`
switch, off by default and not in Settings, is on. Calming a frustrated or
angry block ignores it ([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)
Q6). Every new or changed private block gets its decision request (emotion,
suggestion usefulness and sarcasm) from its originals after its quiet period,
whatever its length ([ADR 49](0049-useful-response-suggestions.md) Q3).
Deliberate composer Rewrite, failure Retry and Rewrite earlier messages keep
their explicit behavior; Reaction Assessment and memory keep their own gates.

**Media (Q4).** A unit with a new or changed analysable image, voice note or
Video Circle is exempt from the text gate and keeps its bounded media
preparation. A unit of Non-AI Media only makes no model call.

**Waiting and readiness (Q5, Q6).** Held messages stay readable as originals.
Unseen readiness and notifications use the original fallback after 30 quiet
seconds with the usual viewing, mute and notification guards; where calming
is on, they wait for the block's decision (ADR 54). Later processing never
alerts again for the same burst. No timeout bypasses the threshold, and there
is no waiting status or character counter.

**Runtime changes (Q11).** The latest saved threshold is checked again before
automatic preparation and before provider dispatch. Raising it holds
undispatched work that no longer qualifies; lowering it releases eligible
waiting work after its quiet period. An attempt already sent to the provider
may finish. Successful caches, unchanged failures, limits and spending stay; a
change starts no history scan and no automatic retry.

**Empty output (Q8).** When a prepared target contains nonblank text or a
transcript, the provider request requires at least one rewrite part
(`minItems: 1`), using the same target-only predicate as final validation,
without Supporting Context. Textless image-only targets may still return
empty visible wording. Final validation stays independent, and there is no
automatic resampling or new retention of failed output. This does not prove
completeness or quality.

**Readable failure (Q9).** The owner sees "The AI returned no rewrite text.
Showing originals." The fixed code stays in operational evidence, and Retry
remains.

**Status words (Q10).** Incoming work in progress reads "Analyzing…", and the
earlier-messages run "Analyzing earlier messages · X of Y"; factual
preparation stages such as transcription keep their own words. A combined
text block shows no status (ADR 54). Streamed composer work reads
"Processing…" with the busy spinner and no "incomplete" suffix while active;
after a deliberate Stop it reads "Stopped · incomplete draft"
([ADR 19](0019-streamed-rewriting-and-observed-message-history.md)). Text stays
provisional until final validation, with one busy indicator.

The 30 quiet seconds before automatic incoming work, final validation,
currentness guards, deliberate Retry, the absence of automatic resampling,
cached results and the budget, target and privacy boundaries are unchanged.

## Consequences

- Group briefings below the threshold leave new originals uncovered until the
  day's text is long enough or new media arrives.
- A threshold cannot make output nonempty or faithful; the schema rule and
  final validation handle emptiness, not semantic loss.
- Known limit: the finite provider check of the empty-output rule left both
  image cases unverified ([inference](../inference.md#retained-acceptance-limits)).

## Questions

- Q1: count the whole block (or briefing day), not each message; no reset
  after a success.
- Q2: governs automatic group briefings, and private text rewriting only
  while `text_rewriting` is on; calming ignores it; decisions run for every
  block (ADR 49 Q3, ADR 53 Q6, ADR 54).
- Q3: Unicode code points of trimmed originals and captions; nothing added or
  generated counts.
- Q4: analysable images, voice notes and Video Circles exempt a unit.
- Q5: original-fallback readiness after 30 quiet seconds; no second alert.
- Q6: no waiting status or counter.
- Q7: one global setting, default 200, 0 to 10,000, explicit Save.
- Q8: at least one rewrite part when the target has text or a transcript.
- Q9: "The AI returned no rewrite text. Showing originals."
- Q10: "Analyzing…" for incoming work; "Processing…" for streamed composer
  work.
- Q11: checked again before preparation and dispatch; raising holds, lowering
  releases.
