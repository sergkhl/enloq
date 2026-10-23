---
status: accepted
---

# Minimal reading controls and notifications when messages are ready

## Context

Duplicate original areas, completion badges and alerts at every arrival and
completion made reading noisy. The owner wants one readable rendering per
message and one useful alert when new messages are ready to read.

## Decision

### Reading

**One rendering per bubble.** A bubble shows either its AI rendering or its
originals. The global Reading View sets the mode
([ADR 14](0014-account-ai-processing-and-reading-view.md)); a message's
long-press sheet offers Show original or Show AI text for that bubble alone,
with the robot icon and never sparkles
([ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md)). A local
choice starts no model work. It, and a chosen earlier version, last while the
chat stays open, across refreshes and arriving output; reopening or reloading
the chat, or changing the global view, resets them. A text block that isn't
calmed has no AI text, so AI reading shows its originals together
([ADR 54](0054-incoming-text-combined-not-rewritten.md)).

**Content in both views.** Attachments show once in either view. Originals
show each source in native order with its own time and details; the AI view
shows the combined rendering, headed with its source count ("1 message", "3
messages"), and those sources' attachments
([ADR 27](0027-header-message-menu-forwarding-details-and-voice-playback.md)).
Raw transcripts, Reply and unavailable-media notes stay reachable. There are
no Rewritten or Unchanged badges, while streaming, waiting, failure and
incomplete states stay visible, and nothing retries an unchanged completed
result automatically. Native message IDs are not shown.

**Versions and edits.** In Originals, Previous and Next version arrows with a
counter such as "2/3" replace one source's displayed text and cached media
together; they never edit Telegram or browse AI attempts. A passive pencil in
the footer marks a Telegram edit, also on a combined AI message; it opens
nothing, and when no earlier version was captured its name says so and no
arrows show. AI rewriting is not an edit. A deleted message shows its last
captured version with a trash icon where the pencil goes (ADR 52).

**Attachment identity.** Adding, removing or replacing an attachment changes
content even when the caption stays the same, so a stable native attachment
identity is kept, telling apart a different photo or document with matching
safe details. Consecutive versions collapse only when their text, link
destinations and attachment identities are equal; a timestamp change alone
adds no step. Older image snapshots without an identity stay uncertain and are
kept, never assumed equal. The pencil reflects Telegram's edit evidence,
independently of how many versions were captured.

**Forwarded and imported lines.** A forwarded original, in either direction,
shows a line above its content: "Forwarded from" the source name Telegram
supplied, with a channel signature and the original date and time when known,
or "Forwarded message" without a name; an imported message shows "Imported"
(ADR 27). Lines are not tappable. Names come only from the forward header or
entities Telegram returned; no hidden source is resolved and no forward chain
followed. When a chat opens or loads more originals, cached messages missing
their forward details are re-read by exact ID through the account's session,
for metadata only: no other history, content, versions, AI work, downloads or
invalidation follow, and a failure leaves the details unknown for a later try.

### Notifications

**When.** New incoming messages in private chats of a member's accounts are
eligible, respecting Telegram's mute; groups never alert. A burst is an
account and chat's incoming messages until 30 quiet seconds, independently of
Incoming Block boundaries. It notifies once its content is ready: its
validated rewrite when one is made, otherwise its originals once any decision
it waits for has published
([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)), or the
original fallback once AI Off, a budget wait, a failure or nothing to analyse
is known. Never at native arrival or the first streamed word. A later burst
may notify again; edits, deletions, old catch-up, explicit retries and
earlier-message runs, outgoing rewrites, refreshes, restarts and reaction-only
changes never do. New sources supersede stale candidates, and notification
work starts no model call.

**Where.** A chat open in an active client of the member alerts nothing. A
browser showing another chat plays one chime. Otherwise push goes to the
member's destinations:

- **One browser**, the one that most recently turned notifications on; it
  never moves to another browser implicitly.
- **Each trusted phone** with notifications on: Android through Firebase Cloud
  Messaging; the iPhone waits for the Apple Developer Program
  ([ADR 38](0038-mobile-expo-client.md)).

Each event uses one channel per destination, deduplicated across tabs,
browsers and phones, never a chime doubled by a system sound.

**What a notification carries.** The push payload carries only an opaque
event ID, expiry and silent flag; the client fetches the details from the
service and builds the banner. It shows the sender, the account and a short
preview: the latest validated rewrite or the current original wording, the
count with the newest preview for several, and a typed label such as Image or
Voice message for textless media. No thumbnails, unfinished text or extra
summary. Tapping opens the exact account and chat route
([ADR 36](0036-account-and-chat-hash-routes.md)) under the usual draft guards.
Previews are visible on the system's notification surface, subject to its
settings.

**Expiry and eligibility.** An undelivered notification expires after five
minutes, and only the latest pending one per account and chat is kept. Source,
account and client eligibility are checked before dispatch; revoking a client
or logging an account out or removing it stops its notifications. Push
subscriptions and tokens are private and tied to the client's trust.

**The owner's other pushes.** A Join Request
([ADR 48](0048-members-on-the-shared-service.md)) and a decision-model outage
([ADR 49](0049-useful-response-suggestions.md)) each send one opaque push to
the owner's destinations; neither concerns a chat or carries message content.

**Controls.** Settings → Notifications on each browser or phone has
Notifications on or off, Sound and Send a test notification
([ADR 47](0047-pairing-settings-menus-and-profiles.md)); each client asks once
per pairing. The web's chime is one bundled sound; Sound Off silences it and
requests silent push. Phones use their own notification sound.
[Operations](../operations.md#desktop-web-push) owns the browser
prerequisites, such as Brave's Google push messaging option.

## Consequences

- One readable rendering and one alert per burst replace stacked copies and
  arrival or completion noise.
- Attachment identity keeps real media edits from being mistaken for
  duplicates; uncertain older history is kept rather than destroyed.
- Push services carry no message text, even encrypted; the banner the client
  builds does show the preview.
