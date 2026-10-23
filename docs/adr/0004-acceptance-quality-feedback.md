---
status: accepted
---

# Allow low-quality results and retain actionable acceptance feedback

## Context

Model output is often weak but well-formed. If every imperfect result stopped
a check, nothing could be evaluated; if weak results were waved through
silently, nothing would improve. Some failures, though, are never acceptable,
whatever the quality of the words.

## Decision

**Finite checks.** A check of live model work states its route, roles,
targets and its request, time and cost bounds before it runs, and stops at the
first bound reached. It uses authored content: check cases may imitate the
shape of real messages but copy none of their bodies, names, numbers, links or
identifiers. Each case gets one first attempt; nothing resamples an unchanged
input automatically.

**Low quality.** A weak but well-formed result is recorded as **Low quality**
with a short note and the check continues within its bounds. The notes, and
any Low quality outcome still awaiting the owner, are kept in
[inference](../inference.md#retained-acceptance-limits). A result keeps its
recorded outcome; a later attempt is a new result, never a rewrite of an
earlier one. A check or gate may set a stricter rule of its own, such as zero
misses on stated items.

**Separate kinds of evidence.** Transport availability, format validity and
semantic quality are judged separately. Malformed output is a failure whose
semantics cannot be assessed; a useful fragment does not repair its format.
Simulation, live Telegram delivery, provider receipts and the owner's own use
are different evidence.

**What Low quality never excuses.** A send to the wrong account or chat,
credential disclosure, duplicate delivery, lost owner text, malformed output
and a broken target or budget bound are defects, repaired and checked before
the affected path continues.

**No feedback machinery.** A note sends nothing, retries nothing, changes no
model, trains nothing and publishes no issue. The app has no Low quality
action, feedback list or qualification records
([ADR 13](0013-clean-slate-rewriting-rebuild.md)). A deliberate Retry or
Rewrite in the app is ordinary work with its own attempt identity.

## Consequences

- Checks finish and leave a record of where quality falls short, without a
  qualification subsystem in the application.
- Ordinary use and the decision to adopt a feature remain the owner's; a
  passing check is not adoption.
