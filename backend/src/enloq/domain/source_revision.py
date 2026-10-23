"""Stable source-content identity shared by generation and reading."""

import hashlib
import json

from .forwarding import attribution_current
from .models import DISPLAY_ONLY, media_class


def dependency_fingerprint(message, attribution=None):
    """Prove equality when an uncached direct reply is later admitted locally.

    Native edit timestamps can collide, and a new cached row has no earlier
    local revision. Keep only a digest of the model's source fields. Unknown
    attachment identity cannot prove equality and remains conservative.
    ``attribution`` digests the message as a recorded ``[forwarded,
    forwarded_from]`` pair saw it, so a later fill-in still proves equality.
    """
    if message.media and not message.attachment_id:
        return None
    if attribution is not None:
        forwarded, name = (list(attribution) + [None, None])[:2]
        message = message.model_copy(
            update={"forwarded": forwarded, "forwarded_from": name}
        )
    return hashlib.sha256(
        json.dumps(
            message.model_dump(
                include={
                    "text",
                    "links",
                    "media",
                    "media_info",
                    "attachment_id",
                    "deleted",
                    "reply_to",
                    "outgoing",
                    "service",
                    "date",
                    "forwarded",
                    "forwarded_from",
                },
                # Display-only audio metadata never proves or breaks equality.
                exclude={"media_info": DISPLAY_ONLY},
            ),
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def proves(source, digest, recorded=None):
    """A transient reference's digest still matches, allowing only filled-in
    forward attribution since it was recorded."""
    if dependency_fingerprint(source) == digest:
        return True
    return (
        recorded is not None
        and attribution_current(recorded, source)
        and dependency_fingerprint(source, recorded) == digest
    )


def fingerprint(messages):
    return hashlib.sha256(
        json.dumps(
            [
                (m.key, m.revision, m.text, media_class(m.media), m.deleted, m.reply_to)
                + ((m.attachment_id,) if m.attachment_id else ())
                + (([link.model_dump() for link in m.links],) if m.links else ())
                for m in messages
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
