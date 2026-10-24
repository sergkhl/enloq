# Open work

This is the single list of what remains open: proposals awaiting an interview,
decisions awaiting the owner, follow-ups an agent can take up and the owner's
own gates. Each item names its owning ADR. Remove an item when it is done;
decisions go into the ADR, not here. Active work is listed in the
([plan index](README.md)).

## Proposals awaiting an interview

None of these is adopted, implemented or authorized.

- **Further decision-model uses** ([ADR 30](../adr/0030-decider-emotion-classification.md)
  Q10): a completeness and invention check of rewritten text (media and softened
  rewrites since ADR 54), a guard against suggestion options that add meaning
  the owner never gave ([ADR 25](../adr/0025-emotion-response-suggestions.md)'s
  rules; ADR 49 Q4 allows optional answer stances), and a source-language check.
  Attention, urgency, task and topic classification stay excluded (ADR 13).
- **A better rewrite of incoming text** ([ADR 54](../adr/0054-incoming-text-combined-not-rewritten.md)):
  text blocks are combined, not rewritten, until a better way is found.
- **Using traits** ([ADR 55](../adr/0055-person-and-account-memory-and-traits.md)
  Q10, Q13): traits are collected and shown only to the owner; no model reads
  them, and how to use or improve them is undecided.
- **Choices made "for now"**: AI settings stay server-wide and the owner's
  ([ADR 48](../adr/0048-members-on-the-shared-service.md) Q13, Q20), nothing
  in Saved goes to a model ([ADR 50](../adr/0050-icon-tabs-saved-messages-and-one-saved-build.md)
  Q3), and group deletions show as in private chats
  ([ADR 52](../adr/0052-deleted-messages-hiding-and-the-header-ai-toggle.md) Q9).

## Decisions awaiting the owner

