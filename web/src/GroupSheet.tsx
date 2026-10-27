import React, { useEffect, useRef } from "react";
import { ChevronRight } from "lucide-react";
import type { useGroupProcessing } from "./GroupProcessingControl";
import type { useHideBots } from "./HideBots";
import "./ask.css";

// Why Group AI processing can't brief now (the app's GroupSheet notes).
function processingNote(
  value: ReturnType<typeof useGroupProcessing>["value"],
  accountEnabled: boolean | null,
) {
  if (value && !value.supported)
    return "This group is unavailable for AI processing.";
  if (value?.access === "lost")
    return "Group access was lost. Cached originals remain available.";
  if (value?.access === "unknown") return "Group access has not been verified.";
  if (accountEnabled === false) return "AI processing is Off for this account.";
  return null;
}

// A group's ⋮ actions sheet, as in the app (ADR 42 Q3, ADR 44 MW2): Group AI
// processing with its notes, absent for retained work, Ask this group while
// it is On (ADR 59 Q1), and Hide bots.
export function GroupSheet({
  processing,
  accountEnabled,
  hideBots,
  onAsk,
  onClose,
}: {
  processing: ReturnType<typeof useGroupProcessing> | null;
  accountEnabled: boolean | null;
  hideBots: ReturnType<typeof useHideBots>;
  onAsk: () => void;
  onClose: () => void;
}) {
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    first.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  const note = processing
    ? processingNote(processing.value, accountEnabled)
    : null;
  const refusal = processing?.error || hideBots.error;
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div
        className="sheet-bottom"
        role="dialog"
        aria-modal="true"
        aria-label="Conversation actions"
        onClick={(event) => event.stopPropagation()}
      >
        {processing && (
          <>
            <button
              ref={first}
              type="button"
              role="switch"
              className="switch-line"
              aria-checked={processing.value?.processing_enabled ?? false}
              disabled={processing.value === null || processing.busy}
              onClick={() => void processing.toggle()}
            >
              <span>Group AI processing</span>
              <span className="switch-knob" aria-hidden />
            </button>
            {note && <p className="sheet-note">{note}</p>}
          </>
        )}
        {processing?.value?.processing_enabled && (
          <button type="button" className="sheet-item ask-row" onClick={onAsk}>
            <span>Ask this group</span>
            <ChevronRight size={18} aria-hidden />
          </button>
        )}
        <button
          ref={processing ? undefined : first}
          type="button"
          role="switch"
          className="switch-line"
          aria-checked={hideBots.hideBots}
          disabled={hideBots.busy}
          onClick={() => void hideBots.choose(!hideBots.hideBots)}
        >
          <span>Hide bots</span>
          <span className="switch-knob" aria-hidden />
        </button>
        {refusal && (
          <p className="sheet-note error" role="alert">
            {refusal}
          </p>
        )}
      </div>
    </div>
  );
}
