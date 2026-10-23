"""Post to, or read back from, the Owner Account's Saved Messages from the
service host (ADR 43).

`mobile/scripts/publish-android` runs this inside the VPS container after it
copies an APK into the data folder's `builds/`:

    python -m enloq.saved --file enloq-0.1.0-1a2b3c4.apk < caption

The caption, or a note without `--file`, is read from standard input. A build
replaces the one Saved Build message in place, which is pinned once when first
posted (ADR 50 Q5). A coding
agent fetches a screenshot the owner left there (ADR 43):

    python -m enloq.saved --latest-image

The service saves the newest image among the last 20 messages as the only file
in the data folder's `inbox/`. A coding agent's browser on the host trusts
itself with the newest pairing code the service posted there, printed alone
while it still works (ADR 43):

    python -m enloq.saved --pairing-code

The owner leaves lists of tasks there too; the last N of their own notes come
back with their text, their images saved in `inbox/` (ADR 51 Q1):

    python -m enloq.saved --notes 7

All drive the running service over the container's loopback with the
bootstrap control credential, which never leaves this host.
"""

import argparse
import sys
from urllib.parse import urlsplit

import httpx

from .config import Settings, private_read
from .domain.models import Refused


def _service(settings: Settings, *, timeout):
    try:
        secret = private_read(settings.data / "bootstrap", max_bytes=200)
    except (OSError, Refused) as error:
        raise Refused(
            "Enloq isn't running. Start its service and try again."
        ) from error
    headers = {"Origin": settings.origin}
    if settings.public_origin:
        headers["Host"] = urlsplit(settings.public_origin).netloc
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{settings.port}",
        headers=headers,
        timeout=timeout,
        trust_env=False,
    )
    return secret, client


def _answer(response, refusal):
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise Refused(detail if isinstance(detail, str) else refusal)
    return response.json()


def post(settings: Settings, *, file: str = "", text: str = "") -> dict:
    # An APK upload takes minutes over Telegram; nothing is sent twice.
    secret, service = _service(settings, timeout=httpx.Timeout(900, connect=5))
    body = {"secret": secret}
    body.update({"file": file, "caption": text} if file else {"text": text})
    try:
        with service as client:
            response = client.post("/api/auth/saved-messages", json=body)
    except httpx.HTTPError as error:
        raise Refused(
            "The post's outcome is unknown: check Saved Messages before trying again."
        ) from error
    return _answer(response, "The service refused the post.")


def latest_image(settings: Settings) -> dict:
    secret, service = _service(settings, timeout=httpx.Timeout(120, connect=5))
    try:
        with service as client:
            response = client.post(
                "/api/auth/saved-messages/latest-image", json={"secret": secret}
            )
    except httpx.HTTPError as error:
        raise Refused("The read did not finish; try again.") from error
    return _answer(response, "The service refused the read.")


def notes(settings: Settings, count: int) -> list[dict]:
    # Up to 20 images may download, so the read gets a few minutes.
    secret, service = _service(settings, timeout=httpx.Timeout(300, connect=5))
    try:
        with service as client:
            response = client.post(
                "/api/auth/saved-messages/notes",
                json={"secret": secret, "count": count},
            )
    except httpx.HTTPError as error:
        raise Refused("The read did not finish; try again.") from error
    return _answer(response, "The service refused the read.")["notes"]


def note_lines(note: dict) -> list[str]:
    """One note for the terminal: a heading with its ID, date, media and
    image file, then its text."""
    heading = [f"Note {note['message_id']}", note["date"]]
    if note["media"]:
        heading.append(note["media"])
    if note["file"]:
        heading.append(note["file"])
    return [" · ".join(heading), *(note["text"].splitlines() or ["(no text)"]), ""]


def pairing_code(settings: Settings) -> dict:
    secret, service = _service(settings, timeout=httpx.Timeout(60, connect=5))
    try:
        with service as client:
            response = client.post(
                "/api/auth/saved-messages/pairing-code", json={"secret": secret}
            )
    except httpx.HTTPError as error:
        raise Refused("The read did not finish; try again.") from error
    return _answer(response, "The service refused the read.")


def posted_line(posted: dict) -> str:
    done = (
        "Replaced the Saved Build in the Owner Account's Saved Messages"
        if posted.get("replaced")
        else "Posted a new Saved Build to the Owner Account's Saved Messages"
    )
    if posted.get("pinned"):
        return done + "; it is pinned."
    return done + "; it isn't pinned, and the next publish tries again."


def main():
    parser = argparse.ArgumentParser(
        description="Post an APK or a note to the Owner Account's Saved Messages,"
        " save the newest image there to the data folder's inbox/, print the"
        " newest pairing code posted there, or print the owner's last notes."
    )
    parser.add_argument("--file", default="", help="an APK in the builds folder")
    parser.add_argument(
        "--latest-image",
        action="store_true",
        help="save the newest image among the last 20 messages to inbox/",
    )
    parser.add_argument(
        "--pairing-code",
        action="store_true",
        help="print the newest pairing code posted there, while it still works",
    )
    parser.add_argument(
        "--notes",
        type=int,
        default=0,
        metavar="N",
        help="print the owner's last N notes there (1-20), oldest first,"
        " with their images saved to inbox/",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.latest_image + args.pairing_code + bool(args.file) + bool(args.notes) > 1:
        parser.error("choose one of --file, --latest-image, --pairing-code and --notes")
    settings = Settings.load("personal", port=args.port)
    try:
        if args.notes:
            found = notes(settings, args.notes)
        elif args.pairing_code:
            code = pairing_code(settings)
        elif args.latest_image:
            image = latest_image(settings)
        else:
            posted = post(settings, file=args.file, text=sys.stdin.read())
    except Refused as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
    if args.notes:
        for note in found:
            print("\n".join(note_lines(note)))
        return
    if args.pairing_code:
        # The digits alone, for a script to read.
        print(code["code"])
        return
    if not args.latest_image:
        print(
            posted_line(posted)
            if args.file
            else "Posted to the Owner Account's Saved Messages."
        )
        return
    print(
        f"Saved Messages image {image['message_id']} from {image['date']}"
        f" is in the data folder: {image['file']}"
    )
    if image["caption"]:
        print(f"Caption: {image['caption']}")


if __name__ == "__main__":
    main()
