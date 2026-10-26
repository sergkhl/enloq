## Default pre-deployment order

Before every routine deployment:

1. Validate and commit changes in the task worktree.
2. Merge its task branch into `main` from the primary checkout. Resolve
   conflicts and rerun affected checks when necessary.
3. Push `main` to `origin` and verify that both name the same revision.
4. Remove the completed, clean, fully merged task worktree with
   `git worktree remove`, then delete its merged local branch.
5. Deploy the verified, pushed revision from the clean primary `main` checkout.

Never deploy from a feature branch, detached HEAD or temporary worktree.
Preserve unrelated dirty or unmerged worktrees, the primary checkout and
runtime data. Never force-push `main` or force-delete a worktree.

# Contributor instructions

This applies throughout this repository. Read [CONTEXT.md](CONTEXT.md), the
ADRs that own the area you change and [the TODO list](docs/plans/TODO.md)
before changing their owning documents.

## A public repository

This is the public repository `sergkhl/enloq`, under the
[GNU Affero General Public License v3.0](LICENSE). Every tracked file and every
commit is published.

- Never commit Telegram account IDs, usernames or phone numbers of real
  people, the Owner Account and Maya included; other people's names; message
  bodies or images; credentials or populated env files. Write role names
  ("the Owner Account", "Maya, the test account"). Private operating values
  live in untracked files, such as `.local/live-targets.json` for live checks.
- Commit as Sergei Khlopenkov with the GitHub noreply address
  `6926565+sergkhl@users.noreply.github.com`, set in each checkout's own Git
  config.
- Test fixtures are authored and synthetic, never copied from real chats.

## How decisions are recorded

- **ADRs own decisions** (`docs/adr/`). Edit an ADR in place when its decision
  changes: no amendment sections, dated history, delivery records, commit
  hashes or past check receipts. Keep ADR numbers and their numbered Q
  decisions stable, because code comments cite them as "ADR N Qm". An ADR
  whose decision was fully replaced keeps a short `status: superseded` note
  naming its successor.
- **CONTEXT.md owns vocabulary.** The runbooks (`docs/operations.md`,
  `docs/inference.md`, `docs/mobile.md`, `docs/media-delivery.md`,
  `docs/history.md`, `docs/local-credentials.md`) own procedures and current
  limits, not evidence logs.
- **Plans exist only while work is active** (`docs/plans/`) and are deleted
  when it is delivered. [TODO](docs/plans/TODO.md) holds open proposals,
  follow-ups and the owner's open gates.
- **Interviews** (`grilling`, `grill-with-docs`) record confirmed decisions
  and keep proposals and unanswered questions explicitly open. An interview
  alone does not authorize implementation; an explicit implementation
  instruction sets the scope without a ceremonial second approval.

## What Enloq is

Enloq is a Telegram client that reduces the cognitive load of correspondence;
the model acts as a communication translator. The ADRs own the details; these
are the standing principles:

- Incoming text is combined into blocks and shown as sent; only explicit
  hostile wording triggers **Soften hostile messages**, keeping every fact,
  request, deadline and threat
  ([ADR 53](docs/adr/0053-calming-hostile-messages-and-removing-adaptive-tone.md),
  [ADR 54](docs/adr/0054-incoming-text-combined-not-rewritten.md)). Voice
  notes, video circles and images are rewritten into clear text.
- Outgoing Rewrite and its tones are removed. Private chats have separate,
  always-visible Send and Suggestions buttons. Generation leaves the draft
  intact. Manual options are complete messages that develop a nonempty
  draft's idea and follow its [bracketed] instructions; choosing one
  replaces the draft with Undo and never sends.
- Each private chat separates **Automatic suggestions** On/Off from **Style**
  Friendly, Professional or Idioms, Friendly by default, for both generation
  paths. Automation Off leaves manual generation available. The owner's
  server-wide switch controls automation only.
- Incoming notifications follow [ADR 20](docs/adr/0020-minimal-reading-and-ready-notifications.md):
  one alert per burst, later updates silent. With softening On, text gets an
  immediate hostile-wording screen with a five-second arrival deadline;
  clean originals notify immediately, hostile previews wait for softening.
  If screening or a required rewrite cannot proceed or fails, send the
  original preview immediately. Softening Off skips screening. Textless
  media uses its type label. Unseen readiness stays separate.
