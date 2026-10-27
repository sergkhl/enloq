---
status: accepted
---

# Ask this group

## Context

A Daily Briefing summarizes one day ([ADR 34](0034-group-chats-and-daily-briefings.md)).
Finding one answer across a group's recent days still means scrolling. Ask
this group answers a member's question from that group's own cached messages,
with source links a decision-model check has confirmed, and says when the
messages hold no answer. It searches only what Enloq already caches: no
Telegram history, no other chat and no web.

## Decision

**Entry and screen (Q1).** While a group's AI processing is On, its ⋮ sheet
offers Ask this group. It opens an Ask screen: the question field at the
bottom, the answer above it as statements with `#ID` source links. A link
opens the group at that original, highlighted, and Back returns to the answer.
The web shows the same screen ([ADR 44](0044-mobile-only-web-in-the-apps-design.md)).

**What it searches (Q2).** Only this group, while account AI, group AI and
verified access are all in place. It searches the cached originals from the
group's first opt-in day, within the 30-day cache: text, captions, cached
transcripts and image meanings. Hidden and ignored bots, validated ads,
service events and Non-AI Media stay out. It reads no Telegram history, starts
no media work and sends a model nothing a Daily Briefing could not.

**Retrieval (Q3).** A question is matched two ways: keyword search over an
in-memory SQLite FTS5 trigram index built for that question, and similarity
between embeddings of the question and of the messages. Reciprocal rank fusion
merges the two lists. The embedding model is Perplexity Embed V1 4B at
Perplexity, which ranked best and fastest on the authored question set among
Qwen3 Embedding 8B, Perplexity Embed V1 4B and bge-m3. Qwen3 Reranker 8B at
Fireworks, the one zero-retention reranker listed, reorders the fused list's
first 40, since it measurably improved retrieval. Both run through OpenRouter's
routers on the proxy's pass-throughs, pinned to their zero-data-retention
provider with no fallback; the other candidates leave the proxy. Voyage's
rerank-3 and rerank-3-lite were measured on authored fixtures only and never
receive real group messages: Voyage's terms allow training on submitted
content unless the customer opts out.

**Message vectors (Q4).** A message's vector is computed when a question
first needs it, kept per message revision with the 30-day content cache, and
deleted with edits and deletions. Its cost is part of that question's.

**Writer (Q5).** gpt-6.1-sol at its medium default writes the answer in a
`group_question` role, on the proxy's `chatgpt/gpt-6.1-sol` subscription
route: up to four statements, each citing messages from the retrieved set, and
none when the messages do not address the question; any other ID fails
validation. It answers in the question's language and says what is still open
when the group left a question open. Nothing streams: an answer shows once
checked. It replaced MiMo after the measurement: MiMo answered 27 of 48
answerable questions correctly with thinking on, and with thinking off said
"not found" to most of them; gpt-6.1-sol answered 43 of 48 in less than half
the time. Its receipts record $0, so no budget limits it, and no other limit
applies: members' questions use the owner's subscription allowance, shared
with the owner's other callers, and group messages reach OpenAI under that
plan's data settings.

**Checked answers (Q6).** Each statement goes through ADR 34's citation
check: whether each cited message supports part of it, and whether together
they support all of it. A statement left with no supported citation is
dropped, and an answer left with none abstains; whether a statement its
citations do not fully support is marked follows ADR 34 citation check Q4,
which is open. If the check cannot run, no answer shows, only "Couldn't check
the answer.", and the member asks again.

**Abstention (Q7).** When search finds nothing, with no model call, or no
statement survives the check, one line names the searched range: "No answer
in this group's messages from 3 Oct to today."

**Budget and limits (Q8).** The member's Group Briefing Budget pays.
Questions run in the manual-suggestion class, one at a time per account. Each
makes the embedding requests its vectors need, at most one rerank request, one
writer request and at most four check requests.

**Not stored (Q9).** The question and answer exist only on the open screen,
and receipts keep usage without content. Back from a source link returns to
the answer; leaving the screen drops it.

**Who can ask (Q10).** Every member, in their own accounts' opted-in groups,
paid from their own budget ([ADR 48](0048-members-on-the-shared-service.md)).

**Measurement (Q11).** Authored group days in Russian, English and both are
labelled before any run: about 100 questions with their relevant message IDs,
a fifth of them unanswerable. Live outputs are labelled afterwards, marked
agent-labelled, with gpt-6.1-sol as a second grader; the owner's reading stays
open. This check and ADR 34's citation check together make at most 1,200
requests and spend at most $1.00, within the live test allowance.

## Consequences

- Group messages reach the chosen embedding provider, and the reranker if one
  is adopted, besides MiMo and the decision model, which briefings already
  use. Both new routes are zero data retention.
- The first question in a busy group pays for its messages' vectors.
- Members' questions run on the owner's ChatGPT subscription with no budget
  or count limit.
- Open: marking a not fully supported statement (Q6, with ADR 34 citation
  check Q4; [TODO](../plans/TODO.md)).
