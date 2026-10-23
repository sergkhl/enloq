---
status: superseded
---

# Distilled dashboard summaries and an explicit rewrite choice

This ADR required distilled summaries of incoming messages on the attention
dashboard and a No rewrite / Professional / Casual choice next to Send.
[ADR 13](0013-clean-slate-rewriting-rebuild.md) retired the dashboard and gave
the composer separate Rewrite and Send buttons with a remembered tone, now
Neutral, Professional or Friendly casual
([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)). Its
composer rules continue there and in
[ADR 19](0019-streamed-rewriting-and-observed-message-history.md): Send
transmits exactly the shown draft, Undo restores the earlier text, and a late
result never overwrites the owner's newer edits.
