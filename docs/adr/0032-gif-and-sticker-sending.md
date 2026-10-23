---
status: accepted
---

# Native GIF and sticker selection and sending

## Context

The composer sent text and photos only. The owner wants to send GIFs and
stickers from their Telegram accounts' own libraries and Telegram's GIF
search, with the same deliberate Send, draft safety and delivery evidence as
every other send. GIF search goes through Telegram's inline GIF bot, which
receives the query.

## Decision

**Sources (Q1-Q2).** The active account's saved GIFs plus deliberate
owner-entered Telegram GIF search, and its installed sticker packs, recent
stickers and favorites. Stickers are chosen from existing items only. User-file
GIF and sticker uploads, custom creation and sticker-pack discovery or
installation are not adopted. Account libraries follow the existing Session
Owner and never cross accounts.

Search uses the current `config.gif_search_username` with an empty search
peer, as Telegram's [GIF guide](https://core.telegram.org/api/gifs) defines.
[Telegram's bot privacy policy](https://telegram.org/privacy/eu#6-3-what-data-bots-receive)
says the inline bot receives the query and may obtain public account
information; the owner accepted that boundary. Queries are only the text the
owner types, never drafts, correspondence, memory or location.

**Selection and entry (Q3-Q4).** The composer's paperclip offers Photos, GIFs
and Stickers. Picking a GIF or sticker stages a visible preview; it does not
send. The separate Send button stays the deliberate delivery action. There
are no sparkles and no instructional hints.

**Formats and received display (Q5-Q6).** Static, animated and video stickers
are supported, respecting the sending account's Premium entitlement;
decorative Premium-effect overlays are not. Received animated and video
stickers display in both reading views with a truthful fallback. Video (WebM)
stickers stay still on the phones and animate in browsers that decode their
transparency ([ADR 46](0046-the-apps-catch-up-units.md) Q10). Received media
keeps the cache and history boundaries below.

**Composition (Q7-Q10).** A GIF may carry an optional authored caption: the
composer's text becomes it, and Rewrite changes only that text without
analysing the GIF. A sticker sends alone; existing text stays as an unsent
draft and is never sent or discarded with it. One GIF or sticker per Send,
with no mixed album: attached photos must be sent or removed before a GIF or
sticker is chosen, and the draft survives the refusal. Another choice replaces
only the unsent media; its X removes it. Both keep the authored text.

**Durable selection (Q11).** The exact chosen item and its preview are saved
per account and chat until sent or discarded, surviving navigation, reloads
and service restarts like other unsent attachments, independently of the
30-day message cache. An unavailable item stays visible with a truthful
failure and needs deliberate reselection, never a silent substitute.

**Picker (Q12-Q16).** GIFs and Stickers open a tall sheet: saved GIFs, a search
after a brief pause in typing (empty input shows saved GIFs) with More, and
recent, favorite and installed stickers with a filter over pack names and
their emoji or keywords, without external discovery. The sheet closes after a
choice and reopens to replace it. Tiles are still images; the selected
preview animates. Received animated stickers loop silently only while
visible. Motion respects reduced-motion settings everywhere. Cached account
collections show at once and bounded lists refresh on opening; changes while
the picker is closed mark them stale instead of starting bulk downloads.

**Web-content GIFs (Q17).** Validated native-document GIFs and downloadable
web-content search results are both supported, preferring an existing native
document. A web-only choice keeps its exact media privately and uploads it
only after deliberate Send, with any authored caption. Ephemeral inline-send
tokens and the bot's prepared wording are not used. Unsupported or unsafe
media is unavailable, never replaced. Uploading a GIF adds it to Telegram's
saved GIFs and may push out the oldest at the list limit; the owner accepted
that side effect. An upload can change the saved list even if the send fails
and is no proof of delivery. Previews and media bytes use the application's
authenticated private routes ([ADR 16](0016-persistent-media-cache-and-non-ai-media.md)).

**Kept draft and reply (Q18-Q19).** While a sticker is selected, its preview
takes the text field's place, with a small Draft kept status when text exists.
That exact text and its Undo state are restored when the sticker is removed
or its send is durably accepted by the service, without waiting for Telegram.
Rewrite, Undo and suggestion insertion pause while the sticker is selected.
Selected media uses the composer's reply target and the deletion guards of
[ADR 31](0031-message-deletion-selection-and-group-ai-controls.md). After a
sticker's durable acceptance the reply target stays with the kept text, or
clears normally when no text is kept; a GIF and its caption consume the reply
normally. A deleted or unresolved target is never silently cleared or
replaced. The pending sticker owns only the sticker and its reply, never the
kept text. Failed acceptance leaves selection and draft intact, and later
failure, uncertainty or recovery never overwrites a newer draft.

**Bounds and retention (Q20-Q21).** Each GIF is capped at 50 MiB and each
sticker, animated and video included, at 5 MiB. Unsupported, unsafe, corrupt
or oversized media is rejected truthfully, without erasing drafts or
substituting another item. A ready selection keeps its validated exact bytes
privately until sent, discarded or no longer needed by its retained Send
Operation, outside the disposable 2 GiB shared media LRU; an expired message
cache never destroys an unsent selection or evidence a retained send needs.
Received media keeps its 30-day source-date expiry and cache-only history,
with no download of missing historical attachments. Library previews are
bounded, reusable and never correspondence history. There is no bulk
background download, older-chat scan or back-fill, and native refreshes and
private routes keep their account, trust, version and availability checks.

**Native documents go out as themselves (Q21).** A native GIF or sticker is
sent in the final `messages.sendMedia` as the chosen document, as Telegram's
own apps send them; nothing is associated with the chat first. Before the
send, a 4 KB read of the same file proves its reference; an expired one is
renewed once through its known source, for the same exact document only. A
GIF that cannot be renewed uploads its retained bytes under Q17, so each GIF
stays the same Telegram document where possible. A sticker that cannot be
renewed stays unsent and asks to be chosen again; it is never recreated,
converted, swapped for a pack item or sent past the account's entitlement.
An expired search token is no reason to search again and pick another hit.

When Telegram gives a definite answer while a GIF, sticker or image is being
prepared, the Not sent line names it, for example "Telegram refused this GIF
(MEDIA_INVALID). Nothing was sent." An unknown failure keeps "Stopped before
reaching Telegram". The final send's own refusals are unchanged.

**Transient search (Q22).** The query and its results last while the same
chat stays open, including closing and reopening the picker, and clear on
leaving the chat or account or reloading. There is no search history.
Superseded results or an account change cannot put media into another draft.

**Suggestions (Q23).** GIF and sticker sends count as ordinary outgoing
replies for incoming-block and reaction Response Suggestions: durable
acceptance suspends current options, native confirmation withdraws them, a
definitive non-send or cancellation may restore still-valid cached options
without a model call, and uncertain delivery keeps them suspended. Emoji
reactions stay distinct. A GIF or sticker alone neither clears the emotion
nor changes wording already inserted into the draft.

**Sending rules.** GIFs and stickers are Non-AI Media under ADR 16. They use
the account- and chat-bound sending, Send Queue and delivery evidence of
[ADR 18](0018-send-operations-ownership.md) and
[ADR 26](0026-mobile-message-actions-and-send-status.md): each send is an
immutable draft, media and reply revision with stable native random IDs, and
a reference refresh or deliberate retry never creates a second delivery
identity. Ordered per-chat dispatch, paid-send refusal and evidence-based
Check delivery and cancellation stay. AI Off still allows choosing and sending
media. Captions keep the 1,024 UTF-16-unit ceiling, bounded by Telegram's
current limit, with no truncation or splitting. Pure GIF and sticker work
makes no model call.

[Media delivery](../media-delivery.md#gif-and-sticker-delivery-and-proof-limits)
owns the mechanics and proof limits.

## Consequences

- The owner's existing Telegram libraries and a staged Send flow cover the
  common cases; media generation, pack management and one-tap sending stay out.
- GIF search discloses the typed query to Telegram's inline bot.
- Sending a searched GIF can change the account's saved-GIF list.
- Preview, simulated tests, native sending and the owner's ordinary use are
  separate evidence.

## Questions

- Q1: saved GIFs plus owner-typed search; no user-file uploads.
- Q2: installed packs, recent and favorite stickers; selection only.
- Q3: a choice stages a preview; the Send button sends.
- Q4: the paperclip offers Photos, GIFs and Stickers.
- Q5: static, animated and video stickers within the account's entitlement;
  no Premium-effect overlays.
- Q6: received animated and video stickers display too, with a fallback;
  video stickers stay still on phones (ADR 46 Q10).
- Q7: a GIF takes the composer's text as an optional caption.
- Q8: a sticker sends alone and keeps the text as a draft.
- Q9: one GIF or sticker per Send, no mixed album.
- Q10: another choice replaces the media; X removes it; text stays.
- Q11: the selection is saved per account and chat until sent or discarded.
- Q12: search after a pause in typing; empty input shows saved GIFs.
- Q13: a tall sheet that closes on a choice and reopens to replace it.
- Q14: still tiles, an animated selected preview, reduced motion respected.
- Q15: the sticker filter searches the account's own library only.
- Q16: cached catalogs show at once and refresh on opening; no bulk downloads.
- Q17: native-document and downloadable web GIFs; uploads after Send, with the
  saved-GIF side effect accepted.
- Q18: a sticker replaces the text field; Draft kept; text and Undo restored
  on removal or durable acceptance.
- Q19: selected media uses the composer's reply target; a sticker leaves it
  with the kept text.
- Q20: 50 MiB per GIF, 5 MiB per sticker.
- Q21: exact bytes kept privately; native documents sent as themselves after a
  4 KB reference check; one renewal; a GIF may upload its bytes; a sticker is
  chosen again.
- Q22: query and results last while the chat is open; no search history.
- Q23: GIF and sticker sends answer suggestions as ordinary replies.
