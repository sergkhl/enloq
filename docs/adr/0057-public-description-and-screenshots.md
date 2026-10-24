---
status: accepted
---

# Public description and screenshots

## Context

Enloq has public AGPL-3.0 source and a personal deployment shared with approved
members. Its public presentation should explain the product and let readers
inspect its actual interface and engineering.

## Decision

- Lead with reading busy conversations, voice-to-text, clearer editable replies
  and daily group briefings linked to their original messages. Keep the user's
  separate decision to Send explicit.
- Explain the name: Enloq comes from *en* (縁, human connection) and *loqui*
  (Latin, “to speak”).
- Show three complementary views: a calmed message with originals accessible,
  reply suggestions with an editable draft, and a group briefing with source
  links. Use the conversation capture on the portfolio card and all three on
  the case study and repository README.
- Capture the actual phone-width web interface in its dark theme at high
  resolution. Preserve its controls and proportions, with short captions and
  no invented interface or device frame.
- Use authored synthetic conversations and prepared fixture outputs. Disclose
  that they demonstrate presentation, not provider quality, live Telegram
  delivery or native-device acceptance.
- Link the public source and identify AGPL-3.0. Describe the personal deployment
  accurately; source publication does not establish a generally available
  service, supported self-hosting or an app-store release.

## Consequences

The README and portfolio explain the same capabilities and use the same image
set. Product behavior and its existing acceptance gates remain with their
owning ADRs and runbooks.
