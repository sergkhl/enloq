---
status: accepted
---

# Account and chat hash routes

## Context

Enloq's web app needs addresses that name an exact account and chat, in the
style of Telegram Web A, so bookmarks, notification clicks and Back and
Forward land where they should. An address is a locator, never an
authorization: browser trust and the exact account's sign-in stay required,
and a link must never silently open a different account or chat.

## Decision

### Destinations and identity

**Scope (Q1).** Routes cover private chats, supported basic groups and
ordinary supergroups, plus each account's Private, Groups and Saved lists.
Individual message positions, Settings, memory, invites, forums and
broadcasts are outside them.

**Account (Q2).** The `account` query is the account's stable Telegram user
ID, and every generated chat link includes it. There are no numbered account
slots: the stable identity keeps a bookmark on the same account through
logout, sign-in again and list reordering.

**Peer fragment (Q3).** The fragment uses Telegram Web A's signed numeric peer
convention, mapped reversibly to the typed native identity:

| Native conversation | Internal peer | URL fragment |
| --- | --- | --- |
| Private user with ID `n` | `user:n` | `#n` |
| Basic group with ID `n` | `chat:n` | `#-n` |
| Ordinary supergroup with ID `n` | `channel:n` | `#-(1000000000000 + n)` |

The supergroup row is arithmetic, not literal syntax: with authored IDs,
`channel:42` becomes `#-1000000000042`, and a private-chat example is
`/?account=123456789#42`. Storage and API identities stay typed and scoped to
their account, and the fragment round-trips the peer type as well as its ID.

### Navigation and availability

**History (Q4).** Deliberate account, tab and chat navigation adds
browser-history entries. Back and Forward revisit destinations through the
same exact-target, draft-save and action guards as ordinary navigation.
Startup restoration, URL normalization and background refreshes add none.

**Eligibility (Q5).** A chat link opens any of the account's private chats
([ADR 51](0051-telegram-phone-look-and-new-messages.md) Q17). A chat that is
not recent shows its history read from Telegram as originals only, gets no AI
on its old messages and stays out of the Private list until a message is sent
or received there. Group links open only groups in the seven-day Groups list
and existing Retained group work, with the retained groups' cache-only and
native-access limits. Links back-fill no history beyond that, admit no other
conversation types and expand no AI eligibility.

**Exact unavailable target (Q6).** A malformed, unknown, signed-out or
otherwise unavailable destination keeps its requested URL and shows the
matching invalid-link, unavailable or sign-in state. Another account or chat
is never substituted, including when authorization or eligibility disappears
after opening, and remembered-selection fallback never retargets an explicit
link.

**Copy chat link (Q7).** The Correspondent Profile and the Group Profile
offer Copy chat link ([ADR 47](0047-pairing-settings-menus-and-profiles.md)
Q8), copying the canonical app URL of that exact account and chat. It copies
no message content and changes no preference or processing.

### Startup and list routes

**Explicit intent and authorization (Q8).** An explicit URL takes precedence
over saved account and tab choices. Its target survives browser pairing and
the exact account's sign-in, and opens after validation if it is still the
current intent; signing in another account does not retarget it. Only
successful navigation updates the browser's Remembered Account and the
account's remembered tab. Failed or cancelled navigation does not, and other
open tabs stay independent. A newer deliberate navigation supersedes a pending
sign-in or lookup (Q14).

**Lists and restoration (Q9).** A list URL has no chat fragment:

| URL, with authored account IDs | Meaning |
| --- | --- |
| `/` | Ordinary startup: the Remembered Account's list. |
| `/?account=123456789` | This exact authorized account's list, on its remembered tab. |
| `/?account=123456789&tab=private` | This account's Private list. |
| `/?account=123456789&tab=groups` | This account's Groups list. |
| `/?account=123456789&tab=saved` | This account's Saved Messages. |

No list opens a chat by itself; the app does not remember a chat per tab
([ADR 44](0044-mobile-only-web-in-the-apps-design.md)). A chat fragment
decides its tab, and generated chat URLs omit `tab`. After restoration or
canonicalization the current history entry is replaced with the canonical
destination. An unavailable named account never falls back to another (Q6).

