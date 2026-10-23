---
status: accepted
---

# The Enloq app, a mobile Expo client

## Context

With the service on a VPS ([ADR 37](0037-vps-hosting-and-remote-client-trust.md)),
the owner wanted a phone client for iPhone and Android beside the web app,
self-hosted where possible. The app has since become the target platform:
features are designed for it first and the web follows its design
([ADR 42](0042-groups-and-briefings-in-the-enloq-app.md) Q7,
[ADR 44](0044-mobile-only-web-in-the-apps-design.md)). The owner has not
joined the Apple Developer Program, so iPhone builds are free development
builds without push. Android push uses Firebase Cloud Messaging, the owner's
chosen exception to self-hosting. The decisions come from one interview whose
numbering this ADR shares with ADR 37, which holds Q2, Q7, Q14-Q19 and Q21.

## Decision

### The app

**Platforms and name (Q1, Q28).** One Expo codebase for iPhone and Android,
built locally for both. The app is **Enloq** (縁 *en*, "human connection",
and Latin *loqui*: a copilot for relationships and conversation context), with
the iOS bundle and Android application ID `com.globesoul.enloq` and the link
scheme `enloq`. Android's development build is **Enloq Dev**
(`com.globesoul.enloq.dev`), installed beside the release app with its own
pairing. The web app shows the same name, and the service answers at
`enloq.globesoul.com`.

**Scope (Q6).** The app has the account switcher and Manage accounts, the
seven-day Private and Groups lists and Saved Messages, reading in both views,
the composer, pending sends, photos viewed full screen, shared and attached,
notification taps that open the exact [ADR 36](0036-account-and-chat-hash-routes.md)
chat, and Copy chat link copying the canonical web URL. Since ADR 42 Q7 and
Q9 it also has everything else the web had, and the web does only what the
app does (ADR 44 Q2). Universal links are not adopted.

**Not adopted (Q8, Q12).** No app lock, and no phone-browser-specific pairing
work.

**Code layout (Q9, Q32).** A `mobile/` Expo app (strict TypeScript, Expo
Router, Expo SDK 57) whose API types come from `config/api-schema.json`
through the schema script. The app (`enloq-mobile`) and the web (`enloq-web`)
are separate npm packages, because Expo pins React and TypeScript versions
that differ from the web's; pure, DOM-free modules live in a `shared/` source
folder that Vite and Metro both import by path.

**Builds (Q10, Q26, Q47, Q49, Q52).** Builds are local: native projects are
generated on demand and never committed, with no EAS build, Expo account or
over-the-air update, and telemetry off. iOS builds run on the Mac, which
Xcode needs. Android builds run on the Mac and on the VPS, and the VPS makes
both the release app and Enloq Dev; Saved Builds come from the VPS
([ADR 43](0043-installable-android-builds-and-phone-setup.md)). Release
builds are signed with Enloq's private release key, kept on the VPS (ADR 43
Q5), so a release build installs over another and keeps its pairing; Enloq
Dev keeps React Native's shared debug key. iPhone builds are
signed by the owner's free Personal Team (Q26): each install stops launching
seven days after signing and the phone needs Developer Mode. The simulator
and active work use a development build that loads JavaScript from Metro; the
owner's everyday iPhone install is a Release-configuration build with the
JavaScript embedded, reinstalled from the Mac at least weekly by
`mobile/scripts/install-iphone`.

**Toolchains (Q29, Q30, Q33, Q50).** The Mac has room for Xcode with the iOS
simulator runtime and CocoaPods, and for the Android SDK. The agent downloads
simulator runtimes, emulator images and the builds' npm, CocoaPods and Gradle
dependencies from their official sources, and accepts the Android SDK license.
On the VPS, native tools live in the box account's home without root: Java 17
from Eclipse Temurin, the Android SDK and NDK at the Mac's versions, and
Maestro. VPS builds run at low priority with Gradle capped at four workers.

