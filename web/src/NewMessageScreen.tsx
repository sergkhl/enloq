import React, { useEffect, useState } from "react";
import { workspaceApi } from "./accounts";
import { Avatar } from "./Avatar";
import type { Chat } from "./conversation-api";
import { Note, ScreenFrame } from "./screenUi";
import {
  newMessagePath,
  openPath,
  peopleSections,
  SEARCH_PAUSE_MS,
  type People,
  type Person,
} from "../../shared/newMessage";

// New Message (ADR 51 Q7, Q15-Q18), opened from the pencil on the Private
// tab: the account's private chats of any age, then its contacts, and for
// three typed characters Telegram's people search. Choosing someone opens
// their private chat, ready to write (the app's `app/new-message.tsx`); for
// Add reminder (ADR 58 Q7), the reminder editor for them.
export function NewMessageScreen({
  account,
  title = "New message",
  onBack,
  onOpened,
}: {
  account: number;
  title?: string;
  onBack: () => void;
  onOpened: (chat: Chat) => void;
}) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<People | null>(null);
  const [problem, setProblem] = useState("");
  const [opening, setOpening] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const timer = window.setTimeout(
      () => {
        workspaceApi<People>(newMessagePath(account, query)).then(
          (answer) => {
            if (!live) return;
            setFound(answer);
            setProblem("");
          },
          (error) => live && setProblem((error as Error).message),
        );
      },
      query ? SEARCH_PAUSE_MS : 0,
    );
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [account, query]);

  async function open(person: Person) {
    if (opening) return;
    setOpening(person.peer);
    setProblem("");
    try {
      onOpened(await workspaceApi<Chat>(openPath(account, person.peer), {}));
    } catch (error) {
      setProblem((error as Error).message);
    } finally {
      setOpening(null);
    }
  }

  const sections = peopleSections(found);
  return (
    <ScreenFrame title={title} onBack={onBack}>
      <input
        className="people-search"
        type="search"
        aria-label="Search people"
        placeholder="Name or @username"
        autoFocus
        autoCapitalize="none"
        autoCorrect="off"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {problem && <Note>{problem}</Note>}
      {!found && !problem && <p className="people-note">Loading…</p>}
      {found && !sections.length && (
        <p className="people-note">
          {query.trim() ? "No one found." : "No chats or contacts yet."}
        </p>
      )}
      {sections.map((section) => (
        <section key={section.title} aria-label={section.title}>
          <h2 className="people-section">{section.title}</h2>
          {section.people.map((person) => (
            <button
              key={person.peer}
              type="button"
              className="people-row"
              aria-label={`${person.name}${person.username ? `, @${person.username}` : ""}`}
              disabled={!!opening}
              onClick={() => void open(person)}
            >
              <Avatar
                account={account}
                peer={person.peer}
                name={person.name}
                photoId={person.photo_id}
              />
              <span className="people-row-body">
                <strong>{person.name}</strong>
                {person.username && <small>@{person.username}</small>}
              </span>
            </button>
          ))}
        </section>
      ))}
    </ScreenFrame>
  );
}
