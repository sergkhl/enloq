---
status: accepted
---

# Installable Android builds and phone setup

## Context

The owner tests on a second Android phone, a Galaxy S25 Ultra on Android 16
with Google Play, without the WireGuard tunnel, and adds real accounts there.
Enloq Dev, the debug build, loads its JavaScript from Metro on the development
machine through adb's port reversal, so a phone without the tunnel or a cable
cannot run it, and its APK carries no bundle. A release-type build embeds the
bundle and needs only the server. The phone needs an APK it can install, a
simple way to pair with the release server, and account sign-in in the app.

## Decision

**The S25 runs Enloq (Q1).** The S25 runs the release-type Enloq, installed
from Saved Messages, with no tunnel. Enloq Dev and its journeys stay on the
S9+. Not adopted: a standalone Enloq Dev and a third Enloq Preview app.

**Disconnect (Q2).** The app can leave its server: the phone revokes its own
credential there, with Forget's effects, drops its FCM registration and
offline copy, and returns to the pairing screen. When the server can't be
reached, the phone forgets locally and its entry stays in the trusted list
until forgotten. It holds one server at a time, in every build. Disconnect
sits at the foot of Settings
([ADR 45](0045-settings-in-the-enloq-app.md) Q4), and a browser has its own.

**Telegram sign-in in the app (Q3).** The Enloq app signs in Telegram accounts
through the service's existing login flow (Q10), amending
[ADR 38](0038-mobile-expo-client.md) Q6, which kept account login web-only.

**Saved Messages (Q4).** Installable APKs, **Saved Builds**, go to the Saved
Messages of the **Owner Account**, which `ENLOQ_OWNER_ACCOUNT` names by
Telegram user ID. The running service, which owns that account's Telegram
session, posts on a host command and posts to no other peer through this
path. No second Telegram client, hostname, secret or login is added, and the
file stays with the owner's other Telegram messages. Without the setting, or
while that account is signed out, Saved Builds and pairing codes are refused
with that reason. Considered and not adopted: a download for trusted clients,
a password-protected file site at the edge, self-hosted S3, GitHub Releases,
Firebase App Distribution and a Telegram bot, whose uploads are capped at
50 MB.

**Building them (Q5).** `mobile/scripts/publish-android` builds a release-type
APK on the VPS from a clean committed `HEAD`, refusing uncommitted changes as
`./scripts/deploy` does, at low priority and for arm64-v8a only (Q6), then
hands it to the service. The agent runs it after deploying any change to
`mobile/`, `shared/` or the API schema, and the owner can run it at any time.
Release APKs are signed with Enloq's own private release key, kept on the VPS
outside both checkouts and the service container and never committed; the
app's config writes the signing into every regenerated Android project, and a
release build without the key fails rather than falling back to React
Native's public debug key, which Enloq Dev keeps. So one APK installs over an
existing Enloq and keeps its pairing, and with one fixed Android version code
any APK installs over any other. The app shows `Enloq <version> · <commit> ·
<build date>` in Settings (ADR 45 Q4).

**The S25 (Q6).** Android 16 with Google Play: arm64-v8a only, with FCM push.

**Q7:** planning scope; no lasting rule.

**Pairing with a code in Telegram (Q8).** Every build carries the release
server's address (`https://enloq.globesoul.com`, set at build time), and the
pairing screen offers **Connect to enloq.globesoul.com** beside scanning or
pasting a link; the web's connection screen offers it too
([ADR 44](0044-mobile-only-web-in-the-apps-design.md) Q10).

- **The code.** Connecting asks the server to post a six-digit code to the
  Owner Account's Saved Messages: the digits alone, in monospace
  ([ADR 47](0047-pairing-settings-menus-and-profiles.md) Q2). Typing it on the
  phone or in the browser completes the pairing, and its last digit submits it
  (ADR 47 Q3).
- **Limits.** A code lasts two minutes, one attempt per client is live at a
  time, five wrong codes end an attempt, at most five code messages go out an
  hour, and wrong codes count toward the service's refusal limit
  ([ADR 40](0040-shared-vps-deployment-and-development.md) Q6). Codes never
  reach logs.
- **The record.** A "was paired" note in Saved Messages records each of the
  owner's clients that pairs, by any route.
- **Signed out.** While the Owner Account is signed out, Connect turns into
  Sign in with Telegram (ADR 47 Q11, Q13).

Whoever can read the Owner Account's Saved Messages can pair a client; the
trusted list with Forget and the notes are the check. The other ways in are
ADR 47's QR pairing from a trusted client and Sign in with Telegram.

**The local test server (Q9).** Enloq Dev's pairing screen offers **Use the
local test server**: one tap pairs with `http://127.0.0.1:8776`, which approves
the claim itself through a fixture-only route. It holds only authored data and
is reachable only through adb's port reversal while it runs; release builds
don't show the button.

