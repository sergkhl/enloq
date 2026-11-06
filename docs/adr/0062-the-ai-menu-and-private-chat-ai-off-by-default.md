---
status: accepted
---

# The AI Menu, and private-chat AI Off by default

## Context

The owner asked: "ai switch in open chats should be a context menu with
separate switches for suggestions, emotion extraction and other processings",
"turn off all private chats processings (except Maya) by default", and "Move
all AI controls and prompt template edits into the AI context menu out of
profile".

Until then the robot at a conversation header's right end only chose the
Reading View and started no work
([ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md) Q13). A
private chat's own controls were in its Correspondent Profile: Soften hostile
messages and Automatic suggestions On by default, Style, and Remember this
chat Off by default
([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md),
[ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md),
[ADR 55](0055-person-and-account-memory-and-traits.md)). Emotion and the
rewriting of voice notes and images had no per-chat switch, and
[ADR 14](0014-account-ai-processing-and-reading-view.md) held that private
chats have no per-chat processing switch, because enabling chats one by one
had been tedious. One decision request per block asked its emotion,
usefulness, hostility and sarcasm together
([ADR 30](0030-decider-emotion-classification.md)); memory learned only from
messages those stages had sent to a model (ADR 55 Q19); and either Automatic
suggestions switch Off paused a chat's reminders
([ADR 58](0058-recurring-message-reminders.md) Q4). Processing Guidance lived
in Settings → AI → Guidance
([ADR 15](0015-owner-controlled-processing-memory-and-prompts.md)), and a
group's controls in its ⋮ sheet
([ADR 42](0042-groups-and-briefings-in-the-enloq-app.md) Q3).

## Decision

**The robot opens the AI Menu (Q1).** A tap on the robot at the right end of a
conversation header opens that chat's **AI Menu**, a bottom sheet. Its first
row is **AI view**: the Reading View, still one remembered choice for all
chats that starts no work (ADR 14). The chat's own controls follow, so
switching the view takes two taps. Saved Messages has no robot and no menu
([ADR 50](0050-icon-tabs-saved-messages-and-one-saved-build.md) Q3). Not
adopted: the robot kept as a one-tap view toggle beside a new ⋮, and a
long-press for the menu.

**Five switches, Off by default (Q2).** Each private chat's AI Menu has five
switches, each governing one kind of automatic work:

- **Automatic suggestions**: automatic Response Suggestions (ADR 56) and the
  sets reminders prepare (Q7).
- **Emotion**: Emotion Classification and Reaction Assessment (ADR 30, Q5).
- **Soften hostile messages**: softening and its immediate notification
  screen (ADR 53).
- **Voice and images**: automatic transcription of the chat's voice notes and
  Video Circles, the owner's own included, and the Incoming Rewrite of its
  media blocks with their image meanings (Q20).
- **Remember this chat**: what Person Memory, Account Memory and Traits learn
  from the chat (ADR 55, Q6).

Every private chat without its own choice has all five Off, the owner's and
every member's, new chats included; the Maya test chat is the exception
(Q10).
A choice belongs to the chat's member and is shared across their clients.
Account AI processing stays the master over the five (ADR 14), and the
owner's server-wide Automatic suggestions and Memory switches stay in
Settings → AI, so automatic work needs both levels On. With Soften hostile
messages Off, a notification shows the original preview at once, unscreened
([ADR 20](0020-minimal-reading-and-ready-notifications.md)). Combining text
into blocks needs no model and happens in every chat
([ADR 54](0054-incoming-text-combined-not-rewritten.md)). This reverses
ADR 14's rule that private chats have no per-chat processing switch. Not
adopted: three switches with one for all other processing, and a master
switch over the five.

**Off stops what runs on its own (Q3).** A switch governs automatic work
only. A deliberate tap works in any chat under the usual account, budget and
capacity guards: the Suggestions button, Show transcript, Retry and Rewrite
earlier messages. A Rewrite earlier messages run keeps going when a switch
turns Off; account AI Off still ends it. Not adopted: Off blocking those taps
too.

**AI controls leave the profile (Q4).** Every AI control of a private chat,
Style included, moves from its Correspondent Profile to its AI Menu, and
Processing Guidance moves there too (Q11). The profile keeps the person
(photo, name, @username, a phone number Telegram shares, bio), their
Reminder Schedules with Add reminder (ADR 58 Q12) and Copy chat link. The
header's photo, name and emoji still open it. This amends
[ADR 46](0046-the-apps-catch-up-units.md) Q11.

**Emotion Off (Q5).** Off, no emotion is estimated, shown or used: the header
and the AI Menu show none, and a current emoji goes at once. Automatic
suggestions in that chat then trigger on usefulness alone, at 0.7 (ADR 56
Q19), and reactions, which need emotion and usefulness, prepare none. A
block's decision request runs while Emotion, Automatic suggestions or Soften
hostile messages is On and asks only what the On switches use: the emotion
for Emotion, usefulness for Automatic suggestions, hostility and sarcasm for
Soften hostile messages. A reaction is assessed only while Emotion is On,
with usefulness asked while Automatic suggestions is On too. Not adopted:
hiding the emoji while still estimating it.

