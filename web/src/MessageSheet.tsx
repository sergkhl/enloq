import React, { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Bot,
  CheckCircle2,
  Copy,
  CornerUpLeft,
  Download,
  Eye,
  EyeOff,
  FileText,
  Plus,
  RotateCw,
} from "lucide-react";
import type { MenuTarget } from "./Bubble";
import type { NativeMessage } from "./conversation-api";
import {
  ownReactions,
  quickReactions,
  type ReactionOptions,
} from "../../shared/reactions";

// A source in the "which message" list: its text, media or plainly Message.
function excerpt(message: NativeMessage) {
  const text = message.text.trim();
  if (text) return text.length > 60 ? `${text.slice(0, 59)}…` : text;
  return message.media ? "Media" : "Message";
}

// A message's sheet, as in the app (ADR 26, ADR 44 MW2): in a private chat
// a reaction row (ADR 46 CU2), Reply and Copy, Copy transcript and Download
// for the row's media (CU1: the app shares, the web downloads), and in a
// group Stop ignoring this bot on a bot's original (ADR 42 Q3).
export function MessageSheet({
  target,
  replyAllowed,
  palette = null,
  onReply,
  onCopy,
  onCopyTranscript,
  onDownload,
  onReact,
  onStopIgnoring,
  onClose,
}: {
  target: MenuTarget;
  replyAllowed: boolean;
  palette?: ReactionOptions | "unavailable" | null;
  onReply: (identity: number) => void;
  onCopy: (text: string) => void;
  onCopyTranscript: (message: NativeMessage) => void;
  onDownload: (message: NativeMessage) => void;
  onReact?: (message: NativeMessage, emoji: string) => void;
  onStopIgnoring?: (author: string) => void;
  onClose: () => void;
}) {
  const first = useRef<HTMLButtonElement>(null);
  const [more, setMore] = useState(false);
  // An emoji waiting for its message, when the row has several.
  const [choosing, setChoosing] = useState<string | null>(null);
  const sources = target.react ?? [];
  const reacting = !!onReact && sources.length > 0;
  const own = sources.length === 1 ? ownReactions(sources[0]!.reactions) : [];
  const emoji =
    palette && palette !== "unavailable"
      ? quickReactions(palette.emoji, own, more)
      : own;
  function react(choice: string) {
    if (sources.length > 1) return setChoosing(choice);
    onReact?.(sources[0]!, choice);
    onClose();
  }
  useEffect(() => {
    first.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  const items = [
    {
      label: "Reply",
      icon: <CornerUpLeft size={20} aria-hidden />,
      disabled: !replyAllowed || target.reply === null,
      run: () => target.reply !== null && onReply(target.reply),
    },
    {
      label: "Copy",
      icon: <Copy size={20} aria-hidden />,
      disabled: !target.copy,
      run: () => onCopy(target.copy),
    },
    ...(target.select
      ? [
          {
            label: "Select",
            icon: <CheckCircle2 size={20} aria-hidden />,
            disabled: false,
            run: target.select,
          },
        ]
      : []),
    ...(target.toggleView
      ? [
          {
            label: target.toggleView.label,
            // The reading view's robot (ADR 52 Q15), never sparkles.
            icon: <Bot size={20} aria-hidden />,
            disabled: false,
            run: target.toggleView.run,
          },
        ]
      : []),
    ...(target.hide
      ? [
          {
            label: "Hide",
            icon: <EyeOff size={20} aria-hidden />,
            disabled: false,
            run: target.hide,
          },
        ]
      : []),
    ...(target.retry
      ? [
          {
            label: "Retry",
            icon: <RotateCw size={20} aria-hidden />,
            disabled: false,
            run: target.retry,
          },
        ]
      : []),
    ...(target.transcripts ?? []).map((item) => ({
      label: item.label,
      icon: <FileText size={20} aria-hidden />,
      disabled: false,
      run: () => onCopyTranscript(item.message),
    })),
    ...(target.downloads ?? []).map((item) => ({
      label: item.label,
      icon: <Download size={20} aria-hidden />,
      disabled: false,
      run: () => onDownload(item.message),
    })),
    ...(target.bot && onStopIgnoring
      ? [
          {
            label: "Stop ignoring this bot",
            icon: <Eye size={20} aria-hidden />,
            disabled: false,
            run: () => target.bot && onStopIgnoring(target.bot),
          },
        ]
      : []),
  ];
  if (choosing)
    return (
      <div className="sheet-scrim" onClick={onClose}>
        <div
          className="sheet-bottom"
          role="menu"
          aria-label="Message actions"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="sheet-heading">
            <button
              type="button"
              role="menuitem"
              aria-label="Back to message actions"
              onClick={() => setChoosing(null)}
            >
              <ArrowLeft size={20} aria-hidden />
            </button>
            <span>{`React ${choosing} to which message?`}</span>
          </div>
          {sources.map((source) => {
            const mine = ownReactions(source.reactions).includes(choosing);
            return (
              <button
                key={source.message_id}
                type="button"
                role="menuitem"
                className="sheet-item sheet-source"
                aria-label={`${mine ? "Remove" : "React"} ${choosing}: ${excerpt(source)}`}
                onClick={() => {
                  onReact?.(source, choosing);
                  onClose();
                }}
              >
                <span>{excerpt(source)}</span>
                {mine && <small>{`Remove your ${choosing}`}</small>}
              </button>
            );
          })}
        </div>
      </div>
    );
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div
        className="sheet-bottom"
        role="menu"
        aria-label="Message actions"
        onClick={(event) => event.stopPropagation()}
      >
        {reacting && (
          <div className="sheet-reactions" role="group" aria-label="React">
            {palette === "unavailable" ? (
              <small>Reactions are unavailable right now.</small>
            ) : (
              emoji.map((choice) => {
                const chosen = own.includes(choice);
                return (
                  <button
                    key={choice}
                    type="button"
                    role="menuitem"
                    aria-label={`${chosen ? "Remove reaction" : "React"} ${choice}`}
                    aria-pressed={chosen}
                    onClick={() => react(choice)}
                  >
                    {choice}
                  </button>
                );
              })
            )}
            {palette &&
              palette !== "unavailable" &&
              !more &&
              palette.emoji.length > 6 && (
                <button
                  type="button"
                  role="menuitem"
                  aria-label="More reactions"
                  onClick={() => setMore(true)}
                >
                  <Plus size={22} aria-hidden />
                </button>
              )}
          </div>
        )}
        {items.map((item, index) => (
          <button
            key={item.label}
            ref={index === 0 ? first : undefined}
            type="button"
            role="menuitem"
            className="sheet-item"
            disabled={item.disabled}
            onClick={() => {
              item.run();
              onClose();
            }}
          >
            {item.icon}
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}
