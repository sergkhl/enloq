---
status: accepted
---

# Members on the shared service

## Context

Enloq began as the owner's own service. Friends were to use it too, which
[ADR 38](0038-mobile-expo-client.md) Q23 and
[ADR 47](0047-pairing-settings-menus-and-profiles.md) Q14 had left open as
"Other users through the app". A public opening (store listings, a privacy
policy, payments) stays out of scope.

## Decision

**One shared service with members (Q1).** enloq.globesoul.com has a member
layer. A **Member** has their own Telegram accounts, trusted phones and
browsers, preferences, budgets and notifications, and a trusted client opens
only its member's accounts. The owner is the first member and the admin, with
the Owner Account as their home account. Not adopted: one instance per
friend.

**The owner pays (Q2).** Every live call goes through the proxy's one caller
key. Each member has their own Daily and Group Briefing Budgets, $0.50 each
by default, which only the owner sets; a member sees theirs read-only. The
owner's own budgets keep their values and range.

**The Member Agreement and the owner's rule (Q3).** Before a join request
reaches the owner, the person reads and accepts the agreement, which the
service serves so both clients show the same words. Since
[ADR 55](0055-person-and-account-memory-and-traits.md) Q27 it is a short
statement that promises no privacy and names no provider or retention:

- Enloq is unofficial and experimental, and the server holds a full session
  for each connected account, so it can read and send anything that account
  can; the two-step password passes through it at sign-in, and Telegram may
  flag the sign-in;
- anything Enloq reads may be sent to any service, kept for any time and read
  by the owner and their agents, and the member's contacts haven't agreed to
  that;
- data may be wiped, and leaving deletes what it holds.

The acceptance is stored with its date and wording version. Only changed
words make a new version; a new provider, retention or feature does not.
Until a member accepts a new version, new AI work for their accounts waits.
After approval AI processing starts On, as for the owner
([ADR 14](0014-account-ai-processing-and-reading-view.md)).

The owner's rule: "we are not going to make automatic actions that user
didn't approve". Enloq acts in a member's Telegram only when they ask, and
never sends, deletes, reacts, posts or marks anything read on its own. It
posts nothing to a member's Saved Messages on its own; a member's own send in
their Saved tab ([ADR 50](0050-icon-tabs-saved-messages-and-one-saved-build.md)
Q4, Q17) is their request.

**No special access rules (Q4).** The owner and agents have the same access
to members' data as to the owner's, and the agreement says so.

**Beta rules (Q5).** Members join under the acceptance-phase rules: app data
may be wiped without a backup and deploys and restarts happen at any time,
while Telegram sessions and spending records stay protected.

**iPhone friends wait (Q6).** They wait for the Apple Developer Program; that
account and Google Play's are out of scope. Not adopted: a Home Screen web
app.

**APKs by hand (Q7).** The owner forwards APKs by hand, with no version
check, so a deploy that changes the API can break a member's older app until
they install the newer APK.

**Everything the owner has (Q8):** several Telegram accounts each, private
chats, and groups with Daily Briefings, within their budgets.

**Leaving (Q9).** A member can leave from Settings, and the owner can remove
a member. Either way the server signs out of their Telegram accounts, forgets
their phones and browsers and deletes their data at once, keeping only
spending totals. A member whose removal has begun gains no new account.

**The VPS keeps its sudo rules (Q10).** The service's data and secrets belong
to the account the agents run as, which is also in the `docker` group, and
agents deploy the service's code, so a sudo password would keep nobody from
members' data.

**Join requests (Q11, Q19).** Anyone may sign in with Telegram, and the
connection screen always offers it. Someone the server doesn't know reads the
agreement and becomes a **Join Request**: their session sits unused, with no
sync and no AI, and their phone or browser waits. A push to the owner's
trusted phones and browsers opens **People** in Settings, where the owner
approves or declines. Declining, or seven days without a decision, signs the
session out and deletes everything; withdrawing the request's last client
withdraws it. At most five requests wait at once. Sign-ins are limited to
five an hour for each phone number and each client address, under a ceiling
of thirty an hour for the server, so a stranger's attempts can't lock the
owner out. Not
adopted: a phone-number list, a Saved Messages note, Telegram bot buttons or
replying to a note.

**A member's other phones and browsers (Q12).** Another phone pairs with Pair
a phone from one of the member's own trusted clients. A browser, or a member
with nothing trusted left, signs in with Telegram as their home account: the
sign-in only proves who they are, so the existing session stays and the new
login is signed out at once. Pairing codes and the "was paired" notes in
Saved Messages stay the owner's.

**AI settings stay the owner's (Q13, Q20).** "For simplicity for now", every
AI setting stays server-wide and only the owner changes it: Guidance, the
Processing Character Threshold, the Memory and Suggestions switches and the
Concurrency Limit. Hide Bots, the reading view and the tone are each
member's, since a shared switch would flip the owner's too. Per-chat choices
in a Correspondent Profile (Calm this chat, Remember this chat, Suggestions)
belong to that chat's member. A member's Settings shows Notifications, Budget
(read-only) and Privacy, with Leave; the owner's adds AI and People.

**Fair turns (Q14).** Members take turns within each class of model work,
and for transcription.

**One timezone (Q15).** The server keeps one configured timezone.

**Pseudonyms (Q16).** Each member has their own provider pseudonym, which
carries nothing about the member; the owner keeps theirs.

**The web too (Q17).** Members use the web app as well as the Android app.

**Q18:** delivery scope; it required an independent review of the boundary
between members before the owner approves anyone.

## Consequences

- A member's correspondence reaches only that member's phones and browsers,
  but the owner and agents on the VPS can read all of it, and every later
  feature has to stay per member.
- Anyone can make the server ask Telegram to send login codes to any number,
  within the sign-in limits above, and strangers' sessions sit on the server
  until the owner declines them or a week passes.
- Every member's sessions share one Telegram API ID, so one member's misuse
  can cut everyone off (Telegram's API terms, 4.2).
- Trusted clients ([ADR 11](0011-durable-browser-trust.md), ADR 38) open
  their member's accounts only. The Daily Budget
  ([ADR 15](0015-owner-controlled-processing-memory-and-prompts.md)) and
  Group Briefing Budget ([ADR 34](0034-group-chats-and-daily-briefings.md))
  are per member and owner-set; the push destination
  ([ADR 20](0020-minimal-reading-and-ready-notifications.md)), Hide Bots, the
  reading view and the tone are per member. ADR 47 Q13's refusal of a sign-in
  while the Owner Account is signed in, and Q14's open proposal, end.
- Model routes, session ownership and the beta rules are otherwise
  unchanged.

## Open owner gates

Sign-ins, the agreement, Join Requests, approvals, leaving and removal are
proven against the fixture's synthetic logins only; nobody else uses the live
service yet. Forwarding the APK to a friend, the friend's real sign-in and
request, the push on the owner's phone and their approval in People are the
owner's. The iPhone gets members with its next build on the Mac.
