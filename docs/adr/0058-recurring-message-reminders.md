---
status: accepted
---

# Message reminders

## Decision

**Schedules (Q1).** A private human chat may have several Reminder Schedules,
each with its own repeat, alert and Message Idea. Repeat is one compact
choice: Once on a date; Every N days from a start date, the first on that
date; Every N weeks on a weekday from a start week; Monthly on a calendar
date; or Yearly on a date. Monthly and Yearly use the last day of a short
month, and 29 February the 28th in other years, without changing the selected
date. A schedule saves its timezone, initially the device's, and an editable
fallback language, initially the chat's recent language when known and
otherwise the device's. Travel changes neither. The editor shows the next
delivery before saving; edits begin at the next future delivery. Recent
conversation never suppresses a reminder. There is no count limit; the Daily
Budget bounds the suggestions reminders prepare.

**Delivery (Q2).** The default is one daily digest across the member's
accounts, initially 09:00 in a saved device timezone, with editable time and
timezone. A schedule's local calendar date becomes eligible for the first
member digest on or after that date begins. An exact-time schedule instead
uses its saved local time and timezone and is excluded from the digest.
Calendar computations advance a daylight-saving gap to the first valid time;
a repeated time has one occurrence, at its first instance. An exact alert
shows the person's name and its Message Idea's first line, or "Check in"
without one; a digest shows "Reminders" with up to three names and "+N" for
the rest. Clients fetch this text from the service; pushes carry none of it.
Alerts never wait for suggestions.

**Lifetime (Q3).** Keep only the latest pending occurrence per schedule.
After downtime the latest missed digest occurrence waits for the next digest;
exact-time alerts recover the latest missed occurrence once. Never repeatedly
alert an old pending occurrence. A schedule without a Message Idea completes
on any Telegram-confirmed outgoing message dated after its occurrence became
due, from any client and of any message type. A schedule with one completes
only when Telegram confirms a send whose text started from its suggestions in
Enloq, edited or not. Dismiss completes either kind. Incoming messages,
reactions, opening and generation complete nothing, and a pending occurrence
never lapses. Completion does not move future calendar dates. A Once schedule
ends, with its Message Idea and Kept Messages, when its occurrence completes.

**Controls (Q4).** Either global or per-chat Automatic suggestions Off pauses
reminders while preserving schedules. On resumes at the next future delivery
without backfill. Account AI Off allows timing, listing and notifications but
blocks generation. Logout hides chat data and stops alerts; local account
removal deletes schedules and their Kept Messages. Member isolation applies
to every list and action. Remove reminder asks for confirmation.

**Suggestions (Q5).** While any of a chat's schedules has a pending
occurrence, reminder suggestions own that chat's suggestion area, however the
chat was opened, and no automatic set is prepared there. One set covers every
pending occurrence of the chat. It is prepared two minutes before the alert;
new messages refresh it in the background after 30 quiet seconds; opening
prepares a fresh set only when the current one no longer fits (new messages,
a draft edit, expiry) and never reruns an unchanged failure. The Suggestions
button requests the same kind of set. Listing reminders and delivering alerts
make no model calls. Editing or removing a schedule, or completing or
replacing its occurrence, invalidates its options and queued alert without
changing drafts.

**Wording (Q6).** Every option is a complete message that keeps the purpose
of each pending occurrence: its Message Idea, or a gentle check-in without
one, with any text in the chat's draft as further intent. Options develop
an idea beyond its own words but invent no facts. Messages that arrived
after an occurrence fell due are answered, with Accept, Decline and Later
variants when they ask for something; earlier messages are reference only,
so an old request is never answered or revived. Context is the 20 newest
cached messages within the 30-day cache, any Kept Messages (Q11) and enabled
memory; without conversation or memory, options use the fallback language.
Each set gets newly generated wording; no wording history is kept to prevent
repetition. Existing Style, writer routes, Decider labels, memory
restrictions, twelve-hour validity, late-result rejection, member budgets and
send guards apply ([ADR 25](0025-emotion-response-suggestions.md)).

**Navigation (Q7).** Private, Groups, Saved, then Reminders with a clock icon.
Reminders says All accounts once, in its header, and starts with one line,
"Daily digest · 09:00", whose sheet sets the digest time and timezone. Add
reminder chooses the account when the member has several, then the person
with New Message's picker, and opens the editor. Pending and upcoming rows
look like chat rows: photo, name, account label when the member has several
accounts, the repeat ("Every Monday · 09:00 digest"), the Message Idea's
first line and one date, when it fell due or the next delivery. Tapping a
pending row opens its chat and tapping an upcoming row opens its editor; a
pending row keeps a compact Dismiss, and the row's ⋯ menu holds Edit. A
digest naming one reminder opens it directly; others open the list. Reminders
is member-scoped, including the `?tab=reminders` destination; other tabs stay
account-scoped.

**Storage (Q8).** Schedules, occurrence and notification claims and Kept
Messages are durable, separate from twelve-hour suggestion state and
independent of seven-day chat visibility and thirty-day content expiry.
Timing runs on the service tick outside AI admission. Schedule and settings
writes carry revisions; Dismiss identifies its occurrence, and suggestion
requests carry the draft revision.

**Later scope (Q9).** Conversation-derived timed-event proposals come next
and remain an open proposal in TODO. Reminders never send automatically.

**Message Idea (Q10).** A schedule's optional text: a draft, instructions in
square brackets, or both. It shapes every occurrence's suggestions until
edited; empty means a gentle check-in. Its first line shows on the reminder's
row, in the Reminder Bar and in exact alerts. It never enters the chat's
draft, and no on-screen hint explains brackets. Bracketed instructions never
choose the language.

**Kept Messages (Q11).** An explicit date in a Message Idea, such as "oct 25",
"25.10" or "25 октября", names a day; a date without a year means its latest
occurrence before the schedule is next due, and relative words such as
"yesterday" name none. Saving keeps a private copy of up to 20 of that day's
cached messages, newest first, with their transcripts and image meanings. A
day still ahead is copied when it ends; a day already older than the 30-day
cache has nothing to copy. Edits and deletions observed while the originals
are cached update the copy, as memory citations do
([ADR 55](0055-person-and-account-memory-and-traits.md)). The copy is deleted
when its schedule ends or is removed, or when the date leaves its idea. The
editor shows its status, such as "Keeps 12 messages from 25 Oct".

**Editor (Q12).** The reminder editor is its own screen, opened from the
Correspondent Profile's Reminder section, which lists the person's schedules
and Add reminder, from the Reminders tab and from the Reminder Bar. It uses
pickers: a time picker, a 1–31 date grid, a searchable timezone list and a
language list. Timezone and fallback language fold into one row showing
their values. One summary line, such as "Every Monday · 09:00 digest · next
Mon 2 Nov", shows the schedule and its next delivery.

**Reminder Bar (Q13).** While a chat has pending occurrences, a slim bar above
its suggestions lists each one by its Message Idea's first line, or "Check-in
reminder", with when it fell due, its own Dismiss and a way to its editor.
It is status, not a usage hint.

## Consequences

Reminders are a narrow exception to ADR 13's exclusion of attention/task
tracking. The member writes every schedule and Message Idea; reminders infer
no obligations, score no relationships and introduce no automatic-send path.
Kept Messages are the one place message text outlives the 30-day cache, for
as long as the schedule whose idea named the day.
