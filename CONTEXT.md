# Assisted Telegram Correspondence

This context describes Enloq, a Telegram client in which an LLM acts as a
communication translator for people's own correspondence. It combines what
arrives, makes voice notes and images readable, softens hostile messages and
offers automatic and manual suggestions for continuing a conversation, while
every original stays one tap away. One shared service, the Service Host, serves the owner and the
friends they approve as Members, through the Enloq app and a web app built to
the same design.

The terms below are settled vocabulary. The [ADRs](docs/adr/) own the decisions
and their reasoning, and the runbooks in [docs](docs/) own procedures,
mechanics and known limits. [ADR 13](docs/adr/0013-clean-slate-rewriting-rebuild.md)
owns the product's scope. Several earlier ADRs describe the application
retired by that rebuild; their vocabulary (attention tracking, topic tags,
recommended answers) is not part of this context.

## Language

### Product and service

**Standalone Telegram UI**:
Enloq's own interface for reading and writing Telegram correspondence. It has
two clients of one Service Host: the Mobile Client, which is the target
platform, and the web app, which shows the Mobile Client's screens in one
centered column about 480 px wide and does only what the app does
([ADR 44](docs/adr/0044-mobile-only-web-in-the-apps-design.md)). Its scope:
private chats, Group Chats with Daily Briefings and each account's Saved
Messages; switching between several Telegram accounts; Incoming Blocks
processed in the background, with media rewrites, hostile-message softening,
transcripts, Image Understanding, Recipient Emotion and Response Suggestions;
and outgoing text and media, with manual suggestion generation in private
chats.

**Communication Translator**:
The LLM's role: reducing cognitive load by making fragmented or difficult
incoming communication readable and offering bounded wording to answer or
continue the conversation. It preserves substance and source language and never supplies
missing intent as fact. The user chooses and sends every answer. Response
Suggestions offer bounded wording; nothing tracks tasks, attention or topics.

**Service Host**:
The machine running Enloq's backend, which every client reads and writes
through: the owner's VPS at `enloq.globesoul.com`
([ADR 37](docs/adr/0037-vps-hosting-and-remote-client-trust.md),
[ADR 40](docs/adr/0040-shared-vps-deployment-and-development.md)). It holds the
Telegram sessions, caches, drafts, memories, credential references and push
keys; clients hold only their trust credential, transient state and, in the
Mobile Client, an offline copy. The Mac's MacHelm-managed instance stays
stopped as the fallback until the owner declares the VPS live.

**Mobile Client**:
The Enloq app: one Expo codebase for iPhone and Android
([ADR 38](docs/adr/0038-mobile-expo-client.md)). It is the target platform:
new and changed features are designed for it first, and the web takes the same
design unless something works on only one platform
([ADR 42](docs/adr/0042-groups-and-briefings-in-the-enloq-app.md)). It is a
remote client of the Service Host under the same accounts, budgets and
processing rules, and starts no AI work the web could not start. Android runs
the release-type Enloq, installed from a Saved Build; Enloq Dev
(`com.globesoul.enloq.dev`) is the development build, which loads its
JavaScript from Metro. iPhone builds are development-signed with a free
Personal Team, and iPhone push waits for the Apple Developer Program. Offline,
the app shows the last-known list and opened chats read-only and keeps drafts
until the service accepts them.

**Member**:
A person using the Service Host with their own Telegram accounts, trusted
clients, Hide Bots, Reading View, budgets, provider pseudonym,
notifications and per-chat choices
([ADR 48](docs/adr/0048-members-on-the-shared-service.md)). A trusted client
opens only its member's Account Workspaces. The owner is the first member and
the admin; the others are friends the owner approved, who share the owner's AI
settings and Processing Guidance and cannot change them. Enloq acts in a
member's Telegram only when they ask, and posts nothing to their Saved
Messages on its own. The owner and coding agents can read every member's
data, as the Member Agreement says. A member can leave from Settings, or the
owner can remove them: either way the server signs out their accounts,
forgets their clients and deletes their data, keeping only spending totals.
Members use the web and the Android app; the owner forwards APKs by hand.

**Home Account**:
The Telegram account a Member first signed in with, which identifies them.
Signing in with it on a new browser, or with nothing trusted left, pairs that
client to them; the sign-in only proves who they are, so the existing session
stays. A member's other accounts can't sign them in. The owner's Home Account
is the Owner Account.

**Owner Account**:
The owner's Home Account, named by `ENLOQ_OWNER_ACCOUNT` in the service's
configuration ([ADR 43](docs/adr/0043-installable-android-builds-and-phone-setup.md),
[ADR 47](docs/adr/0047-pairing-settings-menus-and-profiles.md)). Through a
host-only path, the service posts to its Saved Messages and to no other peer:
pairing codes as the bare six digits in monospace, a "was paired" note for each
pairing of the owner's clients, and the pinned Saved Build. While the account
is signed out, neither codes nor Saved Builds work, and the connection screen's
Sign in with Telegram signs it back in. Host commands of
`python -m enloq.saved` read back from it: the newest image, the newest pairing
code (so a coding agent's browser on the VPS can trust itself) and the owner's
last N own notes, with their images saved to the data folder's `inbox/`.

**Join Request**:
A person waiting for the owner's approval: someone the server didn't know
signed in with Telegram and accepted the Member Agreement. Their session sits
unused (no sync, no AI), and their phone or browser can only wait or withdraw.
The owner's phones and browsers get a push that opens People. Approval starts
their accounts, with AI processing On; declining, withdrawal or seven days
without a decision signs the session out and deletes everything. At most five
wait at once.

**Member Agreement**:
The one screen a person accepts before their Join Request reaches the owner,
recorded with its date and wording version. It promises no privacy and names no
provider or retention: Enloq is unofficial and experimental and holds full
Telegram sessions; anything it reads may be sent to any service, kept for any
time and read by the owner and their agents, and contacts haven't agreed to
that; data may be wiped, and leaving deletes what it holds
([ADR 55](docs/adr/0055-person-and-account-memory-and-traits.md)). Only
changed wording needs a new version, and a member's new AI stages wait until
they accept it.

**People**:
The owner's Settings screen of Join Requests, with Approve and Decline, and of
Members, with today's spending against each budget, Edit, last activity and
Remove. Members don't see it.

**Account Workspace**:
The separate state of one verified Telegram account, belonging to one Member:
its app-owned session, conversation cache, blocks, drafts, memories, media and
avatar caches and Send Operations, plus its profile in the shared registry
([ADR 10](docs/adr/0010-multiple-personal-accounts.md)). Each browser tab
selects its own account. Signed-in accounts sync in the background, spend their
member's budgets and share the Concurrency Limit and transcription. Manage
accounts holds Add account (a QR code, or phone number, login code and
two-step password), Sign in again, Reconnect, Log out, Remove local data, each
account's AI processing switch and About you. Logout signs only Enloq out of
Telegram, clears the media cache and hides the rest until the same account
signs in again; cached content keeps its 30-day expiry, while drafts, memories
and unresolved sends stay. Removing local data is a separate confirmed action
after logout with no unresolved send, and spending records stay.

**Remembered Account**:
The browser's durable most recently opened account, restored when Enloq opens
without a link ([ADR 24](docs/adr/0024-coherent-message-groups-and-focused-viewing.md),
[ADR 36](docs/adr/0036-account-and-chat-hash-routes.md)). A successful link or
notification navigation updates it; a failed or cancelled one does not, and
tabs already open stay independent. An unavailable saved account falls back to
the first authorized account, or sign-in when none remain; an account named in
a link never falls back to another.

**Pairing**:
Making a browser or phone a trusted client of one service origin, with a fresh
random 256-bit credential ([ADR 11](docs/adr/0011-durable-browser-trust.md),
ADR 43, ADR 47). The ways in:
- **The Saved Messages code.** Connect to enloq.globesoul.com asks the service
  to post a six-digit code to the Owner Account's Saved Messages; typing it
  within two minutes pairs the client to the owner, and its last digit submits
  it.
- **Pair a phone.** A trusted phone or browser shows a QR code and link from
  Settings → Privacy, pairing a phone to the same Member; only the Enloq app
  claims it, and Trust on the approving screen finishes it once both sides show
  the same six-digit code.
- **Sign in with Telegram.** The connection screen's sign-in (phone number,
  login code, two-step password) proves a Member's Home Account, signing the
  Owner Account back in when it is signed out, or turns an unknown account into
  a Join Request. Sign-ins are rate-limited per number, per client address and
  per server.
- **Local paths.** The Mac instance's launcher pairs browsers with a native
  dialog, and Enloq Dev's Use the local test server pairs with the authored
  fixture.

A public server refuses pairings started with the host's control credential,
so nothing pairs over SSH. Each client asks once per pairing to turn
notifications on.

