// The Reply Quote's wording (ADR 41), shared by the web and the Enloq app:
// who wrote the replied-to message, its date when it was sent on an earlier
// day, and one line of its original wording or media label.
import { formatDuration, messageDate } from "./messageTime";

export type QuoteReply = {
  message_id: number;
  date: number;
  // The reply header's number; it names a message in this chat only while
  // the reply is neither to another chat's message nor to a story.
  reply_to?: number | null;
  reply_quote?: string | null;
  reply_elsewhere?: {
    peer?: string | null;
    sender_peer?: string | null;
    sender?: string | null;
    date?: number | null;
  } | null;
  reply_story?: boolean;
};

export type QuoteTarget = {
  message_id: number;
  date: number;
  text: string;
  deleted: boolean;
  media?: string | null;
  media_info?: {
    duration?: number | null;
    voice?: boolean | null;
    file_name?: string | null;
    title?: string | null;
    question?: string | null;
  } | null;
};

export type ReplyQuoteText = {
  // "pending" while the replied-to message is still being looked up.
  kind: "message" | "elsewhere" | "story" | "unknown" | "pending";
  sender: string | null;
  date: string | null;
  body: string;
  // The media kind for the body's icon; null for text.
  media: string | null;
  // The body is the reply's own quoted fragment.
  quoted: boolean;
  // The message in this chat a tap jumps to.
  target: number | null;
  // The replied-to message was deleted; the body is what it last said,
  // marked with a trash icon (ADR 52 Q10).
  deleted?: boolean;
};

export function replyTarget(reply: QuoteReply) {
  if (reply.reply_elsewhere || reply.reply_story) return null;
  return reply.reply_to ?? null;
}

export function isReply(reply: QuoteReply) {
  return !!(reply.reply_to || reply.reply_elsewhere || reply.reply_story);
}

function day(value: number, timezone: string) {
  return new Date(value * 1000).toLocaleDateString("en-CA", {
    timeZone: timezone,
  });
}

// The date in the application calendar; the year only outside the current one.
export function quoteDate(value: number, timezone: string) {
  const year = (stamp: number) =>
    new Date(stamp * 1000).toLocaleDateString("en-CA", {
      timeZone: timezone,
      year: "numeric",
    });
  if (year(value) === year(Date.now() / 1000))
    return messageDate(value, timezone);
  return new Date(value * 1000).toLocaleDateString([], {
    timeZone: timezone,
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function earlierDay(target: number, reply: number, timezone: string) {
  return day(target, timezone) < day(reply, timezone)
    ? quoteDate(target, timezone)
    : null;
}

const MEDIA: Record<string, string> = {
  image: "Image",
  video: "Video",
  gif: "GIF",
  sticker: "Sticker",
  location: "Location",
  contact: "Contact",
};

// A media label for the quote's body; null for text and link previews.
export function quoteMediaLabel(target: QuoteTarget) {
  const info = target.media_info;
  const length = info?.duration != null ? formatDuration(info.duration) : "";
  switch (target.media) {
    case null:
    case undefined:
    case "link":
      return null;
    case "audio":
      return info?.voice === false
        ? info.title || info.file_name || "Audio"
        : length
          ? `Voice message · ${length}`
          : "Voice message";
    case "video_note":
      return length ? `Video message · ${length}` : "Video message";
    case "document":
      return info?.file_name || "File";
    case "poll":
      return info?.question ? `Poll · ${info.question}` : "Poll";
    default:
      return MEDIA[target.media] || "Message";
  }
}

function line(text: string) {
  return text.replace(/\s+/g, " ").trim();
}

// The quote for one reply. ``target`` is its replied-to message when known;
// ``pending`` while a lookup may still find it. ``sender`` names a message's
// author with the client's own header rule; ``own`` recognizes the account's
// peer in another chat's header.
export function replyQuoteText(
  reply: QuoteReply,
  target: QuoteTarget | null,
  {
    timezone,
    sender,
    own,
    pending = false,
  }: {
    timezone: string;
    sender: (target: QuoteTarget) => string;
    own: string;
    pending?: boolean;
  },
): ReplyQuoteText {
  const quote = reply.reply_quote ? line(reply.reply_quote) : "";
  if (reply.reply_story)
    return {
      kind: "story",
      sender: null,
      date: null,
      body: "Reply to a story",
      media: null,
      quoted: false,
      target: null,
    };
  const elsewhere = reply.reply_elsewhere;
  if (elsewhere) {
    const name =
      elsewhere.sender_peer && elsewhere.sender_peer === own
        ? "You"
        : elsewhere.sender || null;
    return {
      kind: "elsewhere",
      sender: name ? `${name} · in another chat` : "In another chat",
      date:
        elsewhere.date != null
          ? earlierDay(elsewhere.date, reply.date, timezone)
          : null,
      body: quote || "Message",
      media: null,
      quoted: !!quote,
      target: null,
    };
  }
  if (!target)
    return {
      kind: pending ? "pending" : "unknown",
      sender: null,
      date: null,
      // The reply's own fragment still shows what it answers.
      body: quote || (pending ? "…" : "Reply to an earlier message"),
      media: null,
      quoted: !!quote,
      target: null,
    };
  // A deleted target carries the content it last showed (ADR 52 Q10).
  const label = quoteMediaLabel(target);
  const text = line(target.text);
  return {
    kind: "message",
    sender: sender(target),
    date: earlierDay(target.date, reply.date, timezone),
    body: quote
      ? quote
      : label && text
        ? `${label} · ${text}`
        : label || text || "Message",
    media: quote ? null : label ? target.media || null : null,
    quoted: !!quote,
    target: target.message_id,
    deleted: target.deleted,
  };
}

// A merged AI bubble's quotes (ADR 41 Q11): one per replied-to message outside
// the bubble, in the order its replies appear. Replies to the same message
// share the first reply's quote; a reply inside the bubble gets none.
export function outsideReplies<T extends QuoteReply>(messages: T[]) {
  const inside = new Set(messages.map((m) => m.message_id));
  const seen = new Set<string>();
  const result: T[] = [];
  for (const message of messages) {
    if (!isReply(message)) continue;
    const target = replyTarget(message);
    if (target !== null && inside.has(target)) continue;
    const key = message.reply_story
      ? "story"
      : message.reply_elsewhere
        ? `elsewhere:${message.reply_elsewhere.peer}/${message.reply_to}`
        : `message:${target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(message);
  }
  return result;
}
