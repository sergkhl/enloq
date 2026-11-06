import pytest
from enloq.application.calming import Calming
from enloq.application.memory import Memory
from enloq.application.suggestions import Suggestions
from enloq.application.switches import ChatSwitch


@pytest.fixture(autouse=True)
def switches_on(monkeypatch):
    """ADR 62 Q2 turned every private chat's switches Off by default. Cases
    that predate it exercise Automatic suggestions, Emotion, Soften hostile
    messages and Voice and images On, as their chats then were; Remember this
    chat keeps its own Off default (``remember_on``). ``shipped_defaults``
    restores the defaults as shipped."""
    for owner in (Calming, Suggestions, ChatSwitch):
        monkeypatch.setattr(owner, "default", True)


@pytest.fixture
def shipped_defaults(monkeypatch):
    """Every private chat's five switches Off, as ADR 62 Q2 ships them."""
    for owner in (Calming, Suggestions, ChatSwitch, Memory):
        monkeypatch.setattr(owner, "default", False)


@pytest.fixture
def calming_off(monkeypatch):
    """ADR 30's order, rewrite first and decision after, for cases that
    predate calming (ADR 53): their chats calm nothing. test_calming covers
    calming as shipped, on by default."""
    monkeypatch.setattr(Calming, "default", False)


@pytest.fixture
def remember_on(monkeypatch):
    """Remember this chat on by default, as before ADR 56, for cases that
    predate its Off default: their chats learn without switching on.
    test_memory_default covers the default as shipped."""
    monkeypatch.setattr(Memory, "default", True)
