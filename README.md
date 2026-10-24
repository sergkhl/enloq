# Enloq

**A Telegram client that helps people read busy conversations and write clearer replies.**

Enloq combines incoming messages into readable blocks, turns voice notes and
images into clear text, calms hostile messages without losing their content,
rewrites an authored draft in a chosen tone, and turns group conversations into
daily briefings with links back to their sources. The original messages remain
available, and sending is always a separate, deliberate action.

The product has a React web client and an Expo / React Native app, backed by a
Python service. Enloq comes from *en* (縁, human connection) and *loqui*
(Latin, “to speak”).

| Read with less friction | Edit before you send | Catch up on groups |
| --- | --- | --- |
| [<img src="docs/screenshots/enloq-reading.png" width="270" alt="Enloq conversation showing a Calmed message, with the original available through the AI view toggle">](docs/screenshots/enloq-reading.png) | [<img src="docs/screenshots/enloq-reply.png" width="270" alt="Enloq reply suggestions and an edited draft with separate Rewrite and Send controls">](docs/screenshots/enloq-reply.png) | [<img src="docs/screenshots/enloq-briefing.png" width="270" alt="Enloq daily group briefing with topic summaries, participants and source links">](docs/screenshots/enloq-briefing.png) |
| Read a calm rendering of a frustrated message and return to the original. | Choose a suggestion, edit your draft and send when ready. | Follow the key points back to the messages behind them. |

*Open any screenshot at full size. These are high-resolution captures of the
real phone-width web interface, using authored synthetic conversations and
prepared outputs. They demonstrate presentation, not model quality, live
Telegram delivery or native-device acceptance.*

## What it does

- **Read with less friction.** Show incoming text together as it was sent, and
  rewrite voice notes, video circles and images into clear text. A frustrated
  or angry block is rewritten calmly, keeping every fact, request, deadline and
  threat, and marked Calmed. Switch between AI text and originals; retain
  original media, observed edits, replies and source navigation.
- **Write with control.** Rewrite a draft as Neutral, Professional or Friendly
  casual. Review streamed text, stop an outgoing rewrite, restore the original
  with Undo, and send only when ready. When the latest block shows a clear
  emotion or calls for a reply, suggestions can offer Accept, Decline and Later
  stances; choosing one edits the draft. Each chat's profile can turn calming
  and suggestions off or ask for idioms.
- **Remember what matters.** In chats with memory on, note what each person and
  the owner care about for the suggestions to read, and collect the owner's
  traits and habits for them alone.
- **Catch up on groups.** Opt individual groups into daily briefings, follow
  citations to exact messages, and return to the briefing. Saved Messages has
  its own tab and does not go through AI processing.
- **Keep ordinary messaging useful.** Switch accounts, find people, start a
  private conversation, reply, and work with photos, albums, GIFs, stickers,
  voice notes and videos. Recent background processing covers seven days.
- **Use web and mobile together.** Pair trusted browsers and phones with the
  service. Approved members have their own accounts, preferences and budgets;
  the owner manages access and server-wide AI settings.

## Architecture

| Layer | Implementation | Responsibility |
| --- | --- | --- |
| Service | Python 3.13, FastAPI, Pydantic, Telethon, SQLite | Telegram session ownership, sync, durable work, sending, access checks and spend accounting |
| Web | TypeScript, React, Vite | Reading, composing, streamed updates, pairing and settings |
| Mobile | TypeScript, React Native, Expo | iPhone and Android clients, device trust, media and notifications |
| Shared client rules | Framework-independent TypeScript in `shared/` | Routes, times, reply presentation and reading rules |
| AI and speech | A model proxy, bounded inference roles and Whisper transcription | Rewriting, classification, briefings and speech, with explicit routes and budgets |

The deployed architecture places the service on an owner-managed VPS behind
HTTPS. Telegram authorizations, message caches and durable work stay with that
service; browsers and phones are clients, not independent Telegram session
owners. The former Mac service is a separate fallback, not the home of all
current accounts or trust. [Operations](docs/operations.md) describes hosting,
pairing and recovery; [the mobile runbook](docs/mobile.md) describes builds and
platform-specific delivery limits.

