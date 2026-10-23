---
status: accepted
---

# Response suggestions

## Context

Answering an emotional message, or one that needs a decision, takes effort.
Response Suggestions offer a few short replies the owner can insert and edit,
while substantial answers and commitments stay the owner's. They are a narrow
exception to [ADR 13](0013-clean-slate-rewriting-rebuild.md)'s exclusion of
automatic suggested answers and
[ADR 15](0015-owner-controlled-processing-memory-and-prompts.md)'s
no-suggested-answer output rule. Ordinary incoming and outgoing rewrites keep
their meaning-preserving roles, and the retired attention, task and
classification features don't return.

[ADR 49](0049-useful-response-suggestions.md) added the decision model's
usefulness question, answer stances and the decision model's labels;
[ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md) made
either signal enough and added each chat's choice and idiom sets;
[ADR 55](0055-person-and-account-memory-and-traits.md) lets suggestions read
memory. This ADR states the whole current behavior; those ADRs own their
details.

## Decision

**What they are.** Up to three short, editable replies, each with a purpose
label: Acknowledge, Support, Clarify, Celebrate, or, for answers, Accept,
Decline and Later. The decision model assigns the labels from what each reply
says (ADR 49 Q19). A set offers only replies that suit the conversation,
never fills a quota and never gives communication coaching. The owner
chooses, edits and sends deliberately; preparing a set never touches the
draft.

**When they appear.** Only in private chats, only for the latest unanswered
incoming block or a correspondent's qualifying reaction
([ADR 26](0026-mobile-message-actions-and-send-status.md)), and
automatically:

- An incoming block qualifies with a usable non-neutral emotion (positive or
  warm, excited, sad, worried, frustrated, angry or confused) or with the
  decision model's yes on usefulness while its decision is current. Without
  such an emotion, usefulness must be 0.7 or more (ADR 56 Q3, Q19).
- A reaction needs both: a yes on usefulness and a non-neutral emotion
  (ADR 56 Q13).
- Neutral, unknown, pending, expired or unavailable emotion qualifies nothing
  by itself.

Preparation follows the block's or reaction's decision
([ADR 30](0030-decider-emotion-classification.md)). Unchanged results are
reused across reads, refreshes and restarts. Groups and Saved Messages have no
suggestions.

