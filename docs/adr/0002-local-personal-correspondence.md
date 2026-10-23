---
status: superseded
---

# A local personal client with default private-chat and mention drafting

This ADR chose a single-account personal client in a browser on the Mac,
backed by a local service, with an attention dashboard, default drafting for
private chats and mentions, a seven-day first sync and a 30-day local cache.
[ADR 13](0013-clean-slate-rewriting-rebuild.md) replaced the product with
message rewriting for private chats and keeps the seven-day window, the 30-day
cache and sending only on the owner's action.
[ADR 10](0010-multiple-personal-accounts.md) owns multiple accounts,
[ADR 16](0016-persistent-media-cache-and-non-ai-media.md) the media cache, and
[ADR 37](0037-vps-hosting-and-remote-client-trust.md) and
[ADR 40](0040-shared-vps-deployment-and-development.md) hosting on the VPS. Its
exclusions from automatic processing (bots, service chats, Saved Messages,
broadcast channels and Secret Chats) continue under ADR 13 and
[ADR 50](0050-icon-tabs-saved-messages-and-one-saved-build.md).
