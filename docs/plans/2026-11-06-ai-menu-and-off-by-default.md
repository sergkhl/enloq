# The AI Menu and private-chat AI Off by default

Last meaningful update: 2026-11-06
Status: active since the owner's go-ahead on 2026-11-06; Phase 1 is
delivered, Phases 2 and 3 are being built.

Deliver the adopted
[ADR 62](../adr/0062-the-ai-menu-and-private-chat-ai-off-by-default.md), with
its consequences in the ADRs it amends. Three phases, each deployed and
checked on its own: the AI Menu first, moving today's controls without
changing what runs; then the removal of the hidden `text_rewriting` switch,
which changes nothing live but reworks the fixtures the last phase builds on;
then the per-chat switches, Off by default, with the rollout of existing
choices. Until a phase is delivered, its code, journeys and runbooks keep the
earlier behavior. Delivery covers the service, web, Android Saved Builds and
finite live acceptance; iOS keeps its Mac build and enrollment gates in TODO.

## Phase 1: The AI Menu (Q1, Q4, Q11-Q19)

1. **What a menu reads.** The conversation snapshot gains one `ai` object:
   the chat's switches (Soften hostile messages, Automatic suggestions,
   Remember this chat) and Style, the server-wide Memory and Automatic
   suggestions switches, the account's AI and whether AI work can run there
   (Q14), so the header and the menu draw without the profile's three reads.
   Writes keep the existing per-chat routes. Regenerate the API schema for
   both clients.
2. **The robot and the sheet, app first and web to match (ADR 44).** The
   robot opens the AI Menu in private chats and groups. It is filled while AI
   work can run and an outline otherwise, with an accessible name saying
   which; the "AI off" tag goes. The first row is AI view, the global Reading
   View; private chats then list Q15's rows with their one-line explanations,
   each with a › to its page. Notes say why nothing runs: account AI Off, or
   a server-wide switch Off under the chat switch it overrides. Groups lose
   their ⋮: AI view, Group AI processing with its notes, Ask this group while
   it is On, and Hide bots; a retained group's menu holds AI view and Hide
   bots. Saved Messages keeps no robot.
3. **Pages (Q12, Q16, Q17).** Each control's page holds its explanation, its
   panels and, for the owner only, its Guidance fields as Q17 places them,
   each marked "for all chats" and opening today's field editor with Save,
   Reset, versions, the default text and the unsaved-edit guard. Emotion's
   page takes the emotion's details; Remember this chat's, Cares about and
   Traits with their editing and Retry; Style's, its choice. A group's Group
   AI processing page holds Group briefings.
4. **Profile and Settings (Q4, Q11, Q18).** The Correspondent Profile keeps
   the person, Reminders and Copy chat link. Settings → AI loses Guidance and
   gains the paused-role notice with Keep and Reset; "Paused after repeated
   format failures" opens it there (ADR 45 Q8). `shared/guidance.ts` maps
   each field to its control instead of to Settings' groups.
5. **Checks.** Jest for the sheet, its notes, the pages, the robot's states
   and the trimmed profile. The phone-size web journeys `ai-controls`,
   `reading`, `calming`, `suggestions`, `settings` and `ask` reach the
   switches, Guidance, Ask this group and Hide bots through the AI Menu, and
   so do the Maestro flows `ai-controls`, `read`, `groups`, `settings`,
   `suggestions`, `details`, `media`, `deletion` and `carousel`. Full
   `./scripts/check`, then the Android journeys on the S9+ with Maya or the
   development login only.
6. **Delivery.** AGENTS.md's order: commit, merge, push, remove the task
   checkout and deploy clean main; verify health, move the `web/live` checks
   that tap the profile's switches or "AI view" (`memory`,
   `suggestion-controls`, `calming`) to the AI Menu, run the Phase 1 live
   check, publish the Android Saved Build, update the mobile and operations
   runbooks' header, profile, group sheet and Guidance passages, retake the
   README screenshots only if the header they show changed (ADR 57), and
   record the evidence here.

