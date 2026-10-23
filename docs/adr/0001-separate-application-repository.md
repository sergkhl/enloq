---
status: accepted
---

# A separate application repository

## Context

Enloq is a Telegram client application of its own. It started beside the
author's earlier private support-relay project, but it has its own product,
runtime and plans, so it does not belong in that project's repository.

## Decision

Enloq is its own public repository, `sergkhl/enloq`, under the GNU Affero
General Public License v3.0. It was published on 2026-10-23 without its
earlier history. [ADR 40](0040-shared-vps-deployment-and-development.md)
describes its development checkouts and remote.

A few transport, media and logging mechanics were adapted from the earlier
project; [extraction](../extraction.md) records what each module took and
retains. Nothing from that project is a runtime dependency, and Enloq never
copies or borrows another program's Telegram session
([ADR 6](0006-local-service-and-isolated-test-transport.md)).

## Consequences

- Every tracked file is public. Credentials, Telegram account IDs, real
  correspondence and other people's names stay out of the repository; live
  check targets live in the untracked `.local/live-targets.json`.
- Plans and design records describe the current state; the history before
  publication is not part of the repository.