**Trusted Browser**:
A browser paired with one service origin. It belongs to one Member and opens
only their Account Workspaces, but holds no Telegram authorization. Trust has
no app-imposed expiry; cleared browser data, being forgotten or another origin
requires pairing again. Forget, in Settings → Privacy, revokes it at once: its
tabs stop showing correspondence, any login it started is cancelled and its
requests are refused, while accounts, background work and drafts continue.

**Trusted Device**:
A phone running the Mobile Client, paired with one service origin. It holds its
credential in the Keychain or Keystore, sent as a bearer header, with no
app-imposed expiry, and belongs to one Member. Forget from any of that member's
clients revokes it at once: its event stream closes, its push destination is
retired and the app hides correspondence. Disconnect revokes the phone's own
credential the same way, drops its push registration and offline copy and
returns it to the pairing screen; the app holds one server at a time.

**Saved Build**:
An installable release-type Enloq APK, built on the VPS from a clean committed
`HEAD` by `mobile/scripts/publish-android` and posted by the service to the
Owner Account's Saved Messages with its version, commit, build time and the
changes since the previous one (ADR 43,
[ADR 50](docs/adr/0050-icon-tabs-saved-messages-and-one-saved-build.md)). The
service keeps one Saved Build message, pinned once: each new build replaces
its file and caption in place, the old build stays until Telegram confirms the
new one, and a deleted message is posted and pinned anew. The service deletes
nothing in Saved Messages. Android phones install it without adb or a tunnel;
Enloq Dev is never a Saved Build.

**Session Owner**:
The single component that connects and operates an authorized Telegram
session. It holds that session's exclusive owner lock: normally the account's
transport in the service, otherwise a bounded login, logout, setup or cutover
step. A second owner is refused, and sessions are never copied between
processes. Sessions of other applications keep their own owners.

**Application Credentials**:
The Telegram API application ID and hash that every account's client uses,
every Member's included. They are referenced from private files, never logged
or sent to a client, and are distinct from each account's authorization, its
own app-owned session. See [local credentials](docs/local-credentials.md).

**Settings**:
The screen opened from the last row of the account sheet
([ADR 45](docs/adr/0045-settings-in-the-enloq-app.md), ADR 47,
[ADR 51](docs/adr/0051-telegram-phone-look-and-new-messages.md)). Its rows,
with icons:
- **Notifications** (Android and the web): this client's switch, sound and
  test.
- **Budget**: the member's Daily and Group Briefing Budgets with today's
  spending, read-only to a friend.
- **AI** (the owner only): the Processing Character Threshold, the Concurrency
  Limit, the Memory and Automatic suggestions switches and Guidance, under a
  Decision-Model Outage notice while one lasts.
- **Privacy**: Pair a phone and the member's trusted browsers and devices with
  Forget.
- **People** (the owner only).

Below them come Leave Enloq for a member, Disconnect for this client and, in
the Mobile Client, the build line. Each control explains itself in one line;
there is no Help. Budget and pause messages open their section.

### Chats and navigation

**Recent Chat**:
A one-to-one conversation with a human user whose latest sent or received
Telegram message is within the rolling seven-day window; only Recent Chats are
listed in the Private tab and processed in the background. Bots, Saved
Messages, service notifications, groups, channels and Secret Chats are never
listed there. Drafts, reads and opening a chat do not refresh activity; a new
message restores it, and saved drafts survive leaving the list. Group Chats
follow the same rule in the Groups tab. A chat opened through New Message, a
profile or a link becomes recent only once a message is sent or received
there.

**Saved Messages**:
An account's own Telegram chat with itself, shown in the list's third tab,
Saved, for every account, members' included (ADR 50). It is one timeline as in
Telegram, newest at the bottom, with Load earlier and no tags. Nothing there
goes to a model: it has no rewrites, transcripts, emotion, suggestions, memory,
softening or Reading View toggle. Its messages are the account's own, so they
bring no push or Unseen Message, and its content follows the chats' 30-day
rule. The tab has the chat composer without AI actions and no reactions. Deleted
notes leave it at once. The Owner Account's holds its pairing codes, "was
paired" notes and the pinned Saved Build.

**New Message**:
Telegram's way to start writing, opened from a pencil in the account header on
the Private tab (ADR 51). It searches the account's contacts, its existing
private chats (older than seven days included) and an exact @username: it
lists the existing private chats newest first, then never-chatted contacts
alphabetically, and typing filters both and adds Telegram's people search
under "Global search". People only, never bots, channels, groups or phone
numbers. The chosen chat opens ready to write, even with no history. Until a
message makes it a Recent Chat, it shows its Telegram history as originals,
gets no background AI on old messages and stays out of the Private list.
Manual suggestions may use its draft as intent without processing that history.

**Chat Route**:
The URL of one Account Workspace's exact chat: the stable Telegram account user
ID in `?account=` and a signed numeric fragment that keeps the private,
basic-group or supergroup peer type (ADR 36). It opens any of the account's
private chats, and a group that is recent or holds Retained Group Work. It
never silently opens another account or chat when the target is unavailable,
and a fragment without an account asks which account. Explicit intent takes
precedence over saved navigation. It grants no browser trust, Telegram
authorization, AI opt-in or native-action authority. Copy chat link, in the
Correspondent Profile and the Group Profile, copies it.

**Conversation List Route**:
The URL of one Account Workspace's Private, Groups or Saved list, without a
chat fragment: `?account=<id>`, with `&tab=groups` or `&tab=saved`. A link to
the account's own chat opens its Saved tab.

**Reminders**:
The fourth tab, after Private, Groups and Saved, with a clock icon and
**All accounts** once in its header (ADR 58). Its first line, Daily digest
with its time, opens the digest's settings. It lists a Member's pending and
upcoming reminders as chat-like rows with account labels and
paused/unavailable states; tapping a pending row opens its chat and an
upcoming one its editor. Add reminder picks the account and person.
`?tab=reminders` opens it independently of account selection. A digest opens
the list, or the one reminder it names. Listing starts no model work.

**Reminder Schedule**:
One reminder rule for a private human chat, which may have several (ADR 58):
Once on a date, Every N days, Every N weeks on a weekday, Monthly on a date
or Yearly on a date, with a Daily digest or exact-time alert and an optional
Message Idea. It saves its timezone, initially the device's, and fallback
language, initially the chat's recent language or else the device's; travel
changes neither. Short months use their last day, and 29 February the 28th
in other years, without moving later dates. The reminder editor previews the
next delivery. A schedule survives inactivity and the thirty-day cache; a
Once schedule ends when its occurrence completes.

**Message Idea**:
A Reminder Schedule's optional text: a draft, instructions in square
brackets, or both, shaping every set its occurrences prepare until edited
(ADR 58). Empty means a gentle check-in. Its first line shows on the
reminder's row, in the Reminder Bar and in exact alerts; it never enters the
chat's draft. A day it names explicitly gets Kept Messages.

**Kept Messages**:
A private copy of up to 20 cached messages from a day a Message Idea names
explicitly ("oct 25", "25.10"), with their transcripts and image meanings,
kept with its Reminder Schedule until the schedule ends or is removed or the
date leaves the idea (ADR 58). A day still ahead is copied when it ends.
Edits and deletions seen while the originals are cached update it. It is the
only message text that outlives the thirty-day cache.

**Reminder Occurrence**:
A schedule's due invitation to write, pending until a qualifying send or
Dismiss and never lapsing (ADR 58). Only the latest pending occurrence per
schedule remains. One manual set covers all of a chat's pending occurrences
and owns its suggestion area: prepared two minutes before the alert,
refreshed 30 quiet seconds after new messages, and replaced on opening only
when it no longer fits. Its options keep each reminder's purpose, answer
messages that arrived after it fell due and treat earlier ones as reference.
Without a Message Idea, any Telegram-confirmed later outgoing message, from
any client, completes it; with one, only a confirmed send that started from
its suggestions does. Dismiss completes either. Incoming messages and
opening do not.

**Reminder Bar**:
A slim bar above a private chat's suggestions while it has pending Reminder
Occurrences, listing each by its Message Idea's first line, or "Check-in
reminder", with when it fell due, its own Dismiss and a way to its editor
(ADR 58).

**Reminder Digest**:
One daily alert across the member's accounts, initially 09:00 in a saved
device timezone, with editable time and timezone (ADR 58). It shows
"Reminders" with up to three names and "+N". A schedule's local calendar date
becomes eligible for the first digest on or after it begins. Exact-time
occurrences use the schedule's local time and are excluded; their alerts show
the person's name and the Message Idea's first line, or "Check in". Latest
missed digest occurrences wait for the next digest after downtime; old
pending occurrences never alert repeatedly. Both Automatic suggestions
switches pause reminders; On resumes at the next future delivery without
backfill. Account AI Off allows reminders but blocks new generation.

