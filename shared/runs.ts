import { messageDate } from "./messageTime";

// Message Runs (ADR 51 Q13): one sender's consecutive messages, as Telegram's
// phone apps group them. A run breaks on another sender, a new calendar day,
// the Unread Separator, or more than five minutes between two messages
// (Telegram Android's ChatActivity joins neighbours within `5 * 60` seconds).
// A run's bubbles sit closer together and its last one has a tail; in a group
// another person's run carries their name on its first bubble and their
// avatar beside its last.
export const RUN_GAP = 5 * 60;

export type RunEntry = {
  // Who the row is from: "out" for the owner, else the author's peer or,
  // in a private chat, "in".
  sender: string;
  date: number;
  // The Unread Separator sits right before this row.
  separated?: boolean;
};

export type RunPlace = { first: boolean; last: boolean };

export function joins(
  earlier: RunEntry,
  later: RunEntry,
  timezone: string,
): boolean {
  return (
    !later.separated &&
    earlier.sender === later.sender &&
    Math.abs(later.date - earlier.date) <= RUN_GAP &&
    messageDate(earlier.date, timezone) === messageDate(later.date, timezone)
  );
}

// Each entry's place in its run, for entries in date order (oldest first).
export function messageRuns(entries: RunEntry[], timezone: string): RunPlace[] {
  return entries.map((entry, index) => ({
    first: index === 0 || !joins(entries[index - 1]!, entry, timezone),
    last:
      index === entries.length - 1 ||
      !joins(entry, entries[index + 1]!, timezone),
  }));
}

// A group message's sender for runs: the owner's own user is "out", as their
// sends are; anyone else, an anonymous admin or a channel included, is their
// peer and signature (only an exact own-user peer is the owner, as
// `participantLabel` says).
export function groupSender(
  message: {
    author_peer?: string | null;
    author?: string | null;
    author_signature?: string | null;
  },
  account: number,
): string {
  if (message.author_peer === `user:${account}`) return "out";
  const author = message.author_peer || `name:${message.author ?? ""}`;
  return `${author}|${message.author_signature ?? ""}`;
}

// Telegram's seven name colours, chosen by the peer's ID as Telegram does
// (red, orange, violet, green, cyan, blue, pink), readable on the incoming
// bubble in each colour scheme.
const NAME_COLORS = {
  light: [
    "#c9403f",
    "#c2621b",
    "#7e52c4",
    "#2f8a3c",
    "#16808a",
    "#2f6fb6",
    "#bf4a83",
  ],
  dark: [
    "#ff8a80",
    "#ffb26b",
    "#c5a8ff",
    "#8fdb8a",
    "#7fd8de",
    "#8fc2ff",
    "#ff9ccc",
  ],
};

export function peerIndex(peer: string | null | undefined): number {
  const id = /:-?(\d+)$/.exec(peer ?? "")?.[1];
  if (!id) return 0;
  // Telegram's `id % 7`, digit by digit: IDs outgrow a double's precision.
  let rest = 0;
  for (const digit of id) rest = (rest * 10 + Number(digit)) % 7;
  return rest;
}

export function nameColor(
  peer: string | null | undefined,
  scheme: "light" | "dark",
): string {
  return NAME_COLORS[scheme][peerIndex(peer)]!;
}
