# Message reminders and full-message suggestions

Last meaningful update: 2026-10-26
Status: active; implementation awaits the owner's go-ahead and the open
question below.

Deliver the adopted [ADR 58](../adr/0058-recurring-message-reminders.md)
redesign and [ADR 25](../adr/0025-emotion-response-suggestions.md)'s
Suggestions changes, with their consequences in ADR 13, 15, 20, 36 and 51.
Two phases, each deployed and checked on its own: suggestions first, since
reminder sets reuse their complete-message and replace rules, then reminders.
Until a phase is delivered, its code, journeys and runbooks keep the earlier
behavior. Delivery covers the service, web, Android Saved Builds and finite
live acceptance; iOS keeps its Mac build and enrollment gates in TODO.

## Phase 1: Suggestions (ADR 25)

1. **Writer contract.** Manual options are complete messages. A nonblank
   draft is the starting idea, which options may develop beyond its words
   without inventing facts; bracketed text instructs the writer, never
   appears in options and never chooses the language; plain draft wording
   supplies it. Retire the draft echo stripping for manual sets and keep the
   other format checks. Bump the shipped-defaults revision.
2. **Choosing.** A manual choice replaces the draft's text, keeping images, a
   GIF and the reply target, with the caret at its end and Undo restoring the
   previous draft. Automatic choices keep cursor insertion.
3. **Composer, both clients.** The button reads Suggestions, as visible label
   and accessible name. It is disabled with its spinner while any set for the
   chat is queued or running, and disabled without it while one waits for
   budget, AI processing or the provider; a draft edit that withdraws a set
   frees it. Send asks for confirmation while the draft contains `[...]`.
4. **Checks.** Backend tests for the writer packet, replace and Undo, and the
   button's states; Jest and phone-size web journeys renamed and extended to
   drafts, brackets, replacement, Undo, disabled states and the Send
   confirmation; the Maestro compose, suggestions and saved flows renamed.
   Full `./scripts/check`, then the Android journeys on the S9+ with Maya or
   the development login only.
5. **Delivery.** AGENTS.md's order: commit, merge, push, remove the task
   checkout and deploy clean main; verify health, run the Phase 1 live check,
   publish the Android Saved Build, update the mobile, operations and
   inference runbooks, and record the evidence here.

## Phase 2: Reminders (ADR 58)

6. **Schedules and migration.** Several schedules per chat, each with an id:
   Once, Every N days, Every N weeks on a weekday from a start week, Monthly
   and Yearly (29 February on the 28th in other years), a Message Idea, the
   timezone and a fallback language from a list. Schema 40 moves each existing
   schedule and its pending occurrence to per-schedule tables, keeping
   revisions, and maps free-text languages to the list. Previews, revisions
   and member isolation stay.
7. **Occurrences and completion.** Create each occurrence two minutes before
   its alert for preparation; it becomes pending at its due time, and only the
   latest stays per schedule. Without an idea, any confirmed later outgoing
   message completes it; with one, only a confirmed send whose draft took one
   of its options (edits keep that mark, Undo and clearing drop it). Dismiss
   completes either. Nothing lapses; a Once schedule ends on completion,
   deleting its idea and Kept Messages.
8. **Reminder sets.** One manual set covers every pending occurrence of a
   chat, owns its suggestion area however the chat is opened and stops
   automatic preparation there. It is prepared two minutes before the alert,
   refreshed 30 quiet seconds after any new message and replaced on opening
   only when it no longer fits, never rerunning an unchanged failure; the
   Suggestions button requests the same set. The writer packet carries each
   occurrence's idea or check-in intent, the chat's draft, the 20 newest
   cached messages within 30 days split at the earliest pending due time
   (later ones answered, with stance variants where asked; earlier ones
   reference), Kept Messages, enabled memory and the fallback language.
   Retire the `reminder` route parameter and the separate review request.