**Remember this chat on its own (Q6).** While it is On, memory reads the
chat's new messages itself, whatever the other switches say: their text and
captions, and whatever transcripts and image meanings exist. It starts no
transcription or image reading. This replaces ADR 55 Q19's learning only from
messages another stage had sent to a model. Its other rules stand: Off by
default (ADR 56 Q9), one start from the 30-day cache for a chat Off only by
default, and nothing learned that arrived while its holder had it Off
(ADR 55 Q20).

**Reminders always alert (Q7).** A Reminder Schedule alerts whatever the
Automatic suggestions switches say. The set prepared two minutes before an
alert, and its refreshes, need both Automatic suggestions switches On; the
Suggestions button asks for one either way (Q3). This replaces ADR 58 Q4's
pause. Not adopted: keeping the pause, and adding a reminder turning
Automatic suggestions On.

**Switching On and Off (Q8).** A switch turned On also does its work on the
chat's latest incoming block if that block is under seven days old: its
emotion, a softened rewrite if it is hostile, its media rewrite, or automatic
suggestions if it is unanswered and qualifies. Nothing older is processed;
older media stays with Rewrite earlier messages. Remember this chat keeps its
own start (Q6). Switching Off keeps what was made: softened text, rewrites and
memory stay, automatic sets hide (ADR 56) and the emoji goes (Q5). Not
adopted: only later arrivals, and catching up every block within seven days.

**Earlier choices (Q9).** At rollout every private chat's own choices of
Soften hostile messages, Automatic suggestions and Remember this chat turn
Off, except the Maya test chat's. A chosen Style stays, so turning Automatic
suggestions back on brings it back. Memory keeps what it holds, and what
arrives while Remember this chat is Off there is never learned (ADR 55 Q20).
Not adopted: keeping those choices, and treating those chats as opted in.

**The test chat (Q10).** Both sides of the Maya test chat, the Owner
Account's chat with Maya and hers with the Owner Account, keep Automatic
suggestions, Emotion, Soften hostile messages and Voice and images On, set as
the chats' own choices in the live data so no account ID enters the
repository. Remember this chat stays as it is: On in the Owner Account
(ADR 55 Q29), Off in Maya's. A live check that needs a switch Off turns it
Off for its run and back On. Not adopted: the Owner Account's side alone, and
a per-account default for Maya's account.

**Processing Guidance in the AI Menu, for now (Q11).** The owner's Processing
Guidance fields leave Settings → AI for the AI Menus, each on the page of the
control it shapes (Q17) and marked "for all chats". They stay server-wide:
an edit in any chat changes every chat, with Save, Reset and versions as
before (ADR 15). Only the owner sees them; members' AI Menus have none
([ADR 48](0048-members-on-the-shared-service.md) Q13). Settings → AI keeps
the Processing Character Threshold, the Concurrency Limit, the Memory and
Automatic suggestions switches, the Decision-Model Outage notice and the
paused-role notice (Q18). Not adopted for now: per-chat templates that start
from the server-wide text, and links from the AI Menu with the fields left in
Settings.

**The panels move too (Q12).** The emotion's label, reason, source and time
move to Emotion's page, and Cares about and Traits to Remember this chat's
page, with their editing and Retry as before. About you stays in Manage
accounts, since it belongs to an account. This amends ADR 55 Q21. Not
adopted: only the switches and Style moving.

**Groups (Q13).** In a group the robot's AI Menu replaces the ⋮ sheet: AI
view, Group AI processing with its notes, Ask this group while Group AI
processing is On, and Hide Bots, which decides what briefings read. A
retained group's AI Menu holds AI view and Hide Bots. This amends ADR 42 Q3,
[ADR 47](0047-pairing-settings-menus-and-profiles.md) Q7 and
[ADR 59](0059-ask-this-group.md) Q1. Not adopted: groups unchanged, and a ⋮
kept for Ask this group and Hide Bots.

**The robot shows whether AI runs (Q14).** The robot is filled while AI work
can run in the chat: account AI processing is On and, in a private chat, at
least one switch is On with its server-wide switch permitting it, or, in a
group, Group AI processing is On. Otherwise it is an outline, and its
accessible name says which. The passive "AI off" tag leaves both kinds of
header. The AI Menu says why nothing runs: "AI processing is Off for this
account" under its rows, as the group sheet did, and a server-wide switch
that is Off is named under the chat switch it overrides. The account sheet
still marks an account AI off. In a chat whose switches are Off, blocks show
their combined originals with no status line, and new messages become ready
as originals after their 30 quiet seconds, as group messages do
([ADR 23](0023-local-unseen-messages-and-conversation-previews.md)). Not
adopted: the robot showing the Reading View, and an "AI off" tag on every
chat with nothing On.

