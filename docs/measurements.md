# Measurements

Enloq asks a decision model before it calls a writing model
([ADR 30](adr/0030-decider-emotion-classification.md),
[ADR 53](adr/0053-calming-hostile-messages-and-removing-adaptive-tone.md),
[ADR 54](adr/0054-incoming-text-combined-not-rewritten.md)). Perplexity's
Decider answers named questions with probabilities and writes no text. This
page gives the latency and cost measured for one decision and for the
writing-model steps it replaced or made unnecessary, and the
[ranges](#miss-rate-ranges) around two gates' published check results.

**These figures are indicative, not a benchmark.** They come from small finite
checks on 7 and 9 October 2026, each with its own authored test messages, on
routes and roles that have since changed. They are not a controlled
comparison. Savings
on real traffic are not measured: they depend on how many blocks are hostile
or call for suggestions, and receipts do not record latency. The checks'
private reports hold the authored messages and are not published; only their
numbers and methods are copied here.

## Summary

| Step | Route then | Date (UTC) | Calls | Seconds per call: median (range) | Cost per call: mean |
| --- | --- | --- | --- | --- | --- |
| One decision, in the app | Decider V1.1 | 9 Oct 2026 | 24 | 0.71 (0.62–1.19) | $0.000012 |
| One decision, pre-adoption probe | Decider V1.1 | 9 Oct 2026 | 40 | 0.92 (0.72–1.59) | $0.000015 |
| Reaction Assessment on a writing model, before the Decider took it over | Muse Spark 1.3 | 7 Oct 2026 | 6 | 30.2 (12.4–41.0) | $0.00028 |
| Reaction Assessment on the Decider (8 of the 24 in-app decisions) | Decider V1.1 | 9 Oct 2026 | 8 | 0.71 (0.64–0.88) | $0.000017 |
| Rewriting an incoming text-only block, which ADR 54 stopped for non-hostile text | MiMo V2.6 Flash | 9 Oct 2026 | 18 | 8.8 (2.8–41.5) to the end; 6.7 to first text | $0.00019 |

On these samples a decision took about a second and cost $0.00001 to
$0.00002. The writing-model steps took 9 to 30 seconds at the median and cost
$0.0002 to $0.0003.

## How each figure was measured

### Decisions

Both checks used `perplexity/pplx-decider-v1.1-27b` through Perplexity only,
with no fallback, listed at $0.02 per million input tokens and free output.
Enloq now uses Decider V1.2 (checkpoint `-20261016`), which OpenRouter listed
at the same price on 26 October 2026. Its latency is not measured here.

- **Pre-adoption probe, 9 October 2026, 15:31 UTC.** 40 requests (28, then 12
  in a continuation) sent straight to OpenRouter's Decisions endpoint. Each
  carried one authored block, mostly Russian, or one reaction, and asked for
  its emotion code. Time is each request's duration as the probe recorded it;
  tokens and cost are OpenRouter's usage report. All 40 returned HTTP 200,
  with 723–949 input and 2 output tokens, and together cost $0.00062. 36
  answers matched the authored expected code, 3 were acceptable alternatives
  and 1 missed.
- **In-app route check, 9 October 2026, 17:28 UTC**
  ([`scripts/check-emotion.py`](../scripts/check-emotion.py) as it then
  stood). 24 decisions through the app's own engine, with its reservations and
  receipts and no Telegram calls: 16 emotion classifications of authored
  blocks in Russian, English or both, and 8 reaction assessments. Time is a
  monotonic clock around each engine call. Requests had 483–878 input and 1–2
  output tokens; receipts counted $0.00029 in all. All 24 answers were well
  formed: 20 matched, 3 were acceptable and 1 missed.

### Reaction Assessment before the Decider

**7 October 2026, 16:01 UTC.** 6 authored reactions, one first-attempt request
each, on `openrouter/meta/muse-spark-1.3-contributor` through Meta, listed at
$0.10 and $0.20 per million input and output tokens. Time is each request's
completion time as the check recorded it. The answer has two fields, an
emotion code and whether a reply is useful, yet each call used 734–1,410
completion tokens (about 1,150 on average), which ADR 30 attributes to hidden
reasoning. The 6 calls cost $0.0017. Route check 1 below also ran one
Reaction Assessment on MiMo, which took 13.9 seconds.

### Incoming text rewrites

**9 October 2026, four runs from 14:03 to 15:22 UTC**
([`scripts/check-route.py`](../scripts/check-route.py) checks 1, 2 and 3 as it
then stood; check 2 ran twice). The route was
`openrouter/xiaomi/mimo-v2.6-flash` through Xiaomi, listed at $0.14 and $0.28
per million input and output tokens; Enloq now calls Xiaomi's own API. Every
case was authored, Telegram was a fake transport and each case had one first
attempt. Time is a monotonic clock from the start of each streamed model call
to its first nonblank text and to its end. Cost is the upstream inference cost
OpenRouter reported for each request. OpenRouter's own charge was $0 on every
request; checks 2 and 3 record that they used the owner's own provider key.

| Run | Incoming rewrites (text only) | To the end: median (range) | To first text: median | Cost per rewrite: mean |
| --- | --- | --- | --- | --- |
| Check 1, 14:03 UTC | 5 (3) | 12.5 s (7.1–24.4 s) | 10.2 s | $0.00017 |
| Check 2, 14:26 and 14:31 UTC | 12 (7) | 9.9 s (2.8–15.4 s) | 6.7 s | $0.00019 |
| Check 3, 15:22 UTC | 13 (8) | 10.1 s (6.1–41.5 s) | 7.7 s | $0.00020 |
| Text-only blocks, all four runs | 18 (18) | 8.8 s (2.8–41.5 s) | 6.7 s | $0.00019 |

The other 12 rewrites had a voice note (10) or an image (2). Check 1 also made
5 calls in other roles, which the table leaves out. One check 2 rewrite failed
output validation; its time and cost are included. ADR 54 stopped rewriting
non-hostile text because the rewrites added little beside their originals, and
says "Cost was not the reason"; the table shows what each of those rewrites
took.

## Miss-rate ranges

[Inference](inference.md#retained-acceptance-limits) publishes two
decision-model gates' check results: the cases each gate missed and the cases
it acted on wrongly. The samples are small, so each count is given with the
range of true rates it is consistent with: its two-sided 95% exact
(Clopper–Pearson) interval. Computed on 27 October 2026.

| Gate | Counted | Count | 95% range |
| --- | --- | --- | --- |
| Hostile wording | Hostile cases missed | 6 of 18 (33.3%) | 13.3%–59.0% |
| Hostile wording | Non-hostile controls changed | 0 of 12 | 0%–26.5% |
| Advertising | Hand-labelled ads not excluded | 1 of 13 (7.7%) | 0.2%–36.0% |
| Advertising | Other cases excluded | 0 of 129 | 0%–2.8% |

- **Hostile wording**
  ([inference](inference.md#retained-acceptance-limits), Hostile-wording
  detection). The authored hostile-only pass of 24 October 2026: 18 hostile
  and 12 non-hostile authored blocks, one decision each from the pinned
  decision model, Decider V1.2 (`perplexity/pplx-decider-v1.2` at checkpoint
  `-20261016`). A hostility probability of 0.5 or more softens.
- **Advertising**
  ([inference](inference.md#group-briefing-first-outcomes), Ad filtering).
  Authored and cached group sources, classified on 12 October 2026: 13
  hand-labelled ads and 129 other cases. A probability of 0.90 or more
  excludes. The inference page names no model version for this check; its
  report records Decider V1.1 (`perplexity/pplx-decider-v1.1-27b` at
  checkpoint `-20261006`), which Enloq used before V1.2.

**Method.** The ends of each range are the rates at which a count at least as
extreme as the observed one has a 2.5% chance, from the exact binomial
distribution, so a count of zero still has an upper end above zero. The
interval treats the cases as independent draws at one rate. **These are
authored and cached cases, not live traffic:** the rates on real chats and
groups are not measured.

**What it means.** With 18 positive cases, no threshold can promise at most 5%
misses at 95% confidence: even catching all 18 would leave a one-sided upper
bound of 15.3%. That takes at least 59 positive cases with no misses (72 for
the two-sided bound the table uses).

## Not measured

- The deployment's savings: how often real blocks are hostile, call for
  suggestions or are softened. Receipts record cost but not latency.
- Decider V1.2's latency, and decisions with today's questions, such as
  hostile-wording screening, which these checks did not include.
- Quality beyond these small authored sets. The larger finite checks' results,
  including their Low quality notes, are in
  [inference](inference.md#retained-acceptance-limits).
