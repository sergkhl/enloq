import { messageTime, type HourCycle } from "./messageTime";

// Settings' pure rules, which both clients share (ADR 45; ADR 38 Q32).

// The service counts money in nano-dollars.
export function dollars(nanos: number, digits = 2) {
  return `$${(nanos / 1e9).toFixed(digits)}`;
}

// A typed limit as the service takes it: $0 to $10 in whole cents, with an
// optional "$" and either decimal separator; null when it isn't one.
export function budgetAmount(text: string): string | null {
  const typed = text
    .trim()
    .replace(/^\$\s*/, "")
    .replace(",", ".");
  if (!/^\d{1,2}(\.\d{0,2})?$/.test(typed)) return null;
  const value = Number(typed);
  return value <= 10 ? value.toFixed(2) : null;
}

// The Processing Character Threshold as typed: a whole number to 10,000.
export function characterCount(text: string): number | null {
  const typed = text.trim();
  if (!/^\d{1,5}$/.test(typed)) return null;
  const value = Number(typed);
  return value <= 10_000 ? value : null;
}

// When a budget day ends, in the service's calendar like every app time.
export function resetLabel(
  resetsAt: number,
  timezone: string,
  hourCycle?: HourCycle,
) {
  const day = new Date(resetsAt * 1000).toLocaleDateString([], {
    timeZone: timezone,
    weekday: "short",
  });
  return `${day} ${messageTime(resetsAt, timezone, hourCycle)}`;
}

// The owner's notice in Settings → AI while the decision model's route fails
// (ADR 49 Q14), from when it started, in the service's calendar.
export function decisionModelNotice(
  since: number,
  timezone: string,
  hourCycle?: HourCycle,
) {
  return `Decision model unavailable since ${resetLabel(since, timezone, hourCycle)}. Emotion, suggestions and group ad filtering wait until it answers again.`;
}

export type BudgetFamily = "daily" | "group_briefing";

// Each budget's words and route (ADR 14, ADR 34).
export const BUDGETS: Record<
  BudgetFamily,
  {
    title: string;
    explanation: string;
    route: string;
    reached: (reset: string) => string;
    zero: string;
  }
> = {
  daily: {
    title: "Daily Budget",
    explanation: "Private chats and every Rewrite, for all accounts.",
    route: "budget",
    reached: (reset) =>
      `Today's budget is reached. New private-chat AI work and Rewrite wait until ${reset} or a higher limit.`,
    zero: "At $0, new private-chat AI work and Rewrite stop until the limit is raised.",
  },
  group_briefing: {
    title: "Group Briefing Budget",
    explanation: "Group briefings, for all accounts and groups.",
    route: "group-budget",
    reached: (reset) =>
      `Today's group briefing budget is reached. New briefing work waits until ${reset} or a higher limit.`,
    zero: "At $0, new group briefings stop until the limit is raised.",
  },
};

// The processing controls' explanations, in place of Help (ADR 45 Q7).
export const EXPLAIN = {
  minimum:
    "Group briefings start once a day's messages reach this many characters. Images, voice notes and videos don't need it. 0 turns it off. Private text stays as sent unless hostile wording is softened.",
  calls: "The shared limit for all accounts and model requests.",
  memory:
    "Notes what each person and you care about, and their traits, to shape reply suggestions. Off stops using and updating them; nothing is deleted.",
  // Either signal since ADR 56.
  suggestions:
    "Prepare suggestions when a message shows a clear emotion or calls for a reply. Manual generation stays available when Off.",
  guidance:
    "Changes apply from the next model call. A protected core keeps messages as data and enforces the output format.",
};

// A budget or pause message's Settings section (ADR 45 Q8).
export type SettingsTarget = "daily" | "group" | "guidance";

// The private chat stages that Settings can act on.
export const STAGE_TARGETS: Record<string, SettingsTarget> = {
  budget: "daily",
  format_paused: "guidance",
};
