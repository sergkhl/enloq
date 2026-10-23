# Local credentials and account layout

Credential values live in private files referenced from the private
`config/app.env`; a fresh checkout contains none of them. Never print a
populated environment, a secret file or a session. `./scripts/doctor
--redacted` reports dependency versions, model routes and the configured proxy
endpoint, account-state counts and, for each loaded reference, only whether
its file exists and has no group or other permissions, without
authenticating. In personal mode its `TELEGRAM_API_*` rows show the selected
personal pair, not the prepared files.

On the VPS the same files sit in the service folder's `config/` and `secrets/`,
mounted into the container, and the setup commands below run inside it as
`docker exec -it enloq python -m enloq.setup …`
([operations](operations.md#credentials-and-pairing)).

## References

Keys in `config/app.env` carry the `ENLOQ_` prefix, omitted below; the loader
refuses unprefixed entries. Paths are relative to the repository root (the
container's `/app` on the VPS).

| Key | Private file | Purpose |
| --- | --- | --- |
| `PERSONAL_TELEGRAM_API_ID_REF`, `PERSONAL_TELEGRAM_API_HASH_REF` | `secrets/personal-api-id`, `secrets/personal-api-hash`, or the prepared pair | The Telegram application pair every account's client uses in personal mode. |
| `TELEGRAM_API_ID_REF`, `TELEGRAM_API_HASH_REF` | `secrets/telegram-api-id`, `secrets/telegram-api-hash` | A prepared application pair; read only by `setup credentials --reuse`. |
| `MUSE_ENDPOINT_REF`, `MUSE_API_KEY_REF` | `secrets/muse-endpoint`, `secrets/litellm-api-key` | The LiteLLM proxy's endpoint, an https URL ending in `/v1` (`https://llm.globesoul.com/v1` for this deployment), and its caller key, an `sk-` key that is not an OpenRouter key ([ADR 39](adr/0039-litellm-proxy-and-vps-speech.md)). The names predate the current model routes and are kept. |
| `FCM_KEY_REF` | `secrets/fcm-key.pem` | The FCM service account's private key, with `FCM_PROJECT`, `FCM_ACCOUNT` and `FCM_KEY_ID` ([operations](operations.md#android-push-through-fcm)); all four empty where phones get no push. |
| `PERSONAL_SESSION_REF`, `EXPECTED_ACCOUNT_ID` | `.local/personal/personal.session` | Registration of the first (legacy) account only. |

Personal mode uses only the `PERSONAL_*` application pair and never falls back
to the prepared one. Every live adapter (MiMo, the Decider and speech) calls
the proxy with its caller key, and the provider keys stay in the proxy; the
application reads no OpenRouter or other provider key. The proxy keeps request
bodies in its spend log for 30 days; the adapters ask it to redact them, which
its pass-through routes (the Decider's among them) ignore. The adapters
enforce the endpoint and key binding, the exact Model Routes and bounded
response capture; the dispatcher enforces the Daily Budget
([inference](inference.md)). The adapter's `user` pseudonym is generated in
the private registry, not configured, and the doctor never prints it. The
loader resolves every non-empty `*_REF` key except `CODEX_*` ones, so a
leftover reference is loaded but unused. Bot tokens, proxy master keys,
database passwords and provider keys are not needed.

Each secret file holds one value (surrounding whitespace is ignored), belongs
to the user, is mode `0600` in a `0700` directory and is not a symlink. The
loader reads it internally and never renders it. A running service reads
`config/app.env` only at start, so restart it after changing a reference.
Commands that save references (`setup credentials`, `--reuse`, `setup
proxy-key` and the account pins below) rewrite `config/app.env` from its
parsed values, dropping comments and quotes.

## Selecting the application pair

Neither command needs the service stopped or authorizes a Telegram account:

- `./scripts/setup credentials --reuse` points the `PERSONAL_*` pair at the
  prepared `TELEGRAM_API_*` files. It copies no secret, stops if a prepared
  file is missing or not private, and refuses to replace a different existing
  selection.
- `./scripts/setup credentials`, run in your own terminal, reads the ID and
  hash through hidden prompts, checks their shape and writes
  `secrets/personal-api-id` and `secrets/personal-api-hash` with their
  references. It replaces any existing personal pair, so run it only on
  purpose.

`./scripts/setup proxy-key`, also in your own terminal, asks for the proxy's
endpoint and then, through a hidden prompt, its caller key. It refuses an
endpoint that is not https, does not end in `/v1` or carries user info, a
query or a fragment, and a key that is not an `sk-` proxy key. It writes
`secrets/muse-endpoint` and `secrets/litellm-api-key` with their references.

Accounts are then added with **Add account** or the terminal fallback in
[operations](operations.md#accounts-and-browser-trust). Before the personal
service has ever started there is no registry; `./scripts/setup login` then
signs in the legacy `.local/personal/personal.session` and, after you type
`yes`, pins `PERSONAL_SESSION_REF` and `EXPECTED_ACCOUNT_ID` for the first
start to register. The first personal start creates the registry and
registers a pinned account once, so an account pinned after any personal start
is never registered.

## Accounts on disk

Personal data lives under `.local/personal/` (the service folder's
`data/personal/` on the VPS):

- `accounts.sqlite3` is the shared registry: accounts, members, shared
  settings, notification destinations and the spending records.
- `browsers.sqlite3` holds trusted browsers and devices as token hashes only,
  and `bootstrap` is the launcher's control credential, rewritten at every
  start; treat it as a secret.
- `authorizations/<opaque>/telegram.session` is the session of each account
  added in the app, or with `setup login` once the registry exists.
- `accounts/<id>/` holds that account's `rewriting.sqlite3`, `media-cache/`,
  `avatars/`, `draft-images/`, `selected-media/` and temporary `media/`.
- The first (legacy) account keeps its data in the `.local/personal/` root
  itself, and its session in `personal.session` there until it signs in
  again; a later sign-in stores its session under `authorizations/<opaque>/`,
  while its data stays in the root.
- Owner locks (`*.owner.lock`) and each session's pacing store
  (`<session>.pacing.sqlite3`) sit beside the sessions they belong to.
  `login.pacing.sqlite3` in the root belongs to no session: **Add account** and
  `setup login` share it, so a new authorization file cannot reset login
  cooldowns. The service-wide `.local/service.owner.lock` is one level up.
- `push-vapid.pem` is the Web Push key ([below](#desktop-push-credentials)),
  and `saved-build.json` names the Saved Build message when an Owner Account
  is configured.

Logout deletes the account's session file once Telegram confirms the sign-out
or reports the session already unauthorized; an unconfirmed logout keeps it and
pauses the account for Retry. Removing local data then deletes the account's
data directory and registry entry, keeping spending records; for the legacy
account, whose data shares the root, it deletes only that account's store and
caches. The account's `authorizations/<opaque>/` directory, with its pacing
store, is left in place. A member who leaves or is removed loses their
authorization folders and data as [operations](operations.md#members)
describes.

A credential reference, a pinned account or a registered account is not
evidence of a live connection; the app's account list shows whether each
account is connected.

## Other local requirements

The Mac's MLX Whisper, which offline mode and `scripts/check-voice.py` use,
needs no credential. `./scripts/install-audio` creates the locked `audio/`
environment and downloads the model with its provenance under
`.local/models/whisper/`. `uv run --locked python scripts/check-voice.py`
checks it with authored audio. Personal mode transcribes through the proxy
instead.

`.gitignore` covers `secrets/`, session files, SQLite files, populated `.env`
files (examples excepted) and all of `.local/`, including the live checks'
`.local/live-targets.json` ([operations](operations.md#live-checks-in-a-vps-browser)).
The committed [app example](../config/app.env.example) and
[VPS example](../deploy/app.env.example) contain no secret values.

After rotating an application pair or the proxy key, replace its file as a
private `0600` file without printing or pasting its value, or rerun the setup
command, then restart the service. Never commit these files or include them in
a Git handoff. Normal operation uses only app-owned sessions; never copy a
live session to another process or open two clients against one
authorization file.

## Desktop push credentials

Web Push creates `<app data>/push-vapid.pem` with mode 600 inside the private
application directory. Browser receiver keys and capability endpoints, and
phones' FCM tokens, stay in `accounts.sqlite3`, tied to the trusted browser or
device ID. They must never be printed, pasted into issue bodies, checked in or
sent to another client. The public application-server key is safe to give the
subscribing browser. The VAPID subject is the service's public origin, or
`https://127.0.0.1` on a loopback-only instance; each instance has its own
keys, so browsers turn notifications on again after a move. Each member's last
browser to turn notifications on is their sole browser destination;
expired or revoked subscriptions do not silently select another.
