---
status: accepted
---

# Response suggestions

## Context

Answering and continuing a conversation take effort. Response Suggestions
offer a few short messages the user can insert and edit, automatically or on
a manual request. Outgoing Rewrite and its tone controls are removed;
generation leaves the authored draft intact. Suggestions are a narrow
exception to [ADR 13](0013-clean-slate-rewriting-rebuild.md)'s exclusion of
automatic suggested answers and
[ADR 15](0015-owner-controlled-processing-memory-and-prompts.md)'s
no-suggested-answer output rule. Incoming rewrites keep their
meaning-preserving role, and the retired attention, task and
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

**Automatic generation.** Only in private chats, for the latest unanswered
incoming block or a correspondent's qualifying reaction
([ADR 26](0026-mobile-message-actions-and-send-status.md)):

- An incoming block qualifies with a usable non-neutral emotion (positive or
  warm, excited, sad, worried, frustrated, angry or confused) or with the
  decision model's yes on usefulness while its decision is current. Without
  such an emotion, usefulness must be 0.7 or more (ADR 56 Q3, Q19).
- A reaction needs both: a yes on usefulness and a non-neutral emotion
  (ADR 56 Q13).
- Neutral, unknown, pending, expired or unavailable emotion qualifies nothing
  by itself.

Both the owner's server-wide and the chat's **Automatic suggestions**
switches must be On. Preparation follows the block's or reaction's decision
([ADR 30](0030-decider-emotion-classification.md)). Unchanged results are
reused across reads, refreshes and restarts. Groups and Saved Messages have no
suggestions.

**Manual generation.** A separate, always-visible Generate suggestions button
replaces outgoing Rewrite in private-chat composers; Send remains separate.
It works after the user's own latest message, in a neutral exchange or after
a conversation has ended, without an unanswered-block, emotion or usefulness
gate. Automatic suggestions Off does not block it. Account AI, budget,
capacity, trust and the normal composer action guards still apply. Groups
lose Rewrite without gaining suggestions; Saved Messages keeps no AI.

With an empty draft, a manual request offers complete messages grounded in
the available recent conversation and enabled memory. With a nonempty draft,
the text supplies the user's intent: options are additions that fit its
captured insertion point, using the whole draft as context rather than
repeating or replacing it. A draft alone is sufficient when recent chat
context is unavailable. The request uses bounded cached same-chat context,
transcripts and image meanings, never an older-history scan or a reply chain.
Automatic requests continue to use their conversation evidence, not the
unfinished draft. Generation itself changes no draft text or attachments.

Each deliberate press requests fresh alternatives, even on unchanged input;
duplicate taps during one active request share that attempt. There is no
automatic resampling. A manual attempt owns the suggestion area until
withdrawn or explicitly replaced: later automatic output cannot overwrite
it. Editing its draft or changing its conversation evidence withdraws it,
including late output, and starts no replacement call. Another press captures
the revised draft and context.

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

A draft-guided manual addition follows the intent the user has supplied,
including an explicit answer stance; it does not need to offer an opposing
stance to that intent. This does not authorize facts, feelings or commitments
that neither the draft nor the permitted evidence supplies.

**Evidence.** Automatic sets read the latest original incoming block, or the
reaction and its exact target. Manual sets read the captured draft intent
and available recent conversation as above. Both keep bounded Supporting
Context: up to 20 preceding recent
same-chat messages and the one directly replied-to message, including their
transcripts and image context. The messages decide the response; emotion
guides its expression, and emotion-label templates aren't enough. In a chat
with Remember this chat on, the cares-about entries of Person Memory and
Account Memory may shape the options: one may ask about, wish luck with or
acknowledge what the person cares about, judged against today's date and
never claiming it is still true, and an Account Memory entry is stated only
in the chat it came from (ADR 55 Q15, Q16). No older history is scanned, and
suggestions turn no emotion into a profile.

**A separate request.** Each set is its own bounded request: automatic work
follows the decision, and manual work is queued on the click. MiMo writes
Friendly and Professional sets and Muse Spark 1.3 writes Idioms
(ADR 56 Q20); one decision request then labels the replies. Incoming
rewriting has priority over suggestions, which run ahead of idle memory work. Only complete,
validated sets are published. Incoming text, readiness, unseen state and
notifications never wait for suggestions, and a failed set leaves the
incoming result intact. Every request counts in the Concurrency Limit and in
the Daily Budget of the member whose account it serves
([ADR 48](0048-members-on-the-shared-service.md)).

