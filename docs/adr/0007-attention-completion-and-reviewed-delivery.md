---
status: superseded
---

# Track attention independently from reading and delivery

This ADR tracked Attention Items with Handled, Snooze and Waiting controls,
separately from reading, and bound each deliberate send to the exact reviewed
text and target. [ADR 13](0013-clean-slate-rewriting-rebuild.md) retired
attention and commitment tracking. Its delivery rules continue in
[ADR 18](0018-send-operations-ownership.md) (a send bound to its exact content,
account, chat and reply target, with a stable identity and retained receipts),
[ADR 17](0017-send-hand-off-boundary.md) (a send Telegram may have executed
stays unknown and is never resent silently) and
[ADR 26](0026-mobile-message-actions-and-send-status.md) (the per-chat send
queue).
