---
status: accepted
---

# Multiple personal accounts

## Context

One person often uses several Telegram accounts, and other people on the
shared service ([ADR 48](0048-members-on-the-shared-service.md)) have their own.
Each account needs its own Telegram authorization and correspondence, while
spending and capacity stay bounded.

## Decision

**Accounts per member.** A Member, the owner included, may sign in several
Telegram accounts; the first is their home account, and the owner's is the
Owner Account. Each account has its own Telegram session created by the
service's login, its own store, drafts and runtime, and every signed-in
account synchronizes. No session is ever copied
([ADR 6](0006-local-service-and-isolated-test-transport.md)).

**Choosing an account.** Each client tab or app chooses its current account
in the account sheet, and every content API and route names its account
explicitly ([ADR 36](0036-account-and-chat-hash-routes.md)). A browser
remembers the last account it opened successfully for an ordinary reopening;
already-open tabs keep their own choice, and an account no longer authorized
falls back to the first authorized one or to sign-in
([ADR 24](0024-coherent-message-groups-and-focused-viewing.md)). There is no
combined inbox.

**Manage accounts.** Both clients offer Add account (a QR code for Telegram on
another device, or phone number, login code and two-step password), Sign in
again, Reconnect, Log out and Remove local data
([ADR 43](0043-installable-android-builds-and-phone-setup.md)), and each
account's AI processing switch
([ADR 14](0014-account-ai-processing-and-reading-view.md)). Login secrets are
never logged or kept in browser storage.

**Log out and removal.** Log out revokes only Enloq's authorization, hides the
account's content and keeps its local work under the normal retention;
signing the same Telegram user in again restores it. Remove local data is a
separate, confirmed action. It cannot erase a send whose outcome is
unresolved, and it never resets spending records.

**Shared limits.** A member's accounts share that member's Daily Budget and
Group Briefing Budget
([ADR 15](0015-owner-controlled-processing-memory-and-prompts.md),
[ADR 34](0034-group-chats-and-daily-briefings.md)). One Concurrency Limit
covers every account and member, and transcription runs one note at a time.
Unknown outcomes and attempt identities survive logout, removal and restarts.

**Out of scope.** Creating or deleting Telegram accounts and managing the
account's other Telegram devices.

## Consequences

- Adding an account adds sync and storage, never extra budget or capacity.
- A new account starts with AI processing On, so its recent private chats are
  processed within the member's budget.
