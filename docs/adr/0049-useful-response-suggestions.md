---
status: accepted
---

# Useful response suggestions

## Context

[ADR 25](0025-emotion-response-suggestions.md)'s Response Suggestions
followed only a validated emotion, and emotion followed only a rewrite, so
short messages under [ADR 35](0035-processing-character-threshold-and-empty-rewrites.md)'s
threshold never reached either. The decision route of
[ADR 30](0030-decider-emotion-classification.md), Decider V1.1, also lost its
OpenRouter endpoint, which stopped emotion, Reaction Assessment, suggestions
and group ad filtering. This ADR moves every decision role to Decider V1.2,
asks the decision model whether suggestions would be useful, decides short
blocks from their originals, and lets suggestions answer with contrasting
stances.

## Decision

**Decider V1.2 (Q1).** Every decision role uses
`perplexity/pplx-decider-v1.2` at checkpoint `-20261016`: Emotion
Classification, Reaction Assessment, group ad and bot filtering, the
usefulness question below and the suggestion labels (Q19). It is pinned as
V1.1 was: Perplexity only, on OpenRouter's Decisions endpoint through the
proxy, an exact route, no fallback, no automatic later checkpoint, and the
listing verified before dispatch. It costs $0.02 per million input tokens,
with free output. The switch was gated by `scripts/check-emotion.py`, a
finite in-app check of authored emotion, reaction and ad-filter cases (at
most 40 requests, 20 minutes and $0.01). Not adopted: Decider Flash V1.2,
and letting MiMo decide by returning no options.

**The usefulness question (Q2).** The incoming Emotion Classification
request asks a `suggestions_useful` yes/no question beside its emotion
question, as Reaction Assessment already did. Yes at 0.5 or more is an
implementation default, and a record from before the question counts as no.
The answer only gates suggestions: it is never shown as a "needs reply" mark,
count, sort or notification, so ADR 13's retired attention tracking stays
retired. Q2 C had required usefulness *and* a usable non-neutral emotion; for
incoming blocks that is replaced by
[ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md)'s
either signal (a block without a usable non-neutral emotion needs usefulness
of 0.7 or more, ADR 56 Q19). Reactions still need both.

**Held blocks are decided from their originals (Q3).** Every new or changed
eligible private block gets the decision request, emotion and usefulness,
after its 30 quiet seconds, whether or not it is rewritten.

- A held block is decided once its quiet has passed, by the scheduler's
  admission pass, from the cache only: originals, bounded Supporting Context,
  a cached direct reply, cached transcripts and image meanings. Nothing is
  read from Telegram, and each held revision is decided once; a changed block
  is held anew.
- Since [ADR 54](0054-incoming-text-combined-not-rewritten.md) every text
  block is held this way, and in a chat with calming on so is a block with
  media ([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)
  Q7). In a chat with calming off, a block with images, voice notes or
  circles is classified after its rewrite, which supplies image meanings.
- A block of Non-AI Media only makes no call.

**Contrasting answers (Q4).** When the person asks for an answer, a decision
or an action, options may answer with different stances side by side, so the
owner's choice supplies the intent: for example "Yes, I'll be there" /
"Sorry, I can't make it this time" / "Let me check and get back to you".

- **Still forbidden:** facts that aren't in the supplied conversation (times,
  places, amounts, reasons, the owner's plans or whereabouts), asserting the
  owner's feelings, admitting fault and pressure. ADR 56 Q4 relaxes advice and
  feelings for idiom sets only.
- **Unchanged:** acknowledgement, sympathy, celebration and one gentle
  question stay, and a reaction never counts as agreement.

Not adopted: drafting the owner's most likely answer.

**Guidance for usefulness (Q5).** The usefulness questions have an editable
guidance field (Q9) with the usual Save, Reset and versions. Not adopted:
fixed wording.

**Q6:** delivery scope; no lasting rule.

**The proxy's model list (Q7).** The proxy lists V1.2 in `DECISION_MODELS`.
The service calls the proxy with its own key, `reply-assistant-vps`, which
mirrors the `local` caller's models
([ADR 40](0040-shared-vps-deployment-and-development.md) Q7). `manage.py
provision` changes only `local`, so after it copy the list to
`reply-assistant-vps` with `/key/update`, then prove it with a contentless
decisions request on the service's key, from inside its container, before a
check.

**Answer labels (Q8).** Accept, Decline and Later join Acknowledge, Support,
Clarify and Celebrate. A set holding `accept` or `decline` holds another
stance beside it (Q20).

**When to suggest (Q9).** The field is "When to suggest", under Guidance's
Other work, explained as "When short reply suggestions are worth offering".
It follows the fixed core of both usefulness questions, incoming blocks and
reactions. Its shipped default: "Answer yes when the latest communication
asks the owner something, requests or proposes something, needs a decision,
or shares news or feelings a reply would naturally acknowledge. Answer no when
it needs no answer: ok, thanks, a goodbye, an answer that closes the owner's
own question, a forward or link with no question of its own, or automated
content."

