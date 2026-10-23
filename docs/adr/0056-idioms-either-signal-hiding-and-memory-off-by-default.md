---
status: accepted
---

# Idiom suggestions, either signal, hiding suggestions, and memory off by default

## Context

The owner wanted a chat mode whose suggestions use "phraseological units,
proverbs, set expressions, phrasal verbs", and three further changes:
"Disable chat memories by default", "Allow suggestions to be hidden" and "it
should be either Emotional or Useful, not necessarily both".

Under [ADR 49](0049-useful-response-suggestions.md) Q2 suggestions were rare.
The owner's chats are mostly Russian, and phrasal verbs exist only in
English. The shipped Response suggestions guidance says "Avoid cliches", and
the fixed core forbids advice and the owner's feelings. MiMo tends to read
figurative language literally
([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md),
[ADR 55](0055-person-and-account-memory-and-traits.md)).

## Decision

**Language (Q1).** Idiom sets use the chat's language, under ADR 25's rule: a
Russian chat gets Russian idioms, proverbs and set expressions, an English
chat English ones and phrasal verbs. Not adopted: English practice in every
chat, and a language chosen per chat.

**A per-chat control (Q2).** It lives in the Correspondent Profile, for
members too; Q12 makes it one choice.

**Either signal (Q3).** An incoming block's suggestions need a usable
non-neutral emotion *or* the decision model's yes on usefulness, in every
private chat, idiom chats included. This reverses ADR 49 Q2 C. A yes on
usefulness counts while the block's decision is current, even when its
emotion is unknown; an unavailable, changed or expired decision qualifies
nothing. Q19 sets the bar for blocks without emotion.

**What idiom sets may say (Q4).** An option may give gentle advice through a
proverb or saying, and a common idiom of pleasure or sympathy may voice the
owner's feelings. The rest of the fixed core stays: no invented facts, no
admitted fault, no pressure and no commitments outside answer stances. Other
chats keep ADR 49's limits. Not adopted: keeping every limit.

**From the next block (Q5).** A choice applies from the next block that
qualifies: a set takes the chat's choice when it is scheduled, and only a
later Off cancels queued work. A set already written stays as it is, and
nothing is regenerated.

**Guidance (Q6).** An owner-editable field, **Idioms**, under Settings → AI
→ Guidance, with Save, Reset and versions, used only for idiom sets after a
fixed frame. The frame sets aside "Avoid cliches" and, within Q4's bounds,
the core's advice and feelings rules.

**Suggestions only (Q7).** Rewrite keeps Neutral, Professional and Friendly
casual.

**Q8:** delivery scope; no lasting rule.

**Memory off by default (Q9).** Remember this chat starts Off. Every private
chat without its own choice, members' included, is Off, and the owner turns
on the chats they want. What memory already held stays, visible, editable and
clearable, and nothing reads it while the chat is Off. Not adopted: clearing
what those chats taught.

**Off both ways (Q10).** Off keeps ADR 55 Q18's meaning: such a chat teaches
neither memory, reads neither and collects no traits. About you and the
holder's traits learn only from chats switched on, and suggestions elsewhere
read no memory. Not adopted: a default Off for the person's side only.

**Switching on (Q11).** A chat that was Off only by default learns from its
30-day cache when switched on, as a new account's memory starts. A chat its
holder switched Off keeps ADR 55 Q20's rule: what arrived while it was Off is
never learned.

**The Suggestions choice and the fold (Q12).**

- **One choice per chat (A).** Each private chat's Correspondent Profile has
  one Suggestions choice, **Off · On · Idioms**, in both clients, On by
  default. The owner and every member set it for their own chats, and its
  calls count in that member's own Daily Budget. Off prepares and shows
  nothing in that chat; a set prepared before shows again on switching back
  while it is still valid. The one global Suggestions switch, the owner's,
  still turns every suggestion off. Groups and Saved Messages have no
  suggestions. Not adopted: one setting for every chat, or one per account.
- **A remembered fold (B).** A folded set stays folded on every client and
  after reloads until the next set arrives, replacing ADR 49 Q11's
  session-only fold. Not adopted: sets that arrive folded, and an × that
  removes a set.

**Reactions keep needing both (Q13).** A reaction's suggestions need a yes on
usefulness and a non-neutral emotion. A reaction's emoji alone reads as
positive or negative, so either signal would put options under nearly every
👍.

**Plain to look at (Q14).** Idiom options look as other options do; nothing
marks or explains the expression.

**Every option (Q15).** Each idiom option carries at least one real
expression in its right meaning; when none fits, a set has fewer options or
none.

**The writer (Q16).** The idioms gate compares MiMo and Muse Spark 1.3 on the
same authored chats, and Q20 settled the choice: Muse writes idiom sets, and
plain sets stay on MiMo. Muse's other role is calmed text.

**The idioms gate (Q17).** `scripts/check-idioms.py` runs authored Russian
and English chats through the real path, and a judge model
(`chatgpt/gpt-6.1-sol`, as in the calming and memory gates) reads every
option. A writer passes with no miss on the fixed rules (the chat's language,
the labels and contrasting stances, and no invented facts, admitted fault,
pressure or commitments outside answer stances) and with every option
carrying a real expression in its right meaning in at least 9 sets of 10.
Weaker results are Low quality. The owner reads every set, and the gate
reruns whenever the Idioms guidance changes.

**The name (Q18).** Idioms, explained as "Suggestions use idioms, proverbs,
set expressions and phrasal verbs in this chat's language."

**A calm block needs a clear "useful" (Q19).** A block without a usable
non-neutral emotion needs usefulness of 0.7 or more; emotional blocks and
reactions are unchanged. An answer that closes the owner's own question can
score just over 0.5, while blocks that need a reply score far higher. Not
adopted: showing nothing for a one-sided set, and the 0.5 bar.

**Idioms launched with Muse (Q20).** No writer passed Q17's bar, Muse came
closest, and the owner chose to let Idioms be chosen with Muse writing idiom
sets. Muse's 🥳 written as the escape `\u0001F973` is read as its emoji; a
suggestion with any other control character fails. Not adopted: calibrating
the judge and rerunning, more refining, and keeping Idioms hidden.

## Consequences

- Suggestions appear far more often, for neutral questions and for warm
  messages, and each chat can turn them off.
- Memory learns only from the chats the owner or a member turns on.
- This amends [ADR 25](0025-emotion-response-suggestions.md) and ADR 49 (the
  trigger, a per-chat choice, the remembered fold and idiom sets with their
  limits and writer) and ADR 55 (Remember this chat's default, and Q20's
  never-learned rule for chats Off only by default).

## Known limits and open owner gates

- **Idiom sets' misses.** Muse launched without passing Q17's bar: its idioms
  sometimes imply claims the chat doesn't make. The owner judges them in
  ordinary use.
- **Yes-only plain sets.** MiMo sometimes writes a request's options all
  saying yes, which the app fails as one-sided and shows as "Suggestions
  unavailable: format check failed". Showing nothing for a one-sided set
  instead remains the owner's choice.
- Switching memory on in the chats they want, and trying Idioms, are the
  owner's.