**Conversation Preview**:
A chat row's one-line excerpt of its latest sent or received native message,
with its time and a mute icon
([ADR 23](docs/adr/0023-local-unseen-messages-and-conversation-previews.md)).
It uses the Reading View and a completed rewrite when appropriate, otherwise
the original or a media label; outgoing text is the actual sent text. A
deleted newest message previews what it last said, with a small trash icon,
until it is hidden. It is not a generated summary, a draft or evidence that
the content was viewed.

**Correspondent Profile**:
The screen a private chat's header photo and name open
([ADR 46](docs/adr/0046-the-apps-catch-up-units.md), ADR 47). It shows:
- the person's photo, name, @username, phone number when Telegram shares it
  and bio, read from Telegram on each opening and never stored;
- the Recipient Emotion with its label, reason, source and time;
- the chat's switches: Soften hostile messages, Automatic suggestions and
  Remember This Chat, plus its Suggestion Style;
- its Reminder Schedules, each opening the reminder editor, and Add reminder;
- what the person cares about (Person Memory) and their Traits;
- Copy chat link.

A group member's avatar or name opens the same profile without emotion,
memory or switches, with a Message button to the private chat (ADR 51).
Offline, the app shows the cached photo, name and emotion.

**Group Profile**:
The screen a group's header photo and name open (ADR 47): the group's photo
and title, its @username when public, its member count and description, read
from Telegram on each opening and never stored, with no member list, then Copy
chat link. Offline it shows the cached photo and title.

**Conversation Menu**:
A group's ⋮ Conversation actions sheet, at the header's right end after the
Reading View toggle (ADR 42). It holds Group AI Processing, with a note when
the group is unsupported, its access is lost or unverified or the account's AI
is Off, and Hide Bots. Private chats have none; their per-chat switches live in
the Correspondent Profile.

### Reading

**Reading View**:
AI or Originals, chosen with one icon-only robot toggle at the conversation
header's right end, pressed for AI
([ADR 14](docs/adr/0014-account-ai-processing-and-reading-view.md),
[ADR 52](docs/adr/0052-deleted-messages-hiding-and-the-header-ai-toggle.md)).
AI shows Incoming Rewrites and Streaming Rewrites where they exist, text
blocks as their combined originals and groups' Daily Briefings; Originals
shows each native message in order, with its versions. The choice is each
Member's, remembered across chats, accounts and restarts, initially AI. It is
independent of processing and starts no generation. Saved Messages has no
toggle. Sparkles are never used for AI.

**Message Reading Choice**:
One AI block's own view, chosen in its sheet with the robot icon: Show
original, or Show AI text (ADR 24, ADR 46). It applies to that block only,
starts no generation, lasts while the chat stays open and resets on reopening
or a change of the Reading View. It is offered while usable AI text exists.

**Group AI Controls**:
A message group's own AI controls in its sheet: the Message Reading Choice and
Retry for a failed or interrupted rewrite
([ADR 31](docs/adr/0031-message-deletion-selection-and-group-ai-controls.md),
ADR 46). The group here is an Incoming Block, not a Group Chat. There is no
first-time Rewrite now for one block.

**Message Run**:
One sender's consecutive messages, broken by another sender, a date line, the
Unread Separator or a gap of more than five minutes, as Telegram's phone apps
group them (ADR 51). A run's bubbles sit closer together and its last has a
tail. In groups another person's run carries their name, in one of seven
colours chosen by their ID, on its first bubble and their round avatar
(initials without a photo) beside its last, which opens their profile; the
owner's own runs have neither, and private chats show no names or avatars.

**Following Latest**:
Opening a conversation goes to its first ready Unseen Message, under the
Unread Separator, or else to its newest message; a group whose unseen messages
start under a New Messages Spoiler opens on the spoiler. The view follows new
messages, completed rewrites and media layout while at the bottom, and keeps
the position while the user reads older messages. Scroll to latest returns to
the bottom and resumes following; Send returns to latest, and unchanged
polling never moves the view.

**Unseen Message**:
An incoming source that has reached content readiness (its validated rewrite,
or its originals as the fallback) and has not been viewed in Enloq (ADR 23,
ADR 24). It puts one dot on the conversation row, with no counts or badges,
independently of Telegram's read state and of notification delivery, and its
viewing state is shared across the member's trusted clients. Groups have dots
too, whether their AI is on or off.

**Unseen Reaction**:
A correspondent's actionable 👍, 🤝, 👌 or 👎, newly confirmed on a cached
message no older than seven days in an eligible recent chat, which adds to the
conversation dot ([ADR 26](docs/adr/0026-mobile-message-actions-and-send-status.md)).
Its identity is separate from its target, which may be outgoing or already
viewed. It becomes ready after 30 quiet seconds and a validated assessment, or
the raw-reaction fallback, and opening includes its target. Reactions present
when first seen, unchanged ones and the owner's own make no dot, and none
brings push or sound. The dot outlives the 12-hour AI expiry until it is
viewed, the reaction is withdrawn, its target is deleted or the source
expires.

**Focused Conversation Viewing**:
The trigger that acknowledges every currently ready incoming source and Unseen
Reaction of a conversation after one continuous second with it visible, active
and focused, offscreen messages included (ADR 24). Background tabs, hidden
panes and covering dialogs do not count, and messages folded under a New
Messages Spoiler are not viewed. Acknowledgements stay within their account and
chat, send no Telegram read receipt and do not change inference catch-up.

**Unread Separator**:
A thin accent line labelled Unread messages directly above the earliest ready
unseen incoming message of an open conversation, above its whole block in AI
view ([ADR 28](docs/adr/0028-unread-separator-and-compact-conversation-header.md)).
It is placed on opening, kept while the chat stays open, and moved above
content that became ready while the chat was unfocused once focus returns. It
never scrolls the view, Unseen Reactions place none, and it neither mirrors nor
changes Telegram's read state.

**Message Context Menu**:
A message's bottom sheet, opened by a long-press, a right-click or the menu key
on the web, and a tap on a Pending Send's red "!" (ADR 26, ADR 46). In a
private chat its reaction row comes first. Then, where they apply: Reply and
Copy; Select for a current original in Originals; the Message Reading Choice;
Retry for a failed rewrite; Hide for a Deleted Message; Copy transcript; Share
in the app or Download in the web for media; and, on a bot's original in a
group, Stop ignoring this bot. A Pending Send's sheet offers exactly what the
service allows: Retry, Check delivery, Cancel send, Review for a paused group
send, and Copy. Copy takes the visible AI wording, original, version or
transcript, never metadata or files, and an earlier version's sheet offers only
Copy. Ordinary taps keep their reading, link and media behavior.

**Swipe to Reply**:
Swiping a message from right to left past a threshold, on a touch screen,
shows a reply arrow and, on release, does what the sheet's Reply does for that
row, for an AI block its newest source message (ADR 51). It works wherever the
sheet offers Reply, and is off during Message Selection and on Pending Sends.

**Reply Quote**:
The compact quote of the message a reply answers, above the reply's own content
in both directions: a tinted box with an accent bar holding a sender line and
a one-line body ([ADR 41](docs/adr/0041-reply-quotes.md)). The sender line uses
the message-header names (You, the contact or a group author) and adds the
replied-to message's date when it was sent on an earlier day. The body is that
message's current original wording, never AI text, or its media label; a quoted
fragment replaces it when the reply quotes part of the message, and a Deleted
Message shows its captured content with the trash, hidden or not. Tapping the
quote jumps to the message, in a group bringing a hidden or folded message into
view as a briefing citation does. In private chats an uncached target is read
once from Telegram; otherwise the quote reads "Reply to an earlier message". A
reply to another chat's message or to a story has no same-chat target and
shows what Telegram's reply header supplies. A merged AI bubble shows one quote
per replied-to message outside it, and the composer's reply target, Pending
Sends and the send-resume review use the same quote.

**Forwarded Original**:
A native message whose Telegram forward header marks it as forwarded
([ADR 27](docs/adr/0027-header-message-menu-forwarding-details-and-voice-playback.md)).
Both views show a non-clickable Forwarded from line with the source's name
(Forwarded message without one), the original date and time and a channel
post's author signature; AI bubbles show one line per origin with a count and
date range. Each client reads the missing headers of the messages it loaded,
in batches of 100, and a live forward without a name gets one exact read before
processing; filling in details never invalidates results, while a conflicting
known value does. Rewrites keep forwarded content attributed instead of
presenting it in the sender's voice.

**Imported Message**:
A native message Telegram marks as imported from another app, such as a chat
history imported into a private chat. Its forward header carries the original
sender's name and date. It shows "Imported · original date" instead of a
forwarded line, and AI work treats it as its sender's own message, with the
original date as context.

