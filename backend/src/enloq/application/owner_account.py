"""The Owner Account's own Saved Messages (ADR 43 Q4, Q8, Q11).

Saved Builds and Telegram pairing codes go to the Saved Messages of the one
account `ENLOQ_OWNER_ACCOUNT` names, through the running service's
own Telegram client, so no second client, login or secret is added. Nothing
here takes a peer, and nothing in Saved Messages is ever deleted. One Saved
Build message is kept: each new build replaces its file and caption in place,
and it is pinned once when first posted (ADR 50 Q5). A failed post is
reported; nothing is sent again on its own. While that account is signed out,
a pairing signs it in with Telegram instead (ADR 47 Q11). A host command may
read back the newest image there (ADR 43), such as a
screenshot the owner left for a coding agent, or the owner's last few notes
with their images (ADR 51 Q1), such as a list of tasks.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import stat

from ..browser_auth import CODE_AGE
from ..config import private_directory, private_read, private_write
from ..domain.models import Refused

_LOG = logging.getLogger("enloq.owner_account")

# Plan default (IB1): a Saved Build is an APK under 200 MB.
MAX_BUILD = 200 * 1024 * 1024
BUILD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,118}\.apk")
# Telegram's limits for a non-Premium caption and message.
MAX_CAPTION = 1024
MAX_TEXT = 4096
# The states in which the Owner Account is signed out of this server, so a
# sign-in pairing may sign it in (ADR 47 Q11, Q13).
SIGNED_OUT = {"logged_out", "requires_login"}
# A host's read (ADR 43): the newest still image among
# the last 20 Saved Messages, at most 20 MB.
READ_SCAN = 20
MAX_IMAGE = 20 * 1024 * 1024
IMAGE_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
# A host's read of the owner's notes (ADR 51 Q1): up to 20 of them, found
# among the last 100 Saved Messages.
MAX_NOTES = 20
NOTES_SCAN = 100


class OwnerSignedOut(Refused):
    """The Owner Account is not signed in here, so no code can be posted; the
    pairing screens answer by offering Sign in with Telegram (HTTP 409)."""


class OwnerAccount:
    def __init__(self, manager, settings):
        self.manager = manager
        self.settings = settings
        # The service's data folder (`.local/builds/`), where the host's
        # publish command leaves an APK for one post.
        self.builds = settings.data.parent / "builds"
        # Where a host's read of Saved Messages leaves its one image.
        self.inbox = settings.data.parent / "inbox"
        # The one Saved Build message (ADR 50 Q5): its account, ID and whether
        # its pin is done, so each publish replaces it in place.
        self.build_record = settings.data / "saved-build.json"
        # One upload at a time; a pairing code never waits behind one.
        self._uploading = asyncio.Lock()
        # Notes waiting for the account to connect after its sign-in.
        self._later: set[asyncio.Task] = set()

    @property
    def identity(self) -> int:
        return self.settings.owner_account

    def _entry(self):
        rows = self.manager.registry.rows(
            "SELECT state, phone FROM accounts WHERE account_id=?", (self.identity,)
        )
        return rows[0] if rows else None

    def signed_out(self) -> bool:
        """Configured, but not signed in here: absent, signed out, or revoked
        by Telegram."""
        if not self.identity:
            return False
        entry = self._entry()
        return entry is None or entry["state"] in SIGNED_OUT

    def transport(self):
        if not self.identity:
            raise Refused("No Owner Account is set on this server")
        if self.signed_out():
            raise OwnerSignedOut("The Owner Account is not signed in on this server")
        try:
            assistant = self.manager.get(self.identity)
        except Refused:
            raise Refused("The Owner Account is not signed in on this server") from None
        if not assistant.transport.connected:
            raise Refused("The Owner Account is not connected to Telegram")
        return assistant.transport

    def available(self) -> bool:
        try:
            self.transport()
        except Refused:
            return False
        return True

    def _build(self, name: str):
        if not BUILD_NAME.fullmatch(name):
            raise Refused("Only an APK in the service's builds folder is posted")
        path = self.builds / name
        try:
            details = path.lstat()
        except OSError:
            raise Refused("The APK is not in the service's builds folder") from None
        if not stat.S_ISREG(details.st_mode):
            raise Refused("Only a regular APK file is posted")
        if not 0 < details.st_size <= MAX_BUILD:
            raise Refused("A Saved Build is an APK under 200 MB")
        with path.open("rb") as file:
            if file.read(4) != b"PK\x03\x04":
                raise Refused("Only an APK is posted")
        return path

    def _remembered(self):
        """The Owner Account's Saved Build message, if this server posted one."""
        try:
            value = json.loads(private_read(self.build_record, max_bytes=200))
        except (OSError, ValueError, Refused):
            return None
        if (
            not isinstance(value, dict)
            or value.get("account") != self.identity
            or not isinstance(value.get("message_id"), int)
        ):
            return None
        return value

    def _remember(self, identity: int, *, pinned: bool):
        private_write(
            self.build_record,
            json.dumps(
                {"account": self.identity, "message_id": identity, "pinned": pinned}
            ),
        )

    async def post_build(self, name: str, caption: str) -> dict:
        """A Saved Build: one APK from the builds folder with its caption
        (ADR 50 Q5). The remembered build message takes the new file and
        caption in place; without one, or once Telegram says it is gone, a new
        one is posted and pinned once, without a notification. The previous
        build stays until Telegram confirms the new one, and the service's
        copy is deleted once it has."""
        if len(caption) > MAX_CAPTION:
            raise Refused("A Saved Build's caption is at most 1,024 characters")
        path = self._build(name)
        transport = self.transport()
        async with self._uploading:
            record = self._remembered()
            replaced = False
            if record is not None:
                replaced = await self._replace(
                    transport, record["message_id"], path=path, caption=caption
                )
            if replaced:
                identity, pinned = record["message_id"], record.get("pinned", True)
            else:
                identity = await self._post(transport, path=path, caption=caption)
                pinned = False
                self._remember(identity, pinned=False)
            if not pinned:
                # Only a pin that never happened is tried again: one the owner
                # removed stays removed.
                pinned = await self._pin(transport, identity)
                self._remember(identity, pinned=pinned)
        path.unlink(missing_ok=True)
        return {"message_id": identity, "replaced": replaced, "pinned": pinned}

    async def _replace(self, transport, identity, **post) -> bool:
        """The new build in the remembered message; False once Telegram says
        that message is gone, so a new one is posted instead."""
        try:
            return await transport.edit_saved(identity, **post)
        except Refused:
            raise
        except Exception as error:
            _LOG.warning("saved_build_edit_failed", exc_info=True)
            raise Refused(
                f"Telegram did not take the new build ({type(error).__name__}); the previous one stays"
            ) from None

    async def _pin(self, transport, identity) -> bool:
        try:
            await transport.pin_saved(identity)
        except Exception:
            # The build is posted either way; the next publish pins it again.
            _LOG.warning("saved_build_pin_failed", exc_info=True)
            return False
        return True

    async def post_note(self, text: str, *, monospace: bool = False) -> int:
        """A text note, such as a pairing code (monospace, ADR 47 Q2) or a
        paired client's record."""
        if not text.strip() or len(text) > MAX_TEXT:
            raise Refused("A Saved Messages note needs 1 to 4,096 characters")
        return await self._post(self.transport(), text=text, monospace=monospace)

    async def post_note_soon(self, text: str, *, wait: int = 60):
        """A note that waits up to a minute for the account to connect, as
        right after its sign-in (ADR 47 Q11). It is posted once at most: a
        failed post is not tried again."""
        if self.available():
            with contextlib.suppress(Refused):
                await self.post_note(text)
            return

        async def later():
            for _ in range(wait):
                await asyncio.sleep(1)
                if self.available():
                    with contextlib.suppress(Refused):
                        await self.post_note(text)
                    return

        task = asyncio.create_task(later())
        self._later.add(task)
        task.add_done_callback(self._later.discard)

    async def latest_image(self) -> dict:
        """The newest image in Saved Messages, for the host's command. It is
        the only file in the data folder's `inbox/`, so each read replaces the
        last; nothing is logged. Text never comes back here; a pairing code
        has its own read."""
        transport = self.transport()
        try:
            image = await transport.latest_saved_image(
                scan=READ_SCAN, max_bytes=MAX_IMAGE
            )
        except Refused:
            raise
        except Exception as error:
            raise Refused(
                f"Telegram did not answer the read ({type(error).__name__})"
            ) from None
        extension = IMAGE_EXTENSIONS.get(image["mime"])
        if extension is None:
            raise Refused("Only a photo or a still image is read")
        inbox = self._empty_inbox()
        return {
            "message_id": image["message_id"],
            "date": image["date"],
            "caption": image["caption"],
            "file": self._keep(inbox, image["message_id"], extension, image["data"]),
        }

    async def notes(self, count: int) -> list[dict]:
        """The owner's last `count` own notes in Saved Messages, oldest first,
        for the host's command (ADR 51 Q1): their text and the kind of any
        media, with each photo or still image saved in the data folder's
        `inbox/`, which holds only this read's images. The service's own
        posts (pairing codes and records, the Saved Build) are skipped, and
        nothing is logged."""
        if not 1 <= count <= MAX_NOTES:
            raise Refused(f"Read 1 to {MAX_NOTES} notes")
        transport = self.transport()
        record = self._remembered()
        try:
            notes = await transport.saved_notes(
                count=count,
                scan=NOTES_SCAN,
                max_bytes=MAX_IMAGE,
                build=record["message_id"] if record else None,
            )
        except Refused:
            raise
        except Exception as error:
            raise Refused(
                f"Telegram did not answer the read ({type(error).__name__})"
            ) from None
        inbox = self._empty_inbox()
        answer = []
        for note in reversed(notes):
            extension = IMAGE_EXTENSIONS.get(note["mime"])
            file = None
            if note["data"] is not None and extension is not None:
                file = self._keep(inbox, note["message_id"], extension, note["data"])
            answer.append(
                {
                    "message_id": note["message_id"],
                    "date": note["date"],
                    "text": note["text"],
                    "media": note["media"],
                    "file": file,
                }
            )
        return answer

    def _empty_inbox(self):
        inbox = private_directory(self.inbox)
        for old in inbox.iterdir():
            if old.is_symlink() or old.is_file():
                old.unlink()
        return inbox

    def _keep(self, inbox, identity: int, extension: str, data: bytes) -> str:
        name = f"saved-{identity}.{extension}"
        descriptor = os.open(
            inbox / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        return f"inbox/{name}"

    async def latest_code(self) -> dict:
        """The newest pairing code the service posted to Saved Messages, while
        it still works, so a coding agent's browser on the host can trust
        itself (ADR 43). Nothing is logged."""
        transport = self.transport()
        try:
            return await transport.latest_saved_code(scan=READ_SCAN, max_age=CODE_AGE)
        except Refused:
            raise
        except Exception as error:
            raise Refused(
                f"Telegram did not answer the read ({type(error).__name__})"
            ) from None

    async def _post(self, transport, **post) -> int:
        try:
            return await transport.post_saved(**post)
        except Refused:
            raise
        except Exception as error:
            # The error's class only: a note's wording, such as a code, never
            # reaches the log or the answer.
            _LOG.warning("saved_messages_post_failed", exc_info=True)
            raise Refused(
                f"Telegram did not take the post ({type(error).__name__}); nothing was sent again"
            ) from None
