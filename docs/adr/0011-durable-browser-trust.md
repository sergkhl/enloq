---
status: accepted
---

# One-time Mac approval and durable browser trust

## Context

A trusted client can read and write every account of its member, so trust
has to be granted deliberately, once, and be revocable at once. Telegram
authorization is a separate matter: trust opens Enloq, and each Telegram
account keeps its own login ([ADR 10](0010-multiple-personal-accounts.md)).

## Decision

**Durable trust.** Pairing gives a browser a fresh random 256-bit credential
in a host-only, HttpOnly, SameSite=Strict cookie scoped to `/api`, Secure on
a public origin. A phone's credential is kept in the Keychain or Keystore and
sent as a bearer header ([ADR 38](0038-mobile-expo-client.md)). Trust records
and credential hashes live in the private `browsers.sqlite3`; credentials are
never stored in clear text. Trust has no app-imposed expiry: the 400-day
cookie is renewed on visits, on focus and daily while open, and only browser
expiry, eviction or cleared data require pairing again.

**Pairing attempts.** Every pairing is one two-minute attempt. Approval
targets the exact claiming client, completion requires that client's original
temporary HttpOnly cookie or proof, and it consumes the attempt. Holding a
code or link is not approval. Cancellation, expiry and a service restart end
unfinished attempts. A browser's requests carry its own Origin at every step,
checked against the exact origin the service serves.

**Who approves.** On the VPS, with its public origin:

- **A code in Saved Messages.** Connect asks the service to post a six-digit
  code to the Owner Account's Saved Messages; typing it trusts the browser or
  phone ([ADR 43](0043-installable-android-builds-and-phone-setup.md),
  [ADR 44](0044-mobile-only-web-in-the-apps-design.md),
  [ADR 47](0047-pairing-settings-menus-and-profiles.md)).
- **Pair a phone.** A trusted phone or browser shows a QR code that only the
  Enloq app claims; Trust there finishes it (ADR 47).
- **Sign in with Telegram.** On the connection screen, the account Telegram
  verifies decides: the Owner Account signs back in, a member's home account
  identifies the member, and an unknown account becomes a Join Request
  ([ADR 48](0048-members-on-the-shared-service.md)).

A public origin refuses pairings started with the host's control credential,
so nothing pairs over SSH. On the host, a coding agent's browser may read the
posted code back from Saved Messages to trust itself (ADR 43).

On the Mac instance, served on loopback HTTP with exact Host and Origin
checks, the launcher (`./scripts/open`) reads the private control credential
without exposing it to the browser, URLs, process arguments or logs, opens the
attempt's locator and shows a native dialog with the code; Trust browser
approves that exact browser.

**Members.** Each trusted browser or phone belongs to one Member and opens
only that member's accounts (ADR 48).

**Forget and Disconnect.** Settings → Privacy lists the member's trusted
browsers and phones with Forget. Revocation blocks later requests at once,
closes the client's event streams, retires its push destination, cancels an
unfinished Telegram login it owns and hides correspondence in its open tabs,
while their local draft recovery stays. Accounts, background work, results
and send reconciliation are untouched. Disconnect revokes the client's own
credential (ADR 43). Responses to a revoked browser clear its HTTP cache
([ADR 16](0016-persistent-media-cache-and-non-ai-media.md)).

The design follows [OWASP's session renewal guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#renew-the-session-id-after-any-privilege-level-change)
at authentication, and the cookie lifetime fits the documented
[Chrome expiry limit](https://developer.chrome.com/blog/cookie-max-age-expires).

## Consequences

- Trust is an access boundary for the service, not storage encryption or
  protection against software already acting as the user on a client.
- Whoever can read the Owner Account's Saved Messages can trust a client; the
  trusted list with Forget and the "was paired" notes there are the check.
- With the Owner Account signed out and no trusted client left, Sign in with
  Telegram is the way back in.