**Observed Message History**:
The native text and caption versions Enloq actually captured, in either
direction, with their transcripts and already-cached media
([ADR 19](docs/adr/0019-streamed-rewriting-and-observed-message-history.md),
[ADR 20](docs/adr/0020-minimal-reading-and-ready-notifications.md)). Originals
shows them with Previous and Next arrows and a position such as "2/3"; an
earlier version shows its text and, while cached, its photo. It is for reading
only: excluded from model input and memory, it expires 30 days after the
original message's date, and account removal clears it. It is neither a
history of AI rewrites nor a promise to recover versions Enloq never saw.
Telegram's first copy of a message Enloq sent replaces its receipt record and
is never a version; an edit Telegram hides (`edit_hide`) keeps a version only
for new wording or a changed link target, and a change of the outgoing flag
alone is never an edit. [History](docs/history.md) keeps the mechanics and
attribution limits.

**Native Edit Marker**:
A passive pencil beside an edited message's time, saying whether Originals has
its earlier versions (ADR 20). It is not an AI marker or a history button.
Telegram's first copy of a message Enloq sent, an edit date Telegram hides and
a change of the outgoing flag alone set none. A Deleted Message shows a trash
in its place.

**Attachment Identity**:
The stable native identity of a source attachment, used with text and link
destinations to tell visible source changes apart (ADR 20). Adding, removing
or replacing an attachment counts even with an unchanged caption. Equal file
metadata alone does not prove equal media, and snapshots without an identity
stay uncertain.

**Deleted Message**:
A native message that Telegram reports deleted outside Enloq after Enloq saw its
content (ADR 52). It keeps showing that last content with a trash icon,
in both reading views: its rewrite is withdrawn, earlier versions stay behind
the arrows, and its media comes from Enloq's cache only ("Photo · no longer
available" without it). Copy works; Reply and reactions are off. Hide, in its
sheet or for a selection of deleted messages, removes it for good, with no
confirmation and no unhide, kept by the service per account and message.
Messages deleted through Enloq, notes in Saved Messages, service notices and
messages whose content Enloq never saw are not shown, and "Deleted message"
never appears. With Hide Bots on, a verified bot's deleted messages are hidden
like its others.

**Native Reaction**:
An emoji on one native Telegram message, with its actors and counts (ADR 26,
ADR 46). Chips under messages show it: ⭐ for a paid reaction, ◇ for a custom
one, the count above one and the owner's own outlined. In private chats the
sheet's reaction row adds or removes the owner's own: their current ones first,
six from the account's palette and + for the rest, and an AI block with several
messages asks which one. Groups show chips only, and Saved Messages has none.
A reaction is never an edit of the message text, and only the owner's own
reaction on the latest incoming block counts as answering it (Response
Suggestions).

**Message Selection**:
The transient set of loaded current originals in one chat chosen for a bulk
action, entered from Select in an eligible original's sheet, in Originals only
(ADR 31, ADR 46, ADR 52). A tap on a row adds or removes it, an album is taken
whole, and a bar with the count, Delete and Cancel takes the composer's place.
A selection holds one kind, set by its first pick: a selection of Deleted
Messages offers Hide instead of Delete. Refreshes keep it, new arrivals stay
unchecked, an observed deletion removes its target and an edit needs fresh
review. Cancel, leaving the chat, a reload or an accepted deletion ends it. It
is distinct from selecting text and from a Deletion Operation.

**Message Deletion**:
Native Telegram deletion, for both participants, of selected current
originals, after a confirmation showing the account, chat, exact count and
previews (ADR 31). In a private chat any current ordinary original is eligible;
in a group only the owner's own, where Telegram allows deletion for everyone.
It is offered only in Originals, independently of Account AI Processing.
Messages deleted through Enloq leave the conversation at once, while their
versions, transcripts and cached media keep their expiry. It is neither a local
erasure nor hiding a Deleted Message.

**Deletion Operation**:
The durable record of one confirmed deletion: the captured account and chat,
exact native targets, reviewed source revisions and each sequential chunk's
outcome. It survives closing the client, keeps partial success and uncertainty,
and reconciles after a service restart without deleting further on its own.
Check result only reads; Retry remaining needs a fresh review of the
still-present unresolved originals. Telegram's acknowledgement and an observed
absence from the connected account are different evidence.

### Incoming processing

**Account AI Processing**:
The durable On/Off switch for one Telegram account's AI work, On by default,
including for a Member's account once approved (ADR 14). Off stops new
incoming, suggestion, memory, transcription and group work for that account while
sync, media prefetch, sending, drafts and cached results continue; started
work may finish, and its next stage waits for On. It is the master over Group
AI Processing. The switch is in Manage accounts; the account sheet marks such
an account AI off, and its conversations show a passive AI off tag in the
header, as they do while a group's own switch is Off.

**Incoming Block**:
Consecutive incoming messages of a private chat, also called a message group
(ADR 24). A block ends at the owner's next outgoing message, an intervening
service message or a calendar-day boundary in the server's timezone. Its
processing begins after 30 quiet seconds. A text-only block shows in AI reading
as its originals together in one bubble, headed with its message count and
time, and is decided rather than rewritten unless it is softened
([ADR 54](docs/adr/0054-incoming-text-combined-not-rewritten.md)). A block with
an image, a voice note or a Video Circle gets an Incoming Rewrite of the whole
group into one coherent text, keeping its attachments; an oversized block keeps
its originals with a truthful status. Its originals stay accessible. It is
distinct from a Group Chat.

**Incoming Rewrite**:
The clearer, neutral rendering of an Incoming Block that has an image, a voice
note or a Video Circle, and of a softened block
([ADR 29](docs/adr/0029-mimo-route-and-structured-incoming-rewriting.md),
ADR 54). Under the Faithfulness Rules it preserves facts, requests, refusals,
deadlines, conditions, uncertainty, material complaints, language and the
sender's voice, keeps forwarded content attributed, and never supplies missing
intent as fact. Transcripts become clear written text and images add their
meaning. The model returns ordered parts that the app renders as plain text:
one sentence for a single point, short paragraphs for separate topics and "•"
lines, numbered when order matters, for three or more parallel items, grouped
by topic in the order the sender raised them, with no summary line, ranking or
Markdown. It reads Person Memory as reference context and streams in as a
Streaming Rewrite. Text-only blocks are rewritten only by hostile-message
softening; a hidden `text_rewriting` registry switch, off and not in Settings, restores
text rewriting under the Processing Character Threshold.

**Streaming Rewrite**:
An incomplete incoming rewrite shown in its message as text arrives (ADR 19).
It is provisional until the complete result validates; failure restores the
applicable previous rendering or originals. Incoming rewriting has no Stop.
Reopening attaches to the same service-owned attempt. Suggestion sets publish
only when complete and never stream into the draft.

**Hostile Wording**:
Insults, aggressive blame, intimidation or hostile sarcasm directed at anyone,
judged in context by an explicit decision-model test, independently of emotion
(ADR 53). Frustration, anger, emphatic profanity, friendly teasing or reporting
someone else's insult do not qualify by themselves.

**Soften Hostile Messages**:
The private chat's rewrite of hostile Incoming Blocks, governed by its
**Soften hostile messages** switch in the Correspondent Profile, On by default
([ADR 53](docs/adr/0053-calming-hostile-messages-and-removing-adaptive-tone.md)).
It keeps every fact, request, condition, amount, deadline and threat stated
plainly, removes hostile presentation and renders hostile sarcasm by meaning.
A feeling is stated only when supported, never inferred from hostility alone.
Frustration or anger without hostile wording causes no rewrite. The switch
also governs the immediate notification screen; full blocks and rewrites
retain 30 quiet seconds, and notifications have their own five-second screen
and original fallback. Mood stays independent. The label is **Softened**, the
guidance is **Soften hostile messages**, and the original remains one tap away.
Switching On redoes no earlier result; switching Off leaves softened results
intact. Muse Spark 1.3 writes them. Groups and Saved Messages have no softening.

**Recipient Emotion**:
The estimated emotion expressed in a private chat's latest incoming block, or
in a qualifying correspondent reaction, assigned by Emotion Classification
([ADR 22](docs/adr/0022-recipient-emotion-and-adaptive-rewriting.md),
[ADR 30](docs/adr/0030-decider-emotion-classification.md)). Its fixed codes
are neutral, positive/warm, excited, sad, worried, frustrated, angry, confused
and unknown. It reads original wording, transcripts, image meanings and bounded
recent context, never memory, history, faces or profiles, and never treats
quoted or forwarded feelings as the sender's. It is communication context, not
a durable trait. The header shows its emoji right after the name, 😐 for
unknown or unavailable, and the Correspondent Profile shows its label, reason,
source and time. It expires 12 hours after the latest source message, or after
a reaction's addition, and earlier on new incoming content or a change to a
used source; a send alone does not clear it. The newest eligible communication
becomes current: late older work never replaces it, and removing a reaction
revives no earlier estimate. Groups have none.