**Phones (Q31, Q36, Q43).** The owner's Android development phone is a Galaxy
S9+ on Android 10, where Enloq Dev and its journeys run. The agent installs
newer builds over the owner's release Enloq with
`mobile/scripts/install-android`, keeping its pairing, and never reads that
app's screen. The physical iPhone is not part of the checks.

**Checks (Q11, Q54).** Jest with React Native Testing Library against authored
fixtures; Maestro journeys against the authored fixture server
(`scripts/test_server.py`, port 8776) on the iOS simulator on the Mac and on
the Android phone; backend tests for trust, origin checks and push; and
`./scripts/check`, which runs the app's typecheck and Jest. Enloq Dev's whole
loop (builds, Metro, the fixture server and the journeys) also runs on the VPS
against the phone over WireGuard. No real correspondence enters fixtures.
Physical-phone use, ordinary use and adoption are the owner's.

**Reaching the phone (Q51, Q53, Q55, Q56).** The VPS reaches the phone through
the VPS's wg-easy WireGuard server. The phone and the VPS host each have their
own client carrying only VPN traffic: the tunnel subnet as Allowed IPs, a
25-second keepalive and no DNS servers, so the phone keeps its Wi-Fi's
resolver; the host's `wg-quick` service keeps the server's own traffic out of
the tunnel. The owner adds the phone's client from its QR code. Client keys
are never printed or committed; one is copied only into the host's root-only
WireGuard configuration, and the phone's tunnel is edited without focusing its
masked key fields. The VPS uses adb on the phone's tunnel address and gives
the phone Metro and the fixture server through adb's port reversal. On the
rooted phone, `mobile/scripts/phone-adb boot` keeps network adb on port 5555
across restarts and WireGuard out of Doze, and a Magisk boot script starts the
tunnel. The phone's endpoint is the VPS's IP address rather than its hostname,
since a tunnel started early in the boot cannot resolve names; Android's
Always-on VPN is not used. The phone approves the VPS's adb key once. The
[mobile runbook](../mobile.md) names the clients and addresses.

**Where the work happens (Q25).** Work that touches the app may be edited and
tested in the Mac checkout, pushed to `origin/main`, and then checked and
deployed from the VPS checkout ([ADR 40](0040-shared-vps-deployment-and-development.md));
until the production declaration those pushes and deploys need no further
confirmation.

### Trust

**Pairing and credentials (Q3, Q24).** The **Mobile Client** is the Enloq app;
a **Trusted Device** is a phone paired with the service. The app claims a
pairing without cookies in one of these ways: by scanning or pasting the QR
code that Pair a phone shows on a trusted phone or browser, after which both
show the same six-digit code and Trust on the approving client finishes it
within two minutes ([ADR 47](0047-pairing-settings-menus-and-profiles.md) Q1,
Q10); by Connect, typing the code the service posts to the Owner Account's
Saved Messages (ADR 43 Q8); or by Sign in with Telegram (ADR 47 Q11,
[ADR 48](0048-members-on-the-shared-service.md) Q11). Enloq Dev can also pair
with the local test server (ADR 43 Q9). The app then holds a fresh random
256-bit device credential in the Keychain or Keystore, sent as a bearer
header, with no app-imposed expiry. A trusted client opens only its member's
accounts (ADR 48). Settings → Privacy lists trusted browsers and devices
together; Forget revokes one at once, closing its event stream, retiring its
push destination and hiding correspondence in the app. Disconnect lets the
phone leave its server (ADR 43 Q2).

**Other people (Q23).** Members, each with their own accounts, trusted
clients, budgets and notifications, join after signing in with Telegram and
the owner's approval (ADR 48). The app stays one Android build that the owner
forwards by hand; iPhone friends wait for the Apple Developer Program.

### Push

**Phone destinations (Q5).** Each Trusted Device is its own push destination
beside the browser destination of
[ADR 20](0020-minimal-reading-and-ready-notifications.md), with its own on or
off, sound and test in Settings → Notifications. A push stays quiet while any
of the member's trusted clients reads that chat. There is no reaction-only
push and no app-icon badge count
([ADR 23](0023-local-unseen-messages-and-conversation-previews.md)). The
payload stays opaque (event ID, expiry and a silent flag): the phone fetches
sender and preview from the service and falls back to "New messages are
ready" when it cannot.

