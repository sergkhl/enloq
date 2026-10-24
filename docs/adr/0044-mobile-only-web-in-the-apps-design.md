---
status: accepted
---

# A mobile-only web app in the Enloq app's design

## Context

[ADR 42](0042-groups-and-briefings-in-the-enloq-app.md) Q7 made the Enloq app
the target platform, with the web taking its design, and
[ADR 43](0043-installable-android-builds-and-phone-setup.md) Q13 dropped
desktop browser checks. The web then drew two panes on wide windows and its
own phone layout below, and differed from the app on most shared screens: it
reopened the remembered chat, kept the account picker at the foot of its list,
held the reading switch in a conversation menu and opened menus as popovers.
The web was rebuilt to the app's screens and limited to what the app does.

## Decision

**The app's screens (Q1).** The web is built to the Enloq app's design, on its
React and Vite code. It starts on the conversation list, whose header is the
account switcher with an account sheet. A conversation has the app's header
(Back, avatar, name and emotion, with the robot toggle for the Reading View,
[ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md) Q13). Menus
are bottom sheets. Not adopted: making the web's phone layout the only one
and aligning it area by area later, a rewrite from scratch, and serving the
Expo app in browsers through its web target, which would have required every
app unit to work in browsers.

**Only what the app does (Q2).** The web does what the Enloq app does. A
feature only the web had left it and returned in the design of the app's own
unit for it: ADR 42's group (AG) and catch-up (CU) units, ADR 43's sign-in and
trusted-client units (IB) and [ADR 45](0045-settings-in-the-enloq-app.md)'s
Settings. New units build their feature in both clients. This supersedes ADR
42 Q7's earlier rule that existing web features change only when a later
change touches them.

**One phone-width column (Q3).** Every window shows one column about 480 px
wide, centered on the page background, so a desktop shows what a large phone
shows. Sheets and dialogs open inside it.

**Enter adds a new line (Q4).** As in the app, Enter adds a new line and
Generate suggestions and Send act only from their buttons; ADR 22's Enter, Cmd+Enter and
Shift+Enter shortcuts are gone. Right-click and the menu key still open a
message's sheet, since an Android long-press reaches the web as the same
event.

**Phone browsers (Q5).** Phone browsers work with safe areas and the
on-screen keyboard. The web is not installable to the home screen; that would
bring web push to an iPhone without the Apple Developer Program and stays a
possible later proposal.

**Q6:** planning scope; no lasting rule.

**Order of work (Q7).** The rebuild followed ADR 42's AG units, ADR 43's IB
units and ADR 45's Settings units, which were app-only and ported; the CU
units after it built both clients.

**Phone-size journeys (Q8).** A Chromium suite at phone size (390×844, with
touch) against the authored fixture server mirrors the app's Maestro journeys
and runs in `./scripts/check`. It replaced the desktop Playwright specs, and
each new unit adds its journeys in both clients. Live checks against the VPS
service are a separate Chromium tool in `web/live/`, never run by
`./scripts/check`.

**The held menu/selection plan (Q9).** The
[ADR 31](0031-message-deletion-selection-and-group-ai-controls.md) unified
message menu and header selection is retired as superseded: its screens do not
survive the rebuild. ADR 46's CU3 and CU4 design AI controls, selection and
deletion for the phone.

**Pairing through Saved Messages (Q10).** Every client pairs the way ADR 43's
IB3 and IB4 design for the app: the web's connection screen offers Connect to
`enloq.globesoul.com`, the service posts a code to the Owner Account's Saved
Messages, and typing it trusts the browser. Trusted clients pair phones by QR
code, and anyone may Sign in with Telegram on the connection screen
([ADR 47](0047-pairing-settings-menus-and-profiles.md) Q1, Q11;
[ADR 48](0048-members-on-the-shared-service.md) Q11). The Mac instance keeps
its launcher.

**What only the app has (Q11).** The web gets neither the app's offline copy
(ADR 38 Q20) nor pull to refresh: an unreachable service shows the app's
unreachable state with nothing cached, and the browser reloads.

## Units

The rebuild's units, which code comments cite:

- **MW1:** the column, navigation and the list: the remembered account's list
  on opening, [ADR 36](0036-account-and-chat-hash-routes.md)'s routes, guards
  and history, and Back closing the conversation.
- **MW2:** reading in the app's design, private and group, the message sheet,
  and tappable links in both clients
  ([ADR 46](0046-the-apps-catch-up-units.md) Q9).
- **MW3:** composing: the app's composer with manual suggestions, Enter's new line
  (Q4), Pending Sends and Reply Quotes.
- **MW4:** connecting with the code (Q10), notifications and Settings in the
  app's design.
- **MW5:** removal of what the app does not do.

## Consequences

- Whoever can read the Owner Account's Saved Messages can trust a browser as
  well as a phone; the trusted list with Forget and the pairing notes remain
  the check.
- ADR 36's routes, guards and Copy chat link stay (the link lives in the
  profiles, ADR 47 Q8); its kept position under the phone list and the
  remembered chat per tab are gone.
- The web has no Help, no chimes beyond its one sound, no Copy message ID and
  no Conversation actions in private chats.
- Open: the service still keeps ADR 20's sound presets and custom sound
  (`sound_preset` and `GET`/`POST /api/notifications/sound`), which no client
  offers; the web plays Harp whatever preset is stored. Removing them is a
  later clean-up, not a defect.
- Model, account, budget, target, privacy and session-owner boundaries are
  unchanged.
