---
status: accepted
---

# Decision-model emotion classification

## Context

Recipient Emotion ([ADR 22](0022-recipient-emotion-and-adaptive-rewriting.md))
began as an extra field of the incoming rewrite, with free-form output and a
metadata-only failure path, and Reaction Assessment spent tens of seconds of
hidden reasoning on a two-field answer. Perplexity's decision model writes no
text: it reads the content sent as `state` and answers named questions with
probabilities. A choice question returns one probability per option, the most
likely option and a self-reported `confidence`; a yes/no question returns the
probability of yes; one request may carry up to 128 questions. Its open
weights are too large to run on the owner's machine.

Before adoption, a finite live check on authored Russian text judged it
suitable: text cases matched their authored codes, including sarcasm, slang,
mixed languages, a formal complaint and quoted or forwarded feelings, and
reactions matched once the instructions named each emoji's conventional
meaning. The agent wrote the expected codes itself and the sample was small,
so this supports adoption without establishing semantic reliability.

## Decision

**Adoption and scope (Q1, Q2).** The decision model, not a writing model,
assigns Recipient Emotion. Emotion is not part of the incoming rewrite's
output or its instructions; a separate Emotion Classification request assigns
it. ADR 22's other rules stand: the vocabulary and its display, the permitted
sources and exclusions, the 12-hour lifetime, freshness, withdrawal, reuse and
provenance. Adaptive tone, which also used emotion, was removed
([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)).

**Reaction Assessment (Q3).** One decision request per assessment asks a
choice question for the emotion code and a yes/no question for whether
suggestions would be useful.
[ADR 26](0026-mobile-message-actions-and-send-status.md)'s triggers, quiet
period, inputs, reuse, currentness and 12-hour lifetime are unchanged.

**The route (Q4).** Every decision role uses one exact route,
`perplexity/pplx-decider-v1.2` at checkpoint `-20261016`
([ADR 49](0049-useful-response-suggestions.md) Q1), adopted at $0.02 per
million input tokens with free output.

- **Provider and path.** Perplexity only, on OpenRouter's Decisions endpoint
  (`/api/alpha/decisions`), reached through the owner's LiteLLM proxy's
  pass-through ([ADR 39](0039-litellm-proxy-and-vps-speech.md)). Fallbacks are
  disabled: no fallback to MiMo or another Decider, and no automatic move to a
  later checkpoint.
- **Verification.** OpenRouter's public listing is verified before dispatch:
  exactly one Perplexity endpoint at status 0, within the adopted rates.
- **Unavailability.** An unavailable route is a no-work refusal that leaves
  emotion unavailable; Settings → AI then shows the owner that the decision
  model is unavailable (ADR 49 Q14).
- **Identity.** Requests carry the same private app-level `user` pseudonym as
  MiMo requests.
- **Terms.** Perplexity publishes no retention or training terms for this API,
  and OpenRouter lists none. The owner accepted the route without reviewing
  retention. The proxy's pass-through ignores the redaction request, so
  decision requests and their answers stay in the proxy's spend log for up to
  30 days. This is not a privacy guarantee and doesn't settle the Telegram
  AI-content boundary in the source reuse note.

The roles on this route are Emotion Classification and Reaction Assessment,
the usefulness question and suggestion labels (ADR 49), sarcasm (ADR 53),
group ad and bot filtering ([ADR 34](0034-group-chats-and-daily-briefings.md))
and trait scales ([ADR 55](0055-person-and-account-memory-and-traits.md)).
Generation roles keep their own routes
([ADR 29](0029-mimo-route-and-structured-incoming-rewriting.md)).

**What one incoming request asks.** The emotion code, whether suggestions
would be useful (ADR 49 Q2) and whether the latest block is sarcastic, a fixed
question with no owner field (ADR 53).

**Timing (Q5).** A request runs only for a private chat's block that can
become the current source under ADR 22, after its 30 quiet seconds:

- **Text only.** A block of text is decided from its originals
  ([ADR 54](0054-incoming-text-combined-not-rewritten.md)).
- **With media, calming on.** In a chat with calming on, the default, every
  block is decided from its originals before any rewrite, and image meanings
  don't feed the decision (ADR 53 Q7).
