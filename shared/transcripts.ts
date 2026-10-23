// Show transcript (ADR 27; ADR 51 Q9): one tap reads where a voice note's
// transcript stands, starts a missing or failed one (never in Saved Messages,
// which has no AI), and follows it until it ends. Both clients draw the views.

export type TranscriptStatus = {
  state: "none" | "running" | "done" | "failed";
  transcript: { text: string; language?: string | null } | null;
  reason: string | null;
};

export type TranscriptView =
  | { kind: "loading" }
  | { kind: "running" }
  | { kind: "text"; text: string }
  | { kind: "note"; note: string; retry: boolean };

export const TRANSCRIPT_POLL_MS = 2000;

const sentence = (text: string) => (/[.!?…]$/.test(text) ? text : `${text}.`);

export async function showTranscript({
  read,
  start,
  saved,
  show,
  alive,
  wait,
}: {
  read: () => Promise<TranscriptStatus>;
  start: () => Promise<TranscriptStatus>;
  saved: boolean;
  show: (view: TranscriptView) => void;
  // False once the bubble is gone, which ends the following.
  alive: () => boolean;
  wait: (ms: number) => Promise<void>;
}) {
  show({ kind: "loading" });
  try {
    let status = await read();
    if (!saved && (status.state === "none" || status.state === "failed"))
      status = await start();
    while (status.state === "running") {
      show({ kind: "running" });
      await wait(TRANSCRIPT_POLL_MS);
      if (!alive()) return;
      status = await read();
    }
    if (!alive()) return;
    if (status.state === "done" && status.transcript)
      show({ kind: "text", text: status.transcript.text });
    else if (status.state === "failed")
      show({
        kind: "note",
        note: sentence(status.reason ?? "Transcription failed"),
        retry: true,
      });
    else show({ kind: "note", note: "No transcript yet.", retry: false });
  } catch (error) {
    if (!alive()) return;
    show({
      kind: "note",
      note:
        error instanceof Error && error.message
          ? sentence(error.message)
          : "The transcript couldn't be loaded.",
      retry: false,
    });
  }
}