**Presentation and insertion.** A compact area above the composer shows each
option's full wording and label, in both reading views. It belongs to the
latest qualifying block or reaction, or the current manual attempt. Folding
is remembered on every client until the next set arrives (ADR 56); there is no notification or row
marker. Choosing an option inserts it into an empty draft, or at the saved
cursor in an existing draft (its end when none is known), keeping the text,
chosen images and reply target. Undo restores the whole previous draft. The
composer's action and revision locks apply, focus returns to the composer, and
insertion neither sends nor rewrites.

**Style and language.** The chat's **Style**, Friendly, Professional or
Idioms, applies to both manual and automatic sets (ADR 56). Friendly is the
default. Style changes apply to the next generation; existing and running
sets retain their captured style, with no replacement call. Emotion and
conversation guide the wording within that style. Options use the latest
block's language; for
a mixed block, the owner's established recent language in that chat,
otherwise the dominant incoming language. There is no language selector.
When the draft is the only evidence, its language supplies the language.

**Lifetime.** An automatic set is withdrawn by new incoming content, changed, deleted or
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

A manual set lasts at most twelve hours from its request, rather than the
time of an incoming block. Draft edits, new conversation content, a send or
changed source evidence withdraw it sooner. The pending-send suspension and
non-send restoration rules apply only while its captured inputs remain valid.

**Controls.** Settings → AI names the owner's server-wide switch
**Automatic suggestions**, initially On. Each private chat's Correspondent
Profile separates **Automatic suggestions** On/Off from **Style**
Friendly/Professional/Idioms, set by that chat's member (ADR 56 Q12).
Automation Off hides automatic sets and cancels queued automatic work, while
started work may finish into cache; it neither hides nor disables manual
sets. On reuses still-valid automatic sets and resumes at the next qualifying
new or changed source, without backfill. An account whose AI processing is
Off prepares neither kind of set. Owner-editable guidance retains Save,
Reset and versions for future calls: Response suggestions, When to suggest,
Friendly, Professional and Idioms (ADR 56 Q6).

**No implicit generation.** Apart from the automatic triggers and deliberate
manual presses above, opening,
refreshing, restarting, switching On or editing guidance neither backfills nor
regenerates; a deliberate Retry of an incoming block prepares no set. A failed
set shows an unobtrusive status and composing stays available; the next new or
changed block may qualify again. Budget waits and refusals where the provider
did no work keep the ordinary bounded background handling, and a processed,
unchanged input is never resampled automatically.

**Decision diagnostics.** A content-free debug record explains each automatic
generation or skip: trigger, emotion, usefulness score, threshold and applied
gate, setting or freshness reason, followed by the attempt's outcome. It
distinguishes a decision not to schedule from a scheduled attempt that later
waited, failed, was superseded or produced no options. Use opaque correlation
identities; never record message bodies, drafts, prompts, names, account or
chat identifiers, credentials or model reasoning text. Manual attempts record
their explicit trigger and outcome under the same content limits.

**Natural wording.** Friendly is warm and natural without forced slang or
emojis; Professional is clear and businesslike without needless formality.
Matching the user's personal style beyond those choices is deferred.

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

- **Q1:** short editable messages with purpose labels; outgoing Rewrite and
  its tone controls are removed.
- **Q2:** prepared automatically with the latest block's processing; the
  trigger is ADR 56's either signal.
- **Q3:** qualifying emotions as above; a neutral or uncertain block now
  qualifies through usefulness (ADR 56).
- **Q4:** added meaning as above, with ADR 49's answer stances and ADR 56's
  idiom limits.
- **Q5:** evidence as above; replaced in part by ADR 55: memory's cares-about
  entries are read.
- **Q6:** a separate request after the automatic decision or on a manual
  press; writers and labels as above.
- **Q7:** the area above the composer in both reading views; the fold is
  remembered across clients (ADR 56).
- **Q8:** insertion at the cursor with Undo; never sends or rewrites.
- **Q9:** one per-chat Style for manual and automatic sets; no Rewrite tone.
- **Q10:** the language rule above.
- **Q11:** the lifetime above.
- **Q12:** automatic triggers plus explicit manual generation as above;
  fresh alternatives require a deliberate press, never a read or restart.
- **Q13:** global and per-chat Automatic suggestions switches; manual
  generation remains available when either is Off.
- **Q14:** editable Response suggestions, When to suggest and style guidance.

The natural-wording follow-up has its own numbering:

- **Wording Q1, Q2, Q4, Q6:** suggestions only, shaped by the chosen Style;
  personal-style matching deferred; future generation only.
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
