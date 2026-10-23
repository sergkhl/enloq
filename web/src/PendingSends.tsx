import React, { useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCheck,
  Clock,
  Copy,
  Play,
  RotateCw,
  Search,
  X,
} from "lucide-react";
import { runClasses, useMenuGesture } from "./Bubble";
import type { RunPlace } from "../../shared/runs";
import { workspaceApi } from "./accounts";
import { type Operation } from "./conversation-api";
import type { components } from "./api-schema";
import { messageTime } from "../../shared/messageTime";
import { ReplyQuote } from "./ReplyQuote";
import { SendResumeReview } from "./SendResumeReview";

// Native delivery marks: one check once Telegram accepted it into its cloud,
// two once Telegram reports the recipient read it. No delivered-to-device state.
export function DeliveryMark({ read }: { read: boolean }) {
  const label = read ? "Read" : "Sent";
  return (
    <span className="delivery-mark" role="img" aria-label={label} title={label}>
      {read ? <CheckCheck size={14} /> : <Check size={14} />}
    </span>
  );
}

function when(seconds: number) {
  const at = new Date(Math.ceil(seconds / 60) * 60000);
  const time = at.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  return at.toDateString() === new Date().toDateString()
    ? time
    : `${at.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

// A saved submission that has no native message yet, or whose native message is
// still loading. Presentation only: it is never evidence of a Telegram message.
// It looks like a sent message, its state an icon in the time line (ADR 51
// Q3); a long press, or a tap on the "!" of a send that needs the owner,
// opens a sheet with exactly the actions the service allows (Q4).
export function PendingSend({
  account,
  peer,
  accountName = `Account ${account}`,
  operation,
  timezone,
  onChange,
  run = { first: true, last: true },
}: {
  account: number;
  peer: string;
  accountName?: string;
  // Its place in its Message Run (ADR 51 Q13).
  run?: RunPlace;
  operation: Operation & {
    media?: components["schemas"]["SelectedMediaView"] | null;
  };
  timezone: string;
  onChange: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [sheet, setSheet] = useState(false);
  // Each request opens the paused send's review (ADR 46 CU6).
  const [review, setReview] = useState(0);
  const local = operation.id === "local";
  const text = operation.media?.kind === "sticker" ? "" : operation.text;
  async function act(action: "retry" | "cancel" | "recheck") {
    if (busy || local) return;
    setBusy(true);
    setNote("");
    try {
      const result = await workspaceApi<Operation>(
        `accounts/${account}/operations/${operation.id}/${action}`,
        {},
      );
      if (result.check) setNote(result.check);
      await onChange();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setNote("Copied");
    } catch {
      setNote("Copy unavailable in this browser");
    }
  }
  const sending = ["queued", "prepared", "dispatching"].includes(
    operation.state,
  );
  const held =
    operation.state === "queued" && operation.cause === "reply_deletion";
  // A send proven unsent after group access was lost is Not sent until it
  // is reviewed and resumed (ADR 34, ADR 46 CU6).
  const status = held
    ? "Send held"
    : operation.resumable
      ? "Not sent"
      : sending
        ? "Sending"
        : operation.state === "confirmed"
          ? "Sent"
          : operation.state === "refused"
            ? "Not sent"
            : operation.state === "unknown"
              ? "Delivery unknown"
              : "Cancelled";
  const needs =
    !!operation.resumable ||
    operation.state === "refused" ||
    operation.state === "unknown";
  const actions = local
    ? []
    : [
        ...(operation.state === "refused" && !operation.resumable
          ? [
              {
                label: "Retry",
                icon: <RotateCw size={20} aria-hidden />,
                disabled: busy || !operation.retryable,
                run: () => void act("retry"),
              },
            ]
          : []),
        ...(operation.resumable
          ? [
              {
                label: "Review and resume",
                icon: <Play size={20} aria-hidden />,
                disabled: busy,
                run: () => setReview((count) => count + 1),
              },
            ]
          : []),
        ...(operation.checkable
          ? [
              {
                label: "Check delivery",
                icon: <Search size={20} aria-hidden />,
                disabled: busy,
                run: () => void act("recheck"),
              },
            ]
          : []),
        ...(operation.cancellable
          ? [
              {
                label: "Cancel send",
                icon: <X size={20} aria-hidden />,
                disabled: busy,
                run: () => void act("cancel"),
              },
            ]
          : []),
        ...(!sending && operation.state !== "confirmed" && text
          ? [
              {
                label: "Copy",
                icon: <Copy size={20} aria-hidden />,
                disabled: false,
                run: () => void copy(),
              },
            ]
          : []),
      ];
  const reason =
    (operation.state === "refused" || held) && operation.reason
      ? `${operation.reason}${
          operation.retry_after && !operation.retryable
            ? ` Retry after ${when(operation.retry_after)}.`
            : ""
        }`
      : "";
  const gesture = useMenuGesture(() => {
    if (actions.length || reason) setSheet(true);
  });
  return (
    <>
      <article
        className={`message-bubble app-bubble outgoing pending-send state-${operation.state}${runClasses(run)}`}
        data-operation-id={local ? undefined : operation.id}
        aria-label={`${status} message`}
        tabIndex={local ? undefined : 0}
        {...(local ? {} : gesture)}
      >
        {operation.reply_to && (
          <ReplyQuote
            reply={{
              message_id: 0,
              date: operation.created,
              reply_to: operation.reply_to,
            }}
            variant="target"
          />
        )}
        {!!operation.images.length && (
          <div className="pending-images">
            {operation.images.map((id, index) => (
              <img
                key={id}
                src={`/api/accounts/${account}/chats/${encodeURIComponent(peer)}/images/${id}`}
                alt={`Submitted image ${index + 1}`}
              />
            ))}
          </div>
        )}
        {operation.media && (
          <small className="pending-media">
            {operation.media.kind === "sticker" ? "Sticker" : "GIF"}
          </small>
        )}
        {text && <p>{text}</p>}
        <span className="bubble-meta">
          <time dateTime={new Date(operation.created * 1000).toISOString()}>
            {messageTime(operation.created, timezone)}
          </time>
          {needs ? (
            <button
              type="button"
              className="pending-alert"
              aria-label={`${status}: message actions`}
              onClick={() => setSheet(true)}
            >
              <AlertCircle size={14} aria-hidden />
              {status}
            </button>
          ) : sending ? (
            <span className="pending-status" role="img" aria-label={status}>
              <Clock size={14} aria-hidden />
              {held && "Held"}
            </span>
          ) : operation.state === "confirmed" ? (
            <Check size={14} aria-label="Sent" />
          ) : (
            <span className="pending-status">{status}</span>
          )}
        </span>
        {note && (
          <small className="pending-note" role="alert">
            {note}
          </small>
        )}
      </article>
      {operation.resumable && (
        <SendResumeReview
          account={account}
          accountName={accountName}
          peer={peer}
          operation={operation}
          onChange={onChange}
          request={review}
        />
      )}
      {sheet && (
        <div className="sheet-scrim" onClick={() => setSheet(false)}>
          <div
            className="sheet-bottom"
            role="menu"
            aria-label="Message actions"
            onClick={(event) => event.stopPropagation()}
          >
            <p className="sheet-note">{reason || status}</p>
            {actions.map((item) => (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                className="sheet-item"
                disabled={item.disabled}
                onClick={() => {
                  item.run();
                  setSheet(false);
                }}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
