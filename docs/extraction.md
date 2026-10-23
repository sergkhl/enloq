# Code provenance

Three modules and one test file were adapted from hercules-support-relay, the
author's earlier private project, at its commit `8f455b1`. The two transport
modules and the test file say so in their first line. Tests check their
behavior, never source hashes.

| App code | Origin | What it retains |
| --- | --- | --- |
| `backend/src/enloq/transport/mtproto.py` | `channel-gateway/src/channel_gateway/mtproto.py` | Request pacing, durable waits, bounded retries, unchanged random IDs, one reconnect owner, a private wrapper of the SDK's send queue and the per-request send hand-off witness ([ADR 17](adr/0017-send-hand-off-boundary.md)). Telethon stays pinned to 1.44.0. Enloq's logger and context names replace the source's. |
| `backend/src/enloq/transport/media.py` | `channel-gateway/src/channel_gateway/media.py` | Private temporary leases, orphan cleanup, byte sniffing and bounded image dimensions, with the source's narrower 5 MiB and 4096 px bounds inside the app's own ceilings. The adapted part uses the standard library only; Enloq's additions in the same file use Pillow. |
| `backend/src/enloq/logs.py` | `channel-gateway/src/channel_gateway/logs.py` (a design adaptation) | Quieting sensitive libraries. Enloq goes further: only predefined event codes from `enloq.*` loggers become log text, with safe metadata (UTC time, level, logger name and exception class name); message bodies, exception messages, tracebacks and arguments never do. |
| `backend/tests/test_transport.py` | the source's `test_mtproto.py` | Its request-behavior cases, to which Enloq adds account identity, exclusive session ownership, hand-off, reconnect and scan and gap tests. |

Everything else is Enloq's own: storage, domain identities, budget accounting,
the deterministic fake transport and every product feature. Nothing from the
earlier project is a runtime dependency, and Enloq never uses another
program's Telegram session ([ADR 6](adr/0006-local-service-and-isolated-test-transport.md)).

## Minimal rebuild integration

The rebuilt conversation store and rewrite coordinators keep the transport,
media, subprocess, account and browser-trust boundaries above. Per-account
background tasks share bounded model slots, and one slow transcription does
not block another account's text preparation. [Inference](inference.md) owns
the current bounds and safeguards.
