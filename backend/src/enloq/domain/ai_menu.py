"""What a chat's AI Menu and its robot show (ADR 62)."""

from .models import Model
from .suggestions import Style


class ChatAi(Model):
    """A chat's AI Menu in its conversation snapshot, so the header and the
    menu draw without further reads: the account's AI processing, whether AI
    work can run in the chat (Q14) and, in a private chat, its own switches
    and Style with the owner's server-wide switches over them. Writes keep
    each switch's own route."""

    # Account AI Processing, the master over every chat's switches (ADR 14).
    account: bool
    # Whether AI work can run in the chat: the robot is filled (Q14).
    running: bool
    # A private chat's own choices; None in a group.
    suggestions: bool | None = None
    style: Style | None = None
    calming: bool | None = None
    remember: bool | None = None
    # The owner's server-wide Automatic suggestions and Memory switches,
    # which an Off overrides the chat's (Q2); None in a group.
    server_suggestions: bool | None = None
    server_memory: bool | None = None
