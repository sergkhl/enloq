---
status: accepted
---

# The Enloq app's catch-up units

## Context

[ADR 42](0042-groups-and-briefings-in-the-enloq-app.md) Q9 gave every piece
the web had and the Enloq app lacked a catch-up unit, CU1-CU6. Their phone
designs are the agent's, at the owner's direction for
[ADR 45](0045-settings-in-the-enloq-app.md)'s screens ("decide a good UX on
your own"); the questions covered only scope and behavior. The numbering
continues ADR 45's Q1-Q8. Under
[ADR 44](0044-mobile-only-web-in-the-apps-design.md) Q2 the units build both
clients, the web in the app's design inside its column.

## Decision

**Gaps no unit listed (Q9).** The web did several things no CU unit named:

- Tappable links in message text are in both clients (ADR 44's MW2).
- Sending and removing your own reactions in private chats, and attachment
  cards (polls, locations, contacts and link previews) are CU2's. Opening and
  sharing documents is CU1's.
- A message's own AI controls are CU3's, in its long-press sheet: Show AI
  text or Show original for that message, and Retry. The "Not analysed" note
  is CU3's as well.
- Copying a voice note's transcript is CU1's.
- Copying a message's #ID is dropped.
- Emotion details (the reason, the source and the time behind the header
  emoji) live in the Correspondent Profile, at the owner's direction:
  "emotion details should be in a new user profile screen".

**Video stickers stay still on phones (Q10).** Telegram's video stickers are
WebM clips with a transparent background. iOS plays no WebM, and Android's
built-in decoders drop the transparency, so on the phones these stickers show
as their still image. Animated (Lottie) stickers, static stickers and GIFs
move as usual, and the service converts nothing. The web animates video
stickers where the browser decodes their transparency, a platform difference
ADR 42 Q7 allows. Not adopted: converting each sticker to an animated WebP.

**The Correspondent Profile (Q11).** Tapping a private chat's header photo,
name or emotion opens a profile of the person. It shows their photo and name,
their @username, phone number when Telegram shares it, and bio, then the
emotion with its details and Copy chat link. The service reads the username,
phone and bio from Telegram each time the profile opens, through
`users.GetFullUserRequest`, which its read allow-list permits, and keeps none
of them. Offline, the profile shows what the cached chat holds. Later ADRs
add the chat's own switches and what memory holds about the person: Calm this
chat ([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)),
the Suggestions choice
([ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md)),
Remember this chat, Cares about and Traits
([ADR 55](0055-person-and-account-memory-and-traits.md)). Not adopted: a
profile limited to what the service already stored, and one holding only the
emotion details.

**The agent's design.** These choices carry beyond a single screen:

- **Voice notes** keep playing with the phone locked or another app open, and
  stop when the owner leaves the chat.
- **The iPhone's voice notes** come from an AAC copy, which the service makes
  on the first request from an iPhone and caches beside the original under the
  30-day rule. This settles the choice between remux and transcode that ADR 38
  Q13 left open: AAC plays in every iOS player.
- **Share instead of Download.** On the phones, the long-press sheet's Share
  stands for the web's Download.
- **The account AI switch** sits in Manage accounts.
- **Selection** starts from the long-press sheet.

**Attachment cards show what the service keeps.** The service keeps no phone
numbers, vCards, coordinates or link-preview descriptions
([ADR 16](0016-persistent-media-cache-and-non-ai-media.md)). A contact shows
its name, without a phone. A venue opens the maps app's search for its title:
Apple Maps on iPhones, the default maps app on Android and OpenStreetMap on
the web. A bare point names itself Location or Live location and opens
nothing. A link preview shows its site and title and opens the message's own
link. Not adopted: reading the missing details from Telegram on each tap.

**An owner-written memory entry counts bytes.** The service bounds an entry at
300 UTF-8 bytes ([ADR 21](0021-chat-memory-takeaways-and-idle-processing.md)),
so both clients count bytes: the field shows "n/300 bytes", a Cyrillic letter
counts two and most emoji four, and Add or Save stays off past the bound.
Counting characters would let the owner type entries the service then
refuses.

## Units

- **CU1, media playback** ([ADR 33](0033-inline-video-playback.md), ADR 38's
  ML1): voice notes with Telegram's waveform, seeking, a remembered 1×, 1.5×
  or 2× speed, one recording at a time and the next voice note playing on;
  Copy transcript; video messages in their circle; videos, which play in the
  media carousel ([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q11);
  GIFs looping silently; static, animated and video
  stickers (Q10), with reduced motion respected; Share for files, with cards
  for documents and music files.
- **CU2, message details:** reaction chips (⭐ for a paid reaction, ◇ for a
  custom one, the count above one, the owner's own marked; display-only in
  groups); the owner's own reactions in private chats from a row atop the
  long-press sheet; forwarded and imported lines; the edited pencil and
  version arrows in Originals; Scroll to latest; attachment cards.
- **CU3, AI controls and the profile:** Retry on a failed or unknown block; a
  message's own AI controls; the "Not analysed" note; Rewrite earlier
  messages with its progress and Stop; each account's AI processing switch in
  Manage accounts and the AI off tag in a private chat's header; the
  Correspondent Profile (Q11).
- **CU4, Message Selection and deletion:** Select from the long-press sheet,
  the review sheet, deletion for both participants or for everyone, and the
  operation cards; an accepted deletion ends the selection.
- **CU5, GIF and sticker sending:** the paperclip's Photos, GIFs and
  Stickers, staging a preview above the composer, then Send.
- **CU6, group recovery:** the folded Retained group work section at the foot
  of the Groups list, and Review and resume of a send paused by lost access.

## Consequences

- Opening a profile makes one Telegram read for that account; nothing about
  the person is stored beyond what the service already kept.
- CU1 added native modules (an audio player, a video player, an image view for
  animated GIFs and WebP, and Lottie), so Enloq Dev and the Saved Build carry
  them. The iPhone gets the units with its next build on the Mac, where its
  simulator journeys run ([ADR 43](0043-installable-android-builds-and-phone-setup.md)
  Q15).
- Model, account, budget, target, privacy and session-owner boundaries are
  otherwise unchanged.
