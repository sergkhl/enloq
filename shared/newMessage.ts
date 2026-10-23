// New Message (ADR 51 Q16): the people the service found, in Telegram's
// order: the account's private chats, then contacts never chatted with, then
// Telegram's own people search. Empty sections leave out.

export type Person = {
  peer: string;
  name: string;
  username?: string | null;
  photo_id?: string | null;
  chat?: boolean;
};

export type People = { chats: Person[]; contacts: Person[]; search: Person[] };

export function peopleSections(found: People | null) {
  if (!found) return [];
  return [
    { title: "Chats", people: found.chats },
    { title: "Contacts", people: found.contacts },
    { title: "Global search", people: found.search },
  ].filter((section) => section.people.length > 0);
}

// The search waits for a short pause in typing.
export const SEARCH_PAUSE_MS = 300;

export const newMessagePath = (account: number, query: string) =>
  `accounts/${account}/new-message?query=${encodeURIComponent(query.trim())}`;

export const openPath = (account: number, peer: string) =>
  `accounts/${account}/new-message/${encodeURIComponent(peer)}`;
