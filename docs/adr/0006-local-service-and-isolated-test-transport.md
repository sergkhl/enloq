---
status: accepted
---

# A local service with independent runtime and an isolated acceptance adapter

## Context

Enloq has to keep working while no client is open, reuse proven Telegram
transport mechanics without the support domain they came from, and be tested
without touching real correspondence.

## Decision

**Stack.** The service is Python 3.13 with Telethon (pinned to 1.44.0) and
FastAPI, with private SQLite storage. The web client is React and TypeScript;
the phone client is an Expo app
([ADR 38](0038-mobile-expo-client.md)). The service runs whether or not a
client is open. It runs on the VPS under Docker Compose
([ADR 37](0037-vps-hosting-and-remote-client-trust.md),
[ADR 40](0040-shared-vps-deployment-and-development.md)); the Mac instance,
under MacHelm, is the stopped fallback.

**Independent runtime.** Each Telegram account has a session the service
created through its own login. Enloq never copies or borrows another
program's session and never opens two clients against one authorization file.
It needs no other project's checkout or service to run.

**Extracted mechanics.** Request pacing, media validation and logging
mechanics adapted from the author's earlier private project are recorded with
their origins in [extraction](../extraction.md) and covered by focused
behavior tests. Tests check behavior, never source hashes or prompt wording.

**Isolated tests.** Offline mode runs the same service against a
deterministic fake transport and an authored fixture server; it constructs no
live model adapter. `./scripts/check` runs the backend tests, the web's
phone-size journeys and the app's Jest cases and typecheck. Fixtures are
authored: real message bodies and images never become fixtures.

**Live checks.** Delivery through the real paths is checked only between the
Owner Account and Maya, the test account. Every other chat stays out of tests:
nothing is sent there, and nobody else's messages become test content. Test
messages stay recognisable as tests. Live checks run in a browser on the VPS
first, and on the Android test phone, signed in as Maya only, when a check
needs the phone. They read their origin and account IDs from the untracked
`.local/live-targets.json`; the [operations runbook](../operations.md) has the
procedure.

## Consequences

- The service's behavior is the same offline and live; only the transport and
  model adapters change.
- A live check can reach exactly two accounts, both the owner's.
- Telethon upgrades need the hand-off proofs of
  [ADR 17](0017-send-hand-off-boundary.md) re-verified first.