- Response Suggestions, Person and Account Memory, group briefings and members
  follow [ADR 25](docs/adr/0025-emotion-response-suggestions.md),
  [ADR 49](docs/adr/0049-useful-response-suggestions.md),
  [ADR 56](docs/adr/0056-idioms-either-signal-hiding-and-memory-off-by-default.md),
  [ADR 55](docs/adr/0055-person-and-account-memory-and-traits.md),
  [ADR 34](docs/adr/0034-group-chats-and-daily-briefings.md) and
  [ADR 48](docs/adr/0048-members-on-the-shared-service.md).
- Combined-block background work covers chats active in the last seven days,
  waits for 30 quiet seconds and uses at most the 20 preceding same-chat
  messages plus the one directly replied-to message. It never scans older history or follows
  reply chains. Content is cached for 30 days; unchanged results are reused.
- Do not add attention or task tracking, topic classification, a sparkles
  icon or inline usage hints. Keep accessible names and real status and error
  information.
- **The Enloq app is the target platform** ([ADR 42](docs/adr/0042-groups-and-briefings-in-the-enloq-app.md)
  Q7): design features for the app first. The web shows the app's screens in
  one centered phone-width column and does only what the app does
  ([ADR 44](docs/adr/0044-mobile-only-web-in-the-apps-design.md)).

## Development and deployment

- **Checkouts.** Development happens in `~/pi-workspace/enloq` on the VPS
  (`ssh lrnki-vps`), with GitHub `sergkhl/enloq` as the shared remote. The Mac
  checkout `~/_DEV/enloq` makes native iPhone builds; mobile work edited there
  is pushed and then deployed from the VPS
  ([ADR 40](docs/adr/0040-shared-vps-deployment-and-development.md)).
- **Checks.** `./scripts/check` runs the backend tests, the phone-size web
  journeys and the Jest tests. Android journeys run on the development phone;
  iOS simulator journeys run on the Mac when the iPhone build is made there.
  Desktop browser checks are not required.
- **Deploy.** `./scripts/deploy` in the VPS checkout builds committed `HEAD`
  as `enloq:<sha>` and runs it from the service folder
  `~/pi-workspace/enloq-service` as compose project and container `enloq`;
  `--rollback` runs the previous image. The service folder holds `data/`,
  `config/`, `secrets/` and a `.env` with `IMAGE` and `EDGE_NETWORK`.
  "Delivered" means deployed there. [Operations](docs/operations.md#vps-service)
  owns the procedure.
- **Saved Builds.** After any deployed change to `mobile/`, `shared/` or the
  API schema, run `mobile/scripts/publish-android` on the VPS from a clean
  committed `HEAD`; it replaces the pinned build message in the Owner
  Account's Saved Messages ([ADR 43](docs/adr/0043-installable-android-builds-and-phone-setup.md),
  [ADR 50](docs/adr/0050-icon-tabs-saved-messages-and-one-saved-build.md)).
- **Standing authorization.** Until the owner declares Enloq released in
  production, start, stop, restart and upgrade the service and run finite
  acceptance checks without asking again, within the model, target,
  session-owner, privacy and budget boundaries below. Record test bounds and
  evidence where the owning runbook keeps current limits, preserve unrelated
  work, and report defects or concrete blockers instead of recreating an
  approval gate. Ordinary use and adoption remain the owner's decisions.
- **Application data.** Until that declaration, wiping application data
  (stores, caches, memory, drafts) is an acceptable migration or recovery
  path. Credentials, Telegram authorizations and spending records stay
  protected. After it, treat application data as durable.
- **Owner-only steps.** Credential placement, Telegram logins and the
  production declaration are the owner's.
- **The owner's task notes.** When the owner points at Saved Messages for
  tasks, run `docker exec enloq python -m enloq.saved --notes N` with the
  number they name: it prints their last N own notes, oldest first, and saves
  their images in the data folder's `inbox/`. `--latest-image` saves the
  newest image there. Delete the copies once read, and keep note contents out
  of the repository.
