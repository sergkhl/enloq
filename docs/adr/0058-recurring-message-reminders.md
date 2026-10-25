---
status: accepted
---

# Recurring message reminders

## Decision

**Schedules (Q1).** A private human chat has at most one Weekly or Monthly
schedule, configured in its Correspondent Profile. Weekly chooses a weekday;
Monthly a calendar date, using the last day of a short month without changing
the selected date. The schedule saves its timezone, initially the device's,
and an editable fallback language, initially the device's. Travel changes
neither. Show the next delivery before saving; edits begin at the next future
delivery. Recent conversation never suppresses a reminder.

**Delivery (Q2).** The default is one daily digest across the member's
accounts, initially 09:00 in a saved device timezone, with editable time and
timezone. A schedule's local calendar date becomes eligible for the first
member digest on or after that date begins. An exact-time schedule instead
uses its saved local time and timezone and is excluded from the digest.
Calendar computations advance a daylight-saving gap to the first valid time;
a repeated time has one occurrence, at its first instance.

**Lifetime (Q3).** Keep only the latest pending occurrence per chat. After
downtime the latest missed digest occurrence waits for the next digest;
exact-time alerts recover the latest missed occurrence once. Never repeatedly
alert an old pending occurrence. Any Telegram-confirmed outgoing message
dated after an occurrence became due completes it, from any client and of
any message type; so does Dismiss. Incoming messages, reactions, opening and
generation complete nothing. Completion does not move future calendar dates.

**Controls (Q4).** Either global or per-chat Automatic suggestions Off pauses
reminders while preserving schedules. On resumes at the next future delivery
without backfill. Account AI Off allows timing, listing and notifications but
blocks generation. Logout hides chat data and stops alerts; local account
removal deletes schedules. Member isolation applies to every list and action.

**Review (Q5).** Opening one pending reminder requests its first suggestion
set. Listing reminders and delivering notifications make no model calls.
Reopening reuses valid options; changed context, expiry and failure require
explicit Generate or Retry. Concurrent first reviews share one occurrence's
attempt. Editing/removing a schedule or completing/replacing its occurrence
invalidates its options and queued alert without changing drafts.

**Wording (Q6).** Extend manual Response Suggestions with a separate check-in
intent and occurrence identity. A nonempty draft supplies intent and gets
additions at its captured cursor, with existing insertion and Undo. An empty
draft gets complete check-ins from bounded current context and enabled
memory, or simple wording in the saved fallback language when neither is
available. Each reviewed occurrence gets newly generated wording; retain no
wording history to prevent repetition. Existing Style, writer routes,
Decider labels, memory restrictions, twelve-hour validity, late-result
rejection, member budgets and send guards apply.

**Navigation (Q7).** Private, Groups, Saved, then Reminders with a clock icon.
Reminders clearly says All accounts and lists pending and upcoming schedules
with account labels and paused/unavailable states. It is member-scoped,
including the `?tab=reminders` destination; other tabs stay account-scoped.
Review and exact-alert taps name the exact account, chat and occurrence.
Digest taps open the combined list. A stale occurrence never opens or
generates for a replacement occurrence.

**Storage (Q8).** Schedules and occurrence/notification claims are durable,
separate from twelve-hour suggestion state and independent of seven-day chat
visibility and thirty-day content expiry. Timing runs on the service tick
outside AI admission. Migration creates no schedules. Schedule/settings
writes carry revisions; review/dismiss identify their occurrence, and review
also carries the draft revision and cursor.

**Later scope (Q9).** Conversation-derived timed-event proposals come next.
Other reminder workflows remain explicit open proposals in TODO; this release
authorizes recurring calendar reminders only and never sends automatically.

## Consequences

Reminders are a narrow exception to ADR 13's exclusion of attention/task
tracking. They invite manual review and sending; they infer no obligations,
score no relationships and introduce no automatic-send path.
