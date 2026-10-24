# Prompt notifications, hostile-only softening and manual suggestions

Status: active; implementation authorized.
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

## Open findings

Implementation and deterministic checks are complete. Provider semantic
acceptance, guarded browser checks, VPS deployment and the Android Saved Build
remain. Native phone notification display and an iPhone build are separate
claims; the browser arrival check observes service eligibility metadata.

Provider acceptance bounds: the authored softening pass uses at most 32
rewrite, 32 decision and 30 sarcasm-judge requests, 2,400 seconds and $0.20
counted, with no Telegram client. The subsequent guarded manual browser check
allows three generations and at most 15 writer/label requests, 25 minutes and
$0.15 counted, with no sends. The arrival check allows three authored
Maya-to-owner messages, at most eight model requests, 15 minutes and $0.10
counted. Preserve chat choices and drafts; Remember remains Off. Record every
actual request in TODO. Low semantic quality is recorded without unchanged
resampling; target, format, source, budget and delivery failures remain material.

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
