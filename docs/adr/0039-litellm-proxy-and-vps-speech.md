---
status: accepted
---

# Model calls through the owner's LiteLLM proxy, speech on the VPS

## Context

The owner's LiteLLM proxy (the `codex-litellm` project) runs on the shared VPS
and answers at `https://llm.globesoul.com`. It holds the provider keys and a
self-hosted speech service. Enloq calls every model through it, so the
service holds no provider key of its own. This sets the transport for
[ADR 29](0029-mimo-route-and-structured-incoming-rewriting.md)'s generation route,
[ADR 30](0030-decider-emotion-classification.md)'s Decider route and
[ADR 37](0037-vps-hosting-and-remote-client-trust.md) Q16's speech model;
their models, roles and budgets are theirs.

For speech, a comparison on synthetic English and Russian clips found the
VPS's faster-whisper word for word at about half real time, and ElevenLabs
Scribe v2 the fastest accurate hosted model at the lowest price among them.
The owner chose self-hosted speech-to-text with that hosted fallback.

## Decision

**One endpoint and one key.** Every live adapter calls the proxy with one
caller key. The endpoint is configuration: `MUSE_ENDPOINT_REF` holds the
proxy's OpenAI-compatible base URL, which must be an `https` URL ending in
`/v1`, with no user info, query or fragment. `MUSE_API_KEY_REF` holds an `sk-`
caller key that is not an OpenRouter key (`sk-or-`). `./scripts/setup
proxy-key` saves both. The deployment's endpoint is
`https://llm.globesoul.com/v1`, and the proxy's pass-through routes sit beside
`/v1` on the same origin. The reference names predate the current routes and
are kept. Offline mode constructs no live adapter. Later routes, such as Meta's
Muse Spark 1.3 ([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)),
use the same endpoint and key.

**The generation route.** It is the proxy's `chatgpt/gpt-6.1-terra` (ADR 29):
LiteLLM's ChatGPT provider, which reaches the owner's ChatGPT subscription
through the Codex backend at the route's default medium effort.

- **Verification.** The service reads the proxy's `model/info` for exactly
  one entry of that name, with vision support and no per-token rate. ADR 29's
  24-hour rule applies.
- **Requests.** They send no OpenRouter `provider` block, no reasoning
  setting and no `extra_body`, which would replace the route's own. The route
  drops `response_format` and token caps, so none is sent: the role's JSON
  Schema ends the instructions (ADR 29 Q15). Manual suggestions use the same
  role as automatic suggestions; outgoing Rewrite is removed (ADR 25).
  Reasoning stays hidden and is discarded unread; content keeps the 1 MiB
  capture limit, and the wire may carry up to 16 MiB.
- **Answers.** The response must name the route as its `model`. The private
  `user` pseudonym (one per member, [ADR 48](0048-members-on-the-shared-service.md))
  is sent, and LiteLLM's ChatGPT provider does not forward it.
- **Receipts.** The subscription's receipts are $0, as for Ask this group's
  writer ([ADR 59](0059-ask-this-group.md) Q5). Muse's costs come from the
  proxy: `usage.cost` in a stream's final chunk, or the
  `x-litellm-response-cost` header otherwise.
- **Route changes.** A change of route restarts the format-failure counts and
  makes group briefings updateable once.

**Decider.** Requests go to the proxy's pass-through,
`/openrouter/api/alpha/decisions` on the endpoint's origin
(`https://llm.globesoul.com/openrouter/api/alpha/decisions` in the
deployment). It forwards the body unchanged to OpenRouter with the proxy's key
and returns OpenRouter's answer as sent. The route, checkpoint, provider
pinning, `user` pseudonym and response checks are ADR 30's and
[ADR 49](0049-useful-response-suggestions.md)'s; OpenRouter's public listing
verifies the route and its rates.

**Speech.** The personal instance transcribes through the proxy.

- **Up to two minutes.** A note goes to the proxy's `whisper-large-v3-turbo`
  route: faster-whisper with the int8 conversion of Whisper large-v3-turbo on
  the VPS's CPU (`dropbox-dash/faster-whisper-large-v3-turbo` at a pinned
  revision), each window decoded on its own with no voice-activity filter. So
  ADR 37 Q16's model and denoising-off rule hold, and that audio reaches no
  model provider. It runs at about half real time.
- **The hosted fallback.** A longer note, or a shorter one the VPS cannot
  answer (unreachable, a server error, or no answer within the note's length
  plus a minute), goes to ElevenLabs Scribe v2 (`elevenlabs/scribe-v2`)
  through the proxy's pass-through to OpenRouter's speech-to-text, as Opus
  converted by the service.
- **Its cost.** That call is paid. It is admitted against the member's Daily
  Budget, reserved at the flat unknown estimate and receipted with
  OpenRouter's cost, in the `speech` engine and `transcript` role; a refusal
  that did no work counts nothing. It sends no audio when the listing's rate
  exceeds $0.0000611 a second, the model's list price ($0.22 an hour).
- **Back to the VPS.** A long note the hosted model cannot take (budget
  reached, rate, refusal or failure) falls back to the VPS.
- **Records.** Transcripts record the model that produced them. The Mac's MLX
  worker stays for offline fixtures and `scripts/check-voice.py`.

**Proxy storage.** The proxy keeps request and response bodies in its spend
log (`store_prompts_in_spend_logs`). Every adapter sends
`x-litellm-enable-message-redaction: true`, which keeps generation and Muse
prompts and replies and VPS transcripts out of that log. The pass-through routes ignore
it: Decider requests, whose `state` carries message text, are stored with
their answers. The speech pass-through is not logged. The proxy deletes
spend-log rows after 30 days (`maximum_spend_logs_retention_period`, for every
caller), matching the app's own content expiry.

## Consequences

- Correspondents' voices reach ElevenLabs for long notes and VPS outages; ADR
  37's rule that audio never reaches a provider holds for the VPS route only.
  OpenRouter publishes no retention or training terms for these speech
  providers.
- Message text in Decider requests is stored on the VPS for up to 30 days, in
  the proxy's database beside the app's own.
- The app depends on the VPS for every live model call.
