# Citation check and Ask this group

The decisions are in [ADR 34](../adr/0034-group-chats-and-daily-briefings.md)
(Citation check, Q1-Q7) and [ADR 59](../adr/0059-ask-this-group.md). This
plan holds the order of the work, how each unit is checked and what is still
open.

## Units, in order

1. **Citation check.** A decision role that checks each final topic between
   validation and publishing, applies its removals to citations and
   participant sources, records unchecked outcomes and counts against the
   update's check limit. Checked by domain tests (packets, bounds, answers,
   removals) and pipeline tests with a fake decision model: supported,
   unsupported, failed, refused and over-limit topics.
2. **Check measurement**, `scripts/check-citations.py`. Authored claim and
   message pairs labelled before the run, topic-style and answer-style, in
   Russian, English and both, including near-identical unrelated notes like
   the published failure; then live finals of authored group days, labelled
   afterwards. It reports, with 95% ranges, supported citations removed,
   unsupported ones kept, and topics or statements left with nothing
   supported, with the spread of probabilities.
3. **The owner's decisions**: ADR 34 citation check Q4 and ADR 59 Q6, from
   unit 2's report. Then both clients show Q4's answer, and the citation check
   can deploy ahead of Ask.
4. **Ask this group, service.** Keyword and vector retrieval with fusion, the
   vector cache, the `group_question` role, the check, abstention, the API,
   its budget and class. Checked by an offline retrieval test on authored
   fixtures and pipeline tests with fake adapters.
5. **Ask measurement**, `scripts/check-ask.py`. The retrieval comparison
   (keyword only, each embedder, fused, each reranker and none) by recall at
   k, MRR and nDCG at 10, with latency and cost; then MiMo and gpt-6.1-sol
   answers on the chosen retrieval, checked and graded. The embedder and any
   reranker follow from it, and the other routes leave the proxy.
6. **Ask screens** in the app and the web (ADR 59 Q1), with Jest tests and
   phone-size journeys.
7. **Delivery.** Inference, measurements, operations and mobile runbooks;
   deploy; the Android Saved Build.

## Bounds

Both checks together make at most 1,200 requests and spend at most $1.00
(ADR 59 Q11), added to the [live test allowance](TODO.md#live-test-allowance).
Finite checks run in a one-off container of the image to be deployed, with
the service stopped.

## Open

- ADR 34 citation check Q4 and ADR 59 Q6, after unit 2.
- The embedder, and whether to rerank, after unit 5.
