---
status: superseded
---

# Muse by default, local Whisper, and optional personal Codex

This ADR chose Muse Spark 1.3 Contributor through Meta for every model role,
local MLX Whisper large-v3-turbo for speech, and an optional personal Codex
role for difficult cases. The Codex role was retired, and the app has none.
[ADR 29](0029-mimo-route-and-structured-incoming-rewriting.md) moved
generation to MiMo V2.6 Flash, [ADR 30](0030-decider-emotion-classification.md)
and [ADR 49](0049-useful-response-suggestions.md) give decisions to
Perplexity's decision model, and
[ADR 39](0039-litellm-proxy-and-vps-speech.md) sends every call through the
owner's LiteLLM proxy, with speech on the VPS.
[ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md) and
[ADR 56](0056-idioms-either-signal-hiding-and-memory-off-by-default.md) use
Muse again for calmed text and idiom suggestions only.