**The rows (Q15).** A private chat's AI Menu, in this order, each control
explained in one line ([ADR 45](0045-settings-in-the-enloq-app.md) Q7):

- **AI view**: "Show AI text where it exists, in every chat."
- **Automatic suggestions**: "Prepare replies on their own when a message
  calls for one. The Suggestions button works either way."
- **Style**: Friendly, Professional or Idioms, each with its line (ADR 56).
- **Emotion**: "Estimate how their latest messages feel, shown after their
  name."
- **Soften hostile messages**: "Soften hostile wording, keeping every fact,
  request, deadline and threat."
- **Voice and images**: "Transcribe voice and video messages, read images,
  and write them out as text."
- **Remember this chat**: "Note what they and you care about here. What's
  noted stays when Off."

The switch is named Automatic suggestions, like the server-wide one, because
the Suggestions button stays (Q3). Not adopted: "Suggestions".

**Two levels (Q16).** The AI Menu lists AI view and the controls, each with a
› to its own page. A tap on a toggle switches it; a tap on the row opens the
page, which holds the control's explanation, its panels (Q12) and its
Guidance fields (Q17). Style's page holds its choice. Offline, the switches
wait for the server, as the profile's did. Not adopted: one long scrolling
sheet, and a single More page.

**Where each Guidance field sits (Q17).**

- Automatic suggestions: When to suggest.
- Style: Response suggestions, Friendly, Professional and Idioms, since they
  shape manual sets too.
- Emotion: Emotion extraction.
- Soften hostile messages: Soften hostile messages and Faithfulness rules.
- Voice and images: Incoming rewriting and Faithfulness rules, the same
  field, since it governs both rewrites.
- Remember this chat: Person memory and Account memory.
- A group's Group AI processing: Group briefings.

**Paused roles stay in Settings (Q18).** After repeated format failures a role
pauses for every chat. Settings → AI keeps its notice with Keep and Reset, and
a block's "Paused after repeated format failures" opens it there (ADR 45 Q8).
Not adopted: the notice in every AI Menu.

**The name (Q19).** The sheet is the **AI Menu** in both kinds of chat. It
replaces the term Conversation Menu; a message's long-press sheet stays the
Message Context Menu. Not adopted: AI Context Menu, and Conversation Menu.

**Only text without Voice and images (Q20).** While Voice and images is Off,
nothing in the chat is transcribed automatically and nothing reads its
images. The other switches see text, captions and any transcript a Show
transcript tap made: a voice note gets no emotion, a hostile voice note is
not softened, suggestions don't know what it said, and a softened rewrite of
a block with images leaves them unread. A transcript a tap makes is read by
work that runs later and starts nothing by itself. Not adopted: transcribing
short notes on the VPS for the other switches, and transcribing whatever they
need as before.

**No hidden text rewriting (Q21).** The registry's hidden `text_rewriting`
switch is removed with its code: a private text block is rewritten for
reading only by softening (ADR 54). The Processing Character Threshold
governs automatic Daily Briefings only
([ADR 35](0035-processing-character-threshold-and-empty-rewrites.md)). The
test server, fixtures and tests that turned it on move to media or softened
blocks. Not adopted: leaving it hidden and Off, and folding it into Voice and
images.

## Consequences

- A private chat starts no model work until its member turns a switch on in
  its AI Menu; turning a chat fully on takes up to five taps.
- In a chat with Voice and images Off, no voice note or image reaches a model
  unless a tap asks.
- Guidance is edited from any chat's AI Menu, each field marked as applying to
  all chats; Settings → AI keeps the server-wide numbers, switches and
  notices.
- This amends [ADR 13](0013-clean-slate-rewriting-rebuild.md), 14, 15,
  [ADR 22](0022-recipient-emotion-and-adaptive-rewriting.md), 23,
  [ADR 24](0024-coherent-message-groups-and-focused-viewing.md),
  [ADR 25](0025-emotion-response-suggestions.md),
  [ADR 28](0028-unread-separator-and-compact-conversation-header.md), 30,
  [ADR 34](0034-group-chats-and-daily-briefings.md), 35, 42,
  [ADR 44](0044-mobile-only-web-in-the-apps-design.md), 45, 46, 47, 48,
  [ADR 49](0049-useful-response-suggestions.md),
  [ADR 51](0051-telegram-phone-look-and-new-messages.md), 52, 53, 54, 55, 56,
  58 and 59 where they place the reading control, a chat's AI controls, the
  group sheet or Guidance, set those controls' defaults or describe hidden
  text rewriting.

## Open owner gates

- Per-chat Processing Guidance is a proposal ([TODO](../plans/TODO.md)).
