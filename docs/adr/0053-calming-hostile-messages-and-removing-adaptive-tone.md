---
status: accepted
---

# Calming hostile messages, and removing Adaptive tone

## Context

Enloq works on the functionality that matters to people and removes what
isn't needed. The mood emoji, reply suggestions, deleted
messages' last words, notifications, Hide Bots and Person Memory stay as they
are. Two changes follow: Adaptive tone leaves outgoing Rewrite, and incoming
messages from a frustrated or angry sender are calmed. The Q numbers are this
ADR's own.

## Decision

### Adaptive tone leaves

**Removed (Q1).** Outgoing Rewrite keeps Neutral, Professional and Friendly
casual, in private chats as in groups.

**No backward compatibility (Q2).** A remembered Adaptive tone, the global
one or a person's, became Neutral, and the owner-editable Adaptive guidance
left with shipped-defaults revision 12. The mood decision stays: the mood
emoji, reply suggestions and group ad filtering still use it. This amends
[ADR 22](0022-recipient-emotion-and-adaptive-rewriting.md) and the Adaptive
parts of [ADR 26](0026-mobile-message-actions-and-send-status.md) and
[ADR 30](0030-decider-emotion-classification.md).

### Calming a hostile chat

**What it does (Q3).** In a private chat, an incoming block the mood decision
marks frustrated or angry is rewritten calmly: it keeps every fact, request,
condition, deadline and any threat, stated plainly; says the sender's feeling
in a few neutral words, such as "Alex is angry about the late payment"; and
drops insults, sarcasm, shouting and blame. Only frustrated or angry blocks
are calmed; warm, excited, sad and worried messages keep their wording. Since
[ADR 54](0054-incoming-text-combined-not-rewritten.md) calming is the one
automatic rewrite of incoming text.

**On by default, one switch per chat (Q4).** Calming is on in every private
chat, for the owner and every member. Each person's Correspondent Profile has
a **Calm this chat** switch, beside Remember this chat and Suggestions, that
turns it off for that chat only.

**The Calmed label (Q5).** No header tag shows calming. Each calmed message
carries a small **Calmed** label, and its original stays one tap away, as
every rewrite's does.

**Any length (Q6).** A frustrated or angry block is calmed whatever its
length. The Processing Character Threshold no longer applies to private
chats (ADR 54).

**The decision comes first (Q7).** In a chat with calming on, every eligible
private block gets its mood decision from its originals before any rewrite,
as held blocks do under
[ADR 49](0049-useful-response-suggestions.md) Q3; then at most one rewrite
runs, calmed or not. The rewrite, its readiness and its notification wait for
the decision, typically a few seconds, and image meanings don't feed that
decision. In a chat with calming off, a block with media keeps ADR 30's
order: the rewrite first, the decision after. Not adopted: rewriting first
and again calmly, which would show and notify the hostile version first.

**The Calming guidance (Q8).** An owner-editable field, **Calming**, in
Settings → AI → Guidance holds Q3's wording as its default, after a fixed
calming frame. It is added to the Faithfulness Rules and the incoming
guidance only for blocks being calmed.

**Scope in time (Q9).** Calming applies to blocks rewritten while it is on:
switching it on redoes nothing already shown, and switching it off leaves
calmed messages calmed.

**The mood emoji (Q10).** It shows as usual; the calmed text states the
feeling anyway.

**Private chats only (Q11)**, as the mood emoji and suggestions are.

**Sarcasm.** The incoming decision request also asks, as a fixed question
with no owner field, whether the latest block is sarcastic. A calmed rewrite
of a sarcastic block is told so, and the fixed calming frame says to render
sarcasm by what it means. A missing answer means no hint; a malformed one
fails the decision like the others.

**Muse writes calmed text.** MiMo read sarcasm literally too often, with
reasoning on or off. Calmed rewrites therefore run on
`openrouter/meta/muse-spark-1.3-contributor` through the proxy, whose route
pins Meta with low reasoning hidden, at its listed $0.10 and $0.20 per
million input and output tokens; its rates come from OpenRouter's public
listing, as the proxy records none. The route
[ADR 29](0029-mimo-route-and-structured-incoming-rewriting.md) retired returns
for this role (and ADR 56's idiom sets) only; every other rewrite stays on
MiMo. Muse draws on Meta's shared upstream pool: a calmed rewrite refused
with HTTP 429 retries after 30, 60 and 120 seconds (or the provider's
delay), then fails with Retry, and the originals show meanwhile.

### Quality must not degrade

**The rule (Q12).** About 24 authored blocks, half Russian and half English:
short insults, long rants with amounts and deadlines, legal and safety
threats, sarcasm, ALL CAPS, a voice-note transcript, a forwarded hostile
message, frustrated but polite messages, and four neutral or warm messages.
Calming passes only if, in every case, the calmed text keeps every fact,
request, condition, amount, deadline and threat of the originals, invents
nothing, keeps the language, removes the insults, sarcasm and shouting and
reads sarcasm by what it means, and if no neutral or warm message is calmed.
Zero misses.

**The judges (Q13).** `scripts/check-calming.py` checks automatically that
every authored amount, date, name and threat survives. A judge model,
`chatgpt/gpt-6.1-sol` through the proxy's subscription route, reads authored
texts only and counts a literal reading of sarcasm as a miss; the decision
model may judge where text matching can't. The owner then reads every pair.

**A gate (Q14).** The check must pass before calming goes live. It stays in
the repository, to rerun whenever the Calming guidance is edited.

**Live (Q15).** After deployment, test messages between Maya, the test
account, and the Owner Account check calming through the real paths, in the
VPS browser first.

## Consequences

- A hostile block's readiness waits a few seconds for its decision in
  calming chats; a frustrated or angry one is then rewritten on Muse, at
  Muse's rates, against the member's Daily Budget.
- Muse's shared upstream pool can refuse; a key of the owner's own with Meta
  would be the remedy if refusals become common.
- This amends ADR 22, ADR 26 and ADR 30 (Adaptive), ADR 29 (Muse for calmed
  text), ADR 35 (calming at any length) and ADR 49 (the decision first).

## Open owner gates

The gate passed on Muse. The owner's reading of that passing run's pairs is
still open.
