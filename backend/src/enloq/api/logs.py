"""Extended logs (ADR 61): a client's log reaches the service's, and the owner
reads the service's own last lines in Settings → Logs."""

import logging
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ..domain.models import Model
from ..logs import RECENT_LINES, recent
from .scope import OWNER_ONLY

_LOG = logging.getLogger("enloq.client")
# Bounds on one request, so a client in a loop can't fill the disk; a longer
# stack is cut by the client.
MAX_ENTRIES = 200
MAX_TEXT = 20000
LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


class ClientLogEntry(Model):
    # When it happened on the client, in Unix milliseconds.
    time: float = Field(ge=0)
    level: Literal["info", "warn", "error"]
    source: str = Field(max_length=40)
    message: str = Field(max_length=MAX_TEXT)
    stack: str | None = Field(default=None, max_length=MAX_TEXT)


class ClientLogs(Model):
    # The build or browser it ran in.
    build: str = Field(default="", max_length=200)
    entries: list[ClientLogEntry] = Field(max_length=MAX_ENTRIES)


class ServerLogs(Model):
    lines: list[str]


def log_routes():
    router = APIRouter(prefix="/api/logs")

    @router.get("", response_model=ServerLogs, dependencies=OWNER_ONLY)
    def server_logs(limit: int = Query(default=500, ge=1, le=RECENT_LINES)):
        return {"lines": recent.read(limit)}

    @router.post("/client", status_code=204)
    def client_logs(body: ClientLogs, request: Request):
        for entry in body.entries:
            _LOG.log(
                LEVELS[entry.level],
                "member=%s client=%s kind=%s build=%s at=%d %s: %s%s",
                request.state.member,
                request.state.local_session,
                request.state.client_kind,
                body.build,
                entry.time,
                entry.source,
                entry.message,
                f"\n{entry.stack}" if entry.stack else "",
            )

    return router
