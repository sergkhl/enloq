# Message reminders and full-message suggestions

Last meaningful update: 2026-10-26
Status: active; implementation awaits the owner's go-ahead.

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
   other format checks. Remove the 280-character cap from the prompt,
   validation and storage for every set, giving typography's literal windows
   their own bound; the request's existing output-token bound stays, and an
   option longer than Telegram's 4,096 characters keeps today's visible Send
   refusal. Bump the shipped-defaults revision.
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

- **Phase 1:** one draft set and one bracketed set in each Style, plus one
  long draft, no sends: at most 20 model requests, 20 minutes and $0.05
  counted.
- **Phase 2:** an exact-time Maya reminder whose idea names the day of that
  run's authored test messages, one without an idea, a Maya message for the
  refresh, a send from the set and a Dismiss: at most 60 model requests,
  three authored sends, 45 minutes and $0.15 counted.

Format, delivery and target failures are material; weak wording is Low
quality in [inference](../inference.md#retained-acceptance-limits).

## Validation Log

- 2026-10-26: written from the adopted ADR edits in `dcf9220`. No code
  changed, no model requests, no sends.
- 2026-10-26: the owner removed the option length limit for every set, which
  closed the plan's only open question; ADR 25 and CONTEXT record it.
- 2026-10-26, Phase 1 delivered: `e13e51b` with the profile journey fix
  `853d5aa` and the runbook fix `baac42a`. `./scripts/check` passed on the
  task branch (3,929 backend tests, 58 phone-size web journeys, 307 Jest
  tests). On the S9+ with the development login, the full Android run passed
  its first eleven flows, then stopped in `ai-controls.yaml`, whose Add note
  the longer profile left below the screen; with the flow scrolling to it, the
  run from that flow on passed (20 min). `main` and `origin/main` named
  `baac42a`, the task worktree and branch were removed, and `baac42a`
  deployed healthy (`/health` ready). The Saved Build
  `enloq-0.1.0-baac42a.apk` replaced the pinned message in place.
- 2026-10-26, Phase 1 live check: the routes matched the adopted MiMo, Muse
  Spark 1.3 and Decider V1.2 (`pplx-decider-v1.2-20261016`) rates. Remember
  this chat was On in the Maya chat, switched On on 2026-10-25 at 17:28 UTC
  while no agent session on the VPS was active; it was switched Off for each
  run (nothing was waiting to be learned) and On again afterwards. Friendly
  draft, Friendly bracketed and Professional draft sets were ready; the
  Professional bracketed set failed visibly (`provider`) when its
  decision-model labels call ended with an unknown outcome. A continuation
  from that set, keeping the original start, had Professional bracketed, both
  Idioms sets and the long draft ready. In total: 16 model requests, $0.0139
  counted (the unknown at its $0.01 reservation), 9.5 minutes from the first
  start, no sends, no bracketed text in any option, Undo restored every draft,
  and the chat's Automatic suggestions and Friendly Style were restored.
  Long-draft options ran 227–330 characters. Wording is Low quality
  ([inference](../inference.md#retained-acceptance-limits)): the Friendly
  bracketed set began each option with "not yet:".
- 2026-10-26, Phase 2 checks: `5ec45ea` with the live check fixes that
  followed. `./scripts/check` passed on the task branch (3,975 backend tests,
  59 phone-size web journeys, 313 Jest tests). Enloq Dev was rebuilt with
  `@react-native-community/datetimepicker` 9.1.0. On the S9+ (development
  login), the reminders, compose and ai-controls flows passed first; the full
  run passed its first 16 flows, then push failed because the worktree's
  build lacked the untracked Firebase config. Rebuilt with it, the run from
  push on passed (9 min).
- 2026-10-26, Phase 2 delivered: `main` and `origin/main` named `b642809`, the
  task worktree and branch were removed, and the schema 40 deploy was healthy;
  both account stores read version 40, and the one schema 39 schedule, a
  weekly exact reminder in the Maya chat that no check of this plan made,
  moved with revision 5. The Saved Build `enloq-0.1.0-c1aceff.apk` replaced
  the pinned message.
- 2026-10-26, Phase 2 live check, started 08:47:57 UTC in Pacific/Gambier,
  12 minutes before its midnight. An earlier start sent nothing: Send asked
  about the authored message's square brackets and Playwright declined, so
  the check's messages now use parentheses. The run sent the day's authored
  message and saved an exact Once reminder whose idea named 25 Oct and one
  without an idea, leaving that weekly schedule alone. Listing made no model
  call; at the day's end its three cached messages were kept; the set was
  prepared 60 seconds before the first alert, covered both reminders and
  carried the Kept Messages. The check then failed on reading those Kept
  Messages from the finished set, whose packet is dropped on completion, and
  removed its schedules. A continuation from the same start reused the sent
  message: saving kept the ended day at once, both reminders' sets were
  prepared early, the bar listed both once due, Maya's message brought a
  refresh 30 quiet seconds later while no client had the chat open (the
  automatic set was suppressed, and opening reused the refreshed set), and
  then the web bar's Dismiss could not be tapped: the long idea widened it
  past the phone's edge. `d860166` fixed that with a long-idea journey (the
  app's bar already fitted on the S9+) and deployed as `5ffb383`, healthy.
  The last steps then ran through a one-off script repeating the check's
  steps 6 and 7 on the same reminders: Dismiss sat inside the screen,
  reopening prepared the idea's set, a send from it completed the reminder
  and its Once schedule ended, and the weekly schedule stayed unchanged. In
  total: 14 model requests, $0.0024 counted, three authored sends, 36
  minutes from the start; the chat's Automatic suggestions and Friendly
  Style were restored, the draft was empty and Remember this chat, Off for
  every run, was On again. Wording is in
  [inference](../inference.md#retained-acceptance-limits).

## Open findings

- None.
