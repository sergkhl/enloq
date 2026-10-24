# Prompt notifications, hostile-only softening and manual suggestions

Status: complete; deployed and Android Saved Build published.
Last meaningful update: 2026-10-24

Policy: ADR 20 (notifications), ADR 25 and ADR 49 (suggestions), ADR 53
(softening), ADR 54 (incoming text), ADR 56 (automation and Style).
Scope: backend, shared schema, phone and matching web clients, checks,
runbooks, VPS deployment and Android Saved Build. Preserve credentials,
Telegram authorizations, spending and unrelated work.

## Units

1. Separate automatic generation settings from Style; migrate existing
   Off/On/Idioms without changing spending. Add bounded manual generation,
   draft-intent continuations, explicit attempts, freshness and ownership.
   Log content-free scheduling reasons and outcomes.
2. Replace mood-triggered Calming with explicit hostile-wording softening.
   Screen notification text on arrival, within five seconds including queue
   time; preserve raw fallback, silent burst updates and independent unseen
   readiness. Keep combined-block work at 30 quiet seconds.
3. Replace outgoing Rewrite and tone controls in both clients with manual
   suggestions, retaining cursor insertion, Undo and exact draft/send guards.
   Update profiles, Automatic suggestions, Style and Softened labels; groups
   and Saved remain without suggestion generation. Regenerate API schema.
4. Update meaningful behavior tests and fixture journeys; run ./scripts/check.
   Adapt the finite hostile-only acceptance path and run bounded authored
   provider acceptance with verified routes and accounting. Run guarded live
   browser checks only between the Owner Account and Maya where needed.
5. Update current runbook mechanics and limits, validate and commit the task
   worktree, merge from primary main, push and verify origin/main, remove the
   clean merged task worktree and branch, then deploy from clean primary main.
   Publish the Android Saved Build, verify health and document exact evidence.
   Preserve the completed plan in Git before its separate deletion commit.

## Acceptance

- Automatic Off leaves manual generation usable; Friendly is default, Style
  is shared by both generation kinds, and existing settings convert safely.
- Manual work uses bounded cached context and captured draft/cursor, allows
  draft-only work, and never mutates text until selection. It expires after
  twelve hours from request, withdraws on input changes and cannot be
  replaced by automatic work. Deliberate alternatives are distinct attempts;
  duplicate active taps are deduplicated. Selection inserts with Undo.
- Hostility, including towards third parties, alone triggers softening.
  Non-hostile anger, frustration, teasing and reported insults do not.
- Clean notification text waits for neither quiet nor mood; hostile text
  waits for its valid softened result. Failure/AI Off/budget/timeout exposes
  the original as adopted. Later burst updates and late completion never add
  a sound; private payload and target/eligibility guards remain intact.
- Notification screening reads or teaches no memory and triggers no
  suggestions. Logs include decisions and outcomes without content or native
  account/chat identities. No automatic model/provider fallback is added.
- Backend, phone-size web and Jest checks pass; live claims remain distinct
  from deterministic checks. Runtime image and Saved Build identify the
  committed change. iPhone delivery follows its existing Mac build boundary.

## Delivery and retained limits

Implementation and the complete deterministic checks passed. Provider semantic
acceptance completed as Low quality. The pushed revision is deployed healthy;
the guarded manual and arrival browser checks passed their controls. Reviewing
the manual output found a full-draft echo in Professional options; its
deterministic extraction and regression checks now pass. The follow-up is
deployed healthy, its fresh Professional check passed, and the matching
Android Saved Build is published and pinned. Native phone notification
display and an iPhone build are separate claims; the browser arrival check
observes service eligibility metadata. No agent-owned work remains in this
plan. Owner quality review, phone installation and native delivery gates
remain in TODO; they are not inferred from the deterministic or browser checks.

