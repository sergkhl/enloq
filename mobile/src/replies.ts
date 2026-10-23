import { useEffect, useMemo, useRef, useState } from "react";
import { participantLabel } from "../../shared/participantLabel";
import {
  isReply,
  replyQuoteText,
  replyTarget,
  type QuoteTarget,
  type ReplyQuoteText,
} from "../../shared/replyQuote";
import { readCached, repliesKey, writeCached } from "./cache";
import { useConnection } from "./connection";
import type { NativeMessage } from "./conversation";

// A reply's own header details, as the service learns them (ADR 41).
export type ReplyDetails = Pick<
  NativeMessage,
  | "message_id"
  | "reply_to"
  | "reply_quote"
  | "reply_elsewhere"
  | "reply_story"
  | "reply_read"
>;

// `POST …/originals/replies`: the replied-to messages the service holds, or
// read from Telegram in a private chat; `pending` may still be found later.
export type RepliesAnswer = {
  replies: ReplyDetails[];
  targets: NativeMessage[];
  missing: number[];
  pending: number[];
};

// What the phone keeps with its offline copy of the chat (ADR 38 Q20).
type Kept = {
  targets: NativeMessage[];
  replies: ReplyDetails[];
  missing: number[];
};

export type Replies = {
  // A message with any details the service learned filled in.
  message: (message: NativeMessage) => NativeMessage;
  // The quote for one reply; `title` names the chat's correspondent.
  quote: (reply: NativeMessage) => ReplyQuoteText;
  // The quote of a message by number, for a draft or a send sent at `date`.
  quoteOf: (identity: number, date: number) => ReplyQuoteText;
  // The replied-to message by number, loaded or looked up.
  target: (identity: number) => NativeMessage | null;
};

const BATCH = 100;
const RETRY = 60_000;
// The service's 30-day rule: an older message is read again, never kept.
const KEEP = 30 * 86_400;

const recent = (message: NativeMessage) =>
  message.date >= Date.now() / 1000 - KEEP;

// Only fills in: a reply captured with its details keeps its own (ADR 41).
export function withDetails(
  message: NativeMessage,
  details: Map<number, ReplyDetails>,
) {
  const learned = details.get(message.message_id);
  if (!learned || message.reply_read || learned.reply_to !== message.reply_to)
    return message;
  return { ...message, ...learned };
}

// The replies whose replied-to message the loaded messages lack, and, in a
// private chat, replies captured before their header details existed.
export function lookups(
  messages: NativeMessage[],
  {
    group,
    targets,
    missing,
    answered,
  }: {
    group: boolean;
    targets: Map<number, NativeMessage>;
    missing: Set<number>;
    answered: Set<number>;
  },
) {
  const loaded = new Set(messages.map((message) => message.message_id));
  return messages
    .filter((message) => {
      if (message.deleted || message.service || !isReply(message)) return false;
      const target = replyTarget(message);
      if (
        target !== null &&
        !loaded.has(target) &&
        !targets.has(target) &&
        !missing.has(target)
      )
        return true;
      return (
        !group &&
        !!message.reply_to &&
        !message.reply_read &&
        !answered.has(message.message_id)
      );
    })
    .map((message) => message.message_id);
}

