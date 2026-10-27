// Ask this group (ADR 59), shared by the web and the Enloq app: what the Ask
// screen shows of the service's answer.
import { shortDay } from "./reminders";

export type AskStatement = {
  text: string;
  // The citations the check kept, and those it found unsupported (Q6).
  citations: number[];
  unsupported: number[];
  supported: boolean;
};

export type AskAnswer = {
  status: "answer" | "not_found" | "unchecked";
  // The searched calendar days, YYYY-MM-DD in the service's timezone.
  searched: { start: string; end: string };
  statements?: AskStatement[];
};

// The statements the screen shows, or the one line it shows instead: the
// searched range when nothing answers (Q7), or that the check couldn't run
// (Q6). `today` is today's YYYY-MM-DD in the service's timezone.
export function shownAnswer(
  answer: AskAnswer,
  today: string,
): { statements: AskStatement[]; line: string | null } {
  if (answer.status === "unchecked")
    return { statements: [], line: "Couldn't check the answer." };
  // ADR 59 Q6: a statement shows only with a citation the check kept, and
  // an answer left with none abstains.
  const statements =
    answer.status === "answer"
      ? (answer.statements ?? []).filter(
          (statement) => statement.citations.length > 0,
        )
      : [];
  if (statements.length) return { statements, line: null };
  const { start, end } = answer.searched;
  return {
    statements,
    line: `No answer in this group's messages from ${shortDay(start)} to ${
      end === today ? "today" : shortDay(end)
    }.`,
  };
}