- **Native intake cost.** Do not change native intake persistence under ADR 34
  without the owner's decision on its open TODO entry
  ([operations](docs/operations.md#idle-cost) explains how to check a busy
  service).
- **WireGuard.** wg-easy client keys are private: never print or commit one,
  and copy one only into the VPS host's root-only `/etc/wireguard/enloq.conf`.

### The Mac fallback instance

The Mac instance is a stopped fallback with Start at login off. MacHelm owns its
startup under the label `com.quantumcraft.enloq`. Use only `./scripts/service`
for lifecycle commands; [operations](docs/operations.md#start-stop-and-upgrade)
owns the procedure. Restart with `stop`, optional `install --mode personal`
when the definition needs an upgrade, then `start`; confirm `status` reports
`"state": "running"` and `"owner": "MacHelm"` without `problems`. Stop waits
up to 15 seconds for the backend to release `.local/service.owner.lock`; if it
is still stopping, wait and query status again, and never kill the service
process. Run owner-lock checks between stop and start, and install only before
or after such a check. Never call `launchctl` for the label or write, edit or
delete its LaunchAgent plist. Never use uninstall for a restart. If MacHelm
reports it does not manage the service, is not installed, or lists `problems`,
stop and report the exact message; there is no fallback service. Start at
login is a MacHelm setting, and install does not change it.

## Reuse and access

- Prefer repository APIs, CLIs, SDKs and bounded programmatic reads.
- Use an existing session owner. Never copy a live session to another process
  or open two clients against one authorization file.
- Do not assert on prompt wording or pin source hashes in behavior tests.
- `docs/extraction.md` records the provenance of code adapted from
  hercules-support-relay, the author's earlier private project.

## Live Telegram sends

- Agents may send live messages between the Owner Account and Maya, a
  synthetic test account, to prove delivery through the app's real paths
  without asking first. `.local/live-targets.json` names the service's origin
  and both account IDs (`origin`, `owner`, `maya`); it is never committed.
- Keep every other chat out of tests: never send there, and never make another
  person's messages test content. Keep test messages recognisable as tests.
- Both accounts are signed in on the VPS service, Maya with her AI processing
  off. Remember this chat is Off for the Maya chat in the Owner Account, so
  live sends teach neither Person nor Account Memory; a check that needs
  memory there switches it on, then off again, and removes from About you
  whatever it taught.
- **Browser first.** Run live checks in a browser on the VPS: `npm --prefix web
  run live -- live/<check>.spec.ts`, one file at a time; `./scripts/check`
  never runs them. Write new ones in `web/live/` with `openMayaChat` from
  `web/live/maya.ts`, whose guard refuses any other chat. The browser trusts
  itself with the pairing code read back from Saved Messages and keeps that
  trust in `.local/live-browser/`
  ([operations](docs/operations.md#live-checks-in-a-vps-browser)).
- **The phone only when needed.** Use the Galaxy S9+ only when a check needs
  the phone, using only Maya's test account or the authored development
  login. Never sign in to the Owner Account on this phone, in Enloq or
  Telegram, or retain an owner-linked Enloq pairing. Switching the selected
  workspace to Maya does not remove access to the Owner Account. Revoke any
  owner-linked trust and clear the phone's local credentials and cached data
  before testing; keep the service's Telegram sessions intact.
- Enloq lists only chats active in the last seven days, so the Maya chat can
  fall out of the list. A chat link opens it directly (ADR 36):
  `?account=<owner>#<maya>`, or the reverse from Maya's side.

## Secrets and private content

API IDs and hashes, tokens, session files and strings, model keys and populated
env files are live credentials. Never print their values or commit them. Do not
dump resolved environments or container configuration, even through a later
redaction filter. Inspect presence, permissions and explicitly safe metadata.

Create ignore rules and placeholder examples before any new private file. Use a
private directory and `umask 077`; secret files must be mode `600`. Prefer
secret references and existing owners to copies.

## Model calls and evidence

Every live call goes through the owner's LiteLLM proxy, whose endpoint is
configuration (`MUSE_ENDPOINT_REF`, saved by `./scripts/setup proxy-key`; the
deployment uses `https://llm.globesoul.com/v1`), with one caller key; the proxy
holds the provider keys ([ADR 39](docs/adr/0039-litellm-proxy-and-vps-speech.md)).
The service's own caller key is `reply-assistant-vps`, which mirrors the
`local` key's models. `manage.py provision` changes only `local`, so after it,
copy the list to `reply-assistant-vps` with `/key/update`, then prove it with a
contentless decisions request on the service's own key, from inside its
container, before a check.

The adopted routes, which need no further authorization within their roles:

- **MiMo** (`xiaomi_mimo/mimo-v2.6-flash`, Xiaomi's own API, with its
  `thinking` switch) for image and speech rewriting, Friendly and Professional
  Response Suggestions (manual and automatic), Reaction Assessment, Person
  and Account Memory and trait notes
  ([ADR 29](docs/adr/0029-mimo-route-and-structured-incoming-rewriting.md)).
  Its accepted terms: no prompt training, 30-day retention,
  Singapore/Netherlands processing, one private app-level pseudonym as the user
  identity. Do not claim zero data retention.
- **Muse Spark 1.3** (`openrouter/meta/muse-spark-1.3-contributor` through
  `meta`, under its accepted Contributor terms) writes softened hostile text and idiom
  suggestions (ADR 53, ADR 56).
- **Decider V1.2** (`perplexity/pplx-decider-v1.2`, pinned to checkpoint
  `-20261016`, through Perplexity only on OpenRouter's Decisions endpoint via
  the proxy) for hostile-wording screening, Recipient Emotion, Reaction
  Assessment, group ad and bot filtering, suggestion usefulness and labels,
  and the traits' scales
  ([ADR 30](docs/adr/0030-decider-emotion-classification.md),
  [ADR 49](docs/adr/0049-useful-response-suggestions.md)). Perplexity publishes
  no retention or training terms for it. Never use it for text generation, and
  never move to a later checkpoint without the owner.
- **Speech**: Whisper large-v3-turbo on the VPS's CPU through the proxy's
  `whisper-large-v3-turbo` route, where audio reaches no provider. Notes over
  two minutes, and notes the VPS cannot answer, go to `elevenlabs/scribe-v2`
  through the proxy, a paid call in the Daily Budget. Offline mode and
  `scripts/check-voice.py` use the Mac's `mlx-community/whisper-large-v3-turbo`.
- `chatgpt/gpt-6.1-sol` judges sarcasm only inside `scripts/check-calming.py`.
- The application has no Codex role; do not invoke the owner's personal CLI
  through it. Any new model role needs its own adopted design. These choices
  never authorize unrelated models or automatic provider fallback.

**Budgets.** Each member has a Daily Budget and a Group Briefing Budget,
counting only their accounts and set only by the owner (ADR 48). The owner's
Daily Budget is set from $0 to $10 (default $1) and covers background work,
memory, manual and automatic suggestions, hostile-wording screening, explicit
retries, failures and restarts; the Group
Briefing Budget (default $2) covers briefing image, chunk, final and Retry
requests ([ADR 15](docs/adr/0015-owner-controlled-processing-memory-and-prompts.md),
ADR 34). The ceilings are independent, with no borrowing, shared concurrency
and dispatch-day accounting. Never change a budget automatically. New calls
start only while counted spending (receipts plus a flat estimate per unknown
outcome) is below the budget; a refusal where the provider did no work counts
nothing, and unknown costs are not zero. Wiping content data does not reset
spending records.

**Test allowance.** The owner pre-authorized live tests without asking, up to
10,000 requests in total. [TODO](docs/plans/TODO.md#live-test-allowance) keeps
the count; add each run's requests there.

**Evidence.** Reverify the exact route, current rates, affected role and the
call, time, cost and target bounds before a live run. Transport availability,
format reliability and semantic quality are separate; so are simulation, live
MTProto delivery, provider receipts and human usability. A low-quality semantic
result during finite acceptance is recorded as **Low quality** with a note in
the owning runbook, and the run continues within its bounds. That never excuses
wrong-account or wrong-chat sends, credential disclosure, duplicate delivery,
lost user text, malformed output, or broken target or budget bounds. Failed
format has unassessable semantics. Deliberate Retry and Rewrite run within
normal limits with a distinct attempt identity; there is no automatic unchanged
resampling, note-triggered loop, training or external issue publication.

**Telegram's terms.** Telegram's API terms require an application's own API
identity and forbid interfering with ordinary read and typing behavior. Its
content-licensing and AI terms broadly restrict using platform data with AI,
with possible exceptions that need explicit, informed and continuing consent
from all relevant users. The account owner's permission alone is not such
consent, and local inference or a manual Send button does not resolve it. A
recent-chat filter or an account's processing switch is not permission to
disclose message content to a provider.