**One switch (Q10).** The one Suggestions switch, the owner's and
server-wide, covers every suggestion, answers included. ADR 56 Q12 adds each
chat's own choice beneath it.

**Folding (Q11).** Both clients can fold a set. Since ADR 56 Q12 B a folded
set stays folded on every client until the next set arrives.

**The owner's reaction answers (Q12).** The owner's own reaction, from any
Telegram client, on a message of the latest incoming block answers that block
like a sent message and withdraws its suggestions. It answers only when first
seen after the block's latest message, and it is recorded when first seen,
so removing it brings nothing back. Reaction-derived suggestions keep their
rule: the owner's reactions don't answer them.

**The feature's finite check (Q13).** `scripts/check-useful-suggestions.py`
runs authored cases through the real paths:

- decision requests on short and long Russian, English and mixed blocks:
  questions, requests and invitations with and without emotion, emotional
  news, thanks, ok, goodbyes and forwards;
- suggestion requests on answer cases;
- at most 40 decision and 16 MiMo requests, 30 minutes and $0.02.

Malformed output or a route failure blocks; weak results are Low quality. If
clear cases miss, the numbers go to the owner instead of a change.

**Decision-model status (Q14).** While decision requests are refused or fail
at the route (the listing, the proxy or an HTTP failure), Settings → AI shows
the owner "Decision model unavailable" and since when. One content-free push
goes to the owner's devices when it starts, opening Settings → AI, and the
next answered request clears it. A 402 counts as an outage and 429
throttling doesn't; a 401 or 403, including the proxy refusing a model its
key lacks, is a no-work refusal that counts nothing. Not adopted: following
Perplexity's successor automatically, and silence. Re-reading the listing
sooner than every 24 hours stays a separate TODO question.

**V1.1 left the proxy (Q15).** V1.1 left the `local` and
`reply-assistant-vps` keys once the owner's other proxy user, SkyrimNet, had
moved to V1.2.

**Q16:** a check allowance for the switch; no lasting rule.

**Later tests (Q17).** The owner pre-authorized later tests without asking:
"don't ask me about subsequent tests, you are allowed to make up to 10000
requests".

**Labels follow stances (Q18).** An answer option carries its stance's
purpose: an option that says yes is `accept` even when it also asks. It is
part of shipped-defaults revision 11's wording.

**The decision model labels suggestions (Q19).** MiMo's labels proved
unreliable, so every written set gets one request on the decision route,
with a `purpose_N` choice per reply over the seven purposes and fixed
criteria.

- **What it reads:** the conversation, or the reaction, and the replies'
  text; never MiMo's labels.
- **Labels and one-sidedness:** its answers replace MiMo's labels, and only
  these labels decide whether a set is one-sided.
- **Failures:** a failed label request fails the set without resampling the
  writer.

Not adopted: keeping MiMo's labels, with or without a later check, and
dropping the labels.

**Deferrals may stand alone (Q20).** A deferral commits to nothing, so Q8's
rule applies to a yes or a no only: a set holding `accept` or `decline` holds
another stance beside it, and `later` may stand alone.

**Carried over from ADR 25.**

- Private chats only.
- Automatic only: no Generate or Retry, and no backfill of existing chats.
- Only the latest unanswered block, withdrawn by new incoming content, a later
  outgoing message, changed evidence or 12 hours.
- Insertion never sends or rewrites. MiMo writes plain sets; Muse Spark 1.3
  writes idiom sets (ADR 56 Q20).
- Members' suggestion calls count in their own budgets
  ([ADR 48](0048-members-on-the-shared-service.md)).

## Consequences

- A held block's decision costs about $0.00003, plus about $0.0002 for MiMo
  when suggestions follow. Its text goes to Perplexity, which publishes no
  retention or training terms for this API, and to the proxy's 30-day spend
  log.
- Short blocks show an emotion and can get suggestions without a rewrite.
- This amends ADR 13 and ADR 15 (a further exception to the suggested-answer
  exclusion), [ADR 20](0020-minimal-reading-and-ready-notifications.md) (one
  more owner push), ADR 25 (the trigger, added meaning, guidance and what
  answers a block), [ADR 26](0026-mobile-message-actions-and-send-status.md)
  (its usefulness question's guidance), ADR 30 (the route and the incoming
  request's second question),
  [ADR 34](0034-group-chats-and-daily-briefings.md) (the ad filter's route)
  and ADR 35 (decisions for short blocks).

## Known limits

The Low quality notes from the feature's checks, among them reactions
authored as useful judged not useful, are in [inference](../inference.md)
and await the owner's review.
