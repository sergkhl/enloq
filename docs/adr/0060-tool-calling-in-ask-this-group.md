---
status: accepted
---

# Tool calling in Ask this group

## Context

[ADR 59](0059-ask-this-group.md)'s Ask this group retrieves in one fixed pass:
the question's embedding ranks the group's searchable originals, a reranker
reorders the first 40, and the writer reads the best 25. When those 25 do not
settle the question, the writer cannot look further, for the reply that
confirms a message or for an update the pass ranked lower.

The writer's route, gpt-6.1-sol on the proxy's ChatGPT-subscription route,
calls tools in the chat route's format. The proxy lists function calling for
it, and three authored requests returned a tool call, an answer from the
tool's result, and a text answer under `tool_choice: "none"`. No other route
was needed.

## Decision

**Tools (Q1).** A writer whose route calls tools is given three. They read
only ADR 59 Q2's pool, this group's searchable cached originals, which the
fixed pass also ranks: no other chat, no Telegram history, no web and no
link.

- `search_messages(query)`: the fixed pass's ranking for the writer's own
  words, the query's embedding and then the reranker over the first 40; the
  best 10, best first.
- `messages_around(message_id)`: up to five searchable originals on each side
  of one, with it, oldest first.
- `linked_messages(message_id)`: one original, the one it replies to (none
  when that one is not searchable) and up to ten replies to it.

A result shows each message as the writer's packet does, with the same
fields, so a model receives nothing a Daily Briefing could not. Results are
untrusted data like the messages. The writer cites only messages it has read,
in its packet or in results, and the citation check and abstention stay ADR
59's (Q6, Q7). The tools add no route: a search uses ADR 59 Q3's embedding
and rerank routes, pinned to their zero-retention providers through the
proxy.

**Bounds (Q2).** Per question, at most five writer requests, the fifth sent
with `tool_choice: "none"` so that it answers; at most four searches; and at
most twelve tool calls in all. A call past a bound gets an error to answer
from, and nothing runs. A search makes one embedding request and at most one
rerank request. A reply naming an unknown tool or invalid arguments fails the
answer's format, as an answer citing an unread message does.

**Retrieval (Q3).** The writer reads the fixed pass's 25 first and calls its
tools when they do not settle the question. In the measurement (Q4) it
answered 78 of the 80 answerable questions correctly against the fixed pass's
73, lost none of the fixed pass's correct answers, and called tools for 31 of
the 100 questions, all 20 unanswerable ones among them. Tools alone, with no
messages to start with, answered 74, short of the rule, in twice the time:
the application does not use them, and the check still measures them. A
writer whose route calls no tools, MiMo where no subscription writer is
attached, reads the fixed pass alone.

**Measurement (Q4).** Tool calling is decided by measurement on ADR 59 Q11's
authored question set: the fixed pass, the tools alone, and the fixed pass and
then the tools, each answering all 100 questions through the application's
steps, every statement checked, and one judge request per question grading
the three answers together in a shuffled order. The rule was set before the
run: a tool retrieval qualifies if it answers at least two more of the 80
answerable questions correctly than the fixed pass, no fewer of the 20
unanswerable ones, and has no more statements judged unsupported. The
qualifying one with the most correct answers is adopted if its median time
per question is at most twice the fixed pass's, and is kept behind a setting
otherwise. With none, tool calling is dropped.

The fixed pass then the tools qualified: 78 against 73 of 80 answerable
questions, all 20 unanswerable ones correct as with the fixed pass, and 2
statements judged unsupported against 4. Its median time per question was 7.7
seconds against 6.5. The gain is five questions the fixed pass missed and
none it answered, an exact two-sided McNemar p of 0.0625: the rule's
practical bar, not significance at 5% on 80 questions. Tools alone did not
qualify. [Measurements](../measurements.md#tool-calling-in-ask-this-group)
publishes the results and every miss.

## Consequences

- A question the 25 do not settle waits longer, since nothing streams: 17.9
  seconds at the median when the writer called tools, against 6.7 when it did
  not, and 22.4 seconds at the 95th percentile against the fixed pass's 12.8.
- Each search adds a rerank: a question cost about $0.0014 against $0.0009,
  from the member's Group Briefing Budget.
- The owner's subscription answers 1.6 writer requests per question on
  average instead of one, and at most five.
- The writer's own queries reach the embedding and rerank providers, which
  already receive the group's messages.
- Tools only read this group's pool, and every citation is still checked, so
  a message can at most steer the writer's searches within the same group.