**iPhone (Q4, Q27).** The design is direct APNs from the service with a
privately stored key, without an Expo push relay, plus a Notification Service
Extension that fetches the details. Both wait for Apple Developer Program
enrollment, since free signing cannot receive pushes; until then new messages
show when the app opens, and iPhone builds leave out the push entitlement.

**Android through Firebase Cloud Messaging (Q37-Q42, Q46, Q48).**

- **Transport (Q37).** The app registers with FCM itself, and the service
  sends FCM HTTP v1 data-only messages at high priority, each living until its
  event expires. The phone needs Google Play but no second app. Google sees
  the opaque event, its timing and the phone's FCM token, never a sender,
  preview or chat.
- **Library (Q46).** `expo-notifications` handles registration. A push that
  arrives while the app is closed runs a background JavaScript task that reads
  the credential from the secure store, fetches sender and preview and shows
  the notification, with the generic fallback.
- **Project (Q38, Q39, Q48).** One Firebase project, Enloq
  (`enloq-globesoul`), in the owner's own Google account, on the no-cost plan
  without billing, with Analytics and Gemini off, the Android apps
  `com.globesoul.enloq` and `com.globesoul.enloq.dev` and no iOS app. Its
  Android API key answers only the Firebase Installations and FCM Registration
  APIs, and the unused Browser key is deleted. `google-services.json` carries
  that key, so it lives only in the ignored, mode-600
  `mobile/google-services.json` and is never committed or printed.
- **Credential (Q40, Q41).** A dedicated `enloq-push` service account holds
  only the Firebase Cloud Messaging API Admin role. Its RSA key pair was made
  on the VPS in the service's secrets and only the public certificate was
  uploaded, valid for ten years; deleting the key in the console revokes it.
  The credential exists only on the VPS; the Mac fallback has none.
- **Contact with Google (Q42).** With no Analytics and FCM's automatic start
  off, Enloq first contacts Google when notifications are turned on. The app
  asks once per pairing (ADR 47 Q4), and only a yes registers. Turning
  notifications off, forgetting the phone or pairing again deletes its token.

**Open: the first real push and ntfy's removal (Q22, Q34, Q35, Q44, Q45).**
Before FCM, Android push went through a self-hosted ntfy server at
`ntfy.globesoul.com` with the ntfy app as UnifiedPush distributor (Q22, Q34,
Q35); FCM replaced it, and it carries no pushes. Real FCM pushes reached
Enloq Dev; the first push through the VPS service to the owner's Enloq waits
for the owner to turn notifications on and send a test notification, which
the agent confirms the service recorded as sent (Q44). After that the agent
removes ntfy's container, edge route and DNS record, the service's relay
settings and the repository's ntfy files, and lists the inert leftovers for
the owner to delete (Q45).

### Media and offline

**Voice on iPhone (Q13).** iOS cannot play the Ogg container, so the service
transcodes a voice note to AAC on the first request from an iPhone and caches
the copy beside the original under the 30-day rule
([ADR 46](0046-the-apps-catch-up-units.md)). Android and browsers that play
Ogg get the original. The transcript button works on every platform.

**Without connectivity (Q20).** The app shows the last-known conversation list
and opened chats read-only under an explicit unreachable state, keeps drafts
locally until the service accepts them so no text is lost, and queues nothing
for sending. Forgetting the phone deletes that copy. The web has no offline
copy (ADR 44 Q11).

## Consequences

- ADR 20's push destinations include phones, ADR 26's touch rules and ADR
  36's routes apply to the app, and ADR 11's Trusted Browser has a sibling,
  the Trusted Device.
- The phone shares each member's budgets, the Concurrency Limit and every
  processing rule; it starts no AI work the web could not start.
- Android push depends on Google Play and FCM. iPhone use needs a weekly
  reinstall and has no push until enrollment.
