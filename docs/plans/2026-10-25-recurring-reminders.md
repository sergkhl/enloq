# Recurring message reminders

Last meaningful update: 2026-10-25

Implement the accepted weekly/monthly private-chat reminder release in
[ADR 58](../adr/0058-recurring-message-reminders.md). Delivery includes the
service, web, Android Saved Build and finite acceptance. iOS journeys and
push retain their existing Mac/enrollment gates.

## Units

1. Durable calendar schedules, latest occurrences, confirmed-send completion,
   pause/resume, member digest settings and durable notification claims.
2. Account-scoped schedule/review/dismiss APIs and occurrence-bound manual
   suggestions, retaining draft, freshness, budget and send guards.
3. Fourth Reminders tab in both clients, profile editor, next occurrence,
   combined member list and exact notification navigation.
4. Deterministic timing, isolation, review and client checks; full
   `./scripts/check` and Android fixture journey.
5. Commit, merge, push and verify main; remove the clean merged task checkout;
   deploy clean main, verify health, run finite live acceptance and publish
   the Android Saved Build. Preserve evidence before deleting this plan.

## Acceptance bounds

Authored content only, Owner Account–Maya only. At most 60 model requests,
two Telegram sends, 30 minutes and $0.10, within unchanged member budgets.
Verify the adopted routes and rates first. Restore settings, record actual
requests in TODO and assess semantics separately from format and delivery.

## Validation Log

- Worktree starts at current origin/main; migration must create no schedules.
- Backend: 24 dedicated reminder checks pass, covering calendar edges,
  durable latest occurrences, concurrent review, failures/budgets, member
  isolation, combined/exact alerts, stale targets and restart claims. The
  earlier affected backend batch passed 80 checks.
- Web: all three phone-size reminder journeys pass, including review reuse,
  draft insertion/Undo, configuration and the member-scoped URL.
- Android device journey attempted: no phone is connected over the saved
  tunnel; `mobile/scripts/journeys android` reports "Boot the simulator or
  connect the phone, then run this again." Device acceptance remains open.
- `./scripts/check` passed: 3,937 backend tests, 56 phone-size web journeys
  and 305 Jest tests, plus lint, format and both client typechecks. The final
  conversation occurrence projection also passed its 43 affected backend
  checks. Schema/default assertions now expect schema 39/defaults 18.
- The running service's own proxy caller verified MiMo/Xiaomi, Muse/Meta
  and Decider/Perplexity at the adopted rates and checkpoint. Metadata reads
  made no model requests; finite acceptance re-verifies before its run.

## Open findings

- None yet.
