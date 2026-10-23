"""Host commands for the Owner Account's Saved Messages (ADR 43 Q4).

Only the service host reaches these routes: a request must come from the
container's own loopback, never through the edge, and carry the bootstrap
control credential, which never leaves the host. They take no peer. One posts;
the others read back the newest image there, the newest pairing code the
service posted while it still works (ADR 43), or the
owner's last few notes (ADR 51 Q1).
"""

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import Field

from ..domain.models import Model, Refused

LOOPBACK = {"127.0.0.1", "::1"}


class SavedPost(Model):
    secret: str = Field(min_length=1, max_length=200, repr=False)
    # One APK from the service's builds folder with its caption, or a note.
    file: str = Field(default="", max_length=124)
    caption: str = Field(default="", max_length=1024)
    text: str = Field(default="", max_length=4096)


class SavedPosted(Model):
    message_id: int
    # A Saved Build replaced the remembered message in place, and that
    # message is pinned (ADR 50 Q5); a note is neither.
    replaced: bool = False
    pinned: bool = False


class SavedRead(Model):
    secret: str = Field(min_length=1, max_length=200, repr=False)


class SavedImage(Model):
    message_id: int
    date: datetime
    caption: str
    # Relative to the service's data folder: `inbox/saved-<id>.<type>`.
    file: str


class SavedNotesRead(SavedRead):
    count: int = Field(ge=1, le=20)


class SavedNote(Model):
    message_id: int
    date: datetime
    text: str
    # The media's kind, such as `image` or `audio`, if the note has any.
    media: str | None
    # A photo or still image, saved as `inbox/saved-<id>.<type>`.
    file: str | None


class SavedNotes(Model):
    # Oldest first, as the owner wrote them.
    notes: list[SavedNote]


class SavedCode(Model):
    message_id: int
    date: datetime
    # The six digits, for a coding agent's browser on the host to type.
    code: str


def host_only(request: Request, refusal: str):
    host = request.client.host if request.client else ""
    if host not in LOOPBACK or request.headers.get("x-forwarded-for"):
        raise Refused(refusal)


def saved_routes(owner, auth):
    router = APIRouter(prefix="/api/auth")

    @router.post("/saved-messages", response_model=SavedPosted)
    async def post(body: SavedPost, request: Request):
        host_only(request, "Saved Messages posts are host commands only")
        auth.control(body.secret)
        if bool(body.file) == bool(body.text):
            raise Refused("Post one APK with its caption, or one note")
        if body.file:
            return await owner.post_build(body.file, body.caption)
        return {"message_id": await owner.post_note(body.text)}

    @router.post("/saved-messages/latest-image", response_model=SavedImage)
    async def latest_image(body: SavedRead, request: Request):
        host_only(request, "Saved Messages reads are host commands only")
        auth.control(body.secret)
        return await owner.latest_image()

    @router.post("/saved-messages/notes", response_model=SavedNotes)
    async def notes(body: SavedNotesRead, request: Request):
        host_only(request, "Saved Messages reads are host commands only")
        auth.control(body.secret)
        return {"notes": await owner.notes(body.count)}

    @router.post("/saved-messages/pairing-code", response_model=SavedCode)
    async def pairing_code(body: SavedRead, request: Request):
        host_only(request, "Saved Messages reads are host commands only")
        auth.control(body.secret)
        return await owner.latest_code()

    return router
