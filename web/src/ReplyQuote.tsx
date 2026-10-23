import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  FileText,
  Film,
  Image,
  ListChecks,
  MapPin,
  Mic,
  Music,
  Paperclip,
  Quote,
  Sticker,
  Trash2,
  UserRound,
  Video,
} from "lucide-react";
import { chatApi, type NativeMessage } from "./conversation-api";
import { participantLabel } from "../../shared/participantLabel";
import {
  isReply,
  replyQuoteText,
  replyTarget,
  type QuoteReply,
} from "../../shared/replyQuote";
import "./reply-quotes.css";

// Reply Quotes (ADR 41): the replied-to message's sender and one line of it,
// above the reply's own content. The message cache answers first; in private
// chats the service reads what it lacks once, in one bounded request.

type Details = Pick<
  NativeMessage,
  "reply_to" | "reply_quote" | "reply_elsewhere" | "reply_story" | "reply_read"
>;
type Resolved = {
  replies: ({ message_id: number } & Details)[];
  targets: NativeMessage[];
  missing: number[];
  pending: number[];
};

// Replies whose replied-to message is not loaded, and in private chats the
// replies captured before their header details existed.
function wanted(
  messages: NativeMessage[],
  group: boolean,
  known: Map<number, NativeMessage>,
  settled: Set<number>,
) {
  const loaded = new Set(messages.map((m) => m.message_id));
  return messages
    .filter((m) => {
      if (!isReply(m) || settled.has(m.message_id)) return false;
      const target = replyTarget(m);
      if (!group && m.reply_to && !m.reply_read) return true;
      return target !== null && !loaded.has(target) && !known.has(target);
    })
    .map((m) => m.message_id);
}

