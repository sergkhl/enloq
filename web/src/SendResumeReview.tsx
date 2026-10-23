import React, { useEffect, useRef, useState } from "react";
import { Play } from "lucide-react";
import { workspaceApi } from "./accounts";
import type { Operation } from "./conversation-api";
import { ReplyQuote } from "./ReplyQuote";
import {
  resumeWithin,
  reviewFits,
  type ResumeReview,
} from "../../shared/retained";
import "./retained.css";

type Review = ResumeReview<Operation>;

// Review and resume for a group send proven unsent after access was lost
// (ADR 34, ADR 46 CU6). Review sends nothing: once the service has checked
// the rights again, its short token binds exactly the reviewed send, and
// Resume sends it within that token's time.
export function SendResumeReview({
  account,
  accountName,
  peer,
  operation,
  onChange,
  request,
}: {
  account: number;
  accountName: string;
  peer: string;
  operation: Operation;
  onChange: () => Promise<void> | void;
  // Opened from the pending send's sheet (ADR 51 Q4): each new count opens
  // the review, and no button shows in the bubble.
  request?: number;
}) {
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const alive = useRef(true);
  const sequence = useRef(0);
  const revision = JSON.stringify([
    operation.id,
    operation.state,
    operation.cause,
    operation.text,
    operation.images,
    operation.media,
    operation.reply_to,
    operation.resumable,
  ]);
  const current = useRef(revision);
  current.current = revision;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      ++sequence.current;
    };
  }, []);
  // A changed send needs a new review.
  useEffect(() => {
    ++sequence.current;
    setReview(null);
    setBusy(false);
  }, [revision]);
  useEffect(() => {
    if (!review) return;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    const timer = setTimeout(
      () => {
        setError("This review expired. Review the paused send again.");
        setReview(null);
      },
      Math.max(0, review.expires * 1000 - Date.now()),
    );
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) setReview(null);
    };
    document.addEventListener("keydown", key);
    return () => {
      clearInterval(tick);
      clearTimeout(timer);
      document.removeEventListener("keydown", key);
    };
  }, [review, busy]);

  async function open() {
    if (busy || !operation.resumable) return;
    const read = ++sequence.current;
    const captured = current.current;
    setBusy(true);
    setError("");
    try {
      const value = await workspaceApi<Review>(
        `accounts/${account}/operations/${operation.id}/review-resume`,
        {},
      );
      if (
        !alive.current ||
        read !== sequence.current ||
        captured !== current.current
      )
        return;
      if (!reviewFits(value, account, peer, operation.id))
        throw new Error(
          "The paused send changed. Refresh and review it again.",
        );
      setNow(Date.now());
      setReview(value);
    } catch (cause) {
      if (alive.current && read === sequence.current)
        setError((cause as Error).message);
    } finally {
      if (alive.current && read === sequence.current) setBusy(false);
    }
  }
  async function resume() {
    if (!review || busy || review.expires * 1000 <= Date.now()) return;
    const read = ++sequence.current;
    setBusy(true);
    setError("");
    try {
      await workspaceApi<Operation>(
        `accounts/${account}/operations/${operation.id}/resume`,
        { token: review.token },
      );
      if (!alive.current) return;
      setReview(null);
      await onChange();
    } catch (cause) {
      if (alive.current && read === sequence.current) {
        setError((cause as Error).message);
        setReview(null);
        await Promise.resolve(onChange()).catch(() => undefined);
      }
    } finally {
      if (alive.current && read === sequence.current) setBusy(false);
    }
  }
  // Each request from the sheet opens the review once.
  const opened = useRef(request ?? 0);
  useEffect(() => {
    if (!request || request === opened.current) return;
    opened.current = request;
    void open();
  });
  const reviewed = review?.operation;
  return (
    <>
      {request === undefined && (
        <button
          type="button"
          disabled={busy || !operation.resumable}
          onClick={() => void open()}
        >
          <Play size={14} aria-hidden /> Review and resume
        </button>
      )}
      {error && (
        <small className="pending-reason" role="alert">
          {error}
        </small>
      )}
      {review && reviewed && (
        <div className="sheet-scrim" onClick={() => !busy && setReview(null)}>
          <div
            className="sheet-bottom resume-review"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`resume-title-${operation.id}`}
            onClick={(event) => event.stopPropagation()}
          >
            <h2 id={`resume-title-${operation.id}`}>Resume paused send</h2>
            <p className="resume-destination">
              {accountName} · {review.title}
            </p>
            <div className="resume-content">
              {reviewed.reply_to && (
                <ReplyQuote
                  reply={{
                    message_id: 0,
                    date: reviewed.created,
                    reply_to: reviewed.reply_to,
                  }}
                  variant="target"
                />
              )}
              {reviewed.images.map((identity, index) => (
                <img
                  key={identity}
                  src={`/api/accounts/${account}/chats/${encodeURIComponent(peer)}/images/${identity}`}
                  alt={`Reviewed image ${index + 1}`}
                />
              ))}
              {reviewed.media && (
                <small>
                  {reviewed.media.kind === "sticker" ? "Sticker" : "GIF"}
                </small>
              )}
              {reviewed.media?.kind !== "sticker" && reviewed.text && (
                <p>{reviewed.text}</p>
              )}
            </div>
            <small className="resume-time" aria-live="polite">
              {resumeWithin(review.expires, now)}
            </small>
            <div className="resume-actions">
              <button
                type="button"
                className="sheet-button"
                disabled={busy}
                onClick={() => setReview(null)}
              >
                Cancel review
              </button>
              <button
                type="button"
                className="sheet-button primary"
                disabled={busy}
                onClick={() => void resume()}
              >
                Resume send
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