## Phase 2: No hidden text rewriting (Q21)

7. **Remove the switch.** Drop `text_rewriting` from the registry, the
   store, block admission and rewriting (`storage/accounts.py`,
   `storage/store.py`, `application/blocks.py` and `application/rewriting.py`
   under `backend/src/enloq`), so a text block is admitted for rewriting only
   to be softened. Nothing changes live, where it is Off.
8. **The fixtures.** `scripts/test_server.py` stops turning it on and
   `scripts/calming_fixture.py` loses its route. Journeys that read rewritten
   text use authored media blocks, or hostile text that softens. The 16
   backend test files that use the switch, `conftest.py`'s fixture included,
   move to media or softened blocks, or lose the tests that only covered the
   switch.
9. **Checks and delivery.** Full `./scripts/check` and the Android journeys;
   deploy and verify health, with no live check since nothing live changes;
   the operations and inference runbooks drop the switch.

## Phase 3: Per-chat switches, Off by default (Q2, Q3, Q5-Q10, Q14, Q20)

10. **Storage and defaults.** A chat's own Emotion and Voice and images
    choices are kept as the other three are, a stored row being the chat's
    own choice, with routes like theirs and fields in the `ai` object. Every
    default becomes Off. Remember this chat keeps its distinction between Off
    by default and Off by choice (ADR 55 Q20, ADR 56 Q11). No migration
    changes an existing choice; the rollout does (unit 19).
11. **Gates.** Emotion gates the decision's emotion question and Reaction
    Assessment. Automatic suggestions and Soften hostile messages keep their
    gates. Voice and images gates media blocks' admission, the automatic
    transcription of incoming and the owner's own voice notes and Video
    Circles, and image meanings; while it is Off a softened rewrite gets no
    images or transcripts (Q20). Show transcript, Retry, manual Suggestions
    and Rewrite earlier messages pass whatever the switches say (Q3).
12. **The decision request (Q5).** Ask only the questions the chat's On
    switches use, and send nothing while Emotion, Automatic suggestions and
    Soften hostile messages are all Off. The answer check accepts that subset;
    with Emotion Off, usefulness alone triggers automatic suggestions, and a
    reaction is assessed only while Emotion is On.
13. **Memory reads the chat (Q6).** While Remember this chat is On, memory
    admits the chat's new messages itself (text, captions and the transcripts
    and image meanings that exist) instead of only what other stages recorded.
    The one-time start from the cache and the never-learned rule stay.
14. **Reminders (Q7).** Schedules alert whatever the Automatic suggestions
    switches say; sets are prepared and refreshed only while both are On. The
    clients drop "Paused: Automatic suggestions is Off".
15. **Switching On and Off (Q8).** A switch turned On queues its work for the
    chat's latest incoming block under seven days old: the decision for its
    emotion, softening if it is hostile, its media rewrite, or automatic
    suggestions if it is unanswered and qualifies. Off keeps what was made,
    hides automatic sets and clears the emoji.
16. **Chats that are Off (Q14).** A block whose chat calls for no work
    settles as its originals after its 30 quiet seconds, with no stage, so
    unseen readiness and notifications follow and the robot shows its outline.
17. **Clients.** Emotion and Voice and images join the sheet with Q15's lines
    and their pages. The fixture server sets its authored chats' switches On;
    journeys that check Off turn them Off for themselves.
18. **Checks.** Backend: each switch's default and gates, members' defaults,
    the decision's subsets and their validation, memory's intake and the
    never-learned rule, reminders that alert without a set, switching On,
    readiness in chats that are Off, and no transcription or image reading
    with Voice and images Off. Jest, web journeys and Maestro flows for the
    new rows and the Off states. Full `./scripts/check` and the Android
    journeys.
