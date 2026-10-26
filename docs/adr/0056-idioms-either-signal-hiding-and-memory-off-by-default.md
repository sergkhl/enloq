---
status: accepted
---

# Suggestion styles, automatic generation, folding and memory off by default

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

**Per-chat controls (Q2).** Each Correspondent Profile separates Automatic
suggestions On/Off from Style: Friendly, Professional or Idioms (Q12).
The chat's member sets both, in the app and the web.

**Either signal (Q3).** An incoming block's automatic suggestions need a usable
non-neutral emotion *or* the decision model's yes on usefulness, in every
private chat, idiom chats included. This reverses ADR 49 Q2 C. A yes on
usefulness counts while the block's decision is current, even when its
emotion is unknown; an unavailable, changed or expired decision qualifies
nothing. Q19 sets the bar for blocks without emotion. Manual generation
follows ADR 25 and bypasses these signal gates.

**What idiom sets may say (Q4).** An option may give gentle advice through a
proverb or saying, and a common idiom of pleasure or sympathy may voice the
owner's feelings. The rest of the fixed core stays: no invented facts, no
admitted fault, no pressure and no commitments outside answer stances. Other
chats keep ADR 49's limits. Not adopted: keeping every limit.

**From the next generation (Q5).** Each set captures Style when scheduled,
whether automatic or manual. A style change applies to the next generation;
existing and running sets retain their style. Changing style starts no
request and withdraws no set. Automation Off cancels queued automatic work
only.

**Guidance (Q6).** Owner-editable Friendly, Professional and Idioms fields
under Settings → AI → Guidance have Save, Reset and versions for future
sets of either kind. Friendly is warm, natural and conversational without
forced slang or emojis; Professional is clear, respectful and businesslike
without unnecessary formality. Shared Response suggestions guidance still
applies. Idioms follows its fixed frame, setting aside "Avoid cliches" and,
within Q4's bounds, the advice and feelings rules.

**Suggestions only (Q7).** The three styles apply equally to manual and
automatic suggestions. Outgoing Rewrite, Neutral and its tone controls are
removed (ADR 25).

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

**Automatic suggestions, Style and the fold (Q12).**

- **Separate controls per chat (A).** Each private chat has **Automatic
  suggestions**, On by default, and **Style**, Friendly by default, with
  exactly Friendly, Professional and Idioms as choices. Both live in its
  Correspondent Profile, belong to that chat's member and are shared across
  their clients. The owner's server-wide switch is also Automatic
  suggestions. Automatic work needs both switches On; either Off hides
  automatic sets and stops new automatic work, without blocking or hiding
  manual sets. On reuses valid automatic sets, without backfill. Calls count
  in the account member's Daily Budget. Groups and Saved Messages have no
  suggestions. There is no member-wide or account-wide style or inheritance.

  Under ADR 58, either automation switch Off also pauses calendar reminders
  while preserving schedules. On resumes at the next future occurrence,
  without backfill. Account AI Off still permits reminder timing and alerts
  while blocking reminder suggestions. Reminder suggestions otherwise retain
  the same captured Style and writers as manual generation.

  Existing Off maps to automation Off and Friendly; On to automation On and
  Friendly; Idioms to automation On and Idioms. Existing global On/Off is
  preserved for automation. Removed Rewrite tones supply no preference.
  Settings conversion starts no model work and rewrites no existing result.

- **A remembered fold (B).** A folded set stays folded on every client and
  after reloads until the next set arrives, replacing ADR 49 Q11's
  session-only fold. Not adopted: sets that arrive folded, and an × that
  removes a set.

**Reactions keep needing both (Q13).** A reaction's automatic suggestions
need a yes on usefulness and a non-neutral emotion. A reaction's emoji alone reads as
positive or negative, so either signal would put options under nearly every
👍.

**Plain to look at (Q14).** Idiom options look as other options do; nothing
marks or explains the expression.

**Every option (Q15).** Each idiom option carries at least one real
expression in its right meaning; when none fits, a set has fewer options or
none.

**The writer (Q16).** The idioms gate compares MiMo and Muse Spark 1.3 on the
same authored chats, and Q20 settled the choice: Muse writes idiom sets, and
Friendly and Professional sets stay on MiMo. Muse's other role is softened
hostile text.

**The idioms gate (Q17).** `scripts/check-idioms.py` runs authored Russian
and English chats through the real path, and a judge model
(`chatgpt/gpt-6.1-sol`, as in the softening and memory gates) reads every
option. A writer passes with no miss on the fixed rules (the chat's language,
the labels and applicable stance rules in ADR 25, and no invented facts, admitted fault,
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
adopted: the 0.5 bar. Otherwise valid one-sided sets are shown under ADR 49
Q20 rather than hidden or failed.

**Idioms launched with Muse (Q20).** No writer passed Q17's bar, Muse came
closest, and the owner chose to let Idioms be chosen with Muse writing idiom
sets. Muse's 🥳 written as the escape `\u0001F973` is read as its emoji; a
suggestion with any other control character fails. Not adopted: calibrating
the judge and rerunning, more refining, and keeping Idioms hidden.

## Consequences

- Automatic generation can be turned off per chat without losing the manual
  button. Each chat's Style applies to both paths.
- Memory learns only from the chats the owner or a member turns on.
- This amends [ADR 25](0025-emotion-response-suggestions.md) and ADR 49 (the
  trigger, a per-chat choice, the remembered fold and idiom sets with their
  limits and writer) and ADR 55 (Remember this chat's default, and Q20's
  never-learned rule for chats Off only by default).

## Known limits and open owner gates

- **Idiom sets' misses.** Muse launched without passing Q17's bar: its idioms
  sometimes imply claims the chat doesn't make. The owner judges them in
  ordinary use.
- Switching memory on in the chats they want, and trying Idioms, are the
  owner's.