- Known limit: images cached by the platform's loader (Fresco on Android, the
  URL cache on iOS) outlive a forgotten phone until the system evicts them or
  the app's data is cleared.
- Known limit: on the iOS simulator, a sheet opened a second time in one
  session drops out of XCTest's accessibility snapshot; whether VoiceOver
  loses it too is unverified.
- Camera scanning, the physical iPhone and the Android photo picker and camera
  are not exercised on devices.

## Questions

Q2, Q7, Q14-Q19 and Q21 are in ADR 37.

- Q1: iPhone and Android from one Expo codebase, built locally.
- Q3: the app claims pairings and holds a 256-bit device credential in the
  Keychain or Keystore, sent as a bearer header; immediate revocation.
- Q4: direct APNs for iPhone, waiting for enrollment.
- Q5: each phone is its own push destination with an opaque payload, quiet
  while any of the member's clients reads that chat.
- Q6: the app covers everything; the web follows it (ADR 42 Q7, ADR 44 Q2).
- Q8: no app lock.
- Q9: replaced by Q32: separate npm packages and a `shared/` folder.
- Q10: local builds; no EAS, Expo account or over-the-air updates; telemetry
  off.
- Q11: Jest and Maestro journeys against the authored fixture server.
- Q12: no phone-browser-specific pairing work.
- Q13: an AAC copy of voice notes for iPhones (ADR 46).
- Q20: a read-only offline copy in the app; drafts kept until accepted.
- Q22: replaced by Q37 (FCM); the ntfy relay awaits removal (Q45).
- Q23: replaced by ADR 48's members.
- Q24: a trusted phone or browser pairs a phone through Pair a phone (ADR 47
  Q1).
- Q25: app work may be edited on the Mac, pushed and deployed from the VPS.
- Q26: free Personal Team signing; a weekly-reinstalled Release build on the
  iPhone.
- Q27: iPhone push and the Notification Service Extension wait for
  enrollment.
- Q28: replaced by the Enloq name.
- Q29: the Mac has room for both toolchains.
- Q30: the agent downloads runtimes, images and dependencies from official
  sources.
- Q31: the Galaxy S9+ on Android 10 is the development phone.
- Q32: separate npm packages with a `shared/` source folder.
- Q33: the agent accepts the Android SDK license; the physical iPhone stays
  out of the checks.
- Q34: replaced by Q37; the ntfy server at `ntfy.globesoul.com` awaits
  removal (Q45).
- Q35: replaced by Q37; no ntfy distributor app.
- Q36: the agent installs over the owner's release app, keeping its pairing,
  without reading its screen.
- Q37: FCM HTTP v1 data-only messages; UnifiedPush leaves.
- Q38: the Firebase project `enloq-globesoul`, no billing, Analytics or
  Gemini, Android apps only.
- Q39: `google-services.json` only in the ignored, mode-600 file.
- Q40: the `enloq-push` service account with only the FCM Admin role and a
  key made on the VPS.
- Q41: the FCM credential exists only on the VPS.
- Q42: no contact with Google before notifications are turned on.
- Q43: the FCM build installs over the owner's release app, keeping its
  pairing.
- Q44: open: the owner's first test notification through the VPS.
- Q45: open: ntfy's removal after Q44.
- Q46: `expo-notifications`, with a background task for closed-app pushes;
  no iOS push entitlement until enrollment.
- Q47: Android builds on both the Mac and the VPS; iOS on the Mac.
- Q48: Firebase's unused Browser key is deleted.
- Q49: VPS Android builds sit beside the Mac's.
- Q50: native tools in the VPS account's home, not a container.
- Q51: WireGuard through wg-easy; adb and port reversal over the tunnel; the
  rooted phone restores both after a restart.
- Q52: the VPS builds both the release app and Enloq Dev.
- Q53: folded into Q51: the phone has its own VPN-only client.
- Q54: Enloq Dev's whole loop also runs on the VPS.
- Q55: the phone and the VPS host each keep their own wg-easy client.
- Q56: the phone's tunnel is VPN-only with no DNS servers.