9. **Kept Messages.** Read explicit dates in an idea: English and Russian
   month names before or after the day, `d.m` and `d.m.yyyy` with dots, and
   ISO dates; a yearless date means its latest occurrence before the schedule
   is next due. Copy up to 20 of that day's cached messages, newest first,
   with transcripts and image meanings, on save or when a future day ends in
   the schedule's timezone. Mirror edits and deletions while the originals
   are cached; delete the copy when the schedule ends or is removed or the
   date leaves its idea; report the editor's status line.
10. **Alerts.** Exact alerts fetch the person's name and the idea's first
    line, or "Check in"; digests fetch "Reminders" with up to three names and
    "+N", and one naming a single reminder opens its chat. Pushes stay opaque
    and never wait for suggestions.
11. **APIs and schema.** Per-schedule create, edit, remove and dismiss; the
    member's list as schedule rows; timezone and language lists; Add
    reminder's person search reusing New Message's. Regenerate the API schema
    for both clients.
12. **Clients, app first and web to match (ADR 44).**
    - Reminders tab: one All accounts header, the "Daily digest · 09:00" line
      and its sheet, chat-like rows (photo, name, account label with several
      accounts, repeat summary, the idea's first line, one date), tapping to
      the chat or the editor, a compact Dismiss, Edit in a ⋯ menu, and Add
      reminder (account choice, then the New Message picker).
    - The reminder editor screen, opened from the profile, the tab and the
      Reminder Bar: the compact Repeat choice, the native time picker (a new
      native module; confirm Expo SDK 57 support first), the 1–31 grid, the
      searchable timezone and language lists folded into one row, the Message
      Idea, the summary line, the Kept Messages status and a confirmed Remove.
    - Profile: the person's reminder rows and Add reminder.
    - Chat: the Reminder Bar, each occurrence with its line, Dismiss and
      editor link, above the reminder set.
13. **Checks.** Backend: cadences and calendar edges (intervals, yearly,
    short months, daylight saving), the migration, per-schedule occurrences,
    early preparation, refresh after quiet, combined sets, automatic
    suppression, both completion paths, no lapse, date reading, copying,
    mirroring and deletion of Kept Messages, alert text and member isolation.
    Jest and phone-size web journeys for the tab, editor, Add reminder, bar,
    Dismiss and Remove confirmation; the Maestro reminders flow after
    rebuilding Enloq Dev for the native module. Full `./scripts/check` and the
    Android journeys on the S9+.
14. **Delivery.** As unit 5, with the Phase 2 live check, an Android Saved
    Build and the runbooks' reminder sections. iOS waits for the next Mac
    build.

## Acceptance bounds

Authored content between the Owner Account and Maya only, in the VPS browser
first ([operations](../operations.md#live-checks-in-a-vps-browser)). Verify
the adopted routes and rates before each run, keep Remember this chat Off in
the Maya chat, record the chat's settings and draft and restore them, and add
the actual requests to TODO's allowance.

- **Phase 1:** one draft set and one bracketed set in each Style, no sends:
  at most 20 model requests, 20 minutes and $0.05 counted.
- **Phase 2:** an exact-time Maya reminder whose idea names the day of that
  run's authored test messages, one without an idea, a Maya message for the
  refresh, a send from the set and a Dismiss: at most 60 model requests,
  three authored sends, 45 minutes and $0.15 counted.

Format, delivery and target failures are material; weak wording is Low
quality in [inference](../inference.md#retained-acceptance-limits).

## Open questions

- **Option length for long drafts.** Options are at most 280 characters
  today, too short to carry a long draft's whole idea. Proposed: a
  draft-guided option may run to the draft's length plus 280 characters,
  capped at Telegram's 4,096. Needs the owner's answer before unit 1.

## Validation Log

- 2026-10-26: written from the adopted ADR edits in `dcf9220`. No code
  changed, no model requests, no sends.

## Open findings

- None.
