---
status: accepted
---

# Soften hostile messages

## Context

Hostile wording can make correspondence hard to read. Frustration or anger
alone must not cause rewriting. This ADR owns the explicit hostile-wording
decision and **Soften hostile messages**;
[ADR 20](0020-minimal-reading-and-ready-notifications.md) owns notification
timing. The Q numbers are this ADR's own.

## Decision

### Outgoing rewriting

**Removed (Q1).** Outgoing Rewrite and all its tone controls are removed in
private chats and groups. Private chats instead offer manual suggestions
([ADR 25](0025-emotion-response-suggestions.md)); groups gain no suggestions.
Friendly, Professional and Idioms are suggestion styles
([ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md)).

**No backward compatibility (Q2).** No outgoing rewrite mode, Neutral or
Adaptive tone or outgoing-only guidance is retained. The mood decision stays
for the mood emoji and automatic suggestion eligibility, independently of
hostility. This replaces the outgoing behavior in
[ADR 22](0022-recipient-emotion-and-adaptive-rewriting.md) and the Adaptive
parts of [ADR 26](0026-mobile-message-actions-and-send-status.md) and
[ADR 30](0030-decider-emotion-classification.md).

### Soften hostile messages

**What it does (Q3).** An explicit decision-model answer about hostile
wording, separate from Recipient Emotion, is the only trigger. Hostility
toward anyone may qualify: insults, aggressive blame, intimidation and
hostile sarcasm, interpreted in context. Frustration, anger, emphatic
profanity, friendly teasing and reporting someone else's insult do not
qualify by themselves. Neither a mood code nor sarcasm alone authorizes
softening.

A hostile private block is rewritten with every fact, request, condition,
amount, deadline and threat stated plainly. Insults, hostile sarcasm,
shouting and blame are removed; sarcasm is rendered by its meaning, and an
expressed feeling is named briefly only when supported by the evidence.
Hostility supplies no invented feeling or attribution. An angry or frustrated
block without hostile wording keeps its text. Softening is the only
automatic rewrite of incoming text
([ADR 54](0054-incoming-text-combined-not-rewritten.md)); media retains its
ordinary rewrite.

**On by default, one switch per chat (Q4).** Each private chat's
Correspondent Profile has **Soften hostile messages**, On by default for
the owner and every member, beside Remember this chat, Automatic suggestions
and Style. It controls softening and notification screening in that chat;
Off permits immediate original previews. This vocabulary replaces Calming,
Calm this chat and Calmed throughout the feature. Existing per-chat On/Off
choices are preserved.

**The Softened label (Q5).** Each softened message carries a small
**Softened** label, its original one tap away. No header tag is added.

**Any length (Q6).** A hostile block is softened at any length; the Processing
Character Threshold does not apply to private chats (ADR 54).

**The decision comes first (Q7).** With softening On, notification text and
captions are screened on arrival on the adopted decision route, from original
wording and bounded cached same-chat context. The screen reads no raw images,
memory or traits and scans no history. ADR 20 sets its five-second deadline
and original-text fallback. Clean previews wait for no other AI stage;
hostile previews normally wait for validated softened wording. Textless
media uses a typed label immediately, and a clean later arrival need not
wait for an earlier hostile message.

Combined-block decisions and rewrites retain 30 quiet seconds. Before a
rewrite, the explicit hostility answer decides whether to soften; a mood
decision may accompany it but cannot replace it. Reuse a screening answer
only for proven unchanged inputs and context; changed or combined sources
need a current decision. No partial stream is a notification preview. With
softening Off, media keeps its ordinary rewrite and the mood decision may
follow it. An unavailable or unusable decision authorizes no softening;
notifications use ADR 20's original fallback.

**The softening guidance (Q8).** The owner-editable **Soften hostile
messages** field in Settings → AI → Guidance holds Q3's wording after a
fixed softening frame. It joins the Faithfulness Rules and incoming guidance
only for hostile blocks. The decision's fixed hostile-wording criteria are
independent of editable Emotion extraction guidance.

**Scope in time (Q9).** Switching On redoes nothing already shown; switching
Off leaves softened renderings intact.

**The mood emoji (Q10).** It keeps its separate emotion rule. Softened text
asserts no emotion merely because hostility was found.

**Private chats only (Q11).** Groups and Saved Messages have no softening.

**Sarcasm.** The block decision also asks whether it is sarcastic, with no
owner field. The softened rewrite receives that hint and must render the
meaning rather than the literal sarcastic claim. Missing sarcasm supplies no
hint; malformed output fails the decision. Sarcasm without hostility starts
no softening.

**The writer.** Softened text uses
`openrouter/meta/muse-spark-1.3-contributor` through the proxy, with Meta
pinned and low reasoning hidden, under the adopted route and rates. Every
other incoming rewrite stays on MiMo. The app keeps bounded no-work retries
for Muse refusals. Notifications take the original fallback when required
work cannot proceed, without waiting through those retries; completion
never adds another alert.

### Quality must not degrade

**The rule (Q12).** About 30 authored Russian and English blocks:
insults, rants with amounts and deadlines, legal and safety threats, hostile
sarcasm, shouting, speech, forwarded hostility and third-party attacks, plus
non-hostile frustrated and angry messages, friendly teasing, reported insults
and neutral or warm controls. Preserve every fact, request, condition,
amount, deadline and threat; invent nothing; keep the language; remove
hostile presentation; interpret hostile sarcasm by its meaning. Non-hostile
controls must not be softened. Zero misses.

**The judges (Q13).** The finite check uses deterministic checks for authored
values and threats; the adopted judge in `scripts/check-calming.py` reads
authored text only and checks sarcasm. The owner reads the pairs. Earlier
mood-triggered results do not prove the hostile-only classifier boundary.

**A gate (Q14).** The check must cover the hostile-only rule before the
changed behavior goes live. Rerun it when the Soften hostile messages
guidance changes.

**Live (Q15).** After deployment, recognizable test messages between Maya,
the test account, and the Owner Account check the real paths in the VPS
browser first, within the standing target and budget boundaries.

## Consequences

- Only hostile wording triggers the Muse rewrite, against the member's Daily
  Budget; mood and automatic suggestion eligibility stay independent.
- Clean notifications wait at most five seconds for screening. The chosen
  original fallback may expose hostile wording, including when a required
  softened rewrite fails.
- Ordinary app retries and notification fallback are distinct outcomes.

## Open owner gates

The owner's reading of authored pairs remains open. Current implementation
limits live in [inference](../inference.md#calming) and
[TODO](../plans/TODO.md).
