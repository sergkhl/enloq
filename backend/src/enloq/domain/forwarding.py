"""Forward attribution currentness under the fill-in rule (ADR 27).

Recorded work keeps the ``[forwarded, forwarded_from]`` pair it used. Learning
a previously unknown value later (a legacy row's forward state, or a missing
source name) never makes that work non-current; a conflicting known value does.
The original date, channel signature and imported state are immutable parts of
Telegram's forward header and are only ever filled in, so they are not
recorded as dependencies.
"""


def attribution(message):
    return [
        getattr(message, "forwarded", None),
        getattr(message, "forwarded_from", None),
    ]


def attribution_current(recorded, body) -> bool:
    """``body`` is the stored message (a model or its JSON mapping)."""
    get = body.get if isinstance(body, dict) else lambda name: getattr(body, name, None)
    forwarded, name = get("forwarded"), get("forwarded_from")
    was_forwarded, was_name = (list(recorded) + [None, None])[:2]
    if was_forwarded is not None and forwarded is not None:
        if was_forwarded != forwarded:
            return False
    if was_name is not None and was_name != name:
        return False
    return True


def details_missing(message) -> bool:
    """A cached message whose forward state or details a bounded exact read may
    still supply. After a successful read the header is final, including a
    still-missing source name."""
    if message.forwarded is None:
        return True
    return bool(
        message.forwarded
        and not message.forward_read
        and (message.forward_date is None or message.forwarded_from is None)
    )
