---
status: accepted
---

# Deployment and development on the shared VPS

## Context

The owner's shared VPS already runs the LiteLLM proxy and its speech service
([ADR 39](0039-litellm-proxy-and-vps-speech.md)), behind a shared Caddy edge.
Enloq runs there as a public service, and development happens there too. This
replaces [ADR 37](0037-vps-hosting-and-remote-client-trust.md)'s dedicated VPS
with its own Caddy container (its Q14, Q17, Q19 and Q21).

## Decision

**Host.** The service runs on the shared VPS reached as `ssh lrnki-vps`
(Debian 13, x86_64, 6 vCPU, 23 GiB RAM). The shared Caddy edge owns ports 80
and 443, terminates TLS with Let's Encrypt and proxies `enloq.globesoul.com`
to `enloq:8765` on the Docker network named by `EDGE_NETWORK` in the service
folder's `.env`. No port is published and no Caddy container of Enloq's own
exists. The box's baseline (SSH keys only, no root login, a firewall admitting
SSH, HTTP/HTTPS and WireGuard's UDP port, unattended upgrades, capped Docker
logs) meets ADR 37 Q19 except for its rate limits, replaced by Q6.

**Hostname (Q1).** `enloq.globesoul.com`, a DNS-only A record managed with the
`cf` CLI.

**Scope (Q2).** The VPS image carries ffmpeg and ffprobe and no Whisper
runtime, since speech runs through the proxy's route on the same box (ADR 39).
Phone push and device credentials belong to
[ADR 38](0038-mobile-expo-client.md).

**Development (Q3).** GitHub's `sergkhl/enloq` (public, AGPL-3.0) is the shared
remote of two checkouts:

- **The VPS,** `~/pi-workspace/enloq`: backend, web, documentation, checks
  and service operations. It also builds Android, the release app and Enloq
  Dev alike, and runs Enloq Dev's whole loop, journeys included, against the
  Android phone over WireGuard. Its native tools live in the box account's
  home, and the host joins the VPS's wg-easy network as its own VPN-only
  client. wg-easy client keys are private: they are never printed or
  committed, and the host's copy lives only in the root-only
  `/etc/wireguard/enloq.conf`.
- **The Mac,** `~/_DEV/enloq`: native builds, the iPhone's always (Xcode is
  macOS-only), with the iOS simulator journeys.

Work edited on the Mac is pushed to `origin/main`, then checked and deployed
from the VPS checkout with `./scripts/check` and `./scripts/deploy`. Until the
production declaration, those pushes and deploys need no further confirmation.

**Repository (Q4).** The VPS reaches `sergkhl/enloq` through its
`github-sergkhl` SSH alias.

**Service folder (Q5).** The live service runs from
`~/pi-workspace/enloq-service`, separate from the development checkout, as
compose project and container `enloq`. Its private `data/`, `config/` and
`secrets/` (0700/0600) are bind-mounted at the image's `.local`, `config` and
`secrets`. `./scripts/deploy`, run on the VPS, builds only a clean committed
`HEAD` (`git archive`) into an image `enloq:<sha>` labelled with the commit,
restores the previous image when the new one is unhealthy and offers
`--rollback`. A finite live check that needs the owner lock runs as a one-off
container from the service image while the service is stopped.

**Rate limits (Q6).** Stock Caddy has no rate-limit module, so the service
limits itself: 30 refused API requests (an unauthenticated 401, or a refused
public pairing or sign-in request) from one client address within ten minutes
return 429 until the window passes. The client address comes from
`X-Forwarded-For` only when the peer resolves to the configured edge
(`ENLOQ_TRUSTED_PROXY`).

**Proxy key (Q7).** The VPS service has its own LiteLLM caller key,
`reply-assistant-vps`, mirroring the `local` caller's models, route
restriction and request limit. The proxy's provisioning changes only `local`,
so after it the key's model list is updated to match
([operations](../operations.md#vps-service)). `./scripts/setup proxy-key`
places a key by hand.

**The Mac fallback (Q8).** The Mac instance stays under MacHelm (label
`com.quantumcraft.enloq`), stopped, with Start at login off; starting it by
hand is the fallback. This settles ADR 37 Q18's "AI off or service stopped"
choice.

**Standing authority (Q9).** The owner's standing authorization to start,
stop, restart and upgrade the development service and to run finite
acceptance checks covers the VPS service until the production declaration,
under the same model, target, budget and privacy bounds. "Delivered" means
deployed to the VPS service. The shared-VPS rules allow editing this
service's own edge block and restarting the edge.

**Tools (Q10).** ffmpeg and Playwright's Chromium system libraries are
installed on the VPS host with apt; the phone-size journeys and live checks
run in Chromium ([ADR 44](0044-mobile-only-web-in-the-apps-design.md) Q8).

**Agent setup (Q11).** The VPS carries the same agent skills as the Mac,
shared by every agent profile on the box.

**Credentials.** The Telegram API pair, the proxy key and the FCM key live in
the service folder's `secrets/`. Telegram sessions never move between hosts:
each account signs in on the VPS.

## Mechanics

- `config/app.env` sets `ENLOQ_LISTEN_HOST=0.0.0.0`,
  `ENLOQ_PUBLIC_ORIGIN=https://enloq.globesoul.com` and
  `ENLOQ_TRUSTED_PROXY`. The loader refuses a non-loopback bind without an
  exact `https://` origin, so the Mac instance keeps
  [ADR 11](0011-durable-browser-trust.md)'s loopback rules.
- Host and Origin must match the public origin exactly. Trust cookies are
  `Secure` and responses carry HSTS. Browser labels name the platform
  (`Safari on an iPhone`). The Web Push VAPID subject is the public origin.
- Nothing pairs the VPS from its terminal: clients pair on their connection
  screen ([ADR 47](0047-pairing-settings-menus-and-profiles.md) Q1, Q11).
- Media tools are fixed paths: Homebrew's on the Mac, `/usr/bin` elsewhere.

## Consequences

- The Telegram sessions and caches share a host and Docker daemon with the
  owner's other projects, and with agent CLIs running under the same account,
  which has passwordless sudo. A compromise of any of them could expose the
  sessions; ADR 37's credential, revocation and hardening mitigations remain.
- An edge restart briefly interrupts every site, this one included; event
  streams reconnect.
- The service and the proxy share one host, so a VPS outage stops both. ADR
  39's hosted speech fallback covers only the speech service failing alone.
