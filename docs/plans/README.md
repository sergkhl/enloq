# Plans

A plan exists only while its work is active. It holds what the decisions
leave open: the units and their order, how each is checked, and questions not
yet answered. Proposed choices in a plan are not accepted decisions.

- **Decisions** live in [`docs/adr/`](../adr/), one ADR per subject. An ADR is
  edited in place as its decisions change, so it always states the current
  decision.
- **Vocabulary** lives in [`CONTEXT.md`](../../CONTEXT.md), and lasting
  procedures in the runbooks ([operations](../operations.md),
  [inference](../inference.md), [media delivery](../media-delivery.md),
  [mobile](../mobile.md)).
- **Open items** live in [TODO.md](TODO.md): proposals awaiting an interview,
  decisions awaiting the owner, agent follow-ups and the owner's gates.

## Active plans

1. [Recurring message reminders](2026-10-25-recurring-reminders.md): durable
   schedules, combined reminders, guarded review, validation and delivery.

Other open work is in [TODO.md](TODO.md). An accepted ADR is not evidence of
implementation or deployment.

## How a plan runs

1. An interview settles the design and records it in a new or existing ADR.
2. A plan, `docs/plans/YYYY-MM-DD-<subject>.md`, is listed under Active plans
   while its units are built and checked.
3. When its work is delivered, the ADR and runbooks already hold what lasts,
   and anything still open moves to [TODO.md](TODO.md). The plan is then
   deleted and removed from this index.

Keep credentials and real correspondence out of plans, as everywhere in the
repository.