**Emotion Classification**:
The decision-model request assigning Recipient Emotion (ADR 30, ADR 49,
ADR 53). It returns probabilities for nine codes, with a selected probability
below 0.35 becoming unknown; it writes no text. Block work also asks whether
automatic suggestions are useful and whether the block is sarcastic. An
explicit hostile-wording answer, independent of mood, governs softening.
Every eligible block retains its 30 quiet seconds; text is decided from
originals, and media in a softening chat is decided before rewriting from
originals, transcripts and cached image meanings. With softening Off, media
emotion follows the rewrite and may use fresh image meanings. A failed
decision authorizes no softening and leaves mood unavailable. Immediate
notification screening is separate from this block timing. Decisions read
bounded Supporting Context, never raw images, memory, traits or earlier
versions, under fixed criteria and applicable owner guidance. Mood keeps its
12-hour source lifetime.

**Reaction Assessment**:
The reassessment of Recipient Emotion and useful Response Suggestions from a
correspondent's confirmed 👍, 🤝, 👌 or 👎 on a cached message no older than
seven days in an eligible recent chat, the owner's outgoing messages included
(ADR 26). It runs through Emotion Classification after 30 quiet seconds, with
its exact target and bounded recent conversation, and keeps earlier rewritten
wording. A burst returning to the last processed state starts no call, an
incomplete snapshot does not prove removal, and reactions present when first
seen form a display-only baseline. The owner's own reactions start no
assessment. Its Unseen Reaction has its own readiness and viewing.

**Voice Transcript**:
Machine-generated text of a voice note or a Video Circle's audio, in private
chats and groups, the owner's own included
([ADR 39](docs/adr/0039-litellm-proxy-and-vps-speech.md), ADR 51). Whisper
large-v3-turbo on the VPS's CPU transcribes notes up to two minutes, with
automatic language detection and denoising off, and that audio reaches no
provider; longer notes, and notes the VPS cannot answer, go to the hosted
`elevenlabs/scribe-v2`, paid from the Daily Budget. Each transcript records
its model. It may contain recognition errors and is distinct from the audio and
from any rewrite: an Incoming Rewrite turns it into clear written text, adding
"(?)" after a word, name or number that may be misheard, while the owner's own
voice stays raw. Show transcript, at the end of the player, shows the raw text
or starts a missing transcript with progress; the sheet offers Copy
transcript. There is no deliberate re-transcription of an existing transcript.
Saved Messages has none.

**Image Understanding**:
A generated meaning of an image, used mainly as hidden context for the model:
in rewrites, mood decisions (from cached meanings) and Daily Briefings. It is
hidden by default, cached and reused, and the original image stays available.

**Supporting Context**:
Up to 20 preceding native messages of the same chat within seven days,
including the owner's messages and relevant transcripts and image meanings,
sent beside the current block. The seven days count back from now for
background work and from the target's date for an explicit target older than
seven days. The one directly replied-to message may be older; that exception
adds one source, never a reply chain or history scan, and never makes the
older source a target. A reply to another chat's message or to a story has no
same-chat replied-to message.

**Processing Window**:
The seven-day source age limit for automatic work on new or changed incoming
blocks and unread catch-up. Already-read history is not processed at initial
sync, and opening or refreshing a chat regenerates nothing unchanged. It is
distinct from the 30-day local cache, whose older blocks only Explicit
Processing reaches.

**Processing Character Threshold**:
The owner's server-wide minimum of original text and caption characters
(trimmed Unicode code points) before automatic text work starts, 200 by default
and settable from 0 to 10,000
([ADR 35](docs/adr/0035-processing-character-threshold-and-empty-rewrites.md)).
It governs automatic Daily Briefings, counted over the whole day, and private
text rewriting only while the hidden `text_rewriting` switch is on. Blocks with
new or changed analysable media are exempt, the mood decision judges short
blocks from their originals, and a hostile block in a softening chat is
softened at any length. Short originals stay as they are, with no waiting
status or counter. The latest value is rechecked before automatic preparation
and each dispatch; already-dispatched work may finish.

**Explicit Processing**:
Rewriting existing blocks on the owner's request, within the local 30-day cache
of an eligible chat ([ADR 15](docs/adr/0015-owner-controlled-processing-memory-and-prompts.md),
ADR 31, ADR 54). Rewrite earlier messages, at the top of the loaded history,
offers never-processed blocks with media; its run goes newest first behind new
work, shows Analyzing earlier messages · N of M with Stop, and ends with a
count when a quarter of the Daily Budget remains, processing is turned Off or
the chat stops being eligible. Retry, in a failed or interrupted block's
sheet, starts one guarded attempt and never a first-time or completed-result
regeneration; a Retry of a text block that isn't softened decides it again and
ends combined. Neither fetches nor scans Telegram history.

**Processing Stage**:
The visible state of a block's work: Waiting for a quiet moment, Queued,
Preparing, Transcribing, Reading images, Analyzing, Waiting for budget, Provider
busy, Paused (Account AI Off, or after repeated format failures), done, Nothing
to rewrite or failed. Stages report progress, with no percentage within one
model call. Suggestion work shows its actual preparation, wait or failure
status without changing the draft.

### Response suggestions

**Response Suggestions**:
Up to three editable message options, of any length, above a private chat's
composer ([ADR 25](docs/adr/0025-emotion-response-suggestions.md), ADR 49,
ADR 56).
Automatic sets answer the latest qualifying unanswered block or reaction;
manual sets can continue the conversation after the user's own last message,
a neutral exchange or an ending. Groups and Saved Messages have none.
- **Automatic trigger.** A usable non-neutral emotion or usefulness of at
  least 0.7 without one; reactions need both emotion and usefulness.
  Both Automatic suggestions switches must be On. Reads and restarts reuse
  unchanged results and never generate.
- **Manual trigger.** A deliberate press requests fresh alternatives;
  duplicate requests while active share that attempt. The button is
  disabled while any set for the chat is prepared or waits, with its spinner
  only while prepared. Automation Off does not block it.
  Account AI, budget, capacity and composer guards still apply.
- **Draft intent.** Manual options are complete messages. With text, the
  draft is the starting idea: options develop it and may go beyond its own
  words, and text in square brackets instructs the writer instead of being
  message text. A draft alone is enough without recent chat context.
  Generation changes no text, attachments or reply target. Automatic
  generation does not read the unfinished draft.
- **Reminders.** While a chat has pending Reminder Occurrences, their set
  owns its area and no automatic set is prepared there (ADR 58).
- **Grounding.** Available recent cached same-chat messages, up to the usual
  20 plus one direct reply, transcripts and image meanings, and enabled
  Person and Account Memory. No older-history scan or reply-chain traversal.
  Account Memory facts are stated only in their source chat.
- **Meaning.** Options may acknowledge, support, clarify, celebrate or ask a
  gentle question. Otherwise valid options may share an answer stance or
  offer different ones; a draft-guided message follows the user's stated intent.
  Neither path invents facts or unsupplied feelings and commitments. The
  decision model labels each option by what it says.
- **Lifetime.** Automatic sets keep twelve hours from their source and their
  unanswered-source rule. Manual sets keep twelve hours from the request;
  draft edits, new conversation content, sends or changed evidence withdraw
  them sooner. Pending Send guards apply while inputs stay valid. Manual
  work owns the area until withdrawn or explicitly replaced, so later
  automatic output cannot overwrite it.
- **Use.** A manual choice replaces the draft's text; an automatic one
  inserts at the saved cursor, or at the end without one. Undo restores the
  previous draft, and choosing never sends. Folding is shared across clients
  until the next set arrives.

MiMo writes Friendly and Professional; Muse Spark 1.3 writes Idioms. All
requests count in the account member's Daily Budget and shared capacity.
Language follows the conversation; a draft's own wording supplies the
language of options developed from it, and bracketed instructions never
choose it.

**Automatic Suggestions**:
The On/Off control for automatic preparation and display, separate from
manual generation and style. Each private chat's Correspondent Profile
offers it, On by default, belonging to that chat's member. The owner's
server-wide switch in Settings → AI has the same name; automatic work needs
both On. Off hides automatic sets and cancels queued automatic work, while
started work may finish into cache, and never blocks or hides manual sets.
Turning On reuses valid automatic sets without backfill (ADR 25, ADR 56).

**Suggestion Style**:
A private chat's **Style** choice in its Correspondent Profile: **Friendly**,
**Professional** or **Idioms**, Friendly by default, for manual and automatic
sets alike (ADR 56). Friendly is warm and natural without forced slang or
emojis; Professional is clear and businesslike without needless formality.
A change applies at the next generation; current and running sets retain
their captured style, and changing the setting starts no call. Each member
chooses per chat; there is no account-wide or member-wide style inheritance.

**Idiom Suggestions**:
Response Suggestions with Style Idioms (ADR 56). Every option contains a
real idiom, proverb, set expression or, in English, phrasal verb in its right
meaning and the chat's language. A set may have fewer options or none when
none fits. Idioms may give gentle advice through a proverb or voice pleasure
or sympathy through a common expression, within the fixed evidence limits.
They look like other options. Muse writes them under the owner's Idioms
guidance and a fixed frame.

