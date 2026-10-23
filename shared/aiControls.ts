// A block's AI controls (ADR 28, ADR 31, ADR 46 CU3), the same in the web and
// the Enloq app: Retry, the "Not analysed" note and the earlier-messages bar.

type Block = {
  state: string;
  revision: string;
  rendered?: string | null;
  rendered_revision?: string | null;
  stream?: unknown;
  error?: string | null;
};

// Retry: a failed or unknown attempt with no current AI text.
export function canRetry(block: Block) {
  const current =
    !!block.rendered && block.rendered_revision === block.revision;
  return (
    ["failed", "unknown"].includes(block.state) && !current && !block.stream
  );
}

// A block's id ends with the anchor its Retry route takes.
export function blockAnchor(id: string) {
  return id.split("/").at(-1)!;
}

// The service's code for an empty rewrite, in words.
export function blockError(error: string | null | undefined) {
  if (!error) return null;
  return error.includes("(omitted_target_text)")
    ? "The AI returned no rewrite text. Showing originals."
    : error;
}

const KINDS: Record<string, string> = {
  image: "Photo",
  audio: "Voice message",
  video_note: "Video message",
  video: "Video",
  gif: "GIF",
  sticker: "Sticker",
  document: "File",
  poll: "Poll",
  location: "Location",
  contact: "Contact",
  link: "Link",
  other: "Unsupported item",
  unsupported: "Unsupported item",
};

type Unanalysed = { id: number; kind: string; reason?: string | null };
type Source = {
  message_id: number;
  media_info?: { file_name?: string | null } | null;
};

// "Not analysed: File contract.pdf, Video message" under a block the AI
// skipped parts of.
export function unanalysedNote(
  items: Unanalysed[] | undefined,
  sources: Source[],
) {
  if (!items?.length) return null;
  const names = items.map((item) => {
    const label = KINDS[item.kind] ?? "Item";
    const file = sources.find((source) => source.message_id === item.id)
      ?.media_info?.file_name;
    return file ? `${label} ${file}` : label;
  });
  return `Not analysed: ${names.join(", ")}`;
}

export type OlderState = {
  available: number;
  notice: { remaining: number; reason: string } | null;
};
export type Progress = { done: number; total: number; explicit: boolean };

const ENDED: Record<string, string> = {
  budget: "keeping budget for new messages",
  off: "AI processing was turned Off",
  ineligible: "this chat is no longer eligible",
};

// The bar at the top of the loaded history: a run in progress, how the last
// one ended, or how many earlier blocks wait.
export function olderStatus(
  older: OlderState | null | undefined,
  progress: Progress | null | undefined,
) {
  if (progress?.explicit)
    return {
      kind: "running" as const,
      text: `Analyzing earlier messages · ${Math.min(progress.done + 1, progress.total)} of ${progress.total}`,
      done: progress.done,
      total: progress.total,
    };
  if (older?.notice?.remaining)
    return {
      kind: "ended" as const,
      text: `${older.notice.remaining} remaining — ${ENDED[older.notice.reason] ?? "run ended"}`,
    };
  if (older?.available)
    return {
      kind: "available" as const,
      text: `${older.available} earlier ${older.available === 1 ? "block is" : "blocks are"} not rewritten`,
    };
  return null;
}
