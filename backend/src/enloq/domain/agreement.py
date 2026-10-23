"""The Member Agreement (ADR 48 Q3): what a person accepts before their join
request reaches the owner. The service serves it, so both clients show the
same words. Since ADR 55 Q27 it promises no privacy and names no provider or
retention, so a new provider or feature needs no new version. A new VERSION,
only for changed words, asks every member again, and their accounts' new AI
stages wait until they accept it."""

VERSION = "2026-10-21"

TITLE = "Before you ask to join"

PARAGRAPHS = (
    "Enloq is an unofficial, experimental Telegram app, not made by Telegram.",
    "This server holds a full Telegram session for each account you connect, so"
    " it can read and send anything that account can. Your two-step password"
    " passes through it when you sign in, and Telegram may flag the sign-in or"
    " limit your account. Enloq acts in Telegram only when you ask.",
    "There is no privacy promise. Anything Enloq reads, and anything it works out"
    " from it, may be sent to any AI or other service, kept for any time, and"
    " read by the server's owner and the coding agents they run. Your contacts"
    " haven't agreed to any of this; that risk is yours.",
    "Your data may be wiped, and the app may change or stop, at any time. You"
    " can leave in Settings: Enloq then signs out of your accounts and deletes"
    " what it holds for you. The owner can remove you too.",
    "Nothing is processed until the owner approves your request.",
)


def public():
    return {"version": VERSION, "title": TITLE, "paragraphs": list(PARAGRAPHS)}
