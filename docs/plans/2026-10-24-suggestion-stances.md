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

## Open findings

- Full validation, finite provider acceptance and deployment remain. Old failed attempts retain no
  recoverable options and will not be regenerated automatically; a new
  manual press or qualifying changed source uses the corrected rule.
