"""Preserve native named-link destinations without fetching them."""

from telethon import types

from ..domain.models import TextLink


def text_links(message):
    text = (getattr(message, "raw_text", "") or "").encode("utf-16-le")
    result = []
    for entity in (getattr(message, "entities", None) or [])[:1024]:
        if not isinstance(entity, types.MessageEntityTextUrl):
            continue
        start, end = entity.offset * 2, (entity.offset + entity.length) * 2
        if not 0 <= start < end <= len(text):
            continue
        try:
            text[start:end].decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        if isinstance(entity.url, str) and 0 < len(entity.url) <= 8192:
            result.append(
                TextLink(offset=entity.offset, length=entity.length, url=entity.url)
            )
    return result
