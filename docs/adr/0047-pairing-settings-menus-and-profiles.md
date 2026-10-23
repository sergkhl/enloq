---
status: accepted
---

# Pairing without SSH, Settings menus and profiles

## Context

Clients paired with the VPS over SSH (`./scripts/pair`) or with a code posted
to the Owner Account's Saved Messages
([ADR 43](0043-installable-android-builds-and-phone-setup.md) Q8). Settings
was one long screen ([ADR 45](0045-settings-in-the-enloq-app.md) Q5), the
header held Copy chat link, and groups had no profile. The owner asked to
remove SSH pairing, pair phones by QR code from another client, sign in with
Telegram from the app, submit codes on their last digit, ask for
notifications, split Settings into submenus, and give groups a profile.

## Decision

**No SSH pairing (Q1).** `./scripts/pair` and its module are gone, and a
service with a public origin refuses pairings started with the host's control
credential, so nothing pairs the VPS from a terminal. The control credential
stays for the host-only Saved Messages path, Saved Builds and ADR 43's host
reads; one of those reads returns pairing codes, so a coding agent's browser
on the VPS connects with the ordinary code. The Mac instance keeps its
launcher ([ADR 11](0011-durable-browser-trust.md)) while it exists, and the
authored fixture keeps its own link pairings for Enloq Dev's local test server
and the journeys. This ends [ADR 37](0037-vps-hosting-and-remote-client-trust.md)
Q15's SSH approval authority.

**QR pairing between clients (Q1).** **Pair a phone**, in Settings → Privacy
on a trusted phone or browser, shows a QR code with its link and Copy, for a
phone whose camera is off. Enloq on the new phone scans it (or takes the
pasted link) and shows a six-digit code; the approving screen shows the same
code and the phone's name, and Trust there finishes the pairing within the
attempt's two minutes. A trusted phone approving another, which
[ADR 38](0038-mobile-expo-client.md) Q24 left waiting, is adopted. The new
phone joins the approving client's member
([ADR 48](0048-members-on-the-shared-service.md) Q12).

**Only the code (Q2).** The code message is the six digits alone, in
monospace, which Telegram's apps should copy with a tap. Every pairing of
one of the owner's clients, by code, QR or sign-in, posts a "was paired" note
to the Owner Account's Saved Messages, so it records every client of the
owner's that joins. A member's pairings post nothing (ADR 48 Q12).

**Codes submit themselves (Q3).** The last digit of a pairing code, typed or
pasted, submits it in both clients. So does the last digit of Telegram's login
code in Add account, Sign in again and Sign in with Telegram, whenever
Telegram gives the code's length, which the service passes on. Pair and Sign
in stay for retrying, and the two-step password never submits itself. Each
code keeps one field with the phone's code autofill.

**Asking for notifications on phones (Q4).** Each phone asks once per
pairing: on the first open after pairing, and on the first open of a new
build on a phone already paired with notifications off. Android 13 and later
shows its own dialog straight away. Android 12 and older (the S9+) has none,
so the app asks in a one-question sheet, keeping Google uncontacted until the
owner says yes (ADR 38 Q42). Declining leaves the Settings switch as the way
back, and iOS has nothing to ask until it has push. Not adopted: asking on
every open while notifications are off.

**Asking in browsers (Q5).** The web asks once per pairing too, in a small
sheet whose Turn on opens the browser's prompt, since browsers prompt only
after a click.

**Settings menus (Q6).** Settings is four rows with icons, never sparkles:
**Notifications** (a bell, on Android and the web), **Budget** (a wallet),
**AI** (a chip) and **Privacy** (a lock), with Disconnect and the build line
at the bottom.

- **Notifications** holds this phone's or browser's switch, sound and test.
- **Budget** holds the two budgets with today's spending.
- **AI** holds the Processing Character Threshold, the Concurrency Limit, the
  memory and suggestions switches and Guidance.
