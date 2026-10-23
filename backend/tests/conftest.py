import pytest
from enloq.application.calming import Calming
from enloq.application.memory import Memory
from enloq.storage.store import Store


@pytest.fixture
def calming_off(monkeypatch):
    """ADR 30's order, rewrite first and decision after, for cases that
    predate calming (ADR 53): their chats calm nothing. test_calming covers
    calming as shipped, on by default."""
    monkeypatch.setattr(Calming, "default", False)


@pytest.fixture
def text_rewriting(monkeypatch):
    """Text rewritten automatically, as before ADR 54's combining, for cases
    that exercise the rewrite path with text; voice notes, images and calming
    keep that path in the shipped app."""
    monkeypatch.setattr(Store, "rewrites_text", True)


@pytest.fixture
def remember_on(monkeypatch):
    """Remember this chat on by default, as before ADR 56, for cases that
    predate its Off default: their chats learn without switching on.
    test_memory_default covers the default as shipped."""
    monkeypatch.setattr(Memory, "default", True)
