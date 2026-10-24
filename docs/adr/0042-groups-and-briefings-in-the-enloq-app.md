---
status: accepted
---

# Groups and Daily Briefings in the Enloq app

## Context

[ADR 38](0038-mobile-expo-client.md) Q6 first gave the Enloq app private chats
only, leaving the Groups tab with Daily Briefings, Settings and other web
features to later units. A group opened from a chat link read like a private
chat: no author names and no briefings. The service already served what
[ADR 34](0034-group-chats-and-daily-briefings.md)'s group design needs, so
bringing groups to the app changed the clients and added no model call,
budget or retention rule.

## Decision

**The app is the target platform (Q7).** New and changed features are
designed for the Enloq app first, and the web takes the same design unless
something works on only one platform, so both clients share one design. The
web does only what the app does
([ADR 44](0044-mobile-only-web-in-the-apps-design.md) Q2). This supersedes ADR
38 Q6's split into first-release, later and web-only pieces.

**Writing in groups (Q1).** The app reads and writes in groups: Send, Reply
with its [Reply Quote](0041-reply-quotes.md), photos, GIFs and stickers,
with Pending Sends as in private chats. Groups have no outgoing Rewrite,
tone controls or manual suggestions (ADR 25). The composer works only while the group's access is verified,
as on the web. Retained group work and the review and resume of a send paused
by lost group access are CU6's
([ADR 46](0046-the-apps-catch-up-units.md)), in both clients.

**Today's Briefing Slot (Q2).** The app shows the whole Briefing Slot. `↻ N
new` or `↻ Update` and the amber `Retry` act on a tap, paid from the Group
Briefing Budget exactly as on the web. `Budget` opens that budget in Settings
([ADR 45](0045-settings-in-the-enloq-app.md) Q8). `Waiting`, `Syncing` and the
check only report.

**The group sheet (Q3).** A group's header gets a ⋮ actions sheet holding
Group AI processing, with its notes for an unsupported group, lost or
unverified access and account AI Off, and Hide Bots. Stop ignoring this bot is
in the long-press menu of a bot's original that is not exempt yet; such posts
show once Hide Bots is Off or when a citation or quote brings one into view. A
new group starts with AI processing Off, so this switch starts its briefings
from the phone. The group's photo and name open its Group Profile
([ADR 47](0047-pairing-settings-menus-and-profiles.md) Q7).

**Citations in place (Q4).** Citations keep ADR 34's in-place reveal in both
clients. The day's briefing gives way to its cached originals around the cited
message, which is highlighted, and Return to briefing at the top of that day
brings the briefing back. Show day originals and ADR 41 Q12's quote reveal
work the same way. A separate citation screen was not adopted: it would have
needed a new view in each client.

**Settings (Q5).** The app has a Settings screen of its own, designed in
[ADR 45](0045-settings-in-the-enloq-app.md) and shared with the web.

**Q6:** planning scope; no lasting rule.

**Q8:** withdrawn when Q4 kept the in-place reveal.

**The catch-up (Q9).** Every other piece the web had and the app lacked became
a catch-up unit after the group units, CU1-CU6, designed in ADR 46: media
playback (ADR 38's ML1), message details, AI controls, Message Selection and
deletion, GIF and sticker sending, and group recovery. They build both
clients.

**What the catch-up covers (Q10).** Every gap between the clients, with ADR 46
Q9 placing the ones no unit named. Telegram sign-in and the trusted browsers
and devices are [ADR 43](0043-installable-android-builds-and-phone-setup.md)'s
(Manage accounts, and Settings → Privacy since ADR 47 Q6). Neither client has
Help (ADR 45 Q7).

## Units

The group units' names, which code comments cite:

- **AG1:** the pure group rules in `shared/` (day keys and band labels,
  covered, folded and uncovered messages, the streamed topic split and the
  slot's states); a closed fold's messages stay out of focused viewing until
  it opens; the fixture's authored group, AI Off and unbriefed, which a
  journey briefs through Update.
- **AG2:** the Groups list beside the Private one, with ADR 23's rows, pull
  to refresh and the offline copy; chat links to `chat:` and `channel:` peers
  open the group screen.
- **AG3:** group reading: author lines (the name or You, the signature and
  `(ad)`), Hide Bots, days under the pinned band, briefing cards, folds,
  opening at the earliest unseen ready message, and Reply Quotes in groups.
- **AG4:** citations in place (Q4), the cited message outlined.
- **AG5:** today's Briefing Slot and the group's ⋮ sheet (Q2, Q3).
- **AG6:** writing in groups (Q1).
- **AG7:** the groups journey.

## Consequences

- Group sound and push stay excluded.
- Everything else follows ADR 34 and ADR 41. Model, account, target, budget,
  privacy and session-owner boundaries are unchanged.
- The iPhone gets groups with its next build on the Mac.
