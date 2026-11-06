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
- **The mood decision stays.** Every text block is held and, in a chat whose
  switches call for a decision
  ([ADR 62](0062-the-ai-menu-and-private-chat-ai-off-by-default.md) Q5), decided from
  its originals after its quiet
  ([ADR 49](0049-useful-response-suggestions.md) Q3), for the mood emoji,
  automatic reply suggestions and sarcasm. With
  Soften hostile messages On, the explicit hostility decision governs
  softening and readiness. Notifications follow their immediate bounded
  screen and original fallback in
  [ADR 20](0020-minimal-reading-and-ready-notifications.md), independently of
  the block's quiet period and mood result.
- **Softening is the one rewrite of text.** Only a hostile block is
  softened as [ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)
  says. Frustration or anger alone causes no rewrite.
- **Media keeps its rewrite.** While the chat's Voice and images switch is
  On, a block with an image, a voice note or a video circle is rewritten as
  before: transcripts become readable text and images get their meaning
  (ADR 62 Q20).
- **Memory reads the chat.** Text doesn't reach the rewrite, so while
  Remember this chat is On, Person Memory and Account Memory
  ([ADR 55](0055-person-and-account-memory-and-traits.md)) read the chat's new
  messages themselves (ADR 62 Q6).
- **Explicit work.** Rewrite earlier messages offers only blocks with media. A
  Retry of a text block that isn't softened decides it again and ends combined.
- **The Processing Character Threshold** governs group briefings only.
- **For later.** A better rewrite is future work: softened texts suggest a
  structured account (feeling, facts, requests, deadlines) for long or messy
  messages. No switch restores the earlier text rewriting (ADR 62 Q21).

## Consequences

- Incoming text reaches a writing model only when calmed or as context (media
  rewrites, suggestions and memory); the decision model reads every text
  block of a chat whose switches call for a decision.
