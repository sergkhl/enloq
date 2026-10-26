---
status: accepted
---

# Recipient emotion

## Context

Knowing how the other person sounds in their latest message helps the owner
read and answer, but a guessed feeling must never be presented as fact or
change what the owner says. This ADR defines Recipient Emotion. The Adaptive
tone it once added to outgoing Rewrite was removed
([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)).

## Decision

**Recipient Emotion.** An estimate of the emotion expressed in the latest
incoming block of a private chat, or in a correspondent's qualifying reaction
([ADR 26](0026-mobile-message-actions-and-send-status.md)). It describes that
communication, not a durable trait or a claim to know the person's inner
state; traits are kept separately, for the owner's eyes only
([ADR 55](0055-person-and-account-memory-and-traits.md)). Groups and Saved
Messages have none.

**Evidence.** The block's original wording, its transcripts and any image
meanings already generated, within the bounded same-chat Supporting Context.
Quoted or forwarded feelings are not the recipient's by default, and a
forward's filled-in details never change a used source
([ADR 27](0027-header-message-menu-forwarding-details-and-voice-playback.md)).
Nothing is inferred from faces or profile photos, there is no acoustic
analysis, and memory, traits and earlier versions are never evidence.

**How it is decided.** Emotion Classification on the decision model
([ADR 30](0030-decider-emotion-classification.md),
[ADR 49](0049-useful-response-suggestions.md)), never in a rewrite. With
softening On, the default, every block is decided from its originals before any
rewrite (ADR 53). With softening Off, a text block is decided from its
originals after its quiet period
([ADR 54](0054-incoming-text-combined-not-rewritten.md)), and a block with
media after its rewrite validates, using its image meanings. A failure leaves
emotion unavailable and pauses nothing.

**Vocabulary.** Fixed codes with a mapping the clients own:

| Code | Meaning | Display |
| --- | --- | --- |
| `neutral` | Neutral | 😐 |
| `positive` | Positive or warm | 🙂 |
| `excited` | Excited | 🤩 |
| `sad` | Sad | 😔 |
| `worried` | Worried | 😟 |
| `frustrated` | Frustrated | 😤 |
| `angry` | Angry | 😠 |
| `confused` | Confused | 😕 |
| `unknown` | Mixed or uncertain | 😐 |

A clear dominant emotion is chosen when the evidence supports one. Uncertainty
is not Neutral: when the chosen code's probability is below 0.35 the stored
code is `unknown`. Probabilities and confidence are kept for diagnosis and
never shown, and the model never picks an emoji of its own.

**Freshness.** A current estimate lasts at most 12 hours from the native time
of its latest source message, or of the reaction's addition, not from
completion. New incoming content, or an edit or deletion of material it used,
withdraws it at once until the latest block has a validated estimate. An
outgoing send alone does not clear it. Older catch-up or explicit results
never replace a newer estimate, deleting the latest message never revives an
older one, and no expiry starts a model call. Existing chats are not
reprocessed to gain an emotion; opening, refreshing and restarting start no
call.

**Provenance.** Estimates are scoped to the account and chat, with the
revision and transcript dependencies needed to invalidate changed evidence.
The same validity rule holds in both reading views and while Account AI
Processing is Off. The stored record follows the source's retention; the
12-hour lifetime neither extends it nor turns emotion into memory. Account
removal clears it.

**Header and profile.** While an estimate is current, its emoji sits right
after the name in the conversation header, with an accessible name
([ADR 47](0047-pairing-settings-menus-and-profiles.md)). Tapping the name or
emoji opens the Correspondent Profile, which shows the estimate with its
reason, source and time, or why there is none: pending, expired, changed or
unavailable ([ADR 46](0046-the-apps-catch-up-units.md)). There is no
generated explanation, manual override or emotion Retry.

**Guidance.** One owner-editable Emotion extraction field, shared by incoming
classification and Reaction Assessment, follows a fixed core holding the
permitted sources, quote and forward attribution, the exclusion of facial and
acoustic inference and the fixed code descriptions (ADR 30). A change applies
to future calls without regenerating cached estimates.

**What uses it.** The header and automatic Response Suggestions
([ADR 25](0025-emotion-response-suggestions.md), ADR 49, ADR 56).
Hostile-message softening uses its own explicit wording decision, never a
frustrated or angry mood code (ADR 53). Manual suggestions may use a current
emotion as context but require neither emotion nor usefulness.

**The composer.** Send and Suggestions are separate in private
chats (ADR 13, ADR 25); there is no Rewrite or tone control. Enter adds a new
line, with no keyboard shortcuts (ADR 44). No usage hints or Help are added;
Settings controls keep their concise explanations (ADR 45).

## Consequences

- The owner sees how a message sounds without the estimate shaping their own
  words.
- An uncertain estimate shows 😐 named "Mixed or uncertain", and an
  unavailable one shows no emoji; neither is recorded as Neutral.
- Each qualifying block costs one decision request, which counts in the
  member's Daily Budget.