**Accounts in the app (Q10).** The Enloq app has every Manage accounts action:
Add account (a QR code for Telegram on another device to scan, or phone
number, code and 2FA password), Sign in again, Reconnect, Log out and Remove
local data, with the web's guards: unresolved sends block removal, and
spending records stay. A login belongs to the client that started it and is
cancelled when that client is forgotten or disconnects. A new account starts
with AI processing on ([ADR 14](0014-account-ai-processing-and-reading-view.md))
and spends its member's Daily Budget. Each member manages their own accounts
([ADR 48](0048-members-on-the-shared-service.md)).

**One Saved Build (Q11).** Replaced by
[ADR 50](0050-icon-tabs-saved-messages-and-one-saved-build.md) Q5: one pinned
Saved Build message, whose file and caption each publish replaces in place.
The caption gives the version, commit, build time and the commit subjects
since the previous build, at most ten with a count of the rest. The service
deletes nothing in Saved Messages.

**Developer verification (Q12).** Decided later. If a phone refuses an APK,
the owner turns on Android's Advanced Flow once; adb installs stay exempt.
Registering under a limited-distribution account with the release key (Q5)
is not adopted now.

**Checks (Q13).** Desktop browser checks are not required. Work is checked by
`./scripts/check`, whose phone-size web journeys replaced the desktop
Playwright specs (ADR 44 Q8), and on the Android phone (Jest, typecheck,
Maestro journeys).

**Designed here (Q14).** Sign-in, pairing and the trusted browsers and devices
were designed for the app's account sheet here; the trusted list,
Disconnect and the build line have since moved into Settings (ADR 45 Q4,
ADR 47 Q6).

**The iPhone (Q15).** The iOS simulator runs its journeys on the Mac when the
iPhone build is made there.

## Host reads of Saved Messages

Beside its posts, the host may read three things back from the Owner Account's
Saved Messages. Each is a host command run inside the VPS container,
`python -m enloq.saved`, that drives the running service over the container's
loopback with the bootstrap control credential. The routes take no peer and
log nothing, and no other text comes back.

- **The newest image** (`--latest-image`), for a screenshot the owner leaves
  for a coding agent. The service saves the newest photo or still image file
  among the last 20 Saved Messages, at most 20 MB, as the only file in the
  data folder's `inbox/`, replacing the last, and answers with its date and
  caption. The agent deletes the copy once read.
- **Pairing codes** (`--pairing-code`), so a coding agent's browser on the VPS
  trusts itself. It prints the newest code the service posted, the six digits
  alone, and only while the code works. Any agent on the server can therefore
  trust a browser whenever it likes; Saved Messages still records each pairing
  and Forget ends it. Live checks run first in such a browser, and on a
  physical Android phone only when a check needs one
  ([operations](../operations.md#live-checks-in-a-vps-browser)).
- **The owner's notes** (`--notes N`), when the owner points at Saved Messages
  for tasks ([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q1). It
  returns the owner's last N own notes, oldest first, with their text and
  dates, and saves their images in `inbox/`. It skips the service's own posts
  (pairing codes, "was paired" notes and the Saved Build). The agent deletes
  the copies once read and keeps note contents out of the repository.

## Units

The units' names, which code comments cite:

- **IB1:** the host-only route that posts a file or note to the Owner
  Account's own Saved Messages (Q4).
- **IB2:** Saved Builds and the build line (Q5).
- **IB3:** Disconnect and the pairing screen, with the local test server (Q2,
  Q9).
- **IB4:** pairing with a code in Telegram (Q8).
- **IB5:** the trusted browsers and devices in the app, with Forget.
- **IB6:** the accounts in the app (Q3, Q10).

## Consequences

- The service has a host-only path that posts files and notes to the Owner
  Account's own Saved Messages and reads back its newest image, the pairing
  codes and the owner's own notes; nothing else may reach it.
- Reading the Owner Account's Saved Messages is enough to pair a client.
  While that account is signed out, neither Saved Builds nor codes work, and
  Sign in with Telegram or a trusted client's QR pairs instead.
- Android's developer verification has applied to installs from participating
  stores in Brazil, Indonesia, Singapore and Thailand since 2026-09-30 and
  reaches all apps on certified devices in 2027. Unregistered apps then install
  from a file only with Android's Advanced Flow (a one-time one-day wait, then
  for seven days or indefinitely) or through adb, which stays exempt.
  Registering Enloq with its release key is left for later (Q12).
- Only the release key can sign an update to an installed Enloq. Losing it
  means every phone uninstalls Enloq and pairs again, so the owner keeps a
  backup.
- Open: a trusted-device entry from the S9+'s earlier app, uninstalled when
  the app was renamed, is still active on the VPS service. Forgetting it in
  Settings → Privacy is the owner's.
- Model, account, budget, target, privacy and session-owner boundaries are
  unchanged.