Provider acceptance bounds: the authored softening pass uses at most 32
rewrite, 32 decision and 30 sarcasm-judge requests, 2,400 seconds and $0.20
counted, with no Telegram client. The subsequent guarded manual browser check
allows three generations and at most 15 writer/label requests, 25 minutes and
$0.15 counted, with no sends. The arrival check allows three authored
Maya-to-owner messages, at most eight model requests, 15 minutes and $0.10
counted. Preserve chat choices and drafts; Remember remains Off. Record every
actual request in TODO. Low semantic quality is recorded without unchanged
resampling; target, format, source, budget and delivery failures remain material.

The insertion fix permits one new Professional generation with a different
authored draft: at most five writer/label requests including no-work retries,
ten minutes and $0.05 counted, no sends or memory changes. Restore the prior
settings and empty composer. This verifies changed output handling, not a
resample of the hostile-only semantic corpus.

## Validation Log

- Started from the accepted interview documents; application code remains at
  the existing implementation. No model requests or live sends have run.
- Implemented the initial shared manual path, settings split, explicit
  hostility signal, arrival screening and both clients. A focused 75-case
  backend run and the 19-case settings run passed before subsequent cleanup.
  Initial full baselines: backend 3,839 passed / 51 failed; mobile 283 passed /
  14 failed. These are incomplete validation, not delivery evidence.
- Moved all task files to an isolated task branch/worktree after the owner's
  deployment-order instruction; verified the transfer and left primary main
  clean. Private runtime and client credentials remain in their existing
  locations.
- Updated the mobile controls and composer tests: the focused seven suites
  passed all 92 cases; phone notification tests passed all 11 cases. Web's
  focused journeys passed 12 of 13; the remaining assertion still named
  removed Neutral guidance and was corrected to Professional.
- Removed the remaining outgoing service, endpoints, role, tone schema and
  outgoing-only guidance. Replaced retained draft, budget, memory and
  authorization checks with manual-generation checks. The first complete
  backend regression after removal passed 3,846 cases, with three old endpoint
  expectations failing; those expectations were updated. Full validation is
  still in progress.
- The service's own caller key passed one content-free decision request on
  pinned Decider V1.2 checkpoint -20261016: 1.865 seconds, $0.000017560
  receipted, current input rate $0.02 per million and zero output rate. No
  Telegram session was opened or message sent. Test allowance now 1,778.
- `./scripts/check` passed: 3,850 backend cases, web typecheck and all 53
  phone-size journeys, mobile typecheck/format and all 38 Jest suites / 302
  cases. Subsequent focused checks cover the final diagnostics, target
  eligibility, pending-draft recovery and old mood-release cutover. The
  old outgoing service and role are removed; pending drafts retain their
  authored composition, and a legacy mood flag cannot authorize softening.
- Final focused cutover verification passed 118 cases, including queued
  legacy angry/frustrated releases, draft recovery, held decisions,
  notification guards and manual generation. Ruff and format checks passed.
  Android duplicate-event delivery and silent updates passed their 12-case
  focused suite and the complete Jest run.
- Candidate `737f6a0` was merged and pushed, then built from clean primary
  main for the authored provider check. The service was stopped under its
  existing lifecycle authorization for the check lock and resumed healthy on
  its prior image afterwards. No live Telegram session or send was used.
- Authored hostile-only provider pass: 30 cases, 30 pinned Decider requests,
  12 Muse rewrites and 12 authorized sarcasm judges; 157.50 seconds and
  $0.004644022 counted, within the declared bounds. All formats were usable;
  no route or provider error was recorded. Twelve non-hostile controls stayed
  unchanged. Detection missed six subtle sarcasm, blame or forwarded cases:
  **Low quality**, not a zero-miss semantic pass. All 12 softened outputs
  passed the automated checks; the owner's reading remains open. No unchanged
  resampling ran. Private evidence is the service data folder's
  `calming-check-5qord800/report.json` and `pairs.md`. Allowance now 1,832.
