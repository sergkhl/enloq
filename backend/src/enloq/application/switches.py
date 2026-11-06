"""A private chat's Emotion and Voice and images switches (ADR 62 Q2), kept as
its other three are: a stored row is the chat's own choice, and a chat without
one has the switch Off. The account's AI processing stays their master."""

from ..domain.models import Refused


class ChatSwitch:
    # Off for every chat without its own choice (Q2).
    default = False

    def __init__(self, app, table, name, label):
        self.app, self.store, self.table = app, app.store, table
        # The control's internal name, and its name in the AI Menu.
        self.name, self.label = name, label

    def read(self, sql, args, db=None):
        if db is not None:
            return [dict(row) for row in db.execute(sql, args)]
        return self.store.rows(sql, args)

    def on(self, key, db=None):
        """Whether the chat's switch is On; groups and Saved Messages have
        none."""
        chat = self.read(
            "SELECT json_extract(body,'$.kind') AS kind FROM chats WHERE key=?",
            (key,),
            db,
        )
        if not chat or chat[0]["kind"] != "private":
            return False
        choice = self.read(
            f"SELECT enabled FROM {self.table} WHERE chat_key=?", (key,), db
        )
        return bool(choice[0]["enabled"]) if choice else self.default

    def view(self, key):
        self.private(key)
        return {"enabled": self.on(key)}

    def set(self, key, enabled):
        """The chat's own choice. Turned On, it does its work on the chat's
        latest block (Q8); turned Off, it keeps what was made."""
        self.private(key)
        was = self.on(key)
        with self.store.transaction() as db:
            db.execute(
                f"INSERT INTO {self.table} VALUES (?,?,?) ON CONFLICT(chat_key) DO UPDATE "
                "SET enabled=excluded.enabled,changed=excluded.changed "
                "WHERE enabled!=excluded.enabled",
                (key, int(enabled), self.store.clock()),
            )
        if enabled and not was:
            self.app.switched_on(key, self.name)
        return {"enabled": self.on(key)}

    def private(self, key):
        if self.store.chat(key).kind != "private":
            raise Refused(f"{self.label} is for private chats only")