19. **Delivery and rollout (Q9, Q10).** AGENTS.md's order. Right after the
    deploy, through the service's API: turn Off every own choice of Soften
    hostile messages, Automatic suggestions and Remember this chat in the
    Owner Account's private chats other than the Maya chat, found by a light
    read of its store (two chats today); and set Automatic suggestions,
    Emotion, Soften hostile messages and Voice and images On on both sides of
    the Maya chat, leaving Remember this chat as it is. Record counts, never
    names or IDs. Then the Phase 3 live check, the Android Saved Build, and
    the operations, inference and mobile runbooks, the live checks'
    preconditions included.

## Acceptance bounds

Authored content between the Owner Account and Maya only, in the VPS browser
first ([operations](../operations.md#live-checks-in-a-vps-browser)). Verify
the adopted routes and rates before each run; read and record the Maya chat's
switches, Style, the Reading View and the draft, and restore them afterwards,
even when a run fails; add the actual requests to TODO's allowance.

- **Phase 1:** the AI Menu in the Maya chat from both sides and in the test
  group from Maya's side. AI view switched and restored, every page opened,
  one switch flipped and restored, Guidance fields opened without saving. No
  sends, no model requests, 15 minutes.
- **Phase 2:** none beyond the service's health.
- **Phase 3:** in the Owner Account's side of the Maya chat, with every
  switch Off, Maya sends a plain text and then a captioned photo, each its own
  block: no model request for them, combined originals with no status, an
  unseen dot after quiet and original previews. Emotion turned On then
  decides the latest block from its caption alone, asking emotion only, and
  Voice and images turned On rewrites it. At most 20 model requests (the
  rollout's switches on both sides of the Maya chat included), three authored
  sends, 30 minutes and $0.05 counted.

Format, delivery and target failures are material; weak wording is Low
quality in [inference](../inference.md#retained-acceptance-limits).

## Open questions

- None. The phase order is this plan's proposal.

## Validation Log

- 2026-11-06: written from ADR 62 as adopted in `1e3be1f`. No code changed,
  no model requests, no sends.
- 2026-11-06, Phase 1 built: the snapshot's `ai` object (`ChatAi`), the AI
  Menu and its pages in both clients, the trimmed profiles, Settings → AI's
  paused-role notice and `shared/aiMenu.ts`. Until Phase 3 gives Emotion and
  Voice and images their switches, their rows have pages and no switch, so
  Guidance's Emotion extraction and Incoming rewriting fields and the
  emotion's details have their homes from Phase 1 on; a private chat's robot
  is filled whenever its account's AI is On, since emotion and media
  rewriting still run in every recent chat. A screen over the web's
  conversation now makes it inert, so a field editor opened from a page
  leaves one Back in reach. `scripts/reminder_fixture.py` counts only
  reminder-set requests, which settles TODO's reminders-journey count.
  Checks in the task checkout: `./scripts/check` passed (ruff, backend
  4,039, the 66 web journeys, the app's typecheck, Prettier and 361 Jest
  tests), and the Android journeys passed on the S9+ in 38 minutes with
  Enloq Dev against the fixture, the AI Menu steps included.
- 2026-11-06, Phase 1 delivered: `77952b0` deployed from clean main as
  `enloq:77952b084787`, healthy. `web/live/ai-menu.spec.ts` passed twice in
  the VPS browser (08:50 and 08:53 UTC, 13 and 33 seconds): the AI Menu from
  both sides of the Maya chat and from Maya's side of the test group, AI view
  switched and put back, every page opened, Soften hostile messages flipped
  and put back in the owner's menu, and in the second run all 13 Guidance
  editors opened unsaved (the first counted them before the settings were
  read, which the check now waits for), the guidance version unchanged. The
  Reading View (Originals), both sides' switches, Style and drafts read the
  same afterwards. No sends and no model requests; the browser's routes were
  the AI Menu's own and the Maya chat's and test group's. The README's header
  screenshot needs no retake: its filled robot is what the header still
  shows.

## Open findings

- None.
