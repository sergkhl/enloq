---
status: accepted
---

# Incoming text combined, not rewritten

## Context

Read side by side with their originals, automatic rewrites of incoming text
added little. The owner chose to combine text messages instead, and to find
a better rewrite later. Cost was not the reason.

## Decision

- **Text is combined.** An incoming block whose targets are text only is not
  rewritten automatically, whatever its length. In AI reading it shows as its
  originals together in one bubble, headed "N messages · time" with no AI
  label and no status. This amends
  [ADR 24](0024-coherent-message-groups-and-focused-viewing.md)'s AI reading,
  [ADR 29](0029-mimo-route-and-structured-incoming-rewriting.md)'s structured
  rewriting of text and
  [ADR 35](0035-processing-character-threshold-and-empty-rewrites.md)'s
  threshold for private chats.
- **The mood decision stays.** Every text block is held and decided from its
  originals after its quiet ([ADR 49](0049-useful-response-suggestions.md)
  Q3), for the mood emoji, reply suggestions and sarcasm. With calming on,
  readiness and notifications wait for that decision.
- **Calming is the one rewrite of text.** A frustrated or angry block is
  calmed as [ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)
  says.
- **Media keeps its rewrite.** A block with an image, a voice note or a video
  circle is rewritten as before: transcripts become readable text and images
  get their meaning.
- **Memory learns from the decision.** Text doesn't reach the rewrite, so
  Person Memory and Account Memory
  ([ADR 55](0055-person-and-account-memory-and-traits.md)) learn from what the
  decision read: the same messages, already sent to a model.
- **Explicit work.** Rewrite earlier messages offers only blocks with media. A
  Retry of a text block that isn't calmed decides it again and ends combined.
- **The Processing Character Threshold** governs group briefings only.
- **For later.** The registry keeps a `text_rewriting` switch, off and not in
  Settings; on, it restores text rewriting under the threshold. A better
  rewrite is future work: calmed texts suggest a structured account
  (feeling, facts, requests, deadlines) for long or messy messages.

## Consequences

- Incoming text reaches a writing model only when calmed or as context (media
  rewrites, suggestions and memory); the decision model reads every text
  block.
- Finite checks that need text rewrites set `text_rewriting` for their own
  store only.