- **Group planning and native intake cost** ([ADR 34](../adr/0034-group-chats-and-daily-briefings.md),
  [idle cost](../operations.md#idle-cost)). Each planning pass proves every
  cached briefing source again (about 0.5 s at 1,200 originals), and native
  intake commits each recent group's intake row around every poll (about 0.65
  journal writes a second). Options, none adopted: reuse proofs within one
  round, record group-relevant changes, or skip the replay mark for an empty
  difference. Then measure CPU once a group's seven-day window has filled.
- **Route-listing freshness** ([inference](../inference.md)): background work
  re-reads the exact-route listing only when it is over 24 hours old, so an
  outage that begins between refreshes fails as a counted `Provider
  unavailable` instead of a no-work refusal. Re-checking sooner would also
  refuse calls that would succeed.
- **Daily Briefing limits** ([ADR 34](../adr/0034-group-chats-and-daily-briefings.md)):
  on a busy day the final request's 32,000 bytes, metadata included, fit one
  chunk summary and drop the other paid ones; and split reply packets leave
  the ad classifier covering about a quarter of the 200 sources, spent again
  when newer meanings stale earlier packets.

## Agent follow-ups

- **Remove ntfy** ([ADR 38](../adr/0038-mobile-expo-client.md) Q45) once the
  owner's first real FCM push in Enloq works: stop and remove the `ntfy`
  container, its edge route and the `ntfy.globesoul.com` DNS record (through
  `cf`); drop the service's relay settings and code, `deploy/ntfy/` and the
  relay's section in [operations](../operations.md); then list the
  inert leftovers for the owner to delete (`~/pi-workspace/ntfy`, the
  service's `ntfy-publish-token`, the `enloq-phone` Keychain password).
- **Leftover sound settings** ([ADR 44](../adr/0044-mobile-only-web-in-the-apps-design.md)):
  the service still keeps ADR 20's sound presets and custom sound
  (`sound_preset`, `GET`/`POST /api/notifications/sound`), which no client
  offers; the web always plays Harp. Remove them.
- **The pinned band's flake** ([ADR 42](../adr/0042-groups-and-briefings-in-the-enloq-app.md)):
  on opening a group, the pinned Today band is sometimes not drawn while its
  in-list copy sits as a sliver at the top, so `groups.yaml` misses "Briefing
  up to date". Recompute the pinned day after the opening scroll.
- **Account Memory sources** ([ADR 55](../adr/0055-person-and-account-memory-and-traits.md)):
  a note's sources name their chats, but neither client shows them.
- **Image caches after Forget** ([ADR 38](../adr/0038-mobile-expo-client.md)
  Q20): photos and avatars cached by React Native's image loader outlive a
  forgotten phone until the system evicts them; clearing them needs an image
  library with a cache API, such as `expo-image`. Not scheduled.
- **Reopened sheets on iOS** ([ADR 38](../adr/0038-mobile-expo-client.md)): on
  the iOS 26.5 simulator a `Modal` sheet opened a second time empties
  XCTest's snapshot of the app; whether VoiceOver loses it too is unverified.
- **Expo tooling advisories**: `npm audit` lists advisories in Expo's build
  tooling only; take the SDK's patch releases as they ship, never
  `npm audit fix --force`.

## Watched limits

Act only if they recur or grow.

- **Muse's shared pool** ([ADR 53](../adr/0053-calming-hostile-messages-and-removing-adaptive-tone.md)):
  OpenRouter's shared upstream for Muse Spark 1.3 sometimes refuses softened
  rewrites with HTTP 429; they retry after 30, 60 and 120 seconds. If
  refusals become common, a key of our own with Meta is the remedy.
- **Memory updates** ([ADR 55](../adr/0055-person-and-account-memory-and-traits.md)):
  a large first update can spend all its output tokens on hidden reasoning
  and fail as incomplete; smaller first batches are the remedy. If MiMo's
  literal readings of figurative Russian show up in real chats, trial Muse
  Spark 1.3 for the memory update (Q28).

## Waiting on the owner

- **The Android release key's backup** ([ADR 43](../adr/0043-installable-android-builds-and-phone-setup.md)
  Q5, [mobile](../mobile.md#the-android-phone)): copy both files of
  `~/pi-workspace/enloq-service/android-signing/` to private storage off the
  VPS, never into a checkout, and check the copy opens. Without the key no
  update installs over Enloq.
- **Reinstalling Enloq once on each phone** (ADR 43 Q5): an Enloq signed with
  the old debug key refuses Saved Builds signed with the release key. On each
  phone with the release Enloq (the S25, and the S9+ beside Enloq Dev):
  **Disconnect from enloq.globesoul.com** at the foot of Settings, uninstall
  Enloq, install the pinned Saved Build, Connect with the code, then turn
  notifications back on. Forget the phone's old entry in Settings → Privacy →
  Trusted browsers and devices if it is still listed.
- **The newest Saved Build** ([ADR 43](../adr/0043-installable-android-builds-and-phone-setup.md),
  [ADR 50](../adr/0050-icon-tabs-saved-messages-and-one-saved-build.md)):
  install it from the pinned message in the Owner Account's Saved Messages as
  the everyday Enloq, and use what it carries in ordinary use.
- **The S25** ([ADR 43](../adr/0043-installable-android-builds-and-phone-setup.md)):
  install from a Saved Build, Connect with the code, add accounts and answer
  Android 16's notification dialog
  ([ADR 47](../adr/0047-pairing-settings-menus-and-profiles.md) Q4, Q5).
- **The first FCM push** ([ADR 38](../adr/0038-mobile-expo-client.md) Q44):
  turn notifications on in the release Enloq and send a test notification
  from Settings → Notifications; ntfy's removal follows.
- **A trusted browser** ([ADR 44](../adr/0044-mobile-only-web-in-the-apps-design.md)):
  trust one at `enloq.globesoul.com` with the code, and use the column on a
  desktop and a phone.
- **Pairing** ([ADR 47](../adr/0047-pairing-settings-menus-and-profiles.md)):
  a QR pairing between the owner's own devices; whether a tap on the code in
  Saved Messages copies it; Sign in with Telegram in a real recovery.
- **A leftover device** ([ADR 43](../adr/0043-installable-android-builds-and-phone-setup.md)):
  forget "Conari app on SM-G965F" in Settings → Trusted browsers and devices
  if it is still listed.
- **The first member** ([ADR 48](../adr/0048-members-on-the-shared-service.md)):
  forward the APK to a friend; their sign-in and Join Request, the push on the
  owner's devices and approval in People.
- **Soften hostile messages** ([ADR 53](../adr/0053-calming-hostile-messages-and-removing-adaptive-tone.md)):
  the owner's reading of hostile-only authored pairs remains open. Detection
  missed six subtle sarcasm, blame and forwarded-hostility cases; the current
  semantic limit is recorded in [inference](../inference.md#retained-acceptance-limits).
- **Memory and Idioms** ([ADR 56](../adr/0056-idioms-either-signal-hiding-and-memory-off-by-default.md),
  [ADR 55](../adr/0055-person-and-account-memory-and-traits.md)): switch
  Remember this chat on where wanted; Retry the one failed first memory update
  in that person's profile; try Idioms and judge its sets, whose expressions
  sometimes imply claims the chat doesn't make.
- **Group briefings** ([ADR 34](../adr/0034-group-chats-and-daily-briefings.md)):
  review the app view on the VPS and decide acceptance. A native check of
  authored group sends and own-message deletion needs groups the owner
  selects and authorizes.
- **Quality review** ([inference](../inference.md#retained-acceptance-limits),
  [group briefing outcomes](../inference.md#group-briefing-first-outcomes)):
  the Low quality outcomes kept for media rewrites (ADR 29), Emotion
  Classification and Reaction Assessment (ADR 30), Response Suggestions
  (ADR 49) and Daily Briefings (ADR 34), and ADR 35's two unverified image
  cases.
- **Ordinary use of native paths no check has observed**: queued-send
  recovery after a real interruption or flood wait ([ADR 26](../adr/0026-mobile-message-actions-and-send-status.md));
  native disconnect recovery; downloads across Telegram data centres;
  attachment deletion and interrupted deletion recovery
  ([ADR 31](../adr/0031-message-deletion-selection-and-group-ai-controls.md));
  a live forward without a name
  ([ADR 27](../adr/0027-header-message-menu-forwarding-details-and-voice-playback.md));
  sticker libraries and entitlements ([ADR 32](../adr/0032-gif-and-sticker-sending.md));
  voice, circle and video playback on the phones
  ([ADR 33](../adr/0033-inline-video-playback.md),
  [ADR 46](../adr/0046-the-apps-catch-up-units.md)); the cameras and
  Android's photo picker.
- **The iPhone** ([ADR 38](../adr/0038-mobile-expo-client.md), ADR 43 Q15):
  its next build on the Mac carries every change since its last one; the
  simulator journeys run then, and the owner installs it.
- **Developer accounts** ([ADR 48](../adr/0048-members-on-the-shared-service.md)
  Q6, Q7; [ADR 38](../adr/0038-mobile-expo-client.md) Q27): enrolling in the
  Apple Developer Program and Google Play's developer account. iOS push
  through APNs, its Notification Service Extension, iPhone members and store
  releases wait for them.
- **Go-live and production** ([ADR 37](../adr/0037-vps-hosting-and-remote-client-trust.md)
  Q19, Q21): declaring the VPS live retires the stopped Mac instance's data;
  declaring production ends the acceptance phase, so app data becomes
  durable, nightly encrypted backups start and the standing development
  authorization ends.

## Live test allowance

The owner allows live test requests without asking first, up to 10,000 in
total; 1,857 are used. Add each check's requests here.