**Account-less chat fragment (Q10).** A fragment without `account` keeps its
exact peer and asks the owner to choose an account, then forms the exact
route. It is never bound silently to the current or remembered account, and
the same authorization, eligibility, preference and supersession rules apply.

### Leaving a conversation and refusals

**Back to the list (Q11).** Back in a conversation goes to the current
account's list route through the draft guard and adds a history entry,
stopping playback and clearing message selection. Browser Back and Forward
follow actual prior destinations; a newly opened external link invents no
predecessor, so browser Back may leave the app. Before-unload protection
stays when leaving the document.

**Refusal (Q12).** If sending, attaching, Undo or an exact draft-save failure
blocks a URL, history or list change, the current destination and its exact
draft stay, and the prior committed URL and history position are restored
with the existing error. An unrelated history entry is never altered to fake
the current destination, and a blocked navigation is neither queued nor
replayed later; the owner tries again, and stale work cannot roll back newer
intent. A destination chosen while the browser is still returning from a
refused Back, Forward or fragment edit is added after that return, so the
entry order does not depend on browser timing.

**Position (Q13).** Opening a conversation, including by Back or Forward from
another one, uses the ordinary opening rule: the first ready unseen message,
otherwise the latest. Leaving closes the conversation, so no position is kept
under the list (ADR 44), and there are no persisted scroll snapshots or
message-position fields.

### Notifications and competing navigation

**Latest intent (Q14).** The newest deliberate notification, URL, history or
UI navigation wins. Every asynchronous lookup, guard or sign-in continuation
checks that it still owns the navigation before publishing a destination or
writing preferences; late results and old cleanup cannot replace a newer
route or opaque locator.

A notification click uses the exact canonical chat URL: an existing window
adds an entry, and a newly opened window replaces its temporary opaque
notification URL. At startup the notification locator takes precedence over
other route state. A failed activation keeps the prior destination; a startup
failure shows unavailable instead of opening another conversation. Push
payloads stay opaque, and pairing and notification cleanup preserve the
current route and its history bookkeeping. Test notifications keep their
focus-only behavior. [ADR 20](0020-minimal-reading-and-ready-notifications.md)'s
five-minute rule for undelivered alerts adds no age limit to clicking a
delivered one, and every real target is checked against current
authorization and eligibility.

The Enloq app uses the same identities: a notification tap opens its exact
chat, and Copy chat link copies the canonical web URL.
[Operations](../operations.md#accounts-and-browser-trust) describes the
address forms and their behavior.

## Consequences

- Links are stable across sign-ins and list order, and refuse rather than
  guess when their exact target is unavailable.
- A private chat link can open a chat older than seven days, which then reads
  as originals only.
- Browser-history handling needs care around refusals and asynchronous
  sign-in, which the latest-intent rule keeps consistent.

## Questions

- Q1: private chats, basic groups, ordinary supergroups and the account's
  lists.
- Q2: `account` is the stable Telegram user ID; a locator, not authorization.
- Q3: Telegram Web A's signed numeric peer fragment, round-tripping the type.
- Q4: deliberate navigation adds history entries through the usual guards.
- Q5: chat links open any private chat of the account (ADR 51 Q17); groups
  need the Groups list or Retained group work.
- Q6: an unavailable exact target keeps its URL and is never substituted.
- Q7: Copy chat link lives in the profiles (ADR 47 Q8).
- Q8: explicit URLs win; only successful navigation updates remembered
  choices.
- Q9: list URLs with `tab=private|groups|saved`; no remembered chat per tab.
- Q10: a fragment without an account asks which account.
- Q11: Back goes to the list route with a history entry.
- Q12: a refused navigation restores the prior URL and history.
- Q13: opening uses the first-ready-unseen or latest rule; no kept position.
- Q14: the latest deliberate intent wins; notification clicks use exact
  canonical URLs.
