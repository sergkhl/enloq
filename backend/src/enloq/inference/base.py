from dataclasses import dataclass, field
from typing import Protocol

from ..domain.models import Unavailable


class ProviderRefused(Unavailable):
    """No provider work occurred (unavailable exact route, HTTP 429/402 or a
    refused connection, without usage or cost): counts nothing (ADR 15)."""

    def __init__(self, message, *, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class InvalidOutput(ValueError):
    """A fixed, content-free diagnostic; never retain failed model output."""


@dataclass
class EngineResponse:
    content: dict | str
    usage: dict = field(default_factory=dict)
    actual_nanos: int | None = None


class DraftEngine(Protocol):
    name: str
    model: str
    provider: str

    async def prepare(self, packet: dict) -> int: ...
    async def generate(self, role: str, packet: dict) -> EngineResponse: ...
