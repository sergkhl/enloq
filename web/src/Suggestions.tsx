import React, { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { components } from "./api-schema";
import { chatApi } from "./conversation-api";
import "./suggestions.css";

export type SuggestionsView = components["schemas"]["SuggestionsView"];
const NONE: SuggestionsView = {
  state: "none",
  kind: "automatic",
  style: "friendly",
  reason: null,
  attempt: null,
  options: [],
  folded: false,
  source_time: null,
  expires_at: null,
};
const PURPOSES = {
  acknowledge: "Acknowledge",
  support: "Support",
  clarify: "Clarify",
  celebrate: "Celebrate",
  // Answer stances, always offered side by side (ADR 49 Q4, Q8).
  accept: "Accept",
  decline: "Decline",
  later: "Later",
};
const STATUS: Partial<Record<string, string>> = {
  queued: "Preparing suggestions…",
  running: "Preparing suggestions…",
  budget: "Suggestions wait for budget",
  paused: "Suggestions wait for AI processing",
  retrying: "Suggestions wait for the provider",
  format: "Suggestions unavailable: format check failed",
  provider: "Suggestions unavailable: provider error",
  refused: "Suggestions unavailable: provider refused",
  interrupted: "Suggestions unavailable: interrupted",
  too_large: "Suggestions unavailable: conversation too long",
};

/** The server projection, hidden locally once its source time expires. */
export function useSuggestions(view?: SuggestionsView): SuggestionsView {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const update = () => setNow(Date.now());
    update();
    const wait = (view?.expires_at ?? 0) * 1000 - Date.now();
    const timer =
      wait > 0
        ? window.setTimeout(update, Math.min(wait + 1, 2147483647))
        : undefined;
    document.addEventListener("visibilitychange", update);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, [view?.expires_at]);
  if (!view) return NONE;
  if (view.expires_at && Math.max(now, Date.now()) >= view.expires_at * 1000)
    return { ...NONE, reason: "expired" };
  return view;
}

// A folded set stays folded on every client until the next one arrives
// (ADR 56 Q12 B).
export function SuggestionArea({
  account,
  peer,
  view,
  disabled,
  onChoose,
  onBudget,
  refresh,
}: {
  account: number;
  peer: string;
  view: SuggestionsView;
  disabled: boolean;
  onChoose: (index: number) => void;
  // Waiting for budget opens the Daily Budget (ADR 45 Q8).
  onBudget?: () => void;
  refresh: () => Promise<void>;
}) {
  // A tap's fold shows at once, until a read after the server's answer
  // carries it; a refusal shows the server's state again.
  const [fold, setFold] = useState<{ attempt: string; folded: boolean }>();
  const taps = useRef(0);
  if (view.state === "ready" && view.attempt && view.options?.length) {
    const attempt = view.attempt;
    const hidden = fold?.attempt === attempt ? fold.folded : view.folded;
    const toggle = async () => {
      const mine = ++taps.current;
      setFold({ attempt, folded: !hidden });
      try {
        await chatApi<SuggestionsView>(account, peer, "/suggestions/fold", {
          attempt,
          folded: !hidden,
        });
      } catch {
        if (mine === taps.current) setFold(undefined);
      }
      await refresh();
      if (mine === taps.current) setFold(undefined);
    };
    if (hidden)
      return (
        <section
          className="suggestions is-collapsed"
          aria-label="Response suggestions"
        >
          <button
            type="button"
            className="suggestions-reopen"
            aria-expanded="false"
            onClick={() => void toggle()}
          >
            Suggestions ({view.options.length})
            <ChevronUp size={15} aria-hidden="true" />
          </button>
        </section>
      );
    return (
      <section className="suggestions" aria-label="Response suggestions">
        <div className="suggestions-head">
          <span className="suggestions-title" aria-hidden="true">
            Suggestions
          </span>
          <button
            type="button"
            className="suggestions-toggle"
            aria-label="Hide suggestions"
            aria-expanded="true"
            onClick={() => void toggle()}
          >
            <ChevronDown size={15} aria-hidden="true" />
          </button>
        </div>
        <ul className="suggestion-list">
          {view.options.map((option, index) => (
            <li key={index}>
              <button
                type="button"
                className="suggestion"
                disabled={disabled}
                // Keep the message field's caret: a click must not move it.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onChoose(index)}
              >
                <span className="suggestion-purpose">
                  {PURPOSES[option.purpose]}
                </span>
                <span className="suggestion-text">{option.text}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  const status = view.reason ? STATUS[view.reason] : undefined;
  if (
    (view.state === "preparing" ||
      view.state === "waiting" ||
      view.state === "failed") &&
    status
  )
    return (
      <p className="suggestions-status" role="status">
        {view.reason === "budget" && onBudget ? (
          <button type="button" className="status-link" onClick={onBudget}>
            {status}
          </button>
        ) : (
          status
        )}
      </p>
    );
  return null;
}
