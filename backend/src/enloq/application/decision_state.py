"""The decision model's state: one compact conversation (ADR 30).

Built only from already-prepared items: original wording, local transcripts,
image meanings and quote/forward attribution. Raw images, Chat Memory and
observed history never enter it.
"""


def entry(item):
    value = {"id": item["id"], "from": "owner" if item.get("outgoing") else "recipient"}
    if item.get("text"):
        value["text"] = item["text"]
    transcript = (item.get("transcript") or {}).get("text")
    if transcript:
        # Machine-generated from a voice note or video circle.
        value["transcript"] = transcript
    if item.get("image_context"):
        value["image_meaning"] = item["image_context"]
    placeholder = item.get("placeholder") or {}
    if item.get("media") and not (transcript or item.get("image_context")):
        value["attachment"] = placeholder.get("kind") or item["media"]
    if item.get("forwarded"):
        if item.get("forwarded_from"):
            value["forwarded_from"] = item["forwarded_from"]
        else:
            value["forwarded"] = True
    if item.get("imported"):
        value["imported"] = True
    if item.get("reply_to"):
        value["reply_to"] = item["reply_to"]
    return value


def incoming(messages, context):
    """Supporting context first, then the latest block to classify."""
    return {
        "conversation": [entry(m) for m in sorted(context, key=lambda m: m["id"])]
        + [{**entry(m), "latest_block": True} for m in messages]
    }


def reaction(packet):
    """The conversation as it stood, with the recipient's reaction on one exact
    message, which may be the owner's own."""
    items = sorted([*packet["context"], packet["target"]], key=lambda m: m["id"])
    return {
        "conversation": [entry(m) for m in items],
        "reaction": {
            "by": "recipient",
            "emoji": packet["reaction"]["emoji"],
            "on_message": packet["target"]["id"],
        },
    }


def suggestions(packet, options):
    """ADR 49 Q19: the conversation a suggestion set answers, then its replies
    by number, for the decision model to label each by what it says."""
    reaction_item = packet.get("reaction")
    if reaction_item:
        items = sorted(
            [*packet.get("context", []), *packet["messages"]], key=lambda m: m["id"]
        )
        state = {
            "conversation": [entry(m) for m in items],
            "reaction": {
                "by": "recipient",
                "emoji": reaction_item["emoji"],
                "on_message": reaction_item["target_id"],
            },
        }
    else:
        state = incoming(packet["messages"], packet.get("context", []))
    state["replies"] = [
        {"id": index, "text": option.text} for index, option in enumerate(options, 1)
    ]
    if packet.get("manual"):
        state["draft_intent"] = packet.get("draft", "")
        state["cursor"] = packet.get("cursor")
    return state
