// Traits' wording (ADR 55 Q10-Q13, Q22, Q23), the same in the web and the
// Enloq app: counted habits as sentences, and a scale's or note's confidence.

export type Habits = {
  messages: number;
  reply_seconds: number | null;
  active_from: number;
  active_to: number;
  voice_share: number;
  starts_share: number | null;
};

const percent = (share: number) => `${Math.round(share * 100)}%`;

function duration(seconds: number) {
  if (seconds < 90) return "a minute";
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} minutes`;
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "an hour" : `${hours} hours`;
}

const hour = (value: number) => `${String(value).padStart(2, "0")}:00`;

// The counted habits of a person or of you, one sentence each.
export function habitLines(habits: Habits, subject: "person" | "you") {
  const lines: string[] = [];
  if (habits.reply_seconds !== null)
    lines.push(
      `${subject === "you" ? "You usually reply" : "Usually replies"} in about ${duration(habits.reply_seconds)}.`,
    );
  lines.push(
    `${subject === "you" ? "You write" : "Writes"} most between ${hour(habits.active_from)} and ${hour(habits.active_to)}.`,
  );
  if (habits.voice_share > 0)
    lines.push(`Voice notes: ${percent(habits.voice_share)} of messages.`);
  if (habits.starts_share !== null)
    lines.push(
      `${subject === "you" ? "You start" : "Starts"} ${percent(habits.starts_share)} of conversations.`,
    );
  return lines;
}

// A model's confidence, as the owner reads it (Q12 C).
export function confidence(probability: number | null) {
  return probability === null ? "" : percent(probability);
}