**Added meaning.** Options may offer grounded acknowledgement, sympathy,
celebration and gentle questions the owner hasn't written. When the person
asks for an answer, a decision or an action, options may answer with
different stances side by side, so the owner's choice supplies the intent; a
set holding Accept or Decline holds another stance beside it, and Later may
stand alone (ADR 49 Q4, Q20). Options never invent facts (times, places,
amounts, reasons, the owner's plans or whereabouts), assert the owner's
feelings, admit fault, pressure the person or commit the owner outside those
answer stances. Idiom sets alone may give gentle advice through a proverb and
voice the owner's pleasure or sympathy through a common idiom (ADR 56 Q4).
Support keeps the person's refusals and boundaries; an inferred emotion is
context, not knowledge of someone's inner state; a reaction never counts as
agreement.

**Evidence.** The latest original incoming block, or the reaction and its
exact target, with bounded Supporting Context: up to 20 preceding recent
same-chat messages and the one directly replied-to message, including their
transcripts and image context. The messages decide the response; emotion
guides its expression, and emotion-label templates aren't enough. In a chat
with Remember this chat on, the cares-about entries of Person Memory and
Account Memory may shape the options: one may ask about, wish luck with or
acknowledge what the person cares about, judged against today's date and
never claiming it is still true, and an Account Memory entry is stated only
in the chat it came from (ADR 55 Q15, Q16). No older history is scanned, and
suggestions turn no emotion into a profile.

**A separate request.** Each set is its own bounded background request after
the decision publishes. MiMo writes plain sets and Muse Spark 1.3 writes idiom
sets (ADR 56 Q20); one decision request then labels the replies. Rewriting has
priority over suggestions, which run ahead of idle memory work. Only complete,
validated sets are published. Incoming text, readiness, unseen state and
notifications never wait for suggestions, and a failed set leaves the
incoming result intact. Every request counts in the Concurrency Limit and in
the Daily Budget of the member whose account it serves
([ADR 48](0048-members-on-the-shared-service.md)).

**Presentation and insertion.** A compact area above the composer shows each
option's full wording and label, in both reading views. It belongs to the
latest qualifying block or reaction. Folding a set is remembered on every
client until the next set arrives (ADR 56); there is no notification or row
marker. Choosing an option inserts it into an empty draft, or at the saved
cursor in an existing draft (its end when none is known), keeping the text,
chosen images and reply target. Undo restores the whole previous draft. The
composer's action and revision locks apply, focus returns to the composer, and
insertion neither sends nor rewrites.

**Tone and language.** Wording follows the emotion and the conversation, not
the Rewrite tone; changing the tone regenerates nothing, and an inserted
option can be rewritten as usual. Options use the latest block's language; for
a mixed block, the owner's established recent language in that chat,
otherwise the dominant incoming language. There is no language selector.

**Lifetime.** A set is withdrawn by new incoming content, changed, deleted or
re-transcribed evidence, the 12-hour expiry from the source's time, any later
outgoing message in the chat (from any Telegram client, GIFs and stickers
included, [ADR 32](0032-gif-and-sticker-sending.md) Q23), or the owner's own
reaction on a message of the latest incoming block (ADR 49 Q12). A durably
accepted Pending Send suspends the set; Telegram's confirmation withdraws it,
a definite non-send may restore a still-current cached set without a call,
and unknown delivery keeps it suspended (ADR 26 Q26). Late results never
restore a stale set, and nothing changes wording already inserted into the
draft. The emotion keeps its own validity rule: sending withdraws suggestions
without clearing it.

**Owner controls.** One global Suggestions switch, the owner's and
server-wide, initially On; an account whose AI processing is Off prepares no
sets. Off hides every set and stops new work, while started work may finish
into the cache; On reuses valid sets and resumes on the next new or changed
block, without a backfill. Each private
chat's Correspondent Profile adds Off · On · Idioms (ADR 56 Q2). Three
owner-editable guidance fields shape the work, with the usual Save, Reset and
versions, for future generation only: Response suggestions, When to suggest
(ADR 49 Q9) and Idioms (ADR 56 Q6).

**Automatic only.** There is no Generate, Retry or Regenerate. Opening,
refreshing, restarting, switching On or editing guidance neither backfills nor
regenerates; a deliberate Retry of an incoming block prepares no set. A failed
set shows an unobtrusive status and composing stays available; the next new or
changed block may qualify again. Budget waits and refusals where the provider
did no work keep the ordinary bounded background handling, and a processed,
unchanged input is never resampled automatically.

**Natural wording.** The shipped Response suggestions guidance ends with "Keep
the wording slightly less formal." Matching the owner's personal style is
deferred.

**Typography.** Newly generated options get one-character punctuation
mappings outside protected literal content, keeping the spacing:

| Generated punctuation | Stored replacement |
| --- | --- |
| Em/en sentence separators `—`, `–` | Keyboard `-` |
| Curly/angled double quotes `“`, `”`, `«`, `»` | Straight `"` |
| Curly single quotes/apostrophes `‘`, `’` | Straight `'` |

Ordinary dashes and slashes, slash-separated wording included, are allowed.
Meaningful word punctuation and exact copied values stay as they are: URLs,
dates and ranges, fractions, paths, names, identifiers and uncertain literal
values. There is no broad punctuation or compatibility normalization and no
dash-to-word repair. Mapping runs after the provider's JSON is parsed and
before final checks and storage, never on JSON framing or the models that read
cached options. The nonblank, 280-character and distinct-option checks run
again after mapping, and different raw options that converge fail as format.
The display and the insertion use the same stored text. Cached options and
drafts are never normalized, no option is filtered and no extra call is made.

### Questions

- **Q1:** short editable replies with purpose labels; Rewrite keeps its
  authored-text role.
- **Q2:** prepared automatically with the latest block's processing; the
  trigger is ADR 56's either signal.
- **Q3:** qualifying emotions as above; a neutral or uncertain block now
  qualifies through usefulness (ADR 56).
- **Q4:** added meaning as above, with ADR 49's answer stances and ADR 56's
  idiom limits.
- **Q5:** evidence as above; replaced in part by ADR 55: memory's cares-about
  entries are read.
- **Q6:** a separate background request after the decision; writers and
  labels as above.
- **Q7:** the area above the composer in both reading views; the fold is
  remembered across clients (ADR 56).
- **Q8:** insertion at the cursor with Undo; never sends or rewrites.
- **Q9:** independent of the Rewrite tone.
- **Q10:** the language rule above.
- **Q11:** the lifetime above.
- **Q12:** automatic only; no Generate, Retry or Regenerate, and no backfill.
- **Q13:** one global Suggestions switch, now with each chat's choice beneath
  it.
- **Q14:** an editable Response suggestions field, joined by When to suggest
  and Idioms.

The natural-wording follow-up has its own numbering:

- **Wording Q1, Q2, Q4, Q6:** suggestions only; current wording kept, slightly
  less formal; personal style deferred; future generation only.
- **Wording Q3, Q5, Q7, Q8:** the typography mappings above, deterministic and
  without extra instructions; literals preserved; slashes allowed.

## Consequences

- The owner gets limited new interpersonal wording to start a reply, while
  facts, feelings and commitments stay theirs to add.
- Finite checks kept Low quality options that asserted the owner's feelings or
  offered something the owner never said, recorded in
  [inference](../inference.md#retained-acceptance-limits) for the owner's
  quality review.
- [Inference](../inference.md#response-suggestions) owns the request,
  validation and literal-recognition mechanics.
