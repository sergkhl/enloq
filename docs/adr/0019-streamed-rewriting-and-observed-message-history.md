---
status: accepted
---

# Streamed rewriting, observed message history and speech improvement

## Context

Waiting for a complete rewrite hid progress, edits and deletions in Telegram
erased what the owner had already read, and speech recognition makes mistakes
that matter most in names, numbers and negation. This ADR settles how rewrites
appear while they are written, what Enloq keeps of a message's earlier
versions, and how speech is recognized.

## Decision

**Streamed rewrites.** A rewrite's text replaces the incoming message in AI
view, or the draft in the composer, as it arrives, rather than appearing in a
separate preview. This covers every rewrite: blocks with media, calmed text
([ADR 53](0053-calming-hostile-messages-and-removing-adaptive-tone.md)) and
outgoing Rewrite; text that isn't calmed is not rewritten
([ADR 54](0054-incoming-text-combined-not-rewritten.md)). Incomplete text is
provisional and final validation is still required. The originals stay
reachable, the Reading View stays separate from processing, incoming work
still waits 30 quiet seconds, and outgoing Rewrite starts on the click.

- **Failure.** Failed or invalid output restores the draft as it was before
  the attempt, never overwriting newer edits; incoming work falls back to an
  applicable earlier rewrite or the originals. A short reason and Retry show,
  and partial output is never cached as a completed rewrite.
- **The composer while streaming.** It shows "Processing…" with the busy
  spinner ([ADR 35](0035-processing-character-threshold-and-empty-rewrites.md)).
  Editing text, tone, attachments and reply is paused; Send and Rewrite stay
  visible but disabled. Undo stops applying the stream, restores the earlier
  draft and resumes editing at once.
- **Stop.** Outgoing only. It keeps the text shown so far as an editable draft
  marked "Stopped · incomplete draft", or the original when nothing new has
  arrived, and Undo restores the full earlier draft until the next edit. Late
  chunks and results can't overwrite a stopped draft on any client or after a
  reconnect. Stop cancels the request best effort; Xiaomi's endpoint can't
  cancel a stream, so the provider may finish and bill
  ([ADR 29](0029-mimo-route-and-structured-incoming-rewriting.md)). Costs
  count either way, and cancellation is told apart from invalid format.
  Incoming rewrites have no Stop.
- **Leaving.** Leaving the chat or closing the client cancels nothing started
  on the service; reopening attaches to the same attempt or its result, with
  no duplicate call. A service interruption takes the failure path.

**Observed message history.** Enloq keeps every native text or caption
version and deletion it observes, for incoming and outgoing messages, with
their transcripts and the media already in its cache. It back-fills nothing,
attempts no recovery of versions it never saw and downloads nothing just for
history. AI rewrites have no history. The message's date starts one 30-day
expiry for all its versions; an edit or deletion does not extend it. Text
history is hidden while an account is logged out, logout clears media, and
account removal deletes everything.

Earlier versions are for the owner's reading only: they never enter AI
context, memory or emotion, and the current source's derived work is still
invalidated on change. Originals step through them with version arrows, a
pencil marks a Telegram edit, and a deleted message keeps what it last said
with a trash icon until the owner hides it
([ADR 20](0020-minimal-reading-and-ready-notifications.md),
[ADR 52](0052-deleted-messages-hiding-and-the-header-ai-toggle.md)).

**What counts as an edit.**

- **Telegram's copy of a sent message.** A confirmed send records the message
  from Telegram's receipt. Telegram's own copy, read moments later, replaces
  that record without an earlier version or an edited mark, and the message
  takes Telegram's date. Later edits show both as usual. A sent GIF or
  sticker's retained bytes then serve its preview without a download.
- **Hidden edits.** An edit Telegram marks `edit_hide` sets no pencil, and it
  keeps an earlier version only when the wording or a link target changed;
  other changes replace the stored message quietly.
- **The outgoing flag.** A change of the outgoing flag alone is metadata, never
  an edit; a note in Saved Messages from the account itself is always
  outgoing.

Store schemas 34 to 36 brought records written before these rules into line.
[History](../history.md#attachment-identity-and-native-versions) owns the
mechanics.

**Links and controls.** Web addresses, `www`, `t.me`, native named links and
`@username` are tappable in originals, rewrites and transcripts in both
clients, keeping their real destinations; the web opens them in a new tab.
The composer stays plain text. Controls are icons with accessible names;
Rewrite keeps a short label. Active work shows a spinner, and queued, paused,
failed and completed work stay distinguishable.

**Speech recognition.** Whisper large-v3-turbo, with automatic language
detection and automatic denoising off. The service transcribes through the
proxy: notes up to two minutes on the VPS's faster-whisper, longer ones or
those the VPS cannot answer through ElevenLabs Scribe v2, a paid call counted
in the Daily Budget ([ADR 39](0039-litellm-proxy-and-vps-speech.md)). Offline
mode and `scripts/check-voice.py` keep the Mac's
`mlx-community/whisper-large-v3-turbo`.

- **Required languages.** Russian, English and mixed Russian and English,
  preserving names, numbers and negation; other languages are best effort,
  with the original recording available.
- **Speed.** At most about 30 seconds of processing for a 60-second note
  after download.
- **Why Turbo.** A bounded comparison of local recognizers (full large-v3,
  Parakeet v3, Qwen3-ASR, with and without a noise filter) found no better
  default, and the filter introduced errors. A replacement or another cleanup
  method needs representative natural speech with verified reference
  transcripts and its own bounded check. There is no model menu or cleanup
  toggle.
- **Progress.** A transcription shows progress, then the complete transcript;
  a rewrite starts only after it, and nothing imitates streaming by replaying
  finished text.

**Transcripts.** The raw transcript is behind Show transcript at the end of
the player, which starts a missing one
([ADR 27](0027-header-message-menu-forwarding-details-and-voice-playback.md),
[ADR 51](0051-telegram-phone-look-and-new-messages.md)). The owner's own voice
notes and circles are transcribed raw, never rewritten (ADR 51). There is no
Re-transcribe ([ADR 26](0026-mobile-message-actions-and-send-status.md)). A
rewrite of speech may correct an unmistakable recognition slip from context,
but keeps ambiguous names, numbers, commitments and negation explicitly
uncertain: a possibly misheard word keeps its best reading followed by "(?)"
(ADR 29).

## Consequences

- Progress shows where the owner reads or writes, at the cost of handling
  interrupted output and protecting the owner's draft explicitly.
- Retracted correspondence stays readable on the service for up to 30 days
  from its date, but is never current information for the model.
- Voices of correspondents reach ElevenLabs for long notes and when the VPS
  can't answer.
