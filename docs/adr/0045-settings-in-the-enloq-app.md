---
status: accepted
---

# Settings in the Enloq app

## Context

The Enloq app had no Settings: its list header held the account switcher and,
on Android, a bell for this phone's notifications.
[ADR 42](0042-groups-and-briefings-in-the-enloq-app.md) Q5 gave the app
Settings of its own, and [ADR 44](0044-mobile-only-web-in-the-apps-design.md)
ports it to the web. Every setting the web offered is served under
`/api/settings`, so the work is in the clients only. The owner left the
screens to the agent's design ("decide a good UX on your own").

## Decision

**Q1, Q2:** order of work (these Settings units, then ADR 44's rebuild) and
the catch-up interviews, recorded as Q9-Q11 in
[ADR 46](0046-the-apps-catch-up-units.md); no lasting rule.

**Settings and this client's notifications (Q3).** Both phone platforms and
the web have Settings. On Android and the web, this phone's or browser's
notifications live inside it, under Settings → Notifications
([ADR 47](0047-pairing-settings-menus-and-profiles.md) Q6). Settings opens from
the last row of the account sheet, after Manage accounts and a divider, with a
gear icon ([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q19, Q21);
the list header keeps no gear. The chevron beside the account name shows even
with one account, and the sheet opens without accounts too, so Settings is
always reachable.

**The account sheet keeps accounts (Q4).** Trusted browsers and devices,
Disconnect and the build line live in Settings: the trusted list under
Privacy, Disconnect and the build line at its foot (ADR 47 Q6). The account
sheet keeps the accounts, Manage accounts and the Settings row. Server-wide
items sit with the server-wide settings.

**Submenus (Q5).** Replaced by ADR 47 Q6: Settings is four rows with icons,
Notifications, Budget, AI and Privacy. Processing Guidance's fields, up to
4,000 characters each, keep screens of their own as Guidance under AI.

**Everything the web had (Q6).** Both budgets with today's spending, the
Processing Character Threshold, the Concurrency Limit, the memory and
suggestions switches, and Guidance with its paused-role notice are in both
clients. The paused-role notice is the only way automatic rewriting resumes
after three format failures. The web's five chimes and custom sound are not
offered: the phone's push uses its own sound, and the web plays one.
AI settings are server-wide and only the owner changes them; a member sees
Notifications, their Budget read-only and Privacy, with Leave, and the owner's
Settings adds People ([ADR 48](0048-members-on-the-shared-service.md) Q13).

**No Help (Q7).** Neither client has Help. Each control explains itself in
one line, and the explanations that still hold are a guide in
[`docs/mobile.md`](../mobile.md). Not adopted: a Help screen written for the
app.

**Messages lead to Settings (Q8).** "Waiting for budget" and "Suggestions wait
for budget" open the Daily Budget under Settings → Budget. "Paused after
repeated format failures" opens Guidance under Settings → AI. The Briefing
Slot's `Budget` opens the Group Briefing Budget. Each opens its page with the
item scrolled into view and outlined for a moment.

## Units

The units' names, which code comments cite:

- **ST1:** Settings itself, and what moved into it from the account sheet
  (Q3, Q4).
- **ST2:** the two budget cards (meter, today's line, reset, reached warning,
  Change limit with its checks and the $0 note, Recent changes) and the
  processing controls, each explained in place and reread after each write.
- **ST3:** Guidance: the version, the paused-role notices with Reset guidance
  and Keep, each field with Edited or Default, Save, Reset to default, the
  default text, the count, the faithfulness warning and the unsaved-edit
  guard; the Settings row's Paused badge.
- **ST4:** the links into Settings (Q8).
- **ST5:** the Settings journey and the guide in `docs/mobile.md`.

## Consequences

- The bell is gone from the list header in both clients.
- Help is gone from both clients; explanations live beside each control and
  in the guide.
- Model, account, budget, target, privacy and session-owner boundaries are
  unchanged, and the service is not changed.