**Typography Normalization**:
The deterministic conversion, in newly generated Response Suggestions only, of
long sentence dashes and curly or angled quotation marks and apostrophes to
ordinary keyboard forms (ADR 25). It keeps meaningful punctuation, exact copied
values and uncertain literals, rephrases nothing, and runs before final
validation and storage, so display and insertion use the same text. Ordinary
dashes and slashes stay allowed; cached options and authored drafts are
untouched.

### Memory

**Person Memory**:
One private chat's memory of what the other person cares about: their
priorities, plans, worries, interests, important people and events, likes,
dislikes and boundaries (ADR 55,
[ADR 21](docs/adr/0021-chat-memory-takeaways-and-idle-processing.md)). An
agreement stays when it matters to them; facts about the owner belong to
Account Memory. Each entry cites its messages, and an edited or deleted cited
message removes it without a model call. The owner sees, adds, edits, removes
and clears entries, and the model never changes theirs. It holds at most 40
entries and about 3,000 bytes, each generated entry at most 150 characters
(an owner entry 300 UTF-8 bytes), in the language it was said in, kept until
cleared or the account's local data is removed. MiMo updates it after 30 quiet
minutes in the chat, while no rewrite or transcription waits, one memory call
at a time across the server, from every message already sent to a model; text
that isn't softened reaches it through the mood decision. A failed update keeps
the previous memory, with Retry. It learns only while Remember This Chat is on.
Response Suggestions and media and softened rewrites read it; briefings,
notifications and the mood decision do not. The Correspondent
Profile shows it as Cares about. Groups and Saved Messages have none.

**Chat Memory**:
The earlier name of Person Memory, used by ADR 15 and ADR 21, when it held
takeaways about both people in a chat.

**Account Memory**:
The memory of one Telegram account's holder, the owner or a member for their
own accounts: what they care about, through Person Memory's lens (ADR 55). It
learns from the holder's own messages in that account's private chats where
Remember This Chat is on, read with the conversation around them, and from
entries they write; groups and Saved Messages teach it nothing. It follows
Person Memory's rules for entries, size, control and retention. Response
Suggestions in any chat may let it shape which stances and words fit, but state
an entry only in the chat it came from. Manage accounts shows it as About you,
beside the holder's Traits.

**Traits**:
Lasting tendencies of a chat's person and of an account's holder, collected
for the owner's eyes only and shown in the Correspondent Profile and About you;
no model reads them (ADR 55). They come in three kinds:
- **Scales** the decision model scores from a fixed starter list: how they
  write, how they are toward the other, the Big Five and how they handle
  friction. A scale shows while a yes/no evidence question says yes at 0.7 or
  more, as its likeliest level with that level's probability; one that loses
  its evidence keeps its last level and date.
- **Notes** MiMo writes freely, each cited from at least three of the person's
  own messages on at least two days, at most 10.
- **Habits** counted without a model: reply time, active hours, the share of
  voice notes and who starts conversations.

Anything the evidence supports may be noted, sensitive guesses included. The
owner can set, clear or edit them, and the model never changes the owner's
own.

**Remember This Chat**:
A private chat's switch in the Correspondent Profile, beside Soften hostile messages,
Off by default for every chat without its own choice, members' included
(ADR 55, ADR 56). Off, the chat is out of memory both ways: it teaches neither
Person nor Account Memory, nothing in it reads either, and it collects no
traits; what memory already holds stays, visible and editable. A chat Off only
by default learns once from its 30-day cache when switched on; in a chat its
holder switched Off, what arrived meanwhile is never learned. The owner's
server-wide Memory switch in Settings covers both memories.

### Composing and sending

**Composer Action**:
Separate, always-visible Send and Suggestions buttons in private chats
(ADR 13, ADR 25, ADR 44). Send transmits the exact authored draft, asking
first while it still contains square-bracketed instructions; Suggestions
offers complete, selectable messages above it, without changing or sending
it, and is disabled while any set for the chat is prepared, showing its
spinner, or waits for budget, AI processing or the provider. Outgoing
Rewrite, its tones, streaming, Stop and rewrite Undo are removed; Undo
remains for suggestion choices. Enter adds
a new line, with no keyboard shortcuts. The paperclip opens Photos, GIFs and
Stickers (on the phone Photo library, Take photo, GIFs and Stickers). Image
thumbnails sit above an optional caption, and image-only sends are allowed.
Groups and Saved Messages keep their send composers without AI actions.
No inline usage hints or instructional tooltips are added.

**Media Picker**:
The account's sheet for choosing a GIF or a sticker, opened from the
paperclip's GIFs or Stickers, with GIFs and Stickers tabs
([ADR 32](docs/adr/0032-gif-and-sticker-sending.md), ADR 46). It offers saved
GIFs and owner-typed Telegram GIF search after a brief pause, and the account's
installed, recent and favorite stickers, filtered by name and emoji, following
the account's entitlements without Premium decorations. Tiles are still images.
A choice stages Selected Media for a deliberate Send. It creates no assets or
sticker packs and never searches from correspondence, and a query lasts only
within the open chat.

**Selected Media**:
The exact unsent GIF or sticker staged above the composer in one chat's draft,
durable across navigation, reloads and restarts until sent or removed
(ADR 32). A GIF takes the authored text as its optional caption. A sticker
sends alone: it hides the text field, Draft kept preserves the text, its Undo
and its reply target, and Undo and suggestions pause. There is one
GIF or sticker at a time, never mixed with photos. Its validated bytes are
pinned privately, up to 50 MiB per GIF and 5 MiB per sticker, until it is sent
or no longer needed. It goes out as the chosen Telegram document after a 4 KB
read proves its reference: an expired reference is renewed once, a GIF that
can't be renewed uploads its kept bytes, and a sticker that can't must be
chosen again. Unavailable media is never silently replaced.

**Send Operation**:
One deliberate attempt to deliver an exact draft revision (text, images, a GIF
or sticker, and an optional native reply target) to its account's chat
([ADR 17](docs/adr/0017-send-hand-off-boundary.md),
[ADR 18](docs/adr/0018-send-operations-ownership.md)). It has a stable
identity, saved native random IDs and a state: prepared, dispatching,
confirmed, unknown or refused. Refused means Telegram refused it, Enloq stopped
it before hand-off (a changed draft, reply target or media, or a failed
upload), or its hand-off record proves Telegram did not execute it; unknown
means it may have. A definite refusal while media is prepared is named in the
Not sent line. Editing the draft makes a new revision, and a deleted reply
target refuses the send. An unknown send must be resolved with Check delivery,
which reconciles without resending, before another send in that chat.

**Pending Send**:
A submitted message whose delivery Telegram has not yet confirmed (ADR 26,
ADR 51). Once the service has safely saved its exact content and reply target,
it appears at once, drawn exactly as a sent message, and frees the composer; it
stays bound to its account and chat and never clears later edits. Its state is
an icon in the time line: a clock while Sending (Held while its reply target's
deletion is unresolved), the tick once Sent, a red "!" with Not sent or
Delivery unknown, and Cancelled dimmed. Its actions are in its sheet. It is not
evidence of delivery. A GIF owns its caption, while a sticker leaves kept text
with the draft. Closing the client leaves service-owned work running, and
restart recovery uses native evidence and waits for a deliberate Retry of
definitely unsent work.

**Send Queue**:
The ordered sequence of Pending Sends for one chat. Only one native send in
that chat runs at a time; failure or uncertainty pauses it, while other chats
stay independent within shared connection and flood limits. It is not an
offline queue: a disconnected send becomes Not sent, and a failed local save
leaves the draft in the composer. Cancel send is offered only for proven unsent
items, which stay as Cancelled bubbles with Copy, and accepted items behind
them continue in order. Uncertain sends cannot be cancelled or bypassed.

**Message Delivery Status**:
An outgoing message's state: Sending, Sent when Telegram accepts it into its
cloud, Read when Telegram reports the recipient read it, Not sent on a definite
non-send, or Delivery unknown for an uncertain outcome. Groups show no Read.
It is distinct from local incoming viewing and has no delivered-to-device
state or exact read time. It applies to outgoing messages from Enloq and from
other Telegram clients.

### Media

**Non-AI Media**:
Documents, stickers, GIFs, ordinary videos, music, polls, locations, contacts,
link previews and other items shown as media or cards but not analysed
([ADR 16](docs/adr/0016-persistent-media-cache-and-non-ai-media.md)). A model
receives only a placeholder with safe metadata, and an AI block notes what was
not analysed; such items never fail a block, and a block with nothing else
makes no model call. GIFs loop silently, static stickers are images, animated
stickers play, and video stickers animate in browsers that decode their
transparency and stay still on the phones; reduced motion stops them all.
Documents and music files open Share in the app or Download in the web. A
contact shows its name without a phone, a venue opens a maps search for its
title, a bare location opens nothing, and a link preview shows its site and
title and opens the message's own link.