Member access is scoped in the application. The operator of the shared host
can access its stored data, as disclosed in the member agreement. This is not
an end-to-end encrypted AI processing boundary. Provider handling and current
model routes are documented in [Inference](docs/inference.md) and
[ADR 39](docs/adr/0039-litellm-proxy-and-vps-speech.md).

## Engineering decisions

**Rewriting and sending have different lifecycles.** Incoming generation waits
for a quiet window. Streamed text is provisional until final validation. An
outgoing rewrite only changes the editable draft; it cannot send a message.
Account processing controls are independent of the reading view.

**An uncertain send is not safe to repeat.** Sends are ordered per chat and
retain their operation identity, intended account, peer and media. Failed and
uncertain outcomes remain visible with explicit recovery actions. Checking
delivery observes the original operation rather than sending another copy.
See [media delivery](docs/media-delivery.md).

**Generated text stays connected to its sources.** Group briefings use
validated citations and bounded source sets. Changed or withdrawn source
messages invalidate affected work. Originals and observed history remain
inspectable; model text does not replace the source record. See
[history](docs/history.md) and [ADR 34](docs/adr/0034-group-chats-and-daily-briefings.md).

**Cost and access limits survive restarts.** Durable receipts and reservations
cover successful, failed and uncertain model requests. Daily work and group
briefings have separate budgets. Members' accounts and spending are scoped to
their membership; an account switch cannot reuse another account's draft or
authorization.

These decisions turn the project into a complete product integration: Python
backend, asynchronous model work, Telegram transport, durable storage and two
client applications. They do not establish a numerical accuracy rate or a
measured improvement in users' communication.

## Run an isolated preview

The authored browser fixture starts with disposable data and fake Telegram,
model and notification adapters. It does not load personal configuration or
require live credentials. Use a free local port; the commands below use the
fixture's default `8776`, separate from the legacy personal service's `8765`.

```sh
uv sync --locked
npm --prefix web ci
npm --prefix web run build
FIXTURE_WEB=web/dist uv run --locked python scripts/test_server.py
```

In another terminal, create a pairing link for that fixture:

```sh
uv run --locked python scripts/fixture_pair.py
```

Open the printed loopback URL in a browser. The helper approves that disposable
fixture's browser claim. Stop the fixture with Ctrl+C when finished; its
temporary data is removed. Follow [Operations](docs/operations.md) for a real
installation and [the credential layout](docs/local-credentials.md) for private
configuration.

## Verification

```sh
./scripts/check --unit all
npm --prefix web run build
npm --prefix web run journeys
```

Backend tests exercise authored data and fake external adapters. Browser
journeys cover reading, composing, suggestions, media, pairing, member access,
settings and recovery through the real interface. The mobile app has Jest and
Maestro journeys; their setup is in [the mobile runbook](docs/mobile.md).

These checks establish source behavior and controlled UI results. Live Telegram
delivery, provider format and semantic quality, native-device acceptance, and
deployment are separate evidence: the runbooks state their current limits, and
[the TODO list](docs/plans/TODO.md) holds what remains open. A screenshot of the
fixture proves presentation, not live model or Telegram behavior.

Conversation and derived content normally expire after 30 days. Person and
Account Memory, unsent drafts, unresolved delivery and spend records have
distinct lifetimes; [Operations](docs/operations.md) owns those rules.

- [Domain vocabulary](CONTEXT.md) and [architectural decisions](docs/adr/)
- [Inference and media bounds](docs/inference.md)
- [Private media and native delivery](docs/media-delivery.md)
- [Telegram sync and original history](docs/history.md)
- [Code provenance](docs/extraction.md)

## Status and licence

Enloq runs as one owner's personal deployment, shared with a few approved
members. Its code is published so that others can read and reuse it. There is
no supported way to host your own copy: the configuration, hostnames and
runbooks describe that one deployment, though the Telegram application
credentials, the model proxy endpoint and the edge network are settings rather
than code. The repository starts from a single commit; its earlier development
history is not published.

Telegram's API terms and its content-licensing and AI terms apply to anyone who
processes Telegram data with AI. An account owner's permission is not consent
from everyone in a conversation, and running models locally does not change
that.

Copyright (C) 2026 Sergei Khlopenkov. Enloq is free software under the
[GNU Affero General Public License v3.0](LICENSE): if you run a modified
version as a network service, you must offer its users the corresponding
source.
