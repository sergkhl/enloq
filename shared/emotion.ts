import { messageDate, messageTime } from "./messageTime";

// The Recipient Emotion's face, name and details (ADR 22, ADR 25, ADR 26),
// the same in the web and the Enloq app: the header's emoji and the
// Correspondent Profile's details (ADR 46 CU3).

export type EmotionView = {
  code?: string | null;
  reason?: string | null;
  source_time?: number | null;
  expires_at?: number | null;
  source?: "incoming" | "reaction" | null;
  reaction?: string | null;
  target_id?: number | null;
};

export const EMOTIONS: Record<string, [string, string]> = {
  neutral: ["😐", "Neutral"],
  positive: ["🙂", "Positive or warm"],
  excited: ["🤩", "Excited"],
  sad: ["😔", "Sad"],
  worried: ["😟", "Worried"],
  frustrated: ["😤", "Frustrated"],
  angry: ["😠", "Angry"],
  confused: ["😕", "Confused"],
  unknown: ["😐", "Mixed or uncertain"],
};

// Why there is or isn't a current estimate.
const REASONS: Record<string, string> = {
  available: "Inferred from the latest incoming message block.",
  uncertain: "Mixed emotion or insufficient evidence.",
  pending: "Waiting for the latest incoming messages to be assessed.",
  expired: "The latest estimate is over 12 hours old.",
  changed: "Source material changed; no current estimate.",
  invalid: "The latest incoming messages could not be assessed.",
  unavailable: "No current emotion estimate.",
};

// A reaction source reads the same reasons against its own evidence.
const REACTION_REASONS: Record<string, string> = {
  available: "Inferred from the recipient's reaction and its message.",
  pending: "Waiting for the recipient's reaction to be assessed.",
  changed: "The reaction or its message changed; no current estimate.",
};

// The current estimate, or null once it has expired (12 hours).
export function currentEmotion(
  emotion: EmotionView | null | undefined,
  now = Date.now(),
): EmotionView | null {
  if (!emotion) return null;
  if (emotion.expires_at && emotion.expires_at * 1000 <= now)
    return { ...emotion, code: null, reason: "expired" };
  return emotion;
}

// The header's emoji and name, when there is a current estimate.
export function emotionFace(emotion: EmotionView | null | undefined) {
  const code = currentEmotion(emotion)?.code;
  return code ? (EMOTIONS[code] ?? null) : null;
}

export function emotionReason(emotion: EmotionView | null | undefined) {
  const reason = emotion?.reason ?? "unavailable";
  return (
    (emotion?.source === "reaction" && REACTION_REASONS[reason]) ||
    REASONS[reason] ||
    REASONS.unavailable!
  );
}

// The profile's lines: the estimate, why, its source and its time.
export function emotionDetails(
  emotion: EmotionView | null | undefined,
  timezone: string,
) {
  const current = currentEmotion(emotion);
  const face = current?.code ? EMOTIONS[current.code] : null;
  return {
    label: face ? `${face[0]} ${face[1]}` : "No current estimate",
    reason: emotionReason(current),
    source:
      current?.source === "reaction" && current.reaction
        ? `A reaction ${current.reaction}${current.target_id ? ` on message #${current.target_id}` : ""}`
        : "The latest incoming messages",
    time: current?.source_time
      ? `${messageDate(current.source_time, timezone)} · ${messageTime(current.source_time, timezone)}`
      : null,
  };
}