export function useReplyResolution(
  account: number,
  peer: string,
  group: boolean,
  messages: NativeMessage[],
) {
  const [targets, setTargets] = useState<Map<number, NativeMessage>>(
    () => new Map(),
  );
  const [details, setDetails] = useState<Record<number, Details>>({});
  // Replied-to messages the service could not find, or a group lacks.
  const [missing, setMissing] = useState<Set<number>>(() => new Set());
  // Replies answered for this open chat; unresolved ones return after a minute.
  const settled = useRef(new Set<number>());
  const [round, setRound] = useState(0);
  useEffect(() => {
    settled.current = new Set();
    setTargets(new Map());
    setDetails({});
    setMissing(new Set());
  }, [account, peer]);
  const filled = messages.map((m) =>
    !m.reply_read && details[m.message_id]
      ? { ...m, ...details[m.message_id] }
      : m,
  );
  const needed = wanted(filled, group, targets, settled.current).join(",");
  useEffect(() => {
    if (!needed) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const identities = needed.split(",").map(Number);
    const local = new Map(filled.map((m) => [m.message_id, m]));
    void (async () => {
      const later: number[] = [];
      for (let i = 0; active && i < identities.length; i += 100) {
        const batch = identities.slice(i, i + 100);
        try {
          const value = await chatApi<Resolved>(
            account,
            peer,
            "/originals/replies",
            { identities: batch },
          );
          if (!active) return;
          const found = new Set(value.targets.map((t) => t.message_id));
          const waiting = new Set(value.pending);
          const answered = new Map(
            value.replies.map((reply) => [reply.message_id, reply]),
          );
          const absent: number[] = [];
          for (const identity of batch) {
            const reply = answered.get(identity) || local.get(identity);
            const target = reply ? replyTarget(reply as QuoteReply) : null;
            const legacy = !group && !!reply?.reply_to && !reply.reply_read;
            if ((target !== null && waiting.has(target)) || legacy)
              later.push(identity);
            else if (target !== null && !found.has(target)) absent.push(target);
            settled.current.add(identity);
          }
          setTargets((prior) => {
            const next = new Map(prior);
            for (const target of value.targets)
              next.set(target.message_id, target);
            return next;
          });
          setDetails((prior) => ({
            ...prior,
            ...Object.fromEntries(
              value.replies
                .filter((reply) => reply.reply_read)
                .map(({ message_id, ...rest }) => [message_id, rest]),
            ),
          }));
          setMissing((prior) => new Set([...prior, ...absent]));
        } catch {
          if (!active) return;
          later.push(...batch);
          for (const identity of batch) settled.current.add(identity);
        }
      }
      if (!active) return;
      if (later.length)
        timer = setTimeout(() => {
          for (const identity of later) settled.current.delete(identity);
          setRound((value) => value + 1);
        }, 60_000);
      // Settled replies leave the wanted list without another request.
      setRound((value) => value + 1);
    })();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [account, peer, needed, round]);
  const loaded = new Map(filled.map((m) => [m.message_id, m]));
  return {
    messages: filled,
    target: (identity: number) => loaded.get(identity) || targets.get(identity),
    // Still being looked up: neither loaded, found nor known to be missing.
    pending: (identity: number) =>
      !loaded.has(identity) && !targets.has(identity) && !missing.has(identity),
  };
}

type Context = {
  account: number;
  peer: string;
  name: string;
  group: boolean;
  timezone: string;
  target: (identity: number) => NativeMessage | undefined;
  pending: (identity: number) => boolean;
  jump: (identity: number) => void;
};

const ReplyContext = createContext<Context | null>(null);

export function ReplyQuotes({
  value,
  children,
}: {
  value: Context;
  children: React.ReactNode;
}) {
  return (
    <ReplyContext.Provider value={value}>{children}</ReplyContext.Provider>
  );
}

const ICONS: Record<string, typeof Paperclip> = {
  image: Image,
  audio: Mic,
  video_note: Video,
  video: Film,
  gif: Film,
  sticker: Sticker,
  document: FileText,
  poll: ListChecks,
  location: MapPin,
  contact: UserRound,
};

// The replied-to message for the composer and sends, which are not loaded
// replies: loaded or resolved first, otherwise one read by number.
function useTarget(identity: number | null) {
  const context = useContext(ReplyContext);
  const known = identity !== null ? context?.target(identity) : undefined;
  const [fetched, setFetched] = useState<
    { identity: number; message: NativeMessage | null } | undefined
  >();
  useEffect(() => {
    if (identity === null || known || !context) return;
    if (fetched?.identity === identity) return;
    let active = true;
    void chatApi<NativeMessage | null>(
      context.account,
      context.peer,
      `/originals/${identity}`,
    )
      .then((message) => active && setFetched({ identity, message }))
      .catch(() => active && setFetched({ identity, message: null }));
    return () => {
      active = false;
    };
  }, [identity, !!known, context?.account, context?.peer]);
  if (identity === null) return { message: null, pending: false };
  if (known) return { message: known, pending: false };
  if (fetched?.identity === identity)
    return { message: fetched.message, pending: false };
  return { message: null, pending: true };
}

export function ReplyQuote({
  reply,
  variant = "bubble",
  interactive = true,
}: {
  reply: QuoteReply;
  // A sent message's quote, or the composer's and a pending send's target.
  variant?: "bubble" | "target";
  interactive?: boolean;
}) {
  const context = useContext(ReplyContext);
  const identity = replyTarget(reply);
  const loaded =
    variant === "bubble" && identity !== null
      ? context?.target(identity)
      : undefined;
  const lookup = useTarget(variant === "target" ? identity : null);
  if (!context) return null;
  const target = variant === "target" ? lookup.message : loaded || null;
  const quote = replyQuoteText(reply, target, {
    timezone: context.timezone,
    own: `user:${context.account}`,
    sender: (message) =>
      participantLabel(
        message as NativeMessage,
        context.name,
        context.account,
        context.group,
      ),
    pending:
      variant === "target"
        ? lookup.pending
        : identity !== null && context.pending(identity),
  });
  const heading =
    variant === "target"
      ? quote.sender
        ? `Reply to ${quote.sender}`
        : quote.kind === "story"
          ? null
          : "Reply"
      : quote.sender;
  const Icon = quote.media ? ICONS[quote.media] || Paperclip : null;
  const said =
    quote.kind === "pending"
      ? "loading"
      : quote.deleted
        ? `deleted message, ${quote.body}`
        : quote.body;
  const label = !heading
    ? said
    : `${variant === "bubble" ? "Reply to " : ""}${heading}${
        quote.date ? ` · ${quote.date}` : ""
      }: ${said}`;
  const content = (
    <>
      {heading && (
        <span className="reply-quote-sender">
          {heading}
          {quote.date && (
            <span className="reply-quote-date"> · {quote.date}</span>
          )}
        </span>
      )}
      <span className="reply-quote-body" data-kind={quote.kind}>
        {quote.quoted && <Quote size={11} aria-hidden="true" />}
        {/* What a deleted message last said (ADR 52 Q10). */}
        {quote.deleted && <Trash2 size={12} aria-hidden="true" />}
        {Icon && <Icon size={12} aria-hidden="true" />}
        <span>{quote.body}</span>
      </span>
    </>
  );
  // Only a message in this chat can be jumped to (ADR 41 Q5).
  const jumpable = interactive && variant === "bubble" && quote.target !== null;
  return jumpable ? (
    <button
      type="button"
      className="reply-quote-box"
      aria-label={label}
      onClick={() => context.jump(quote.target!)}
    >
      {content}
    </button>
  ) : (
    <div className="reply-quote-box" role="note" aria-label={label}>
      {content}
    </div>
  );
}

// The highlighted element for a jump: the exact original, or the bubble that
// merges it in AI reading. Folded or hidden group sources have no such element.
export function jumpTarget(root: ParentNode | null, identity: number) {
  if (!root) return null;
  return (
    root.querySelector<HTMLElement>(
      `section.native-source[data-source-id="${identity}"]`,
    ) ||
    root.querySelector<HTMLElement>(
      `article.message-bubble[data-source-ids~="${identity}"]`,
    )
  );
}

export function highlightJump(
  stream: HTMLElement | null,
  element: HTMLElement,
) {
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (stream) {
    // Centred in the conversation, clear of a group's pinned date band.
    const box = element.getBoundingClientRect();
    const frame = stream.getBoundingClientRect();
    stream.scrollTo({
      top:
        stream.scrollTop +
        box.top -
        frame.top -
        Math.max(48, (frame.height - box.height) / 2),
      behavior: still ? "auto" : "smooth",
    });
  }
  element.classList.remove("reply-jump-highlight");
  // Restart the highlight when the same message is jumped to again.
  void element.offsetWidth;
  element.classList.add("reply-jump-highlight");
  setTimeout(() => element.classList.remove("reply-jump-highlight"), 1600);
}

export { isReply };
