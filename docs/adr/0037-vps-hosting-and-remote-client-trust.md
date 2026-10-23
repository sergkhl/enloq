---
status: accepted
---

# Hosting on a VPS with remote client trust

## Context

The service first ran on the owner's Mac behind a loopback-only browser UI.
A phone client ([ADR 38](0038-mobile-expo-client.md)) needs a backend that
is reachable without the Mac being awake or tunnelled, so the service itself
moves to a server, and the web app and the Enloq app both become remote
clients of it. The owner's direction is to self-host where possible. The
decisions here come from the same interview as ADR 38 and share its
numbering: this ADR holds Q2, Q7, Q14-Q19 and Q21.
[ADR 40](0040-shared-vps-deployment-and-development.md) places them on the
owner's shared VPS.

## Decision

**Service host (Q2, Q7).** The backend, its Telegram sessions, caches, drafts,
memory, model calls and push sending run on an owner-managed VPS, the shared
`lrnki-vps` (ADR 40), answering at `enloq.globesoul.com`. There is no
Mac-to-phone tunnel and no dependence on the Mac being awake. The model
routes, budgets, provider terms, session-owner and privacy rules are the same
as on the Mac.

**Exposure (Q14).** The service is reached over public HTTPS on the owner's
domain. A Caddy edge (the VPS's shared `lrnki-caddy`, ADR 40) terminates TLS
with Let's Encrypt certificates and proxies to the service, which accepts
only the configured public origin in its Host and Origin checks, sets its
trust cookies as Secure and sends HSTS. From the internet only SSH, HTTP,
HTTPS and the VPS's WireGuard port are reachable. Access is gated by trust
credentials alone, so the sign-in surface is public. Making the service
reachable only over WireGuard was not adopted: phone push detail fetches
would depend on an on-demand tunnel, and certificates would need DNS-01 or
transport exceptions.

**Pairing (Q15).** The host approves no pairings: a service with a public
origin refuses pairings started with the control credential
([ADR 47](0047-pairing-settings-menus-and-profiles.md) Q1). A client becomes
trusted in one of three ways: a trusted phone or browser shows a QR code from
Pair a phone and approves the new phone after comparing six-digit codes; the
connection screen's Connect asks the service to post a code to the Owner
Account's Saved Messages ([ADR 43](0043-installable-android-builds-and-phone-setup.md)
Q8); or Sign in with Telegram proves the account (ADR 47 Q11,
[ADR 48](0048-members-on-the-shared-service.md) Q11). The two-minute attempt,
proof binding, fresh random credential, consumption and cancellation rules of
[ADR 11](0011-durable-browser-trust.md) hold for each. The control credential
stays for host commands such as Saved Builds; one of them reads back the
newest posted code, so a browser running on the VPS can trust itself through
Connect (ADR 43). The Mac instance keeps its own launcher.

**Speech-to-text (Q16).** MLX Whisper runs only on Apple silicon, so the VPS
transcribes with faster-whisper (CTranslate2) and an int8 conversion of
Whisper large-v3-turbo on its CPU, with automatic denoising off. It is reached
as the LiteLLM proxy's `whisper-large-v3-turbo` route on the same VPS, where
audio reaches no model provider; notes over two minutes, and notes that route
cannot answer, go to a paid hosted fallback
([ADR 39](0039-litellm-proxy-and-vps-speech.md)). The Mac instance keeps
`mlx-community/whisper-large-v3-turbo` for offline fixtures and its own
checks.

**Deployment (Q17).** Docker Compose on Debian stable. The service is its own
compose project and container, `enloq`, in `~/pi-workspace/enloq-service`,
with the private data directory and credential references bind-mounted under
the 0700/0600 layout and restarted by Docker's policy. It joins the edge's
Docker network, named by `EDGE_NETWORK` in that folder's `.env` (ADR 40). Its bind address is
configurable for the compose network, replacing the loopback-only guard, and
the exact-origin checks above replace the loopback Host rule.
`./scripts/deploy`, run on the VPS, deploys a clean committed `HEAD`. Bare
systemd units were not adopted. MacHelm and launchd rules apply to the Mac
instance only.

**Cutover (Q18).** The VPS started as a fresh installation: no content,
memory, drafts or spending records moved, so budget counting started from
zero there. Telegram sessions never change hosts; accounts sign in again on
the VPS. The Mac service is stopped, with Start at login off, so the same
accounts are not processed and billed twice; its data stays untouched as the
fallback. Moving the data directory was not adopted.

**Hardening and backups (Q19).** SSH keys only, a firewall allowing only the
ports above, unattended security updates, the service's own limit on refused
requests ([ADR 40](0040-shared-vps-deployment-and-development.md) Q6) in
place of edge rate limits, and no message bodies in logs. No backups until
the owner declares production; after that, a nightly encrypted copy of the
data directory to a place the owner chooses.

**Mac instance (Q21).** The Mac service stays installed under MacHelm as the
stopped fallback until the owner declares the VPS live. Development happens
on the VPS, except iPhone builds, which need the Mac (ADR 40 Q3). On that
declaration the Mac service is retired and
its data removed under the acceptance-phase cleanup permission.

## Consequences

- Telegram sessions and caches live on a rented host behind a public sign-in
  surface. A VPS compromise exposes them; 256-bit credentials, the refusal
  limit, immediate revocation and the hardening baseline are the mitigations.
  Signing in from a datacenter address may trigger Telegram security notices.
- ADR 11's loopback, Host, Origin and launcher rules apply only to the Mac
  instance. [ADR 16](0016-persistent-media-cache-and-non-ai-media.md)'s media
  cache and [ADR 20](0020-minimal-reading-and-ready-notifications.md)'s push
  sending run on the VPS, which has its own Web Push keys.
- Whoever can read the Owner Account's Saved Messages, controls a trusted
  client or runs commands on the VPS can trust a new client; the trusted list
  with Forget and the "was paired" notes in Saved Messages are the check.
- The repository keeps both runtimes until the Mac instance retires: the
  MacHelm adapter and its tests, and the compose and deploy paths.

## Questions

- Q2: the service runs on an owner-managed VPS; no Mac tunnel.
- Q7: hosting on the VPS removes the Mac-awake constraint.
- Q14: public HTTPS behind Caddy with Let's Encrypt; exact origin checks,
  Secure cookies; WireGuard-only not adopted.
- Q15: the host approves no pairings (ADR 47 Q1); QR from a trusted client,
  the Saved Messages code or Sign in with Telegram.
- Q16: faster-whisper int8 large-v3-turbo on the VPS CPU, denoising off,
  through the proxy with a hosted fallback for long notes (ADR 39).
- Q17: Docker Compose on Debian, behind the shared edge (ADR 40); no bare
  systemd.
- Q18: a fresh installation; sessions sign in again; the Mac stopped as the
  fallback.
- Q19: hardening baseline with the service's own refusal limit; backups only
  after production.
- Q21: the Mac stays the stopped fallback until the VPS is declared live.
