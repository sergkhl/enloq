import React, { useState } from "react";
import { chatApi } from "./conversation-api";
import {
  olderStatus,
  type OlderState,
  type Progress,
} from "../../shared/aiControls";

// Rewrite earlier messages at the top of the loaded history (ADR 28, ADR 46
// CU3): how many earlier blocks wait with Rewrite, the run's progress with
// Stop, or how the last run ended.
export function EarlierBar({
  account,
  peer,
  older,
  progress,
  processing,
  onChange,
}: {
  account: number;
  peer: string;
  older: OlderState | null | undefined;
  progress: Progress | null | undefined;
  processing: boolean | null;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const status = olderStatus(older, progress);
  if (!status) return null;
  async function run(path: string) {
    setBusy(true);
    setError("");
    try {
      await chatApi(account, peer, path, {});
      onChange();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="earlier-bar" role="status">
      <span>{status.text}</span>
      {status.kind === "running" && (
        <progress
          aria-label="Earlier messages analysed"
          max={status.total}
          value={status.done}
        />
      )}
      {status.kind === "running" ? (
        <button
          type="button"
          className="bubble-link"
          aria-label="Stop rewriting earlier messages"
          disabled={busy}
          onClick={() => void run("/stop")}
        >
          Stop
        </button>
      ) : (older?.available ?? 0) > 0 ? (
        <button
          type="button"
          className="bubble-link"
          aria-label="Rewrite earlier messages"
          disabled={busy || processing === false}
          title={
            processing === false
              ? "AI processing is Off for this account"
              : undefined
          }
          onClick={() => void run("/process-older")}
        >
          Rewrite
        </button>
      ) : null}
      {error && <small role="alert">{error}</small>}
    </div>
  );
}
