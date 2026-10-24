---
status: accepted
---

# Telegram's phone look, swipe to reply, a media carousel and new messages

## Context

The owner keeps task notes in the Owner Account's Saved Messages. One set of
notes asked for six changes: transcripts of the owner's own voice notes,
swipe to reply, writing to someone new, pending sends that look sent,
Telegram's message layout with avatars, and a photo and video carousel.

Before this ADR, Show transcript only read a stored transcript, and the
owner's own notes got one only as Supporting Context. Reply started only from
the long-press sheet. No list had search or a compose button, and a chat
opened only if Telegram's dialog list or intake had admitted it (ADR 13 and
[ADR 36](0036-account-and-chat-hash-routes.md) Q5 kept the app to existing
private chats). A Pending Send had a dashed outline, "Sending" and an inline
Cancel send. Every message had its own bubble with no avatar, groups repeated
the author on every message, a photo opened alone in a modal, and albums
weren't tracked.

## Decision

**Notes read back on the host (Q1).** `docker exec enloq python -m
enloq.saved --notes N` prints the owner's last N own notes in the Owner
Account's Saved Messages, oldest first, with their dates, and saves their
images in the data folder's `inbox/`. It skips the service's own posts
(pairing codes, "was paired" notes and the Saved Build), answers only the
container's loopback with the bootstrap control credential, takes no peer and
logs nothing. Delete the copies once read, and keep note contents out of the
repository. This amends
[ADR 43](0043-installable-android-builds-and-phone-setup.md)'s rule that only
images and pairing codes come back. Not adopted: reading the Saved tab
through the VPS browser's trust, and pasting tasks into the chat.

**Q2:** delivery scope; each task is its own unit, for both clients.

**A pending message looks sent (Q3).** A Pending Send is drawn exactly as a
sent message: same fill, size and shape, no outline. Its state is an icon in
the time line: a clock while Sending, turning into the tick once Sent. Not
sent and Delivery unknown add a red "!" and their word in the warning colour;
Send held keeps the clock with "Held"; Cancelled stays, dimmed, with
"Cancelled".

**Actions move to the sheet (Q4).** No pending message has inline buttons. A
long-press, or a tap on the red "!", opens the message sheet with exactly the
actions the service allows: Retry, Check delivery, Cancel send, Review for a
group send, and Copy. Only a proven unsent item can be cancelled, and an
uncertain one can't be bypassed. This amends
[ADR 27](0027-header-message-menu-forwarding-details-and-voice-playback.md)'s
inline actions for pending bubbles and where
[ADR 26](0026-mobile-message-actions-and-send-status.md) put them. Not
adopted: no Cancel at all, since a Not sent item holds later sends in its
chat.

**Swipe to reply (Q5).** Swiping a message from right to left past a
threshold shows a reply arrow with a light vibration; letting go does what
the sheet's Reply does for that row (for an AI block, its newest source
message). It works wherever the sheet offers Reply: incoming and outgoing
originals, AI blocks, groups and Saved. It is off during Message Selection
and on Pending Sends, and works on touch screens in both clients; a desktop
mouse keeps the long-press and right-click sheet.

**Telegram's phone look (Q6).** The owner: "exactly as Telegram's phone look;
and private messages don't have an avatar". Bubbles stay, the owner's on the
right without an avatar or name. In groups other people's messages get their
avatar and name (Q13); private chats show no avatars. AI blocks and the
Unread Separator keep their places. Not adopted: Telegram Desktop's list.

**New Message (Q7).** A search across the account's Telegram contacts, its
existing private chats (older than seven days included) and an exact
@username. Choosing someone opens the chat, even with no history, ready to
write. This ends ADR 13's "start with existing private DMs" for opening and
writing; Q15-Q18 shape it.

**The owner's own voice is transcribed (Q8).** Each outgoing voice note and
Video Circle in a private chat is transcribed once Telegram confirms it, sent
from Enloq or another Telegram app, under the incoming notes' rules: the
account's AI on, VPS speech for notes up to two minutes, the hosted fallback
for longer ones against the Daily Budget. It is the raw transcript, never
rewritten, behind the same Show transcript button. In groups the day's
briefing preparation already transcribes the owner's notes. Saved stays
without AI ([ADR 50](0050-icon-tabs-saved-messages-and-one-saved-build.md)
Q3).

**Show transcript starts one (Q9).** When a voice note or circle has no
transcript (older ones, earlier failures), Show transcript starts one with
progress, in private chats and groups, charging no briefing batch. With the
account's AI off it says so.

