# Operations

One backend service owns the Telegram accounts. The web app and the Enloq app
are its clients: closing one stops no background processing. The service runs
on the shared VPS at `https://enloq.globesoul.com`
([ADR 40](adr/0040-shared-vps-deployment-and-development.md)); the Mac instance
under MacHelm is the stopped development fallback
([below](#cutover-and-the-mac-fallback)). [The Enloq app](mobile.md) walks
through both clients' screens; this runbook owns hosting, lifecycle, finite
checks and the service's recovery rules.

Until the owner declares production, starting, stopping, restarting, upgrading,
deploying and rolling back the service and running finite acceptance checks
have standing authorization; do not ask again. Follow the scope and safeguards
in the [contributor instructions](../AGENTS.md). Record the candidate, a
check's bounds and its outcome in the owning plan, or in the
[TODO](plans/TODO.md) for work without a plan. A restart or a passing check
does not declare production.

## Start, stop and upgrade

This section covers the Mac instance, in the checkout `~/_DEV/enloq`; the VPS
service uses [deploy](#deploy-and-roll-back).

```sh
./scripts/service status
./scripts/service start
./scripts/open
```

The installed service runs at `http://127.0.0.1:8765`. To update its code:

```sh
./scripts/service stop
npm --prefix web run build
./scripts/service start
```

MacHelm owns the service's startup. `./scripts/service` writes no LaunchAgent:
`install` hands MacHelm the service definition, and `start`, `stop`, `status`
and `uninstall` ask MacHelm through its runner,
`/Applications/MacHelm.app/Contents/Library/HelperTools/MacHelmServiceRunner`
(`ENLOQ_MACHELM_RUNNER` overrides the path). Without MacHelm, or while MacHelm
does not manage the service, each command stops with a message; none falls
back to a LaunchAgent of its own. MacHelm refuses to replace a LaunchAgent it
did not write, so an existing `com.quantumcraft.enloq.plist` is imported and
taken over in MacHelm's Startup Services, never overwritten. Whether the
service starts at login is a MacHelm setting: a new definition starts with it
off, and `install` preserves it.

`./scripts/service install --mode offline` builds the UI and installs a
credential-free fixture service; `--mode personal` uses the prepared private
configuration. Installation requires the service to be stopped and does not
start it. `./scripts/dev --mode offline` is the foreground alternative.

`./scripts/service stop` waits up to 15 seconds for the backend to exit and
release its owner lock. If it has not, the command says the service is still
stopping; MacHelm shows Stopping until it has, and nothing is force-killed.

For a restart or upgrade, run `./scripts/service stop`, optionally
`./scripts/service install --mode personal` when the service definition needs
updating, then `./scripts/service start`. Confirm with
`./scripts/service status` that `state` is `running`, `owner` is `MacHelm` and
no `problems` are listed. If stop is still in progress, wait and ask status
again. Run checks that take `.local/service.owner.lock` between stop and start;
install must finish before such a check or start after it, because MacHelm
refuses definition changes while the lock is held. Never use uninstall for a
restart, call `launchctl` for `com.quantumcraft.enloq`, or write, edit or
delete `~/Library/LaunchAgents/com.quantumcraft.enloq.plist` from outside
MacHelm. If a command says MacHelm does not manage the service or is not
installed, or status lists `problems`, stop and report the message to the
owner; there is no fallback.

Reload an open browser tab after a build. The journeys in `./scripts/check`
build their own UI into `.local/web-journeys-dist` and serve it from their
authored fixture, so checking never replaces the `web/dist` a running service
serves. Only a delivery (`./scripts/service install`, or
`npm --prefix web run build` before a restart) changes the served UI; build it
from the commit the service will run, or a reload pairs the new UI with the old
API. A cross-process owner lock rejects a second backend, and each Telegram
session has its own owner lock as well. Never copy a live session.

A store left by the pre-rebuild application (`assistant.sqlite3`) stops the
service with _Run the local content cutover before starting the rebuilt
application_. `./scripts/cutover --mode personal` inventories it without
message bodies or secret values; with the service stopped,
`./scripts/cutover --mode personal --apply` retires it as
[ADR 13](adr/0013-clean-slate-rewriting-rebuild.md) allows, keeping unresolved
send identities and spending records. Never remove a session to get past it.

`./scripts/service uninstall` removes the definition from MacHelm while
preserving credentials and local data. In `.local/service/`, `service.log`
(stdout) holds only the start-up banner. Lines the app's log handler writes to
`service-error.log` (stderr) read `<UTC time> <level> <logger> <event>`,
optionally followed by `error=<exception class>`. Only the app's fixed event
codes (listed in `backend/src/enloq/logs.py`) appear; library messages, Python
warnings and arguments show as `event_redacted`, and tracebacks are dropped
except for the exception class. Errors raised before the server starts and
uncaught thread tracebacks bypass the handler and can still reach
`service-error.log` raw, so do not dump logs or configuration that might
contain content. `./scripts/doctor --redacted` reports dependency versions,
the model routes and configured proxy endpoint, account-state counts and each
reference's presence and permissions, without authenticating or printing
secrets.

### Finite checks

Each finite check holds the service owner lock, so stop the service first and
start it again after the bounded pass. On the Mac run
`.venv/bin/python scripts/<check>.py`; on the VPS run it in a one-off container
of the image to be deployed ([below](#deploy-and-roll-back)). Every check uses
wholly authored cases with a fake transport, so it makes no Telegram
connection, send or reaction. It verifies its exact routes and refuses rates
above those adopted, uses the shared Daily and Group Briefing ledgers, keeps
every owner setting and gives each case one first attempt; it is not an
automatic retry. Its report is a unique private folder under `.local/`, and
the terminal shows counts and paths, never content. Where a check names a
judge model, it is `chatgpt/gpt-6.1-sol`, the proxy's subscription route,
which spends nothing.

**Memory** ([ADR 55](adr/0055-person-and-account-memory-and-traits.md) Q8,
Q26): `scripts/check-memory.py`. Rerun it whenever the Person memory or
Account memory guidance is edited. Wholly authored chats, half Russian, go
through the application's stages on the real routes in three groups:

- **Cares about.** Four chats fold once each (MiMo). The judge reads every
  note against its chat: a Person Memory note must concern the other person
  and an Account Memory note the owner, each supported by the messages. A note
  naming an authored date or amount keeps it, Russian chats keep Russian
  notes, and the small-talk chat notes nothing.
- **Suggestions.** Authored memories reach six real suggestion requests. The
  judge decides whether an option reveals an owner note from another chat
  (Q15) or claims a remembered thing is still true or still ahead (Q16).
- **Traits.** Five authored chats and the owner's side through the real trait
  roles: nothing may show for the three-message chat, and the scales shown
  must land within one level of their authored level on at least 9 in 10. The
  judge reads every note too: it must describe its subject and keep the
  subject's script; one that overreaches its messages is listed for reading.

The cares-about and suggestion groups pass only with zero misses; the owner
then reads every result in `results.md` beside the private
`.local/memory-check-*/report.json`. Suggestion answers go through the app's
own validation, and an unreadable one gets one more attempt. The check stays
within 6 fold, 8 suggestion, 10 decision, 10 note and 80 judge requests, 1,800
seconds and $0.20 counted.

**Suggestions** ([ADR 25](adr/0025-emotion-response-suggestions.md)):
`scripts/check-suggestions.py` runs eight authored cases (the seven qualifying
emotions and one mixed-language block) through the production scheduling and
generation path. Emotions are authored and stand in with a useful decision, so
no incoming rewrite or decision is requested. It keeps the owner's
Suggestions switch and guidance and allows at most eight requests, 900 seconds
and $0.05 counted, reporting to `.local/suggestions-first-pass-*/report.json`.

**Useful suggestions** ([ADR 49](adr/0049-useful-response-suggestions.md) Q13):
`scripts/check-useful-suggestions.py`. Its 16 authored private blocks are
short and long, Russian, English and mixed:

- questions, requests and invitations with and without emotion;
- emotional news;
- thanks, ok and a goodnight;
- an answer that closes the owner's question;
- a forwarded link.

Each goes through the real Emotion Classification with its usefulness
question: a block under the saved threshold by the held path, a longer one
after an authored rewrite stand-in, so no incoming rewrite is requested. Each
block with a usable non-neutral emotion, or a yes on usefulness at 0.7 or more
([ADR 56](adr/0056-idioms-either-signal-hiding-and-memory-off-by-default.md)'s
either signal and Q19), then runs the real Response Suggestions request.
Memory is off. It allows at most 40 decision and 16 suggestion requests, 1,800
seconds and $0.02 counted, starting a request only while the flat unknown
estimate still fits. Malformed answers or option sets, and route failures, are
listed as `blocking`; the emotion, usefulness and answer-stance verdicts are
for review. The report is `.local/useful-suggestions-check-*/report.json`.

**Soften hostile messages** ([ADR 53](adr/0053-calming-hostile-messages-and-removing-adaptive-tone.md)
Q12-Q14): `scripts/check-calming.py`. Rerun it after softening guidance changes.
Its 30 authored Russian and English blocks cover insults, rants, amounts,
deadlines, legal and safety threats, hostile sarcasm, speech, forwarded and
third-party hostility, with non-hostile anger, frustration, teasing, reported
insults and neutral or warm controls. Text is combined, decided from originals
and softened only when the independent hostility answer qualifies; speech is
prepared, decided and rewritten once. Muse writes softened text.

Text checks require retained facts, numbers, conditions and threats, source
language, removed hostile wording and no invented currency or gender. A judge
checks only sarcasm being read literally. Missing format has unassessable
semantics. Low semantic quality is recorded as **Low quality**, and the bounded
pass continues; transport, format and quality remain separate claims. The
owner reads the saved pairs. Failed rewriting leaves originals. No-work
refusals follow the existing bounded retry, at most twice per case in this
check; a still-refused case remains unverified. Bounds are 32 rewrite,
32 decision and 30 judge requests, 2,400 seconds and $0.20 counted. Evidence
remains `.local/calming-check-*/report.json` and `pairs.md`.

**Idioms** ([ADR 56](adr/0056-idioms-either-signal-hiding-and-memory-off-by-default.md)
Q16, Q17): `scripts/check-idioms.py`. Rerun it whenever the Idioms guidance is
edited. Twelve authored private chats, half Russian, each with an authored
decision on its latest block (no rewrite or mood decision is requested), go
through the app's own suggestion dispatch in a chat set to Idioms, once with
MiMo writing and once with Muse Spark 1.3; the decision model labels each set
as the app has it do. Code checks each option's language, and the app's own
validation catches a malformed set; one-sided sets are valid under ADR 49 Q20.
The judge reads every set:
the expression each option uses, whether it is real and in its established
meaning, and whether the option invents facts, admits fault, pressures or
commits the owner beyond a plain yes. A writer passes with no miss on those
fixed rules and the expression rule met in at least 9 sets of 10; the one with
fewer misses wins, and a tie goes to the owner. Muse writes idiom sets (Q20).
The owner reads every set in `sets.md` beside the private
`.local/idioms-check-*/report.json`. A provider refusal is retried after its
delay (at most two minutes), at most twice per set; an unreadable judge answer
gets one more attempt. It verifies the MiMo, Muse and decision routes and
allows at most 30 writer, 30 decision and 48 judge requests, 1,800 seconds and
$0.05 counted.

**Decision route** ([ADR 30](adr/0030-decider-emotion-classification.md) Q11,
ADR 49 Q1): `scripts/check-emotion.py` runs 30 authored cases:

- 16 incoming blocks in Russian, English and mixed languages, including image
  meanings, a voice transcript and forwarded and quoted feelings;
- 8 correspondent reactions covering `suggestions_useful`;
- 6 group ad-filter cases, one authored group with AI on per case, whose posts
  form one real classifier request.

Each incoming case stands in an authored rewrite and image meaning where the
rewrite would publish them and then runs the real classification job, so no
MiMo request is made. Reactions run the real assessment path and ad cases the
real group filter, which never judges bots here. Suggestion and memory calls
are off. It allows at most 40 first attempts within 1,200 seconds and $0.01
counted across both budgets, and writes `.local/emotion-decider-check-*/report.json`.
A request starts only when its worst-case cost (every request byte a token,
plus a framing allowance) fits the remaining bound; an unknown outcome counts
the flat estimate and stops the check. That flat estimate alone equals the
bound, so an unknown outcome after earlier receipts would exceed it by those
receipts; `within_bounds` in the report records whether every bound held.
Malformed answers and route failures are listed as `blocking`; weak but
well-formed results are recorded for review.

**MiMo route** ([ADR 29](adr/0029-mimo-route-and-structured-incoming-rewriting.md)):
`scripts/check-route.py --check 1` covers every role on the MiMo route with the
current guidance, except the decision roles above (at most 16 requests, 1,800
seconds and $0.05 counted); `--check 2` covers structured incoming and speech
rewriting (at most 12 requests, 1,200 seconds and $0.03); `--check 3` runs all
thirteen authored incoming cases of both (at most 16 requests, 1,800 seconds
and $0.03). Voice notes carry authored transcripts through a stand-in
recognizer, so no audio exists. Format outcomes go only to its report, never to
the running route's format counts; the report,
`.local/route-check-<n>-*/report.json`, includes the authored raw output of
each streamed rewrite for diagnosis. After a concrete contract correction,
`--case <name>` (repeatable, Check 2's cases) with lower `--requests`,
`--seconds` and `--counted-nanos` runs a continuation inside the check's
remaining recorded allowance; the helper refuses any value above the recorded
bound. Record the hypothesis and bounds before the run.

**Incoming format** ([ADR 35](adr/0035-processing-character-threshold-and-empty-rewrites.md)):
`scripts/check-threshold.py` runs six authored cases through real incoming
stream and final validation: combined 199- and 200-character blocks, trimmed
Cyrillic and emoji, a prepared transcript, text with an authored image, and an
image-only target with textual Supporting Context. No audio exists.
Deliberate cached processing keeps the saved threshold and the older-work
reserve; emotion, suggestions and memory are off in its fixture. It allows at
most six first attempts, 600 seconds and $0.05 counted within the Daily
Budget, and needs room for the larger of the conservative prepared
input/output cost and the ordinary unknown estimate before dispatch. Bounds can
only be lowered. An unknown outcome stops further requests, unfinished cases
stay unverified, and no unchanged failed case is resampled. The report is
`.local/threshold-check-*/report.json`. Automatic admission itself is
backend and journey evidence, separate from provider shape and semantic
quality.

`scripts/check-group-briefings.py`, the briefing path's check, is described in
[inference](inference.md), and `scripts/check-voice.py` in
[local credentials](local-credentials.md#other-local-requirements).

### Idle cost

The scheduler looks for due work four times a second. Between local changes
that look-up writes nothing and plans no group work:

- A transaction that changed nothing ends without a commit, so SQLite creates
  no journal file beside the stores. Schema changes, pragmas and savepoints
  always commit.
- A chat or group row reported again unchanged, as every sync cycle does for
  each dialog, is not rewritten, so it wakes no client.
- Native intake keeps polling every recent group. For a group whose AI is Off
  those intake and access rows still commit and still reach clients, but they
  leave group planning answers standing; planning reads only groups with AI On.
- A group planning answer (nothing to run, and whether group work keeps memory
  waiting) stands until the first of: the next local commit, a change in the
  account's connection or sign-in state, a queued group stage's due time, the
  end of an eligible group's quiet, four-hour or refusal hold, the next budget
  day, or 15 seconds. Sources leaving the seven-day window or 30-day retention,
  and writers outside the service, are therefore noticed within 15 seconds.
  Claiming and every stage still recheck everything exactly.

One planning pass after a change proves every cached briefing source of that
account again, so its cost grows with the enabled groups' cached days; the
[TODO](plans/TODO.md) keeps the measurement and the open follow-up.

To check a service that seems busy on the VPS, read `docker stats --no-stream
enloq` twice a minute apart. On the Mac, take its PID from
`./scripts/service status` and read `ps -o %cpu,time -p <pid>` twice a minute
apart; macOS also records sustained writing as
`python3.13_<date>_<host>.diag` in `/Library/Logs/DiagnosticReports`, with
`Event: disk writes` and the `com.quantumcraft.enloq` resource coalition. Its
heaviest stack names the code path; the report holds no correspondence. Stop
the service while investigating; nothing needs to be killed.

## VPS service

The service runs on the shared VPS (`ssh lrnki-vps`) at
`https://enloq.globesoul.com`. The host's address and port stay in
`~/.ssh/config`, never in this repository. Development happens in the VPS
checkout `~/pi-workspace/enloq`; the live service is a separate compose
project, `enloq`, in its own folder (`ENLOQ_SERVICE_DIR` overrides the path
for `./scripts/deploy`):

| Path in `~/pi-workspace/enloq-service` | Holds |
| --- | --- |
| `compose.yaml` | Installed from `deploy/compose.yaml` by every deploy. |
| `.env` | `IMAGE=`, the running image, which deploy writes, and `EDGE_NETWORK=`, the edge's Docker network (`lrnki` on the shared VPS), which deploy keeps. |
| `config/app.env` | Installed once from `deploy/app.env.example`: `LISTEN_HOST`, `PUBLIC_ORIGIN`, `TRUSTED_PROXY`, `TIMEZONE`, `OWNER_ACCOUNT`, the FCM lines and the secret references, each with the `ENLOQ_` prefix. |
| `secrets/` | The Telegram API pair, the proxy endpoint, the proxy caller key and the FCM key pair `fcm-key.pem` and `fcm-cert.pem` (0600). |
| `data/` | The image's `.local`: `personal/` stores, sessions, caches, VAPID keys and owner locks, plus `builds/` and `inbox/`. |
| `previous-image`, `deployments.log` | Rollback target and deployed commits. |

The shared Caddy edge (`~/pi-workspace/edge`, container `lrnki-caddy`, the
service's `TRUSTED_PROXY`) terminates TLS and proxies `enloq.globesoul.com` to
`enloq:8765` on the `EDGE_NETWORK` network; the container publishes no port.
The DNS record is a DNS-only A record made with `cf`. Edit only this service's
block in the edge Caddyfile, validate it, commit it there and restart the edge
(every site blinks for about a second). Validate in the edge's own
environment, since another site's block reads variables only it has:

```sh
cd ~/pi-workspace/edge
docker compose run --rm --no-deps -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose restart caddy
```

### Deploy and roll back

Routine deployment follows the contributor instructions in order:

1. Validate with `./scripts/check` and commit the task worktree.
2. From the primary checkout, merge its branch into `main`; resolve conflicts
   and rerun affected checks.
3. Push `main` to `origin` and verify both refs name the same revision.
4. Remove the clean, fully merged task worktree with `git worktree remove`,
   then delete its merged local branch. Preserve unrelated worktrees and data.
5. From clean primary `main`, run `./scripts/deploy` for that pushed revision.
   Use `./scripts/deploy --rollback` when recovery needs the prior image.

Feature branches and temporary worktrees are for development and finite
checks; routine service deployment uses the clean primary checkout.

Before the first deploy, write `EDGE_NETWORK=<the edge's network>` into the
service folder's `.env` (`EDGE_NETWORK=lrnki` on the shared VPS): compose
refuses to start without it, and deploy keeps that line whenever it rewrites
`IMAGE=`.

`./scripts/check` runs the backend's ruff and pytest, the web's typecheck and
phone-size journeys, and the Enloq app's typecheck, Prettier and Jest. The
journeys (`web/journeys`, ADR 44 Q8) drive Chromium at 390×844 with touch
against their own authored fixture on port 8777 (`FIXTURE_PORT`), so they
never meet the app journeys' fixture on 8776.

Deploy builds `HEAD` with `git archive` (uncommitted changes are refused), tags
the image `enloq:<12-character commit>`, starts it and waits for the
container's health check. An unhealthy start restores the previous image. Only
the running and previous images are kept.

Everyday operations, run in the service folder:

```sh
docker compose ps
docker compose logs --since 10m app
docker compose restart app
docker compose stop app
```

Restart after changing `config/app.env` or a secret: the service reads them
only at start. `stop` waits up to 30 seconds for the backend to release its
owner lock. For a finite check, stop the service and run the check in a
one-off container, for example
`docker compose run --rm app python scripts/check-emotion.py`, then start it
again. To check the image about to be deployed, build it as `./scripts/deploy`
does (`git archive HEAD | docker build --file deploy/Dockerfile --tag
enloq:<12-character commit> -`) and name it for the run:
`IMAGE=enloq:<commit> docker compose run --rm --no-deps -T app
python scripts/<check>.py`; `./scripts/deploy` then reuses the built layers.

### Credentials and pairing

The owner places credentials inside the container, through prompts:

```sh
docker exec -it enloq python -m enloq.setup credentials
docker exec -it enloq python -m enloq.setup proxy-key
docker restart enloq
```

`setup credentials` stores the Telegram API pair through hidden prompts.
`setup proxy-key` asks for the LiteLLM proxy's endpoint, an https URL ending in
`/v1` (`https://llm.globesoul.com/v1` here), and then, through a hidden
prompt, its caller key: an `sk-` key, never an OpenRouter key. It saves both
under `secrets/` and rewrites the references in `config/app.env`
([local credentials](local-credentials.md)). The VPS service's key is the
proxy's `reply-assistant-vps` caller, with the same models, route restriction
and request limit as `local`; revoke or replace it there.

To trust a browser, open `https://enloq.globesoul.com` and choose **Connect to
enloq.globesoul.com** (ADR 44 Q10): the service posts a two-minute six-digit
code to the Owner Account's Saved Messages
([below](#saved-messages-of-the-owner-account)), the six digits alone in
monospace so a tap in Telegram copies them (ADR 47 Q2), and typing it sets the
browser's trust cookie; Saved Messages then records the pairing, naming the
browser from its user agent. No terminal command pairs a client: there is no
`./scripts/pair`, and the service refuses pairings started with the control
credential (ADR 47 Q1). **Settings → Privacy** forgets a browser or phone, in
either client. Accounts are added with **Add account** in Manage accounts, in
either client (ADR 43 Q10), and each browser turns its notifications on in
Settings, since the VPS has its own Web Push keys.

A phone pairs with the same code (ADR 43 Q8): the Enloq app's **Connect to
enloq.globesoul.com** has the service post it, and typing it issues the device
credential; Saved Messages then records the pairing, naming the phone. Five
wrong codes end an attempt, at most five code messages go out an hour, each
browser or phone has one live attempt, every refused code counts toward the
refusal limit below, and no log or answer carries a code; a browser's code sets
its cookie, so its token never reaches the page. Whoever reads that Saved
Messages can pair a browser or a phone, so the trusted list with Forget and
those records are the check. **Disconnect** in either client's Settings
revokes that client's own credential with Forget's effects
(`POST /api/auth/disconnect`).

**Sign in with Telegram** leads both connection screens (ADR 48 Q11): anyone
may sign in with a phone number, the code Telegram sends and the two-step
password, through the service's own login (`/api/auth/sign-in-pairings`), on a
server with `ENLOQ_OWNER_ACCOUNT` set. The account Telegram verifies decides
what pairs: the Owner Account, or a member's first (home) account, pairs the
client to them, its new login signed out at once while the account is signed
in here and kept as its session while it is signed out. This is also the way
back in with no trusted client left and how a fresh install pairs its first
client. Another account a member holds is refused; an account the server
doesn't know reads the Member Agreement and asks to join ([Members](#members)).
Connect still posts the owner's code and answers 409 while the Owner Account
is signed out. At most five sign-ins start an hour for each number and for
each client address, and thirty for the server; six sign-ins and eight other
logins run at once, a sign-in from the same phone for the same number ends its
earlier one, and refused steps count toward the refusal limit below. Saved
Messages records only the owner's pairings.

A trusted browser or phone pairs another phone with a QR code (ADR 47 Q1): it
starts a pairing (`POST /api/auth/client-pairings`), shows the code
(`…/qr.svg`), compares the six-digit code the phone then shows and approves
it, all within two minutes, and Saved Messages records the pairing. Only the
Enloq app claims such a code; a browser that opens its link is refused and
told to scan it in Enloq (Q10). The app claims without cookies, receives its
device credential once and sends it as a bearer header; the list shows
browsers and devices together, and forgetting a device ends its requests and
event stream at once.

On the VPS, 30 refused API requests from one client address within ten minutes
return _Too many refused attempts_ (429) until the window passes. Successful
requests never count.

### Members

[ADR 48](adr/0048-members-on-the-shared-service.md) lets other people use the
service as members, each with their own Telegram accounts, phones and
browsers, Hide Bots, reading view, tone, budgets, provider pseudonym and
notifications. A trusted client reaches only its member's accounts; the owner
is the member `owner`.

- **Joining.** Someone signs in with Telegram on either client's connection
  screen. An account the server doesn't know reads the Member Agreement
  (`domain/agreement.py`), and Agree and ask to join makes a **Join
  Request**: their account is held but never started (no sync, no AI) and
  their phone or browser shows Waiting for approval, which reaches only
  `/api/auth/session`, `/api/members/me` and Disconnect (Withdraw). At most
  five requests wait; a week without a decision expires one.
- **Deciding.** The owner's phones and browser destination get a push, "*name*
  asks to join Enloq", which opens **Settings → People**. Approve starts the
  member's accounts and lets their client in; Decline signs the held session
  out and deletes everything. People also lists members with their accounts,
  last activity and today's spending, **Edit budgets** (a member's Daily and
  Group Briefing Budgets start at $0.50; `POST /api/people/{member}/budget`)
  and **Remove**.
- **Leaving.** A member's Settings has Notifications, a read-only Budget,
  Privacy and **Leave Enloq**. Leave and Remove sign the server out of every
  account the member holds, delete their authorization folders and data
  (unresolved sends and deletions included) and forget their clients; their
  spending stays under their member ID. When Telegram can't confirm a sign-out,
  the session file goes anyway and the answer says to end the Enloq session in
  Telegram → Settings → Devices. A removal first marks the member `leaving` and
  forgets their clients, so no login of theirs can add an account meanwhile;
  one removal runs per member, and one a restart interrupted resumes in the
  retention pass, each minute (People's Remove also repeats it).
- **The agreement's version.** The agreement promises no privacy and names no
  provider or retention (ADR 55 Q27), so a new provider or feature needs no
  change. Only changed words need a new `VERSION` in `domain/agreement.py`:
  every member reads it again before the list, and their accounts' new AI
  stages wait until they accept.
- **What stays the owner's.** The AI settings, Processing Guidance and the
  Concurrency Limit are server-wide and only the owner changes them (members
  get 403). Codes, "was paired" notes and Saved Builds go only to the Owner
  Account's Saved Messages; nothing is posted to a member's. Enloq acts in a
  member's Telegram only when they ask.
- **Access.** Members' data shares the VPS with the owner's; the owner and
  coding agents there can read it, which the agreement says (Q4). The members
  tables are `members` and `member_settings` in `accounts.sqlite3`, with
  `member` columns on `accounts`, `model_reservations`, `budget_changes` and
  `browsers.sqlite3`'s `browsers`.
- **Logs.** `request_expiry_unavailable` means the minute's expiry pass for
  week-old requests failed; the next minute tries again.

### Saved Messages of the Owner Account

[ADR 43](adr/0043-installable-android-builds-and-phone-setup.md) Q4 names one
signed-in account the **Owner Account**:
`ENLOQ_OWNER_ACCOUNT=<its Telegram user ID>` in `config/app.env`, read at
start. The service posts Saved Builds, pairing codes and pairing records to
that account's own Saved Messages through its running Telegram client, and
nothing else: there is no peer to choose, and the service deletes nothing in
Saved Messages. Without the setting, or while that account is signed out or
disconnected, posts are refused with that reason. A trusted phone or browser
still pairs, and while the account is signed out the connection screen signs
it back in with Telegram ([above](#credentials-and-pairing)).

Posts are host commands. `mobile/scripts/publish-android`
([the app's runbook](mobile.md#saved-builds)) copies an APK into the data
folder's `builds/` and runs, with the bootstrap control credential:

```sh
docker exec -i enloq python -m enloq.saved --file enloq-<version>-<commit>.apk < caption
```

It drives `POST /api/auth/saved-messages` over the container's loopback with
the bootstrap control credential; the route refuses any request that is not
from that loopback or that the edge forwarded. The service keeps one Saved
Build message (ADR 50 Q5), named in the data folder's
`personal/saved-build.json` with its account and whether it is pinned. Each
build replaces that message's file and caption in place; with no such message,
or once Telegram says it is gone, the build is posted anew and pinned once,
without a notification. A pin that failed is tried again by the next publish,
but one the owner removed is not. The service deletes its copy once Telegram
confirms the upload. A failure keeps the copy and the previous build, is
reported by its error class, logs `saved_messages_post_failed` or
`saved_build_edit_failed` and sends nothing again; a failed pin logs
`saved_build_pin_failed`.

Reads are host commands too. For a screenshot the owner leaves there for a
coding agent:

```sh
docker exec enloq python -m enloq.saved --latest-image
```

It drives `POST /api/auth/saved-messages/latest-image` the same way. The
service saves the newest photo or still image file among the last 20 Saved
Messages, at most 20 MB, as the only file in the data folder's `inbox/`
(`~/pi-workspace/enloq-service/data/inbox/` on the VPS), and the command
prints its date and caption. Other text never comes back, and nothing is
logged. Delete the copy once read.

The newest pairing code comes back too, for a coding agent's browser on the
VPS to trust itself ([below](#live-checks-in-a-vps-browser)):

```sh
docker exec enloq python -m enloq.saved --pairing-code
```

It drives `POST /api/auth/saved-messages/pairing-code` and prints the six
digits alone, but only for a code the service posted, the digits alone in
monospace, and only for its two minutes; otherwise it refuses. Whoever runs it
can trust a browser, which Saved Messages records and Forget ends.

When the owner points at Saved Messages for tasks (ADR 51 Q1), read the number
of notes they name:

```sh
docker exec enloq python -m enloq.saved --notes 7
```

It drives `POST /api/auth/saved-messages/notes` the same way and prints the
owner's last N own notes (1-20) among the last 100 Saved Messages, oldest
first: each one's ID, date, media kind and text. The service's own posts are
skipped: pairing codes, "was paired" notes and the Saved Build. Each photo or
still image of up to 20 MB is saved in the data folder's `inbox/`, which then
holds only that read's images. Nothing is logged. Delete the copies once read,
and keep note contents out of the repository.

### Live checks in a VPS browser

Checks with real accounts run in a browser on the VPS first, and on the
Android development phone only when they need the phone (AGENTS.md, Live
Telegram sends). The Galaxy S9+ may use only Maya or an authored development
login, never the Owner Account or an owner-linked Enloq pairing (ADR 38 Q31,
Q36). Checks use only the chat between the Owner Account and Maya, the test
account:

```sh
npm --prefix web run live -- live/maya-chat.spec.ts
```

The checks read their targets from the untracked `.local/live-targets.json`, a
JSON object with `origin` (the service's public origin), `owner` (the Owner
Account's Telegram user ID) and `maya` (Maya's). Those values never go into a
tracked file. The Maya chat's link is `<origin>/?account=<owner>#<maya>`, or
`?account=<maya>#<owner>` from Maya's side, which opens it even after it has
left the seven-day list.

`web/playwright.live.config.ts` runs Chromium at phone size against that
origin. Its global setup (`web/live/trust.ts`) reuses the browser's trust kept
in `.local/live-browser/` (0700, its state file 0600), or connects once with
the code read back [above](#saved-messages-of-the-owner-account).
`web/live/maya.ts` opens only the Maya chat, by its chat link, from either
side; every request that names another correspondent is refused, and anything
but a read of one fails the check. Write new checks in `web/live/` with
`openMayaChat`. These checks may send real messages, so `./scripts/check`
never runs them; run one file at a time.

`live/manual-suggestions.spec.ts` asks for a draft set and a bracketed set in
each Style, plus one long draft, with automation Off and no sends; its ceiling
is 20 model requests, 20 minutes and $0.05 counted. It checks that every set
is ready with no bracketed text in its options, the unchanged draft,
replacement with Undo and restored settings. After a provider failure, a
continuation keeps the original start in `ENLOQ_MANUAL_ACCEPTANCE_SINCE`
(Unix seconds) and runs from set `ENLOQ_MANUAL_ACCEPTANCE_FROM` (1–7, in the
order above); every ceiling still counts from the original start.
`live/arrival-notifications.spec.ts` sends three authored Maya messages and
reads only the service's scoped eligibility and screening metadata through
Docker, using no second Telegram client. It checks early clean eligibility and
silent later updates; OS-banner display is a separate phone check. Both save
private evidence under `.local/live-evidence/`.

`live/reminders.spec.ts` checks ADR 58 in the Maya chat. It sends the day's
authored test message, then saves two exact-time Once reminders due a few
minutes later: one whose Message Idea names that day and one without an idea.
Listing makes no model call; the chat's set is prepared before the first alert
and covers both; a Maya message refreshes it after 30 quiet seconds while no
client has the chat open; Dismiss leaves one, and opening prepares its set
again; a send from that set completes it and ends its schedule. When some
timezone's midnight is at most 22 minutes away, the reminders use it, so the
idea's day ends within the run and its Kept Messages are copied first. It
requires the Maya composer to be empty, Remember this chat Off, no Maya
schedule pending or due during the run, the owner's account AI On, Maya's AI
Off, global Automatic suggestions On and available Daily Budget. It turns the
chat's Automatic suggestions On for the run, then restores the chat's
settings, clears its draft and removes only its own schedules, checking that
any others are unchanged. Its ceiling is 60 model requests, three
authored sends, 45 minutes and $0.15 counted, within the unchanged member
budget. Route/rate verification precedes each run, and Docker reads request
counts and the set's shape, never message text. Private evidence is in
`.local/live-evidence/`; actual requests go in TODO.

### Android push through FCM

The service sends Android pushes as FCM HTTP v1 data messages
([ADR 38](adr/0038-mobile-expo-client.md)) for the Firebase project
`enloq-globesoul`, in the owner's Google account, as its service account
`enloq-push`, which holds only the Firebase Cloud Messaging API Admin role.
That account's RSA key pair was generated in the service's `secrets/`
(`fcm-key.pem`, `fcm-cert.pem`, 0600); only the certificate went into the
Cloud console, so the private key exists nowhere else. The service reads four
lines in `config/app.env` at start and refuses to start with only some of
them:

```sh
ENLOQ_FCM_PROJECT=enloq-globesoul
ENLOQ_FCM_ACCOUNT=enloq-push@enloq-globesoul.iam.gserviceaccount.com
ENLOQ_FCM_KEY_ID=c173c7922357abcdd6eb3c672cd63433ceb45e21
ENLOQ_FCM_KEY_REF=secrets/fcm-key.pem
```

Each ready burst gets one copy per phone with push on, unless a trusted client
is reading that chat; a phone that alerted closes the burst, so no browser
chimes for it later, and a failed browser push does not withdraw a phone's
copy. A token FCM reports as unregistered or foreign retires that phone's
destination; a network failure, 429 or 5xx is retried after 15 and 30 seconds;
a 401 renews the cached access token. A forgotten phone's destination is
deleted within a second.

To replace the key, generate a new pair beside the old one in the service
folder, upload its certificate (Cloud console: IAM & Admin → Service accounts
→ `enloq-push` → Keys → Add key → Upload existing key), point `FCM_KEY_REF`
and `FCM_KEY_ID` at the new pair and key ID, restart the service, then delete
the old key in the console:

```sh
(umask 077 && openssl req -x509 -noenc -newkey rsa:2048 -days 3650 \
  -keyout secrets/fcm-key-2.pem -out secrets/fcm-cert-2.pem -subj "/CN=unused")
```

The Android key Firebase created answers only the Firebase Installations and
FCM Registration APIs. Firebase's own `firebase-adminsdk-fbsvc` account has no
keys.

### The VPS's WireGuard client

For Android development from the VPS (ADR 38 Q50-Q51), the host is a client of
the wg-easy server that runs on this same box: `wg-quick@enloq`, enabled at
boot, as wg-easy client mighty03 with the address 10.8.0.4 and a route for
10.8.0.0/24 only, so the server's own traffic never enters the tunnel. Its
configuration, `/etc/wireguard/enloq.conf` (root, 0600), has the shape of a
wg-easy client configuration without its DNS line or IPv6 address and with a
25-second keepalive; never print it or commit it. It holds mighty03's keys,
copied from wg-easy's database; the phone uses mighty02 (10.8.0.3) and
mighty01 belongs to another of the owner's devices. A client can be live on
only one device: two devices on one client take the tunnel from each other,
and wg-easy's endpoint for it flips between them.

Its MTU is 1360, set in the configuration and with
`ip link set dev enloq mtu 1360`. At wg-easy's 1420, packets to the phone
above about 1,370 bytes were dropped silently on the phone's network: small
adb commands answered, but screenshots, larger transfers and Maestro's driver
calls stalled until they timed out (`ping -M do -s 1340 10.8.0.3` passes and
`-s 1350` fails). The lower MTU makes TCP to the phone send smaller segments.
A root-only copy of the 1420 configuration is
`/etc/wireguard/enloq.conf.mtu1420.bak`. Check it with:

```sh
systemctl status wg-quick@enloq
sudo wg show enloq latest-handshakes
ping -c 2 10.8.0.1
ping -c 2 10.8.0.3    # the phone, while its tunnel is on
```

### Android push relay

The service and the Enloq app no longer use ntfy, the earlier UnifiedPush
relay ([ADR 38](adr/0038-mobile-expo-client.md) Q22, Q45): Android push goes
through [FCM](#android-push-through-fcm), and the service reads no relay
settings. Removing ntfy waits for the owner's first real FCM test notification
in Enloq. Until then it may still run as its own compose project in
`~/pi-workspace/ntfy` (`compose.yaml` from `deploy/ntfy/compose.yaml`, the
container `ntfy` on `lrnki`, the edge route for `ntfy.globesoul.com` to
`ntfy:8080` and a DNS-only record). `curl -s https://ntfy.globesoul.com/v1/health`
answers `{"healthy":true}` while it runs.

To remove it once that test has passed: stop and remove the container
(`cd ~/pi-workspace/ntfy && docker compose down`), its block in the edge
Caddyfile ([above](#vps-service)) and the `ntfy.globesoul.com` record with
`cf`, then `deploy/ntfy/` from the repository. List the inert leftovers for
the owner to delete: the `~/pi-workspace/ntfy` folder, the service's
`secrets/ntfy-publish-token`, any `ENLOQ_PUSH_RELAY_*` lines in
`config/app.env` and the `enloq-phone` password in the Mac's login Keychain
(service `ntfy.globesoul.com`).

### Cutover and the Mac fallback

The cutover order makes sure no account is processed and billed by both
instances ([ADR 37](adr/0037-vps-hosting-and-remote-client-trust.md) Q18,
ADR 40 Q8). First stop the Mac service with `./scripts/service stop` and turn
its start at login off in MacHelm's Startup Services. Then place the VPS
credentials, pair a browser, add the accounts and turn notifications on again,
as above. Telegram sessions never change hosts: each account signs in again on
the VPS and the Mac keeps its own sessions. Verify with `/health` on the
public origin, `docker compose ps` and the recent log.

The Mac service stays stopped with start at login off:
`./scripts/service status` reports `"state": "inactive"` and
`"automatic_startup": false`. Its data, sessions and credentials stay untouched
as the fallback until the owner declares the VPS live; then they are retired
under the cleanup permission (ADR 37 Q21).

To fall back, stop the VPS service first (`docker compose stop app` in its
folder), or switch AI processing off there for the accounts the Mac also
holds. The Mac service runs the checkout's current source but serves the
last-built `web/dist`, so after a pull build the UI from the same commit
before starting:

```sh
npm --prefix web run build
./scripts/service start
./scripts/service status
```

## Accounts and browser trust

On the VPS, credentials, pairing and accounts follow
[Credentials and pairing](#credentials-and-pairing). For a fresh Mac
installation, prepare credentials with `./scripts/setup credentials` and
`./scripts/setup proxy-key`, install and start personal mode and run
`./scripts/open`. Use **Add account** for QR or phone and password login;
ordinary upgrades keep the app-owned sessions and need no login. Do not put a
login code, password or authorization file into a chat or Git. Without a
browser, run `./scripts/service stop`, then `./scripts/setup login`, then
`./scripts/service start`. Once the service has started before, the command
uses the same phone challenge and registration as **Add account**, without QR.
[Local credentials](local-credentials.md) describes the references and the
account layout on disk.

`./scripts/open` pairs a browser with the Mac instance:
`./scripts/open --browser Safari` chooses the browser. Compare the code in the
browser with the Mac's approval dialog, then trust it. The private bootstrap
control credential never belongs in a browser field or URL, and a server with
a public origin takes no launcher pairing. Later visits use durable browser
trust; clearing browser storage may require approval again.

The account sheet, opened from the list's header, switches accounts and opens
**Manage accounts** and, in its last row, **Settings**
([ADR 51](adr/0051-telegram-phone-look-and-new-messages.md) Q19). Background
intake continues for every signed-in account. Manage accounts gives each
account Reconnect while it is signed in but disconnected, Log out (Retry
logout while one is pending), Sign in again, Remove local data for a
signed-out account, its **AI processing** switch and **About you**. Logout
preserves drafts; local removal requires every send that may have reached
Telegram to be resolved and preserves spending records. **Settings → Privacy**
lists trusted browsers and devices; Forget blocks every tab or request of that
client without stopping sync or erasing drafts.

The plain address opens the list of this browser tab's account, or, in a fresh
tab, of this browser's last successfully opened account, on the tab (Private,
Groups or Saved) last chosen for it in this browser tab. Only the numeric
account choice is stored durably; Telegram credentials stay on the service.

The address bar names the destination
([ADR 36](adr/0036-account-and-chat-hash-routes.md)):

- `/?account=<Telegram user ID>#<peer>` is one chat. The peer is a user ID, a
  negated basic-group ID, or `-100…` followed by a supergroup ID. The
  account's own user ID opens its Saved tab.
- `/?account=<id>&tab=private`, `&tab=groups` or `&tab=saved` is that
  account's list or Saved Messages, with no chat opened.
- `/?account=<id>` opens that account's list on its remembered tab.

An explicit address wins over saved choices and survives browser pairing and
sign-in to that exact account. A signed-out, removed or otherwise unavailable
target keeps its address and shows a sign-in or unavailable state; no other
account or chat opens in its place. A chat fragment without an account asks
which account to use. Saved account and tab choices change only after a
successful opening.

Account, tab and chat changes add browser history entries. Back, Forward and an
edited fragment pass the same draft and send guards as the controls; a refused
change restores the previous address and is not replayed later. The
conversation's Back opens the list address. **Copy chat link**, in the
Correspondent Profile or Group Profile (ADR 47 Q8), copies the exact
account and chat address and nothing else.

A notification opens its validated exact account and chat under the draft
guard, at that chat's address. A target that no longer exists shows _This
notification's conversation is unavailable_; a test notification only focuses
the app. Later manual navigation takes precedence over delayed or queued
notification navigation; failed and cancelled destinations keep the prior
preference.

_Sync incomplete; retrying bounded recovery_ means the account is connected but
some sync work remains. _Reconnecting_ means connection recovery is still
active, including when the service starts without a network. Ordinary network
failures retry after 2, 4, 8, 16, 32, 64, 128 and 256 seconds, then at most
once every five minutes until connectivity returns. Telegram's longer flood
holds take precedence. Reconnect can restart an account's recovery without
changing its saved authorization or bypassing these holds. _Reconnect
required_ means recovery has stopped for an unknown session key, an unexpected
failure or an unsafe incoming-handler boundary; it never describes a retry
still in progress. A running account whose authorization is revoked or
replaced leaves the switcher and asks to **Sign in again**, as at connect; see
[Telegram sync](history.md#intake).

A disconnected service shows a retry state. Revoked Telegram authorization
instead asks for account login.

## Group native intake

[ADR 34](adr/0034-group-chats-and-daily-briefings.md) adds existing basic
groups and ordinary supergroups under typed account and group identities.
Forum and topic groups, broadcasts, unsupported channel types and protected
targets are excluded before content admission. Sparse metadata can withdraw
eligibility but cannot restore it; restoration requires fresh supported native
evidence. Participant names and native author peers are kept separately from
message direction. An outgoing flag alone never identifies an anonymous or
channel author as the connected personal user.

Group intake reads the rolling seven-day native window regardless of the
group's initially Off AI preference. Each cycle takes at most one 100-message
recent page per group; channel recovery takes at most five 100-item difference
slices. Raw page boundaries survive withheld content, so filtering a protected
post does not turn a full page into false completion. Intake keeps its
observed cutoff, oldest source, omissions, epoch and channel checkpoint. A
too-long difference leaves coverage incomplete and restarts bounded recent
intake; its suffix proves neither a whole day nor older history. Normal
difference effects finish before a checkpoint advances. Exact group reads
require retained known source identities within the 30-day cache and never
backfill missing older history.

Native access and scope evidence is separate from account connectivity. Group
content requests recheck that evidence after awaits and at the paced dispatch
boundary; stale discovery or read results cannot restore revoked access or
admit late sources. Confirmed loss and gaps invalidate the corresponding
observed source proofs while preserving cached originals, drafts and the
durable group preference. A gap opens a new intake epoch and the window is
read again, about seven minutes for a thousand originals. Daily Briefings
stand through it: the group shows its coverage as unverified until the read
completes, a running update waits for its originals to be proven again, and
only an original seen to change withdraws a briefing. Sending, deletion and
media access keep requiring proof in the current epoch. A basic group's
migration records a separate channel peer initially Off; it does not alias
sources, drafts, settings or accepted operations, and stale old-peer metadata
cannot undo the migration. Group intake starts none of the private emotion,
suggestion, memory, alert or background-prefetch paths.

The list's Private, Groups and Saved tab is remembered per account for the
browser tab's session; Saved shows the account's own Saved Messages in place of a list
([ADR 50](adr/0050-icon-tabs-saved-messages-and-one-saved-build.md)), and
private-chat notifications open Private. Group originals use native
participant identity, Unknown participant when absent, and separate anonymous
signatures; only the connected user's verified author identity becomes You.
Viewing history or exact sources reads the 30-day cache without implicit
native backfill.

Group AI processing is initially Off, durable per account and typed group, and
switched in the group's ⋮ sheet beside **Hide bots**; it works only while the
account's AI processing is On. The header's reading toggle stays a separate global preference.
Groups have ordinary Send, attachment and reply controls and no outgoing
Rewrite, tone controls or suggestion generation.

A recent group with retained originals, a draft or unresolved sends stays
visible after access loss, with its exact draft editable and Send disabled. This cached read projection admits no native sync or actions. Valid
cached avatars stay available under the 30-day, size and account limits; new
avatar reads require unchanged native scope and access and exact photo
evidence through metadata, file chunks and wire dispatch. Private emotion,
memory, suggestions, earlier-message processing, group Read and reaction
writes are absent. AI reading shows daily briefings with final-only streaming,
validated coverage and bounded exact cached source navigation; Originals keeps
native chronology. Deletion controls admit only verified own-user current
originals under exact review and native rights; reactions stay display-only.

Group local readiness waits for 30 quiet seconds and uses native author peers,
independent of message direction, AI, threshold, budgets and briefing
generation. Verified own users, unknown authors, service and topic messages
and reaction-only events supply no group dot. Opening places the earliest
ready unseen source at its validated day briefing when covered, otherwise at
its exact original. One focused second acknowledges all ready originals,
including paged and offscreen sources, through current tokens shared across
trusted clients. A briefing version itself creates no unseen identity. No
group sound, push or native read or listen receipt accompanies local viewing.

Outside seven days, a folded **Retained group work** at the foot of Groups
lists saved drafts, selected media and unresolved operations for their
original account and typed peer. Saved drafts stay readable and copyable;
cancelled accepted sends keep their content in the saved operation card.
History, media, avatars and forwarding details use only current retained
bytes within the 30-day source cache. Missing content never starts discovery,
backfill or a native download. New Send, AI opt-in, selection and
deletion are unavailable. Checked deletion Retry may renew exact review for
current own sources within 30 days under restored access and native rights; it
never admits fresh inactive deletion.

**Review and resume** is available only for an access-loss-paused accepted
send with proven non-delivery. Review refreshes native rights and an existing
exact reply, then shows its account, group, text, media and reply without
sending. Its one-use, 120-second token belongs to the trusted client and binds
the complete operation, access revision and source proof. Deliberate Resume
rechecks native rights and all reviewed evidence before requeueing the same
operation and native random IDs. Earlier refusal evidence is archived and
cleared, newer drafts are preserved, and unknown delivery keeps Check delivery
rather than replay. Migration never retargets accepted operations.
Cancellation keeps their authored content.

Normal text, image, album, GIF and sticker sends keep the connected personal
account, exact typed peer, reply and retained bytes. Fresh one-peer metadata,
native rights and personal Send As eligibility are checked before the request,
then guarded through pacing and MTProto handoff; no saved sender identity is
changed and Stars payment is zero. Accepted sends capture the group access
revision. Access loss or a loss and restoration cycle holds proven unhanded
work with its content kept, disabling ordinary Retry until reviewed Resume.
Possible handoffs keep unknown receipts and never resend automatically. These
mechanics are proven with authored offline tests; native group sends,
permissions and deletion still need owner-selected, authorized targets for
qualification.

## Recurring reminders

[ADR 58](adr/0058-recurring-message-reminders.md) owns the behavior. Each
account keeps its schedules in `reminder_schedules`, one row per schedule
with its own id and revision, several per chat; the latest occurrence of each
in `reminder_occurrences`; confirmed-outbox watermarks per chat in
`reminder_outbox`; pending refreshes in `reminder_refresh`; and Kept Messages
in `reminder_kept_days` and `reminder_kept`. Schema 40 moved each schedule of
schema 39, one per chat, with its pending occurrence to the per-schedule
tables, keeping its revision; Weekly became Every week from that day and
Monthly kept its date, and a free-text fallback language became the listed
language it names, else English. These tables survive content expiry and the
recent-chat window. Removing local account data deletes them; logout hides
them and stops alerts.

The service tick runs calendar timing outside AI admission, every second. It
creates each occurrence two minutes before its alert, which becomes pending
at its due time, and then prepares the chat's reminder set through the
manual suggestion path ([inference](inference.md#reminder-sets)); account AI
Off or a refusal leaves the occurrence and its alert as they are. New
content in a chat with an occurrence marks a refresh 30 quiet seconds later.
Schedule and digest preference writes use revisions. Global automation
changes carry a persisted epoch, including Off/On between ticks, so
resumption starts at the next future occurrence without backfill. Digest
preferences live in the member's `reminder_digest` setting. A missed digest
occurrence waits for the next daily slot after recovery; exact alerts recover
the latest missed one once. Without a Message Idea, a native outgoing
timestamp after the due time completes the occurrence, including
external-client sends; with one, only the Telegram confirmation of a send
whose draft took one of its set's options does, which the Send Operation
carries from the draft's mark. An unresolved Send Operation completes
nothing, and a Once schedule ends with its completion.

Kept Messages: saving reads the first explicit date in the Message Idea and
copies up to 20 of that day's cached messages, newest first, with cached
transcripts and image meanings, once the day has ended in the schedule's
timezone; a day still ahead is copied by the tick when it ends, and one that
ended over 30 days ago has nothing to copy. Every 30 seconds the tick mirrors
edits, deletions and newer derivatives of originals still cached; the copy
is deleted with its schedule or when the date leaves the idea.

`GET/POST /api/accounts/{account}/chats/{peer}/reminders` list and create a
chat's schedules, with the chat's recent language; `/preview` calculates the
next delivery and what saving would keep, without saving; `/{id}` reads,
edits (`POST`, with its revision) and removes (`DELETE`) one schedule, and
`/{id}/dismiss` names its pending occurrence. `/open` prepares the chat's set
when it no longer fits. Member-scoped `GET /api/reminders` lists every
schedule across accounts, `GET/POST /api/reminders/settings` own digest time
and timezone, and `GET /api/reminders/timezones` and `/languages` serve the
editor's lists.

The registry's `reminder_notices` is a durable outbox into the existing
browser/phone delivery machinery and permission controls. Each exact
occurrence claims one notice; a digest claims one member/calendar-day
identity, and occurrences joining that day's digest after it alerted join it
silently. Repeated ticks and restarts reuse those claims, without alerting an
old pending occurrence again. Editing/removing a schedule, completion, logout
and lost trust invalidate queued targets before delivery. A client fetches
an alert's text with its credential: an exact alert shows the person's name
and the Message Idea's first line, or "Check in"; a digest shows "Reminders"
with up to three names and "+N". A tap on an alert naming one pending
reminder opens its chat's ordinary route; any other opens `?tab=reminders`.
No message content goes into a push payload.

## Working and recovery

The recent list holds human private chats with native activity in the last
seven days, muted and archived chats included. **New message**, the pencil on
the Private tab, searches private chats, contacts and Telegram's people search
and opens any private chat; such a chat gets no AI until a message makes it
recent ([ADR 51](adr/0051-telegram-phone-look-and-new-messages.md)). Eligible
human chats default to AI On
([ADR 14](adr/0014-account-ai-processing-and-reading-view.md)). Each account's
**AI processing** switch in Manage accounts controls that account across
trusted clients, reconnects and restarts; it does not change any budget. While
it is Off, a private chat's header shows **AI off**, and sync, sending,
originals, cached results and drafts carry on. Inactive chats are excluded
from background work; their drafts stay saved. Opening originals or **Load
earlier messages** never widens model eligibility or rewrites old history;
loading reads the local cache first and asks Telegram only past the locally
contiguous range.

What the AI does with an incoming private block
([ADR 54](adr/0054-incoming-text-combined-not-rewritten.md)):

- **Text is combined, not rewritten.** After 30 quiet seconds a text-only
  block is held and decided from its originals (its emotion, whether a reply
  is useful and whether its wording is hostile). In AI reading its
  originals show together in one bubble headed "N messages · time", with no
  AI label or status.
- **Soften hostile messages.** With the profile switch On (the default),
  explicitly hostile wording is softened, independently of mood. Anger or
  frustration alone leaves text unchanged. The block head ends with
  **Softened**. Unseen readiness waits for its decision and validated output;
  notifications follow their independent arrival screen below.
- **Media is rewritten.** A block with an image, a voice note or a video
  circle is rewritten: transcripts become readable text and images get their
  meaning. Its head reads "AI · N messages · time", and "Not analysed: …"
  names what it skipped. A block of only stickers or files shows **Nothing to
  rewrite**; unknown items say to open Telegram.

Settings → AI → **Minimum characters** (the Processing Character Threshold, 0
to 10,000, initially 200, applied with Save across accounts and clients)
governs group briefings only. The registry's `text_rewriting` switch, off and
not in Settings, would restore private text rewriting under it. Save rechecks
cached pending work without scanning history or retrying unchanged failures,
and already dispatched valid work may finish.

Rewriting existing messages is always a click. A chat with never-processed
blocks with media in its 30-day cache shows "N earlier blocks are not
rewritten" at the top of the loaded history with **Rewrite earlier
messages**, which queues them newest first behind new work without
confirmation. A run shows "Analyzing earlier messages · n of m" with Stop,
which cancels work not yet sent to the model; running attempts finish. The run
stops by itself when less than a quarter of the day's budget remains, when AI
processing is turned Off or when the chat loses eligibility, showing how many
remain and why; it never continues the next day. A current failed or
interrupted block offers **Retry** beside its status and in its sheet, across
the same 30 days ([ADR 31](adr/0031-message-deletion-selection-and-group-ai-controls.md)),
off while the account's AI processing is Off. Retry reviews the group revision
and accepts one new attempt; stale sources, pending work, first-time
processing and validated completed text are refused. A Retry of a text block
that isn't calmed decides it again and ends combined. A completed rewrite has
no manual repeat; qualifying changed evidence is handled automatically, and
editing a source of an older block withdraws its rewrite rather than rerunning
it.

Each block shows its Processing Stage: Waiting for a quiet moment, Queued,
Preparing, Transcribing, Reading images, Analyzing, Waiting for budget,
Provider busy (retrying shortly), Paused after repeated format failures,
Paused while AI processing is Off, Nothing to rewrite, or its error. Waiting
for budget opens Settings → Budget and the format pause opens Guidance
([ADR 45](adr/0045-settings-in-the-enloq-app.md) Q8). Chat rows with pending
work show a small activity marker. Clients update from change notifications on
`/api/events` (chat keys and setting names, never content). Committed changes
wake this feed at once, including from HTTP worker threads; the server keeps a
15-second keepalive and recovery check. Conversation reads coalesce while one
is in flight and apply each received snapshot, so frequent rewrite chunks
cannot starve partial output. A slow client fallback timer covers time-based
transitions and recovery.

Streamed rewrite text replaces the current rendering as it arrives and stays
marked incomplete until final validation. A failed attempt restores an
applicable prior result or the originals; source deletion removes the affected
rendering. Budget exhaustion visibly waits. Off prevents each new provider or
local transcription stage, including after capacity waits. Started work can
finish with its valid result and receipt. A finished transcript waits for On
before rewriting. Incoming revisions coalesce while paused and keep their
original quiet deadline; resuming reconciles cached unread targets without
resetting native cursors or rereading old history. Incoming rewrites have no
individual Stop. See [streaming and speech recovery](inference.md#provisional-streaming).

A private composer has separate, always-visible **Suggestions** and **Send**
buttons. Generation preserves the draft, attachments and reply target. With a
blank draft it offers messages to continue the conversation; with text, the
draft is the starting idea that complete messages develop, and its
[bracketed] text instructs the writer. A manual choice replaces the draft's
text without sending, keeping images, a GIF and the reply target; an
automatic one is inserted at the caret; **Undo suggestion** restores the prior
draft. Suggestions stays disabled while a set for the chat is being prepared
or waits, and Send asks first while [bracketed] text remains. New draft or
source changes withdraw the set without regenerating. Groups and Saved
Messages have ordinary Send and no Suggestions button. Enter adds a
new line. Outgoing Rewrite, tone controls, draft streaming and Stop are removed.

Web URLs, Telegram links, usernames and native named links are tappable in
originals, rewrites and transcripts; they open in a new tab without automatic
site fetching. Draft editing stays plain text. Spinners indicate active work
only.

Long-press, right-click or the menu key opens a message's sheet, and swiping a
message right to left replies wherever its sheet offers Reply (ADR 51). In a
private chat the sheet starts with a reaction row: the owner's own first, six
from the account's palette and + for the rest; choosing one's own removes it.
Its items are Reply, Copy, Select for eligible originals, Show original or Show
AI text, Hide for a deleted message, Retry, Copy transcript, Download and, for
a bot that Hide bots hides, Stop ignoring this bot. On an AI block, Reply
targets the latest source and a reaction asks which original. Copy takes the
selected text, otherwise the visible wording: AI text in AI reading, the exact
original or viewed version in Originals, or a cached transcript; never
metadata or files. Download saves a photo, audio file, video message, video or
document up to 50 MiB under a Telegram-Desktop-like name from its kind and the
message's local date and time (`audio_2026-10-08_13-47-00.ogg`,
`video_….mp4`); a historical version offers it only while its media is cached.
Historical versions and deleted messages offer Copy and, while cached,
Download; their native actions stay read-only. Reaction chips show their exact
source; custom and paid reactions show as ◇ and ⭐. Arriving content does not
close an open sheet.

The paperclip opens Photos, GIFs and Stickers. GIFs use the account's saved
library or a search through the configured bot; stickers use installed packs,
recent and favorites, with a local name, emoji and keyword filter. A tile
stages one selection above the composer and **Send** stays the deliberate
action. A GIF keeps an optional authored caption, and manual suggestions can
add to that text without reading the GIF. A sticker sends on its own: the text
field steps aside as **Draft
kept**, and generation, Undo and suggestion insertion wait until removal or
durable local send acceptance restores the exact text, Undo and reply. With no
kept text, a sticker consumes the reply normally, as a GIF with its caption
does. A selection survives navigation, reload and restart; unavailable media
keeps its failure and needs a deliberate Retry or a new choice. Private byte
ownership, native recovery and resource bounds are in
[media delivery](media-delivery.md#gif-and-sticker-selection-and-delivery).

Response suggestions ([ADR 25](adr/0025-emotion-response-suggestions.md),
[ADR 49](adr/0049-useful-response-suggestions.md),
[ADR 56](adr/0056-idioms-either-signal-hiding-and-memory-off-by-default.md))
appear above the message field in both reading views when the latest incoming
block has a usable non-neutral emotion or the decision model finds a reply
useful: up to three options, each with a purpose or answer-stance label (such
as Accept, Decline or Later) and its full wording. Choosing one inserts it at
the field's last caret, or at the end when none is known, keeps images and the
reply target, returns focus after the inserted text and offers **Undo
suggestion** until the next edit; it never rewrites or sends. **Hide
suggestions** folds that set to **Suggestions (N)** on every client and after
reloads until the next set arrives. Preparation, budget or processing waits
and failures show one small status line, with no Retry, Generate or
Regenerate, and composing stays available. New incoming messages, the owner's
next message from any client, changed sources or 12 hours from the source time
remove the options without touching inserted text. The Settings
**Suggestions** switch hides them for every account and stops queued work.
Each private chat's Correspondent Profile chooses **Off · On · Idioms**; Idiom
sets use idioms, proverbs, set expressions and phrasal verbs in the chat's
language.

The header's robot toggle ([ADR 52](adr/0052-deleted-messages-hiding-and-the-header-ai-toggle.md)
Q13), named "AI view", switches the reading view: pressed shows AI, unpressed
Originals. The choice is global across accounts and restarts, independent of
tone and AI processing, and both views stay usable while processing is Off;
switching preserves drafts and cached results. A private chat's header shows
Back, the photo, the name, the emotion emoji right after it, the **AI off** tag
while processing is Off and the toggle; a group's adds its ⋮. Tapping the
photo, name or emoji opens the Correspondent Profile.

The emoji shows the current inferred emotion; the profile gives its label,
reason, source and time. It expires after 12 hours without starting work.
Unknown and unavailable evidence use 😐, keeping their distinct labels and
reasons; that icon alone does not establish a neutral estimate.

Opening a chat goes to its first ready unseen cached source or reacted-to
message when present, otherwise the latest; sending returns to the latest. A
thin **Unread messages** line marks the earliest ready unseen message, at the
top of the view on opening; it stays while the chat is open and moves above
messages that became ready while the chat was not focused once you return.
Reactions place none, and it is Enloq's local marker, not Telegram's read
state. The row dot records local viewing independently of Telegram reads and
notification delivery; a correspondent's 👍/🤝/👌/👎 also shows it, without
push or sound. A current original viewed before readiness qualifies after its
end is visible for one continuous second. Once incoming sources are ready, one
continuous focused second with the conversation active acknowledges all of
them, including offscreen sources. Hidden panes, background tabs and obscuring
sheets interrupt this dwell. See [local viewing](history.md#local-viewing).
New messages, finished rewrites and media layout changes follow only while at
the bottom. Reading older messages preserves a native-source anchor, including
when changing view or loading earlier originals. **Scroll to latest** resumes
following.

Voice notes play with Telegram's captured waveform (flat bars when the note
carries none); a tap or drag on it seeks without starting playback. Audio files
use the same compact player with a seek bar. Only one voice note, audio file,
video message or video plays at a time. The 1×, 1.5× and 2× speed control is
remembered in this browser. A finished voice note continues with the directly
following native message only when it is also a voice note; playback never
scrolls and stops on leaving the conversation, switching account or reading
view, or closing the page. Video messages play in their circle with sound only
after a tap; videos prepare on **Play**, with complete-file caching and the
browser's controls. **Show transcript** at the end of a voice note or video
message shows its raw transcript, starting a missing one (the owner's own
voice notes included), with a machine-generated label. Waveform and performer
are display-only metadata, never model input or content identity. GIFs loop
silently while visible and focused; static stickers are images; reduced motion
stops animation. Other non-text items render as typed cards: file name, size
or duration for files, poll questions and options, and location and contact
names. Playback and caching are in
[media delivery](media-delivery.md#incoming-media-cache-and-playback).

Voice notes and video circles are prefetched when they arrive, so playback and
seeking read the private media cache; image, GIF and sticker previews are
prepared on first visible use through the account's file lane. Ordinary video
bytes wait for a deliberate Play or Download. The app sends no read
acknowledgements: opening chats, loading originals, playing, prefetching and
transcribing never mark messages as read or voice notes and circles as played
in Telegram, and no listened or unplayed indicators are shown. Logout and
account removal also clear the requesting browser's cached copies; other
browsers revalidate every use, so they can no longer display that media.

**Memory** ([ADR 55](adr/0055-person-and-account-memory-and-traits.md)) has
two parts: Person Memory, what one private chat's person cares about, shown as
**Cares about** in the Correspondent Profile, and Account Memory, what the
account's holder cares about, shown in Manage accounts → **About you**. Both
show the subject's traits beside them, which no model reads. **Remember this
chat**, in each Correspondent Profile, starts Off; while Off, the chat teaches
neither memory and reads neither. Updates fold messages already sent to a model after 30 quiet minutes
in the chat and idle rewriting and transcription across accounts, one memory
call at a time across the server. Entries cite their sources; a deleted or
edited cited message removes its entry without a model call. The owner can
add, edit, remove and clear entries, and the model never changes theirs. A
memory holds at most 40 entries and about 3,000 bytes, 150 characters per
generated entry. A failed update keeps the previous memory with Retry. Memory
survives the 30-day content expiry, is hidden while an account is logged out
and is deleted with the account's local data. Settings → AI → **Memory** is a
server-wide switch; Off keeps memory but neither uses nor updates it.

**Settings** opens from the account sheet's last row and has Notifications,
Budget, AI and Privacy, plus People for the owner
([ADR 47](adr/0047-pairing-settings-menus-and-profiles.md) Q6, Q12; the
[app's runbook](mobile.md) shows each page). Budget shows today's counted
spending against the Daily Budget and the Group Briefing Budget, each with
**Change limit** ($0.00 to $10.00), its reset time and **Recent changes**. The
Daily Budget keeps its $1 default and pays for private AI, arrival screening and manual or automatic suggestions; the $2-default Group Briefing Budget pays for briefing images,
chunks, final synthesis and Retry across all accounts and groups. A change
applies to new stages and is logged; $0 holds new work in that family,
preserving the other ceiling and all spending. Shared refresh protects each
unsaved field, and every Save reads the current server state rather than
restoring an older response. When the private budget is reached, blocks show
_Waiting for budget_ and Settings says when the budget day resets. **Model
calls at once** (1 to 8, default 4) is shared by all accounts and applies at
once; arrival work precedes suggestions and idle memory, and local
transcription runs one at a time. A block whose provider refused the call shows _Provider busy, retrying
shortly_ and retries up to three times at no cost before asking for Retry.

**Guidance**, under Settings → AI, groups the editable Processing Guidance
fields as Rewriting (Faithfulness rules, Incoming rewriting, Soften hostile
messages), Suggestion styles (Professional, Friendly) and Other work
(Emotion extraction, Person memory, Account memory, Response suggestions,
Idioms, When to suggest and Group briefings), each with its default and
**Reset to default**; saving creates a new version used from the next model
call. Emptying the faithfulness rules shows a warning. If a role's output keeps
failing format checks after a change, it pauses and Settings offers **Reset
guidance** or **Keep**; automatic work for that role waits meanwhile.

Draft updates are revision-checked. A failed save blocks navigation; unsaved
text has a per-tab recovery copy until saved. Send shows the submission at
once ([ADR 26](adr/0026-mobile-message-actions-and-send-status.md)), as a sent
message with a small state icon in its time line (ADR 51 Q3); when the service
has saved its exact content, media and reply target, it frees the composer in
the same transaction, and a sticker restores its kept draft. The owner can
write and navigate while the service sends. If the submission cannot be saved
(disconnected, invalid, a changed draft), nothing is queued and the draft
stays. Each chat sends its submissions in order, one native send at a time;
chats are independent within the account's connection and flood limits, and
closing the client does not stop delivery.

A pending send's actions are in its message sheet. A send that Telegram
provably did not execute shows **Not sent** with its reason (for example,
Telegram asked this account to wait, it disconnected before sending, or a
definite Telegram answer while its media was prepared) and holds its chat:
**Retry** is a deliberate new attempt with the same native random IDs, offered
once any requested wait has passed, and **Cancel send** keeps it as a
cancelled item so later submissions continue. Nothing is resent automatically
and there is no offline queue: a restart leaves accepted but unsent work Not
sent, and Not sent work never expires out of its chat's queue. While a
submission is being saved, the composer and its reply target are held; a
Reply chosen meanwhile applies to the next draft. An unknown send shows
**Delivery unknown**, cannot be cancelled or bypassed, and must be checked
with **Check delivery**; this resolves the saved native identifiers and does
not resend the message, and when Telegram cannot be checked, including while
the account is disconnected, it says so and leaves the send unknown.
Already-stored complete receipts or applicable refusals can resolve a check
without connecting. Every unconfirmed item keeps its submitted content and
Copy and media controls; captionless sticker Copy excludes kept draft text,
and a newer draft is never overwritten. A pending item is presentation, not
evidence: it gives way to the native message once Telegram confirms it.
Outgoing messages show one check once Telegram accepted them and two once
Telegram reports them read. See
[Send Operations ownership](media-delivery.md#send-operations-ownership) for
the local lifecycle and evidence boundary. Unknown sends recorded before
[ADR 17](adr/0017-send-hand-off-boundary.md) clear only through a receipt,
Telegram's own stored refusal of that send, or the acceptance-phase data
cleanup. Partial albums keep their individual receipts. Keep originals
available when judging a rewrite; format reliability and semantic quality are
separate.

The Daily Budget (default $1; $0.50 for a member) includes private background
work, its explicit retries, arrival screens and manual or automatic
suggestions across that member's accounts. The separate Group Briefing Budget (default $2; $0.50 for a member)
has no borrowing or combined ceiling. Both use the configured-timezone
dispatch day, immutable purpose attribution and the same monotonic day
watermark; late receipts settle the original family and day. Ledger rows from
before the Group Briefing Budget count as Daily. Content deletion never resets
counted spending. Historical import refuses a different saved family rather
than moving costs or deleting the original evidence.

The exact MiMo and Decider routes and rates are refreshed before dispatch when
needed. While decision requests fail at the route, the owner's Settings → AI
says since when, and one push announced the start (ADR 49 Q14). Look first at
the public model listing and at the proxy's log (`docker logs codex-litellm`):
a 403 there means the caller key lacks the model. `DECISION_MODELS` in
`codex-litellm/scripts/manage.py` and its `provision` fix the `local` key
only; copy that model list to `reply-assistant-vps` with the proxy's
`/key/update`, wait 60 seconds (the proxy caches key permissions that long),
then prove the route with a contentless decisions request on the service's own
key, from inside its container, before any check. No fallback provider,
automatic unchanged resampling or personal Codex CLI is available. Data
eligibility stays in force; the recent list alone does not permit disclosure
to a model provider.

Account processing is stored in the shared registry separately from
authorization and tone. Chat eligibility keeps explicit provenance: old
unmarked native blanket-off records adopt the default; explicit exclusions
stay excluded through rediscovery.

## Native deletion operations

[ADR 31](adr/0031-message-deletion-selection-and-group-ai-controls.md) adopts
deletion of exact current native originals for both participants. The backend
records a reviewed account and chat, original IDs and source revisions under
one client-created operation identity before dispatch. Repeating the same
accepted submission returns that operation; it does not repeat the mutation.
Deletion does not depend on AI processing or consume the model budget.

[ADR 34](adr/0034-group-chats-and-daily-briefings.md) extends the native
contract to existing basic groups and ordinary supergroups, only for originals
whose native author is the connected personal user. Others, anonymous and
channel authors, and unknown authors stay ineligible even with an outgoing or
administrator flag. Group confirmation uses **Delete for everyone**, reviews
the exact account, group, wording and media, and carries a full-source
fingerprint from the cached current original. Acceptance checks that
fingerprint atomically: a changed attachment name or size invalidates review
even when the native timestamp and revision stay the same. Repeated submission
keeps the exact original proof. Selection unfolds the loaded days into
Originals without changing the reading view.

Each sequential group chunk rechecks copied source proofs, native author
identity, the personal connected account, fresh native group metadata and the
captured access and rights generation through pacing and physical MTProto
packing. Basic groups use `messages.deleteMessages` with revoke and the current
native revoke time limit; supergroups use `channels.deleteMessages` for
verified own-user originals, without borrowing moderator authority. Rights and
identity proofs expire after 15 seconds. Server refusal stays authoritative.
Telegram's [basic deletion](https://core.telegram.org/method/messages.deleteMessages)
and [supergroup deletion](https://core.telegram.org/method/channels.deleteMessages)
definitions and current official client behavior
([TDLib](https://github.com/tdlib/td/blob/master/td/telegram/MessagesManager.cpp),
[Telegram Desktop](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/history/history_item.cpp))
own the native semantics; authored SDK tests qualify this implementation
separately.

Known acknowledged chunks can repair their exact local tombstone and
invalidation consequences after access loss without another native call. An
inaccessible group cannot block other queued deletion work. An uncertain chunk
keeps its uncertainty: bounded exact reads of known cached IDs within 30 days
can establish connected-account absence only, never an acknowledgement or
every member's view. Group reaction updates keep aggregate chips, with no
native writes or removals, private assessment events, viewing dots, sound or
push. `test_group_deletion.py`, `test_group_delete_transport.py`,
`test_group_deletion_api.py` and `test_group_deletion_review.py` cover these
source boundaries.

**Select**, in an eligible original's sheet in Originals, starts Message
Selection in both clients ([ADR 46](adr/0046-the-apps-catch-up-units.md)):
round checkboxes beside the originals, where a tap on a row adds or removes it
instead of opening its links or media, and a bar with "N selected", Delete and
Cancel in the composer's place; the composer keeps its draft underneath. A
selection holds one kind, set by its first pick: a selection of deleted
messages offers Hide in place of Delete
([ADR 52](adr/0052-deleted-messages-hiding-and-the-header-ai-toggle.md)). New
arrivals stay unchecked and service messages are excluded. Edits require fresh
review and external deletions prune selection. Cancel, Escape in the web, the
phone's Back, leaving the chat or the AI view, a reload and an accepted
deletion end it. Cancel in the confirmation returns to it.

Every **Delete** opens a confirmation showing the account, conversation, exact
count and original previews before **Delete for both participants**. A changed
unsubmitted review cannot confirm. Once submission starts, its captured
identity and payload stay fixed: repeating Confirm uses the same intent. A lost
response shows **Submission unconfirmed** and **Check submission**, which reads
only the local operation record. **Review submission** reopens an in-memory
capture with the same identity, targets and Retry ancestry; only another
explicit confirmation submits it. An unavailable operation record does not
prove native absence or for-both success. Reload keeps only opaque pending
identities in browser session storage, while durable progress belongs to the
service; it never replays a delete automatically. Fresh owner-selected review
stays available after reload, with service-side overlap and currentness
checks. Current originals show **Deleting** until confirmation. Late cached
reads cannot replace a newer edit, tombstone or expiry decision, or reopen a
stale confirmation.

The service executes individually awaited chunks of at most 100 IDs with
`revoke=True`, rechecking currentness and account ownership at the final
native hand-off. It stops subsequent chunks on refusal, failure or missing
acknowledgement. Deletion requests bypass automatic flood, transient and SDK
resends. Each chunk keeps its own acknowledged, failed, uncertain or unstarted
outcome. Closing a client leaves accepted work in the service; stopping or
restarting the service stops unstarted work and admits only bounded exact
reads for recovery. No startup path repeats deletion.

**Check result** reads all captured identities in bounded exact batches. It
distinguishes messages still present, native connected-account absence and
unavailable reads. Neither absence nor cache expiry supplies a for-both
receipt. **Retry remaining** requires fresh review and confirmation of
still-present unresolved originals under a new operation identity; completed
and absent originals are excluded. Unresolved evidence survives content
expiry, and local account removal waits for unresolved operations.
Acknowledged native results establish Telegram's acceptance of the submitted
for-both contract; correspondent disappearance is separate evidence.

Confirmed deletion and observed native absence use the existing ingestion
pipeline. A message deleted elsewhere keeps what it last said, with a trash
icon, until the owner hides it (ADR 52); messages in an Enloq deletion that
reached Telegram leave at once. Already observed versions, transcripts and
cached media stay until their original expiry. A pending local consequence
marker commits with the tombstone and clears after dependency invalidation
succeeds. Startup and housekeeping replay unfinished local consequences,
including cited generated memory after source expiry, without recreating
expired content or calling Telegram or provider APIs. Owner-authored memory
stays protected. Late downloads and transcripts cannot repopulate expired or
deleted source content. When a native deletion update omits its peer after
content expiry, only a unique retained same-account private identity can
resolve it: generated citations, reviewed deletion membership, existing block
sources or pending consequences. Ambiguous, owner-only, unknown, service and
channel evidence admits no source. Resolve every member before effects run; a
failed member keeps its recovery marker while the batch continues to record
and process the other members.

Replies to pending or unresolved deletion targets stay held at the ordered
queue's head. Send preparation, queue claim and actual native append all check
the target, including async upload, pacing and flood waits and SDK protocol
corrections. Confirmed target deletion refuses a provably unhanded send while
keeping its submitted text, images and reply reference. Possibly handed sends
keep their unknown evidence and Check delivery; they are never resent
automatically. Newer composer work is preserved. Logout drains work and keeps
the operation ledger; expiry removes previews rather than uncertainty or reply
holds.

Backend fault-injection and API checks are in
`backend/tests/test_deletion_operations.py`,
`backend/tests/test_deletion_transport.py`,
`backend/tests/test_deletion_api.py`,
`backend/tests/test_deletion_reply_holds.py`,
`backend/tests/test_deletion_consequences.py` and
`backend/tests/test_deletion_lifecycle.py`, with expired peerless updates
covered by `backend/tests/test_peerless_deletion.py`. They use authored data
and fake native responses. The web's selection and recovery journey is
`web/journeys/deletion.spec.ts`.

Native verification uses exact owner-authorized disposable private-chat
originals. Fixture sends require explicit authorization for that finite run.
Record account, chat, IDs, current revisions, mutation, read and time
ceilings and any affected AI role's route, rates and call, time and cost bounds
before the run. Use the existing service and session owner; create no other
person's deletes or second authorization client. A finite native client must
initialize metadata-only with a passive update loop: Telethon otherwise asks
for a broad update difference even with `catch_up=False`. If no eligible
authorized targets are identified, keep native proof as pending.

Record acknowledged for-both requests and connected-account exact readback
separately, with each chunk's partial, uncertain or unstarted evidence. A lost
acknowledgement followed by native absence stays unconfirmed for both.
Observing the correspondent's disappearance is separate proof. Ordinary AI
consequences keep eligibility, privacy and the Daily Budget. An authored
journey or backend result does not establish native deletion or production
adoption.

Plain-text for-both deletion of private-chat originals between two owned
accounts has been verified natively through the application's delivery and
deletion interfaces, including exact readback of absence from both accounts.
Not yet verified natively: foreground deletion from a client, attachment
deletion, multi-chunk and uncertain recovery, unsolicited native deletion
updates and any group deletion.

## Current profile photos

Rows, the conversation header and account controls use the current small
Telegram photo, with a fixed-size initials fallback. Photos are read through
the existing account session owner and served only through authenticated
account-scoped routes. They never become model input. Native discovery
refreshes chat photo identities; the connected account identity refreshes at
most once per five minutes. Changed or removed identities invalidate cached
bytes and stale client requests.

The account-local `avatars/` cache uses private directories and files, at most
256 entries, a 30-day expiry, a 256 KiB download bound and a 1024px,
one-megapixel decoding bound. Downloads expire after 30 seconds, simultaneous
loads coalesce, and an unavailable image has a 60-second negative cache.
Logging out a running account clears photos; account removal and retention
include the cache. These are display images, separate from message
attachments and incoming image understanding.

## Local reading and native versions

The reading view is global ([Working and recovery](#working-and-recovery)),
but a rewritten block's sheet offers **Show original** (**Show originals** for
several sources) and its originals' sheets **Show AI text**, both with the
robot icon. The choice holds for that block alone until the header's toggle
changes ([ADR 52](adr/0052-deleted-messages-hiding-and-the-header-ai-toggle.md)
Q13) and starts no AI work. Local choices survive refresh, streaming and native
changes while the chat stays open; reopening, reloading or changing the global
view resets them. No source or version choice edits Telegram or generates a
reply.

A rewritten block shows its AI text under "AI · N messages · time", always
with the source count, and its attachments once. Originals show each native
message with its own time in the configured timezone, reaction chips and raw
transcript; in groups, other people's message runs carry their name and
avatar (ADR 51). Sources expand through bounded local cache pages across the
ordinary native-page boundary. Missing sources leave the kept attachments
accessible with a truthful status.

Each forwarded original has a non-tappable line with its origin and original
date and time in the configured timezone, the year only outside the current
year: **Forwarded from News Channel (Author) · Mar 4, 2024 · 10:04 PM**,
**Forwarded message · …** when the source name is unavailable, or **Imported ·
…** for a message imported from another app. AI bubbles show one such line per
origin, in first-appearance order, with a count above one and a date range
across days; nameless sources group as **Forwarded messages**. Copy never
includes these lines. Previously cached messages gain the metadata
asynchronously when opened or loaded; unavailable reads leave the content
usable and retry later ([history](history.md#pacing-and-read-state)).

An edited message shows a passive pencil, which can appear without captured
earlier content. In Originals, Previous, "2/3" and Next step through observed
native versions with their cached media; an earlier version's sheet offers
only Copy and, while cached, Download. A deleted message keeps what it last
said with a trash icon in both views; without a cached copy its media reads
"no longer available". Its sheet offers Copy and **Hide**, and Select can hide
several: hiding is final and both clients agree. A message deleted before
Enloq saw its content, and a deleted service notice, are not shown at all.

## Desktop Web Push

[ADR 20](adr/0020-minimal-reading-and-ready-notifications.md) notifies from
service arrival. With **Soften hostile messages** On, the pinned decision model
screens original text or captions within five seconds, including queueing.
Clean originals notify as soon as screened; hostile text waits for validated
softening. AI Off, the switch Off, unavailable budget or route, failed or
malformed screening, deadline and unavailable softening use originals as soon
as known. Textless media uses a typed label. No memory, history scan or media
preparation runs for a screen. Combined-block processing and Unseen readiness
keep their independent 30-second/full-output rules.

The first eligible preview in a 30-second arrival burst alerts; later arrivals
and late softening silently update that notification. A clean later arrival
can notify before an earlier hostile one, and the older completion cannot
replace the newer preview. Each update has a fresh opaque event ID and stable
group; source, mute, account, foreground and expiry guards are rechecked before
delivery. The web worker uses that group as its notification tag; Android uses
its existing per-chat notice identifier. Known-invalid locators show no banner;
Android retains its generic fallback only when the service cannot be reached.

A newly paired browser asks once
([ADR 47](adr/0047-pairing-settings-menus-and-profiles.md) Q5): when the list
first shows, a browser that can show notifications, hasn't decided and isn't
already on opens a **Turn on notifications?** sheet, whose Turn on starts the
browser's prompt, since browsers prompt only after a click. The ask belongs to
the browser's trusted entry (kept in its `localStorage`), so pairing again
asks again; declining leaves Settings → Notifications.

Settings → Notifications has this browser's **Notifications** switch, its
**Sound** switch and **Send a test notification**. Sound defaults On and
unlocks after interaction in the app. Turning Notifications on requests
permission and subscribes; the last browser to turn them on is the member's
one browser push destination, and phones are additional destinations. Denial,
unsupported browsers, expired registrations and audio restrictions are shown
there. The test sends an opaque test event through push, and the worker gets
the authored visible text from the service; with only Sound on it plays one
chime. The push test reports the push service's acceptance, not a banner the
owner saw.

The notification worker intercepts no page or media requests and caches no
correspondence for offline reading. It keeps only short-lived event IDs to
suppress transport retries, revalidates browser, source and account access
before showing a visible notification, and uses the draft-saving navigation
guard when a click opens its account and chat. Payload protection is RFC 8188
`aes128gcm` with VAPID via [pywebpush](https://github.com/web-push-libs/pywebpush).
The payload carries only the opaque event ID, expiry and silent flag; sender,
account name, count, chat identity and preview text come only from the
worker's authenticated validation, and the visible banner keeps its sender and
preview. Outbound destinations are restricted to the standard Apple, Chromium
and Firefox push services over HTTPS, without redirects; expired endpoints are
retired. Delivery has at most three attempts within its five-minute lifetime.
Subscription state and push-service acceptance do not prove OS display or
sound. Apple's visible-notification requirement and platform behavior are
documented in [Apple Web Push integration](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers).
The service must be running to produce alerts. `backend/tests/test_push.py`
covers offline decryption of both delivery paths.

Brave subscribes only after the owner turns on **Use Google services for push
messaging** in `brave://settings/privacy` and restarts Brave. That is a
browser-wide privacy setting: the owner changes it, never the app or an agent.
macOS notification permission for the browser is also owner-controlled.
Without the setting, turning notifications on fails with `Registration failed -
push service error`
([Brave community](https://community.brave.com/t/push-notifications-not-working-registration-failed-push-service-error/314471)).
With it, the subscription uses Google's push service (`fcm.googleapis.com`),
which carries only the encrypted opaque payload. Brave's on-exit clearing of
**Site and Shields settings** removes the origin's notification permission
while browser trust survives; the next presence report then retires the
destination. Keep that on-exit option off, and after any reset of the site's
permissions turn Notifications on again.

The web plays one chime. The service still stores a sound preset and accepts
one custom audio file per trusted browser (a counted raw-request ceiling before
JSON parsing, strict base64, size, safe filename metadata and recognized audio
containers, served only to that browser's exact current revision with
`no-store` and `nosniff`), but no client offers them, and the web plays its
chime for any preset. Forgetting the browser makes the file unavailable at
once and removes it at the next notification cleanup tick; custom audio
reaches neither Telegram nor a model or push provider.
`backend/tests/test_notification_sounds.py` covers those bounds, and
[sound provenance](../web/src/assets/notification-sounds.md) lists the bundled
chime's author, license and source.

A focused matching account and chat stays silent, including while reading
history. A focused other chat uses one app chime. With no focused app, the
member's push browser and phones receive the alert; an open audio-capable tab
is the fallback when push is unavailable. Inactive selected chats do not count
as attention. Presence expires after twelve seconds and chime claims are
consumed once across tabs. Sound Off prevents app chimes and requests silent OS
push; system restrictions still apply. The app never adds a chime alongside a
push notification. Groups never alert.