**Voice Waveform**:
Telegram's own amplitude summary of a voice note, kept as display-only metadata
for the voice-note player and never sent to a model (ADR 27). Notes without one
show flat bars; Enloq computes none. A tap or a drag along it seeks. The player
plays one item at a time at a remembered 1×, 1.5× or 2× speed, plays a directly
following voice note next, keeps playing with the phone locked and stops when
the chat closes.

**Video Circle**:
A Telegram round video message. It plays in place in its circle on a tap, which
also pauses it, sharing one-at-a-time playback without speed control,
auto-play or the Media Carousel (ADR 27). Its audio is transcribed like a voice
note, and its frames never reach a model. Its sheet offers Copy transcript, and
Share in the app or Download in the web.

**Video Playback**:
Viewing an ordinary Telegram video, or a video file sent as a document
([ADR 33](docs/adr/0033-inline-video-playback.md), ADR 51). A tap opens it in
the Media Carousel, where it plays with sound once the complete file has loaded
into the Media Cache (at most 50 MiB per file, in a 2 GiB shared cache), with
Cancel and Retry while loading; the phones use the system's controls and full
screen. Playback is coordinated with voice notes, audio and Video Circles. Its
position lasts only while the chat stays open, with no watch history or
Telegram read state, and its contents stay Non-AI Media.

**Media Carousel**:
The full-screen viewer a tap on a photo or ordinary video opens: every photo
and video Enloq holds for the open chat (its 30 days and anything Load earlier
brought), in date order, starting at the tapped one, with swipe between items,
zoom on photos, a position counter, Share and swipe down to close (ADR 51).
Videos play there instead of inline; GIFs, stickers and Video Circles stay
inline, and Daily Briefings keep linking to their sources.

**Album**:
Photos and videos Telegram sent together as one group (its `grouped_id`). It
shows as one grid, as Telegram draws it; any item opens the Media Carousel
there, and Message Selection takes it whole (ADR 51).

**Media Cache**:
The private copy of message media, kept up to 30 days from the message date
within a shared size limit and cleared on logout or account removal (ADR 16).
Newly arrived voice notes and Video Circles are prefetched; groups get no
group-wide prefetch. The cached bytes of an observed older version are kept
for history reading. A voice note's AAC copy, for iPhones and browsers without
Ogg Opus, is made once from the cached source and kept beside it under the
same rule. Browser copies are revalidated on each use. Selected Media's pinned
bytes are separate.

### Groups and briefings

**Group Chat**:
A Telegram conversation with several participants: an existing basic group or
ordinary supergroup, listed in the Groups tab under the seven-day activity
rule, muted and archived ones included
([ADR 34](docs/adr/0034-group-chats-and-daily-briefings.md)). Forum or topic
groups and broadcast channels are not supported, and Enloq neither creates,
joins nor administers groups. AI reading shows Daily Briefings instead of
individual rewrites. The owner sends, replies and
deletes only their own current originals where Telegram allows deletion for
everyone; reactions are chips only and sends show no Read. Groups have Unseen
Message dots but no emotion, suggestions, memory, softening, sound or push. A
basic group migrated to a supergroup is a separate chat, with group AI Off. It
is distinct from an Incoming Block, which combines one correspondent's
messages in a private chat.

**Group AI Processing**:
The durable opt-in for one account's group, initially Off and subordinate to
Account AI Processing (ADR 34). It is in the group's Conversation Menu. Off
holds new briefing and media stages, including Retry, while started stages may
finish. Choosing AI reading enables nothing. The group's send composer has
neither outgoing Rewrite nor suggestion generation.

**Retained Group Work**:
The initially collapsed Retained group work section of the Groups tab, outside
the seven-day list, for groups with a saved draft or an unresolved send
(ADR 34, ADR 46). Its history is cache-only with normal expiry. A send accepted
but never handed to Telegram before confirmed loss of group access stays
paused until the owner reviews and resumes it once the same group's access
returns; an unknown delivery is never replayed. It backfills no history and
enables no group AI.

**Daily Briefing**:
The AI summary of one Group Chat's calendar day in the server's timezone,
shown in place of that day's messages in AI reading (ADR 34). It covers
substantive participant posts, the owner's and informative bots' included, from
text and captions, image meanings and voice and circle transcripts; routine
service events, Non-AI Media contents, hidden bots and excluded ads stay out.
It is a concise account by Briefing Topic of the discussion, decisions, dates
and open questions, with material attribution and source links, inventing no
agreement, facts, commitments or tasks. A validated day with nothing
substantive reads "No substantive updates in the covered messages.", and a day
of only excluded posts completes locally without a model call.
- **Streaming.** Only the final briefing streams: headings and summaries
  replace the earlier wording as they arrive, and participants and source links
  activate after validation. A failed attempt restores the last valid briefing,
  or originals.
- **Sources.** A source link reveals that day's cached originals in place and
  highlights the cited message, with Return to briefing.
- **One per day.** Messages it does not cover yet fold under the New Messages
  Spoiler until a later update covers them. An original seen to change
  withdraws the day's briefing until it is rebuilt; a new Intake Epoch alone
  withdraws nothing.
- **Updates.** Automatic updates follow a rolling four-hour clock per group and
  the Processing Character Threshold over the whole day, with new or changed
  analysable media exempt. Each is bounded: five generative, twelve
  classifier and twelve Citation Check requests, 200 new or changed sources
  and 20 minutes of new speech. Incomplete coverage is labelled partial. The
  first opt-in starts with today's cached messages, later updates catch up
  missed or changed days since opt-in within seven days, and nothing before
  opt-in is backfilled. Earlier days have no Briefing Slot; their automatic
  work continues unseen.

It is distinct from an Incoming Rewrite, Response Suggestions and memory.

**Briefing Topic**:
One substantive subject in a Daily Briefing: a bold title, a concise summary,
the native participants who substantively contributed and supporting source
links. Later fragments and corrections stay with the subject. Topics follow
their first substantive appearance in the day, while the originals keep their
order. Three participant names show, and the full list expands; attribution
needed to explain distinct positions stays in the summary.

**Briefing Slot**:
The single place for today's briefing work: the right end of today's date band
in AI reading. Each day's band is pinned and green-tinted and reads Today,
Yesterday, or the weekday and date. The slot shows one icon and a word at most,
with no tooltip: `↻ N new` or `↻ Update` (Manual Briefing Update), `Waiting`
while a run is active, amber `Retry` for a failed attempt, `Budget` when the
Group Briefing Budget is spent (opening it in Settings), `Syncing` while
Telegram messages are verified again, a grey check when up to date, and nothing
while AI is Off. Green and amber act on a tap, grey only reports. N counts the
messages under the New Messages Spoiler.

**Manual Briefing Update**:
The deliberate action, from today's Briefing Slot while today's briefing is
stale, that brings it up to date, including its first generation (ADR 34). It
skips the automatic four-hour, quiet-period and character admission, but needs
account and group AI On, group access, budget, capacity and the work limits.
Unchanged valid work is reused and reported up to date; new or changed
sources, consumed media evidence, changed guidance or bot visibility qualify.
It freezes the day and source cutoff, keeps one run per group and leaves the
automatic clock alone. It is distinct from Retry of a failed attempt.

**New Messages Spoiler**:
On a day with a Daily Briefing, the one folded `N new messages` control holding
the messages that briefing does not cover yet, in native order. It starts
closed, stays open once opened until the chat closes, and opens for a composer
send. The chat opens on it, under the Unread Separator, when unseen messages
start inside it. Folded messages are not viewed. Days without a briefing have
none.

**Intake Epoch**:
One round of proving a Group Chat's cached originals against Telegram. A new
one starts when Telegram cannot replay what was missed, and the seven-day
window is read again from the newest original down. It changes no original by
itself. New AI stages and native actions need proof in the current epoch; a
Daily Briefing and running briefing work stand until an original is seen to
change, including one the read passes without finding.

**Hide Bots**:
Each Member's preference, on by default, that leaves every native-verified bot
out of their Group Chats: its originals, old and new, disappear from both
reading views and are neither read nor summarized by Daily Briefings, with no
model judgment (ADR 34). Its switch is in a group's Conversation Menu. Stop
ignoring this bot, in a bot original's sheet, exempts one bot in one group.
Unknown authors stay visible, since a name is no proof, and human ads stay
labelled in Originals. Off shows the cached originals again and lets bots be
read, judged and classified. Changing it makes today's briefing updateable and
withdraws nothing. It is local reading and processing, not Telegram deletion.

**Ignored Bot**:
A native-verified bot remembered for exclusion from one account's group after
the decision model, judging its latest ten distinct surviving messages within
seven cached days, confirms they are useless (ADR 34). It is judged only while
Hide Bots is off. The cutoff is 0.90, and missing required media evidence holds
the sample before any classifier call. The ignore stays until the owner
corrects it, with no automatic reassessment or expiry, and the bot's messages
keep normal retention. It is an author decision, distinct from one source's
Group Ad Classification and from native service events.