- Verified current rates before that pass, per million input/output tokens:
  MiMo/Xiaomi $0.14/$0.28; Muse/Meta $0.10/$0.20; Decider/Perplexity
  checkpoint -20261016 $0.02/$0.00. The service's own configured caller key
  served the check, with no model/provider fallback or configuration change.
- Merged and pushed `a8b5a5d`, verified origin/main equality, removed the clean
  task worktree and merged branch, then deployed from clean primary main.
  The running image `enloq:a8b5a5d801c7` reported healthy.
- Guarded Owner Account/Maya browser checks passed: three manual generations
  exercised Friendly, Professional and Idioms with automation Off, unchanged
  draft during generation, insertion, Undo and no send. Three authored native
  arrivals exercised clean/hostile/clean screening; first clean eligibility
  took 2.076 seconds from service arrival. A hostile source held, later clean
  text updated silently in the same group, and completed softening could not
  replace the newer preview. Settings were restored; Remember stayed Off.
- The two browser runs used eleven receipted requests: three writers and
  three label calls ($0.001778381), plus three arrival screens, one block
  decision and one softening ($0.000748140). Total $0.002526521; no refused or
  unknown outcomes. Allowance now 1,843. Private arrival metadata and scoped
  receipts are retained outside Playwright's cleared output directory in
  `.local/live-evidence/`. No physical-phone banner claim is made.
- The first manual browser assertion missed a semantic output-contract
  defect: Professional sometimes repeated the complete draft in its options.
  A follow-up task isolates deterministic extraction of an exactly preserved
  prefix/suffix at the captured UTF-16 cursor, leaving only its addition.
  Punctuation insertion and copied draft literals are covered too; generated
  alternatives do not rewrite authored text or resample on failure. The live
  check now rejects whole-draft echoes and preserves evidence across runs.
- Follow-up verification passed 259 backend cases across manual generation,
  output extraction, typography, scheduling, controls, settings, acceptance
  and source state. It includes exact mid-draft and UTF-16 extraction,
  punctuation, unchanged draft/Undo and echo-only format failure without
  another call. Ruff, format, web typecheck and whitespace checks passed.
- Follow-up `e8fe3d9417986dfcab5ce688c3062c41b02d2685` was committed in its
  task worktree, merged and pushed from primary main, and verified against
  origin/main. The clean merged worktree and branch were removed before
  deploying `enloq:e8fe3d941798` from clean primary main. The container is
  healthy and its OCI revision matches the commit.
- Reverified the exact MiMo/Xiaomi and pinned Decider/Perplexity routes and
  unchanged rates before the separately bounded Professional check. The fresh
  authored Monday draft passed generation, unchanged composition, insertion
  exactly once, Undo and no-send assertions in 34.5 seconds. Its one writer
  and one label request cost $0.000484814, both receipted. Settings and empty
  composer were restored, Remember stayed Off, and there was no resampling.
  Allowance now 1,845. Private route, generated-option and scoped receipt
  evidence is retained in `.local/live-evidence/`.
- Corrected remaining runbook descriptions of removed group composer tones,
  outgoing budget roles and concurrency; manual draft-only generation remains
  distinct from automatic recent-chat admission.
- Built the release arm64 Android APK from clean primary commit `e8fe3d9`
  with `mobile/scripts/publish-android`. The service confirmed replacement of
  its one Saved Build and that it is pinned. The local publication record
  matches the full deployed revision. APK signature verification passed;
  size is 57,667,848 bytes and SHA-256 is
  `645616f9cc12f19b8b3a1ad6a8947075425d838858550724dab89203c57d8e29`.
  The script exited successfully; its temporary service upload was removed.
- Final read-only checks confirmed an empty Maya composer, Remember Off and
  restored Automatic suggestions On / Friendly. The deployed container
  remains healthy. Documentation local-link and whitespace checks passed.
  The complete implementation and delivery record is committed before the
  separate plan-deletion commit; remaining source changes are documentation
  only and require no additional model calls or deployment.