- **With media, calming off.** The decision runs right after the rewrite
  validates and publishes, using its freshly generated image meanings. A
  failed or withheld rewrite leaves emotion unavailable.

Every request reads the original wording, transcripts, image meanings when it
has them, quote and forward attribution and bounded Supporting Context. Raw
images, Person Memory and Account Memory are never sent, and nothing is
inferred from faces. The header shows no emoji until the decision publishes,
and the Correspondent Profile says it is waiting. Suggestions follow the
decision. In a chat with calming on, readiness and notifications wait for the
decision; otherwise they don't wait for emotion.

**Uncertainty (Q6).** `unknown` is an explicit option. When the selected
option's probability is below 0.35, the stored code is `unknown`.
Probabilities and confidence are stored for diagnosis and never displayed, so
ADR 22's rule against showing numerical confidence stands.

**Guidance (Q7).** The owner-editable Emotion extraction field is shared by
incoming classification and Reaction Assessment. Its text becomes the emotion
question's instructions after a fixed core covering the permitted sources,
quote and forward attribution, no facial or acoustic inference and the code
descriptions. For reactions, the core adds each emoji's conventional meaning
toward its target (👍 approval, 🤝 agreement or a settled deal, 👌 fine, 👎
disapproval): agreement or approval reads as positive unless the context shows
reluctance. This relaxes, for emotion only, the rule that an emoji is never
agreement; Response Suggestions still take no commitment, agreement or fact
from a reaction. The usefulness questions follow ADR 49's When to suggest
field. Changed guidance applies to future calls and regenerates nothing.

**Failures (Q8).** A missing or invalid answer, or an unknown option, leaves
emotion unavailable and records a fixed content-free diagnostic.

- **Other roles.** It never pauses another role, reruns the rewrite or
  retries automatically.
- **Cost.** Its cost counts.
- **Refusals.** A provider refusal follows the background backoff of 30, 60
  and 120 seconds, three tries at most.
- **Account AI Off.** Off stops a classification that has not started.

The incoming role's format-failure count doesn't depend on emotion.

**Capacity and budget (Q9).** A request takes a slot under the one
Concurrency Limit, in the priority class of the work it follows (new work,
catch-up or reaction). It counts in the Daily Budget of the member whose
account it serves ([ADR 48](0048-members-on-the-shared-service.md)) from the
receipt's `usage.cost`, the larger of charged and upstream cost when both
appear, with the flat $0.01 estimate for an unknown outcome. No new budget,
setting or capacity is added.

**Other uses (Q10).** Each further use of the decision model needs its own
adopted decision; the uses above were adopted that way. The
[TODO](../plans/TODO.md) keeps the remaining proposal: a completeness and
invention check of rewritten text, a guard against suggestion options that add
meaning the owner never gave, and a source-language check.

**The route check (Q11).** Before a decision route switches,
`scripts/check-emotion.py` runs a finite in-app check through the real engine
and ordinary accounting, with the service stopped and Telegram neither read
nor sent: authored Russian, English and mixed-language incoming blocks
(including an image meaning and forwarded and quoted feelings), authored
reactions with their usefulness, and authored group ad-filter cases. It allows
at most 40 requests, 20 minutes and $0.01 counted (ADR 49 Q1). A malformed
answer or a route failure blocks the switch; weak but well-formed results are
recorded as Low quality.

**Existing records.** Emotion records from before a route change stay valid
until they expire; nothing is reprocessed.

## Consequences

- A typed classifier removes emotion's free-form output and metadata-only
  failure path, shrinks the incoming rewrite's contract and makes uncertainty
  explicit. Reaction Assessment takes about a second, for a tiny fraction of a
  cent.
- The app depends on a second provider, on an alpha endpoint with undocumented
  retention, and makes one decision request per qualifying block. Decided from
  originals, an image-heavy block is judged without its image meaning.
- Not adopted: classifying in parallel with the rewrite in a chat with
  calming off (it would miss new image meanings), and sending raw images
  (which would allow facial inference).
- Known limits, kept in [inference](../inference.md#retained-acceptance-limits)
  for the owner's quality review: the decision model judged authored
  reactions that called for an answer not useful, so reaction-derived
  suggestions are rare.
- [Inference](../inference.md#emotion-classification) owns the request and
  validation mechanics.