**Group Ad Classification**:
The decision-model stage that excludes obvious unsolicited commercial promotion
from Daily Briefing inputs (ADR 34). One yes/no evaluation per source, at
probability 0.9 or more, may exclude a whole source, mixed promotional posts
included; uncertain or unclassified sources stay eligible, as do useful
informative bots, requested recommendations and discussion about advertising.
Bot posts are classified only while Hide Bots is off. It follows the day's
character and media admission, uses bounded context, native metadata and
cached media meanings, and runs before fresh media preparation, with no second
pass in an update. It is paid from the Group Briefing Budget, at most 12
requests per update and 32 targets per request. Excluded ads are handled
sources, not missing coverage: Originals marks the exact source `(ad)` when the
classification matches its revision, and a day of only ads completes locally
as Ads skipped. Failure keeps normal work available, with no separate Retry or
resampling.

**Citation Check**:
The decision-model stage that confirms a Briefing Topic's source links before
the final briefing publishes (ADR 34). One request per topic asks whether each
cited message supports part of the topic and whether together they support
all of it; a citation below probability 0.5 is removed, with its participant
unless another remaining citation is theirs, and the text is unchanged. It is
paid from the Group Briefing Budget, at most 12 requests per update. A check
that cannot run leaves the allowlist-checked citations, recorded unchecked,
with nothing shown. Ask This Group checks its answers' statements the same
way. It is distinct from Group Ad Classification, which filters inputs.

**Ask This Group**:
A Member's question about one Group Chat, answered from that group's cached
originals since its first opt-in day, within the 30-day cache, as up to four
statements with checked source links (ADR 59). It opens from the group's
Conversation Menu while Group AI Processing is On, searches by embeddings and
a reranker, writes with gpt-6.1-sol and runs the Citation Check on every
statement, dropping one left with no supported citation.
When nothing answers, one line names the searched range. Nothing is stored:
the answer lives on the open screen. It is paid from the Group Briefing
Budget, one question at a time per account.

### Notifications

**Notification Burst**:
New incoming messages in one chat grouped by the same 30 quiet seconds, distinct
from an Incoming Block's boundaries. Edits, old catch-up, manual retries and
outgoing work start no burst.

**Incoming Notification**:
The first alert for a Notification Burst as soon as its preview is eligible
(ADR 20). With Soften hostile messages On, text and captions receive an
immediate decision-model hostile-wording screen, with at most five seconds
from service arrival including queue time. Clean wording releases the
original immediately; hostile wording waits for a validated softened result,
whose combined-block processing keeps 30 quiet seconds. If screening or a
required rewrite cannot proceed or fails, or screening exceeds five seconds,
the original preview notifies immediately without further screening. That
fallback may expose hostility and never overrides AI, budget or capacity
controls. With softening Off, originals notify on arrival. Textless media
uses a typed label immediately, without transcription or image analysis.

Later arrivals update the group silently; a cleared clean arrival need not
wait for an earlier hostile one. Completion never re-alerts, and late older
output cannot replace a newer preview. A foreground client reading this chat
suppresses the alert; another foreground web chat plays a chime, otherwise
Push Destinations receive it, respecting Telegram mute and platform policy.
Groups and Unseen Reactions bring no sound or push. Unseen Message readiness
remains independent.

**Push Destination**:
Where a Member's pushes go: the one browser that most recently turned
notifications on, and each of their Trusted Devices with notifications on
(ADR 20, ADR 38). The payload is opaque (event ID, expiry, silent flag); the
browser or phone fetches the sender and preview from the service, falling back
to a generic notice. A phone's push is suppressed while any of the member's
clients reads that chat. Undelivered alerts expire after five minutes, and only
the latest pending alert per chat remains. Android phones use Firebase Cloud
Messaging and first contact Google when notifications are turned on; iPhone
push waits for the Apple Developer Program, with direct APNs as its design.
The owner's destinations also receive Join Request and Decision-Model Outage
pushes.

### Models, guidance and budgets

**Model Route**:
The exact model and provider pair that serves a model role, reached through
the owner's LiteLLM proxy with one caller key; the proxy's endpoint is
configuration (`MUSE_ENDPOINT_REF`) (ADR 29, ADR 30, ADR 39). Its listing and
rates are verified before dispatch. There is no model selector, fallback or
automatic resampling, and unavailability is a no-work refusal under the role's ordinary handling;
notification delivery uses its explicit original fallback. The routes:
- **MiMo V2.6 Flash** (`xiaomi_mimo/mimo-v2.6-flash`, Xiaomi's own API):
  incoming media rewrites, image meanings, Friendly and Professional suggestions,
  memory updates, trait notes and Daily Briefings. Its terms are no prompt
  training and 30-day retention.
- **Muse Spark 1.3** (`openrouter/meta/muse-spark-1.3-contributor`, through
  Meta, with low reasoning hidden): softened rewrites and Idiom Suggestions.
- **Decider V1.2** (`perplexity/pplx-decider-v1.2` at checkpoint `-20261016`,
  Perplexity only, on OpenRouter's Decisions endpoint): hostile-wording screening,
  Emotion Classification, Reaction Assessment, suggestion usefulness and
  labels, group ad and bot filtering, Citation Checks and trait scales. Its retention terms
  are unpublished, and a later
  checkpoint needs the owner.
- **gpt-6.1-sol** (`chatgpt/gpt-6.1-sol`, the proxy's ChatGPT-subscription
  route, medium effort): Ask This Group answers. Its receipts record $0.
- **Perplexity Embed V1 4B** and **Qwen3 Reranker 8B** (through OpenRouter's
  embeddings and rerank routers, at Perplexity and Fireworks, zero data
  retention): Ask This Group's retrieval.

Every route receives one private pseudonym per Member. Speech recognition is
separate (Voice Transcript).

**Decision-Model Outage**:
The state while decision requests fail at the route: a refused listing, a 401,
402 or 403, another HTTP failure, an unreachable endpoint or a reply from the
wrong model (ADR 49). One state covers every account, recorded with when and
how it began. Only the owner sees it, above Settings → AI, and one push without
content to the owner's phones and browser announces its start; the next
answered request ends it. A 401 or 403 does no work and counts nothing.

**Processing Guidance**:
The owner's server-wide, editable instructions for model work, applied from
the next call (ADR 15). Settings → AI → Guidance groups its fields, each with
Save, Reset and versions:
- **Rewriting:** Faithfulness rules, Incoming rewriting and Soften hostile
  messages.
- **Suggestion styles:** Friendly, Professional and Idioms.
- **Other work:** Emotion extraction, Person memory, Account memory, Response
  suggestions, When to suggest and Group briefings.

A fixed core the owner cannot edit frames content as untrusted data and
enforces each output contract; softening and Idioms guidance apply only to
hostile blocks and idiom sets, after a fixed frame. Each result records its guidance version
and shipped-defaults revision, and a new revision makes group briefings
updateable once. After repeated format failures a role pauses until the owner
resumes it from Guidance's paused-role notice.

**Faithfulness Rules**:
The editable Processing Guidance that preserves substance: facts, requests,
refusals, conditions, uncertainty and language, the sender's voice in incoming
rewrites, and no added answers, apologies or promises. The shipped text is the
default; owner edits may weaken it.

**Daily Budget**:
A Member's ceiling on model spending per budget day in the server's timezone
(ADR 15, ADR 48). The owner's is $1 by default and settable from $0 to $10; a
friend's is $0.50 by default, set only by the owner and read-only to them. It
counts only that member's accounts and pays every role except Daily Briefing
work, the hosted speech fallback included. New calls start only while counted
spending is below it: receipts at the larger of charged and upstream cost,
plus a flat $0.01 per running or unknown outcome. Started calls may finish, and
a refusal that did no work counts nothing. It never changes automatically, and
wiping content keeps the spending records.

**Group Briefing Budget**:
A Member's separate daily ceiling for Daily Briefing work: image meanings,
chunk summaries, final synthesis, ad and bot classification, Citation Checks
and failed-briefing Retry (ADR 34), and Ask This Group questions (ADR 59). The owner's is $2 by default and settable from $0 to $10; a
friend's is $0.50 by default, set by the owner. It is independent of the Daily
Budget, with no borrowing, double charging or combined ceiling, and each
request's budget is fixed at dispatch. Private suggestion and hostile-wording
requests use the Daily Budget, and VPS speech has no provider charge.

**Concurrency Limit**:
The owner's server-wide number of model calls that may run at once across all
accounts, 1 to 8 and 4 by default (ADR 15). Members take turns within each
class of work, and transcription runs one at a time. Private work and
manual suggestion work, Ask This Group included, run ahead of Daily Briefings, which never take the last free
slot at two or more, and briefings run ahead of idle memory work.