**A media carousel (Q10).** Tapping a photo or video opens a viewer holding
every photo and video in the open chat that Enloq holds (its 30 days plus
anything Load earlier brought), in date order, at the tapped one: swipe left
and right, pinch and double-tap zoom, a "3 / 12" counter, Share, and swipe
down to close. It works in private chats, groups and Saved, AI blocks'
attachments included. GIFs, stickers and Video Circles stay inline; Daily
Briefings keep linking to their sources
([ADR 34](0034-group-chats-and-daily-briefings.md)). Not adopted: only the
tapped message's own photos.

**Videos open in the viewer (Q11).** Tapping a video opens and plays it in
the carousel instead of inline, amending
[ADR 33](0033-inline-video-playback.md).

**Albums (Q12).** Photos and videos Telegram sent together as an album show
as one grid, as Telegram draws them, and tapping any of them opens the
carousel there. The service records each message's album.

**Message runs (Q13).** One sender's consecutive messages form a Message Run,
broken by another sender, a date line, the Unread Separator or a gap of more
than five minutes. In groups, another person's run carries their name, in one
of Telegram's seven colours chosen by their ID, on its first bubble and their
round avatar beside its last; someone without a photo gets initials on that
colour, and a channel posting into the group or an anonymous admin shows that
channel's or the group's photo and name with the signature. The owner's own
messages have no "You" line. Every run, private chats included, has smaller
gaps inside and a tail on its last bubble; private chats show no names or
avatars. The service serves photos of group members it sees as authors,
whether or not it has a chat with them.

**A member's profile (Q14).** Tapping a group member's avatar or name opens
their profile: photo, name, @username and bio, read from Telegram on opening
and never stored, as [ADR 47](0047-pairing-settings-menus-and-profiles.md)'s
profiles are. Its Message button opens the private chat with them as New
Message does.

**The New Message button (Q15).** A pencil icon in the account header, as in
Telegram for iOS, in the place Q19 gives it. Not adopted: a floating button,
and a search field at the top of the Private list.

**What New Message finds (Q16).** It opens on the account's existing private
chats, newest first, older than seven days included, then the contacts it has
never chatted with, alphabetically. Typing filters both by name and @username
and adds Telegram's own people search, which finds public usernames, under
"Global search". People only: no bots, channels, groups or phone numbers. The
read-only contacts and search calls are on the MTProto allow-list.

**An opened chat before it is recent (Q17).** A chat opened through New
Message, a member's profile or a chat link shows its history read from
Telegram (the latest page, then Load earlier) as originals only: its old
messages get no automatic rewrite, emotion, suggestions or memory. It stays out of the
Private list until a message is sent or received there; from then on the
Recent Chat rules apply. Manual suggestions can use its draft as intent,
without processing or scanning older history (ADR 25). Chat links open any of
the account's private chats, amending ADR 36 Q5. Not adopted: making an
opened chat recent at once, which would process its unread messages.

**People only, members included (Q18).** New Message neither creates groups
nor reopens older ones, which return to the Groups tab when active again. It
works on members' accounts, a member's use of it being their own request
([ADR 48](0048-members-on-the-shared-service.md)).

**Settings in the account sheet; the pencil in the gear's place (Q19).** The
owner: "put the settings gear into avatar menu; show pencil instead of the
gear on private tab". The gear leaves the list header, and Settings opens
from a row in the account sheet that the avatar and name open, amending
[ADR 45](0045-settings-in-the-enloq-app.md) Q3. On the Private tab the pencil
stands where the gear was; Groups and Saved show no pencil.

**The header keeps its place (Q20).** On Groups and Saved the pencil's place
stays empty, so the tab icons never move when the tab changes.

**The Settings row (Q21).** "Settings", with the gear icon, is the account
sheet's last row, after Manage accounts and a divider; the sheet keeps its
"Accounts" title. The chevron beside the name shows even with one account,
and the sheet opens without accounts too, so Settings is always reachable.

## Consequences

- The host-only Saved Messages path reads text, but only the owner's own
  notes and only on the host; it still posts nowhere new and deletes nothing.
- A Pending Send no longer looks different from a sent message; its state
  icon and the sheet carry what the dashed outline and inline buttons did.
- The app's swipe, zoom and carousel use React Native's own PanResponder,
  Animated and Vibration, with no added native module.
- Outgoing voice reaches the speech engines on purpose; long notes spend
  Daily Budget.
- Private chats can open and send outside the seven-day list and to people
  never written to before, but they spend nothing on AI until a message makes
  them recent.
- The service reads photos and profiles of group members it has no chat with,
  on demand, keeping only the avatar cache it already has.
