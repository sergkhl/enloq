// Links in message text (ADR 46 Q9: tappable in both clients since ADR 44's
// MW2). Telegram's own text links come with offsets; addresses, `www.`,
// `t.me/` and @usernames written in plain text are found here. Only http and
// https destinations open; anything else stays text.
export type TextLink = { offset: number; length: number; url: string };
export type TextSegment = { text: string; url: string | null };

export function safeURL(value: string): string | null {
  if (/[\u0000- \u007f]/.test(value)) return null;
  const normalized = /^(www\.|t\.me\/)/i.test(value)
    ? `https://${value}`
    : value;
  try {
    const url = new URL(normalized);
    return ["http:", "https:"].includes(url.protocol) && !!url.hostname
      ? normalized
      : null;
  } catch {
    return null;
  }
}

const FOUND =
  /https?:\/\/[^\s<>"']+|\b(?:www\.|t\.me\/)[^\s<>"']+|(?<![\p{L}\p{N}_@])@[a-zA-Z][a-zA-Z0-9_]{3,31}\b/giu;

// Trailing punctuation and unbalanced closing brackets end a sentence, not
// the address.
function trimmed(found: string) {
  let label = found.replace(/[.,!?:;]+$/, "");
  for (const [open, close] of [
    ["(", ")"],
    ["[", "]"],
    ["{", "}"],
  ] as const) {
    while (
      label.endsWith(close) &&
      label.split(close).length > label.split(open).length
    )
      label = label.slice(0, -1);
  }
  return label;
}

function plain(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let cursor = 0;
  for (const match of text.matchAll(FOUND)) {
    const label = trimmed(match[0]);
    const offset = match.index ?? 0;
    if (offset > cursor)
      segments.push({ text: text.slice(cursor, offset), url: null });
    segments.push({
      text: label,
      url: safeURL(
        label.startsWith("@") ? `https://t.me/${label.slice(1)}` : label,
      ),
    });
    cursor = offset + label.length;
  }
  if (cursor < text.length)
    segments.push({ text: text.slice(cursor), url: null });
  return segments;
}

export function linkSegments(text: string, links: TextLink[] = []) {
  const segments: TextSegment[] = [];
  let cursor = 0;
  for (const link of [...links].sort((a, b) => a.offset - b.offset)) {
    const end = link.offset + link.length;
    if (
      !Number.isInteger(link.offset) ||
      !Number.isInteger(link.length) ||
      link.length <= 0 ||
      link.offset < cursor ||
      end > text.length
    )
      continue;
    segments.push(...plain(text.slice(cursor, link.offset)));
    segments.push({
      text: text.slice(link.offset, end),
      url: safeURL(link.url),
    });
    cursor = end;
  }
  segments.push(...plain(text.slice(cursor)));
  return segments;
}
