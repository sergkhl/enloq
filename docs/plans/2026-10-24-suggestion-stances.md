# Allow one-sided suggestion sets

Last meaningful update: 2026-10-24

The owner chose to show otherwise valid options even when their answer
stances all agree or all decline. ADR 49 Q4, Q8, Q19 and Q20 own the rule;
it applies to automatic and manual generation in every Style.

## Work and acceptance

1. Remove the contrast-only validation and its draft exception; align the
   writer's fixed core, owning ADRs, vocabulary and runbooks. Preserve label,
   schema, text, duplication, grounding, budget and source-validity rules.
2. Prove one-sided sets publish and insert without sending across both
   generation paths and all styles. Malformed sets must still fail. Run
   `./scripts/check`.
3. Run a finite authored provider check on the committed candidate: at most
   six writer and six label requests, 600 seconds and $0.10 counted, through
   the adopted MiMo, Muse and Decider routes. Verify rates before calls;
   hold the service owner lock with the service stopped, use a temporary
   content store and fake Telegram transport, and preserve shared spending.
4. Merge and push main, verify the remote revision, remove the merged clean
   worktree and branch, then deploy from clean primary main. Confirm the
   running revision and health. This changes no mobile code or API schema.
5. Record validation, retain durable findings in their owning documents,
   then close this plan in a separate commit.

## Validation Log

- Diagnosis: both recent writer and label requests completed successfully;
  the final contrast-only validator rejected their sets. The existing 134
  targeted tests passed and confirmed the old rejection was intentional.
- Implementation removes the contrast-only validator and its special
  contexts, updates fixed guidance to revision 17 and records the rule in
  the owning ADRs. Focused validation: 253 tests passed, covering all styles
  and both paths, real label replacement, unchanged draft, insertion without
  sending, no resampling and retained malformed-output failures. Ruff passed;
  before/after suggestion JSON schemas are identical. Full checks are running.
- The six-case provider harness passed offline with fake routes and keeps its
  source beside private evidence. Live preflight found no running model jobs;
  the existing Daily Budget admits the bounded check without changing it.
- Candidate `e6e4726` passed the authored live format check in 57.45 seconds:
  six writer and six label requests, $0.001383608 counted, no receipt errors
  and six published sets. Five sets had only accept or only decline stances.
  All three styles passed through automatic and blank-draft manual paths,
  preserving the draft and inserting without a send. Exact MiMo/Xiaomi,
  Muse/Meta and pinned Decider/Perplexity routes and current rates were
  verified before calls. The service restarted healthy after the check.
  Private evidence and harness: service `data/suggestion-stances-9t15_1xw/`.
  The allowance count includes these 12 requests.
- Semantic review remains separate: manual Friendly made a firm refusal
  tentative and offered an unsupplied update, and Professional invented being
  busy as a reason. Both are **Low quality**, retained in `docs/inference.md`;
  no request was resampled and the format fix does not claim to repair them.
- `./scripts/check` passed: Ruff and formatting, 3,913 backend tests, web
  typecheck and 53 phone-size journeys, mobile typecheck/formatting and 302
  Jest tests in 38 suites. No code changed after these checks; subsequent
  changes record acceptance and its request allowance only.
- Delivered `653fb25` from clean primary main after the task branch was
  merged, pushed, verified against the remote and removed with its clean
  worktree. The running image's revision matches; container health is healthy
  and the public health endpoint returns HTTP 200. A content-free in-container
  check confirms fixed guidance revision 17 and accepts both all-accept and
  all-decline sets. No mobile code, shared code or API schema changed.

## Open findings

- No remaining implementation or delivery work for the format failure.
  The old-attempt behavior is retained in `docs/inference.md`; a new manual
  press or qualifying changed source uses the corrected rule.
- The two semantic misses remain Low quality in the inference runbook and
  fall under TODO's existing Quality review item. This is not a semantic
  acceptance claim.
