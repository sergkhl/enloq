import { previewTime, type HourCycle } from "./messageTime";

// People's words and rules, which both clients share (ADR 48): the owner's
// Join Requests and members, and a member's own Settings.

export const PEOPLE = {
  title: "People",
  requests: "Asking to join",
  members: "Members",
  none: "Nobody else uses Enloq on this server yet. Anyone who signs in with Telegram here asks to join, and you decide.",
  requestNote:
    "Approving starts their accounts and lets their phone or browser in. Declining signs them out and deletes everything.",
  memberNote:
    "Each member spends their own budgets, which only you set. Removing someone signs Enloq out of their Telegram accounts here and deletes everything it keeps for them.",
  removeConfirm: (name: string) =>
    `Remove ${name}? Enloq signs out of their Telegram accounts here and deletes everything it keeps for them. Their spending stays in your totals.`,
  leave: "Leave Enloq",
  leaveConfirm:
    "Leave Enloq? It signs out of your Telegram accounts here and deletes everything it keeps for you, with your phones and browsers.",
  unconfirmed:
    "Telegram didn't confirm every sign-out. End the Enloq session in Telegram → Settings → Devices.",
  memberBudget: "The server's owner sets your budgets.",
};

// The People row's detail in Settings.
export function waitingLabel(count: number) {
  return count ? `${count} waiting` : "";
}

// A person's @username and phone tail, as Telegram reported them.
export function personLine(person: { username: string; phone: string }) {
  return [person.username ? `@${person.username}` : "", person.phone]
    .filter(Boolean)
    .join(" · ");
}

export function lastActive(
  at: number | null | undefined,
  timezone: string,
  hourCycle?: HourCycle,
) {
  return at ? `Active ${previewTime(at, timezone, hourCycle)}` : "Not active";
}

export function askedAt(at: number, timezone: string, hourCycle?: HourCycle) {
  return `Asked ${previewTime(at, timezone, hourCycle)}`;
}