- **Privacy** holds Pair a phone and the trusted browsers and devices with
  Forget.

The budget and pause messages open their submenu (ADR 45 Q8). A member's
Settings has no AI row and adds Leave; the owner's adds People (ADR 48 Q13).
This replaces ADR 45 Q5's one screen.

**The header and the Group Profile (Q7).** In a conversation's header the
emotion sits right after the name, which is cut short before the emoji is,
and a tap on either opens the profile. Tapping a group's photo or name opens a
**Group Profile**: the photo and title, the @username when the group is
public, the member count and the description, read from Telegram on each
opening and kept nowhere, with no member list. Offline it shows the cached
photo and title. The group's ⋮ sheet keeps Group AI processing and Hide Bots
([ADR 42](0042-groups-and-briefings-in-the-enloq-app.md) Q3). A group
member's avatar or name opens that member's profile
([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q14).

**Copy chat link in the profiles (Q8).** Copy chat link is in neither header;
it lives in the profiles: the Correspondent Profile's row
([ADR 46](0046-the-apps-catch-up-units.md) Q11) and the Group Profile's. This
amends [ADR 44](0044-mobile-only-web-in-the-apps-design.md) Q1's header.

**Q9:** implementation scope; no lasting rule.

**Only Enloq claims a QR code (Q10).** A browser that opens a Pair a phone
link is told to scan it in Enloq; browsers pair on their own connection
screen. Not adopted: a browser claiming the link with a code of its own.

**Signing in with Telegram (Q11).** The connection screen offers **Sign in
with Telegram**: the phone number, the login code Telegram sends and the
two-step password, through the service's existing login. Both clients offer
it, and a fresh install pairs its first client this way. The account Telegram
verifies decides the outcome (ADR 48 Q11, Q12):

- **The Owner Account** pairs as the owner, and signs back in on the service
  if it was signed out, so codes and Saved Builds work again.
- **A member's home account** proves who they are: the existing session stays
  and the new login is signed out at once.
- **Another account a member holds** is refused.
- **An unknown account** reads the Member Agreement and waits as a Join
  Request.

At most five sign-ins start in an hour for each phone number and each client
address, under thirty an hour for the server, and wrong codes count toward the
refusal limit. Not adopted: the host's Telegram login over SSH.

**AI is the name (Q12).** The submenu formerly called Processing is **AI**,
matching the AI view, the AI off tag and each account's AI switch, and
Processing Guidance's row is **Guidance**.

**When the Owner Account is signed out (Q13).** When Connect can't post a code
because the Owner Account is signed out, or isn't on the server yet, the
service says so and the pairing screen turns Connect into Sign in with
Telegram. Q13's refusal of sign-in while that account is signed in ended with
ADR 48 Q11.

**Other people (Q14).** Replaced by ADR 48: other people use the service as
members.

**The Owner Account stays configured (Q15).** `ENLOQ_OWNER_ACCOUNT` stays. It
is set once over SSH with the credentials a fresh install needs anyway, and it
survives a data wipe. Without it nobody could approve a Join Request, so a
server without an Owner Account takes no sign-in. Not adopted: choosing the
account in Manage accounts.

## Consequences

- Nothing pairs the VPS over SSH. With no trusted client left, Sign in with
  Telegram on the connection screen is the way back in; without the Owner
  Account setting, nothing pairs at all.
- A trusted phone can mint a lasting credential for another phone. It can
  already read every account of its member, and Forget stays immediate.
- Anyone can make the server ask Telegram for login codes, within the hourly
  and refusal limits.
- Opening a Group Profile makes one Telegram read for that account.
- Sign in with Telegram is proven against the fixture's synthetic login. A QR
  pairing between the owner's devices, whether a tap copies the code in
  Telegram and the S25's own notification dialog await the owner.
- Model, budget, target, privacy and session-owner boundaries are otherwise
  unchanged.