// The replied-to messages of a chat's loaded replies (ADR 41): the service's
// cache first, Telegram only for a private chat, a minute between retries
// while the chat stays open. A failed lookup leaves the quote unknown and
// never stops reading.
export function useReplies(
  account: number,
  peer: string,
  messages: NativeMessage[],
  {
    title,
    group,
    timezone,
  }: { title: string; group: boolean; timezone: string },
): Replies {
  const { api } = useConnection();
  const keep = repliesKey(account, peer);
  const [targets, setTargets] = useState(new Map<number, NativeMessage>());
  const [details, setDetails] = useState(new Map<number, ReplyDetails>());
  const [missing, setMissing] = useState(new Set<number>());
  const [pending, setPending] = useState(new Set<number>());
  const answered = useRef(new Set<number>());
  const restored = useRef(false);

  useEffect(() => {
    let live = true;
    void readCached<Kept>(keep).then((kept) => {
      restored.current = true;
      if (!live || !kept) return;
      setTargets(
        (shown) =>
          new Map([
            ...kept.targets
              .filter(recent)
              .map((t) => [t.message_id, t] as const),
            ...shown,
          ]),
      );
      setDetails(
        (shown) =>
          new Map([
            ...kept.replies.map((r) => [r.message_id, r] as const),
            ...shown,
          ]),
      );
      setMissing((shown) => new Set([...kept.missing, ...shown]));
    });
    return () => {
      live = false;
    };
  }, [keep]);

  const shown = useMemo(
    () => messages.map((message) => withDetails(message, details)),
    [messages, details],
  );
  const latest = useRef(new Map<number, NativeMessage>());
  latest.current = new Map(
    shown.map((message) => [message.message_id, message]),
  );
  const wanted = lookups(shown, {
    group,
    targets,
    missing,
    answered: answered.current,
  }).join(",");

  useEffect(() => {
    if (!wanted) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const path = `accounts/${account}/chats/${encodeURIComponent(peer)}/originals/replies`;
    async function ask(identities: number[]) {
      const retry: number[] = [];
      // Only a first lookup shows as pending; a retry keeps the fallback.
      const first = identities.filter((i) => !answered.current.has(i));
      setPending((kept) => new Set([...kept, ...first]));
      for (let i = 0; live && i < identities.length; i += BATCH) {
        const batch = identities.slice(i, i + BATCH);
        try {
          const answer = await api<RepliesAnswer>(path, {
            body: { identities: batch },
          });
          if (!live) return;
          setDetails(
            (kept) =>
              new Map([
                ...kept,
                ...answer.replies.map((r) => [r.message_id, r] as const),
              ]),
          );
          setTargets(
            (kept) =>
              new Map([
                ...kept,
                ...answer.targets.map((t) => [t.message_id, t] as const),
              ]),
          );
          setMissing((kept) => new Set([...kept, ...answer.missing]));
          // A reply whose message may still be found is asked again later.
          const later = new Set(answer.pending);
          retry.push(
            ...batch.filter((identity) => {
              const reply = latest.current.get(identity);
              const target = reply ? replyTarget(reply) : null;
              return target !== null && later.has(target);
            }),
          );
        } catch {
          retry.push(...batch);
        }
      }
      if (!live) return;
      for (const identity of identities) answered.current.add(identity);
      setPending((kept) => {
        const next = new Set(kept);
        for (const identity of identities) next.delete(identity);
        return next;
      });
      if (retry.length) timer = setTimeout(() => void ask(retry), RETRY);
    }
    const identities = wanted.split(",").map(Number);
    void ask(identities);
    return () => {
      live = false;
      clearTimeout(timer);
      // An interrupted first lookup is asked again or falls back.
      setPending((kept) => {
        const next = new Set(kept);
        for (const identity of identities) next.delete(identity);
        return next;
      });
    };
  }, [account, api, peer, wanted]);

  // Resolved quotes read offline too (Q20); the copy goes with the chat's own.
  useEffect(() => {
    if (!restored.current || (!targets.size && !details.size)) return;
    writeCached(keep, {
      targets: [...targets.values()].filter(recent),
      replies: [...details.values()],
      missing: [...missing],
    } satisfies Kept);
  }, [keep, targets, details, missing]);

  const target = (identity: number) =>
    latest.current.get(identity) ?? targets.get(identity) ?? null;
  // In a group the quote names the author line's participant (ADR 41 Q12).
  const options = {
    timezone,
    own: `user:${account}`,
    sender: (found: QuoteTarget) =>
      participantLabel(found as NativeMessage, title, account, group),
  };
  return {
    message: (message) => withDetails(message, details),
    target,
    quote: (reply) => {
      const filled = withDetails(reply, details);
      const replied = replyTarget(filled);
      return replyQuoteText(filled, replied === null ? null : target(replied), {
        ...options,
        pending: pending.has(reply.message_id),
      });
    },
    quoteOf: (identity, date) =>
      replyQuoteText(
        { message_id: 0, date, reply_to: identity },
        target(identity),
        options,
      ),
  };
}
