import { messageTime } from "./messageTime";
import { quoteDate } from "./replyQuote";

// Forwarded and imported lines (ADR 27, ADR 46 CU2), the same in the web and
// the Enloq app. They are not tappable and Copy never takes them.

export type Forward = {
  forwarded?: boolean | null;
  forwarded_from?: string | null;
  forward_date?: number | null;
  forward_signature?: string | null;
  imported?: boolean | null;
  forward_read?: boolean;
};

// Whether a bounded exact read may still supply a cached message's forward
// header (the service's details_missing rule).
export function forwardDetailsMissing(message: Forward) {
  if (message.forwarded == null) return true;
  return !!(
    message.forwarded &&
    !message.forward_read &&
    (message.forward_date == null || message.forwarded_from == null)
  );
}

// Telegram's header is immutable, so a captured version without it borrows
// the current message's.
export function forwardOf(shown: Forward, current: Forward): Forward {
  const known = shown.forwarded == null ? current : shown;
  return {
    forwarded: known.forwarded,
    forwarded_from: known.forwarded_from || current.forwarded_from,
    forward_date: known.forward_date ?? current.forward_date,
    forward_signature: known.forward_signature ?? current.forward_signature,
    imported: known.imported ?? current.imported,
  };
}

function origin(forward: Forward) {
  if (forward.imported) return "Imported";
  if (!forward.forwarded_from) return null;
  return `Forwarded from ${forward.forwarded_from}${
    forward.forward_signature ? ` (${forward.forward_signature})` : ""
  }`;
}

export type ForwardedLine = { text: string; imported: boolean };

// One message's line, or null when it isn't forwarded: the origin, then the
// original date (the year only outside this one) and time when known.
export function forwardedLine(
  forward: Forward,
  timezone: string,
): ForwardedLine | null {
  if (!forward.forwarded) return null;
  const parts = [origin(forward) || "Forwarded message"];
  if (forward.forward_date != null)
    parts.push(
      quoteDate(forward.forward_date, timezone),
      messageTime(forward.forward_date, timezone),
    );
  return { text: parts.join(" · "), imported: !!forward.imported };
}

// An AI block's lines: one per origin in first-appearance order, with a
// count above one and the dates' range.
export function forwardedGroups(
  forwards: Forward[],
  timezone: string,
): ForwardedLine[] {
  const groups = new Map<string, Forward[]>();
  for (const forward of forwards) {
    if (!forward.forwarded) continue;
    const key = forward.imported
      ? "imported"
      : forward.forwarded_from
        ? `from:${forward.forwarded_from}\n${forward.forward_signature || ""}`
        : "nameless";
    groups.set(key, [...(groups.get(key) ?? []), forward]);
  }
  return Array.from(groups.values()).map((members) => {
    const imported = !!members[0]!.imported;
    if (members.length === 1) return forwardedLine(members[0]!, timezone)!;
    const parts = [
      origin(members[0]!) || "Forwarded messages",
      `${members.length} messages`,
    ];
    const dates = members
      .map((member) => member.forward_date)
      .filter((value): value is number => value != null)
      .sort((a, b) => a - b);
    if (dates.length) {
      const first = quoteDate(dates[0]!, timezone);
      const last = quoteDate(dates.at(-1)!, timezone);
      parts.push(first === last ? first : `${first} – ${last}`);
    }
    return { text: parts.join(" · "), imported };
  });
}
