from ..domain.models import Chat, Message


def seed(store):
    if store.rows("SELECT key FROM chats LIMIT 1"):
        return
    fixtures = [
        (
            "user:101",
            "Анна · дизайн",
            "private",
            False,
            False,
            [
                (1, "Привет! Пришлёшь обновлённый макет до пятницы?", False),
                (2, "И отдельно подтверди, пожалуйста, стоимость иллюстраций.", False),
            ],
        ),
        (
            "user:102",
            "Alex · weekend plans",
            "private",
            False,
            False,
            [(1, "Can you bring the board game on Saturday?", False)],
        ),
        (
            "chat:201",
            "Studio · launch",
            "group",
            True,
            False,
            [
                (1, "@you Could you review the launch checklist today?", True),
                (2, "The new illustrations look great.", False),
            ],
        ),
        (
            "user:103",
            "Мария · документы",
            "private",
            False,
            True,
            [
                (
                    1,
                    "Вы опять всё задержали. Где договор? Мне нужен подписанный файл до 18:00.",
                    False,
                )
            ],
        ),
    ]
    for peer, title, kind, muted, archived, messages in fixtures:
        store.put_chat(
            Chat(
                account_id=store.account_id,
                peer=peer,
                title=title,
                kind=kind,
                muted=muted,
                archived=archived,
                ai_eligible=True,
                read_through=max(m[0] for m in messages),
            )
        )
        for identity, text, mention in messages:
            store.apply_message(
                Message(
                    account_id=store.account_id,
                    peer=peer,
                    message_id=identity,
                    revision=1,
                    date=store.clock(),
                    text=text,
                    mentioned=mention,
                )
            )
