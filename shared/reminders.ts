// Calendar reminder presentation shared by the app and its web screens (ADR 58).
export const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

export const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

export type Repeat = "once" | "days" | "weeks" | "monthly" | "yearly";

// The editor's compact Repeat choice (Q1): a short label, and the name
// screen readers hear.
export const REPEATS: { value: Repeat; short: string; label: string }[] = [
  { value: "once", short: "Once", label: "Once" },
  { value: "days", short: "Days", label: "Every N days" },
  { value: "weeks", short: "Weeks", label: "Every N weeks" },
  { value: "monthly", short: "Monthly", label: "Monthly" },
  { value: "yearly", short: "Yearly", label: "Yearly" },
];

// The fields of a Reminder Schedule the presentation reads.
export type Rule = {
  repeat: Repeat;
  start: string;
  interval: number;
  weekday: number;
  month: number;
  month_day: number;
  timezone: string;
  delivery: "digest" | "exact";
  time: string;
  language: string;
  idea: string;
};

export type Kept = {
  day: string;
  state: "waiting" | "kept" | "expired";
  count: number;
};

export function remindersPath(account: number, peer: string) {
  return `accounts/${account}/chats/${encodeURIComponent(peer)}/reminders`;
}

export function reminderPath(account: number, peer: string, id: string) {
  return `${remindersPath(account, peer)}/${encodeURIComponent(id)}`;
}

// A local calendar date as YYYY-MM-DD.
export function isoDay(value: Date) {
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${value.getFullYear()}-${month}-${day}`;
}

function weekdayOf(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  // Monday is 0, as the service counts.
  return (new Date(year!, month! - 1, date!).getDay() + 6) % 7;
}

// A new schedule: weekly on today's weekday at the digest, in the device's
// timezone, with the chat's recent language or else the device's (Q1).
export function reminderDefaults(
  timezone: string,
  language: string,
  today = isoDay(new Date()),
): Rule {
  const [, month, date] = today.split("-").map(Number);
  return {
    repeat: "weeks",
    start: today,
    interval: 1,
    weekday: weekdayOf(today),
    month: month!,
    month_day: date!,
    timezone,
    delivery: "digest",
    time: "09:00",
    language,
    idea: "",
  };
}

// The listed language for a device tag such as "en-US", else English.
export function listedLanguage(
  tag: string,
  languages: { code: string; name: string }[],
) {
  const code = tag.toLowerCase().split(/[-_]/)[0];
  const alias: Record<string, string> = { no: "nb", nn: "nb", iw: "he" };
  const wanted = alias[code ?? ""] ?? code;
  return (
    languages.find((language) => language.code === wanted)?.name ?? "English"
  );
}

function ordinal(value: number) {
  const tens = value % 100;
  const suffix =
    tens >= 11 && tens <= 13
      ? "th"
      : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[value % 10] ||
        "th";
  return `${value}${suffix}`;
}

function shortDay(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  return `${date} ${MONTHS[month! - 1]!.slice(0, 3)}${
    year === new Date().getFullYear() ? "" : ` ${year}`
  }`;
}

// "Every Monday · 09:00 digest": the repeat and its alert (Q7).
export function repeatLine(rule: Rule, digestTime: string) {
  const alert = rule.delivery === "digest" ? `${digestTime} digest` : rule.time;
  const weekday = WEEKDAYS[rule.weekday]!;
  const repeat =
    rule.repeat === "once"
      ? `Once on ${shortDay(rule.start)}`
      : rule.repeat === "days"
        ? rule.interval === 1
          ? "Every day"
          : `Every ${rule.interval} days`
        : rule.repeat === "weeks"
          ? rule.interval === 1
            ? `Every ${weekday}`
            : `Every ${rule.interval} weeks on ${weekday}`
          : rule.repeat === "monthly"
            ? `Monthly on the ${ordinal(rule.month_day)}`
            : `Yearly on ${rule.month_day} ${MONTHS[rule.month - 1]!.slice(0, 3)}`;
  return `${repeat} · ${alert}`;
}

// "Mon 2 Nov": one date in the schedule's timezone.
export function dateLabel(value: number | null | undefined, timezone: string) {
  if (!value) return "";
  try {
    const date = new Date(value * 1000);
    const parts = new Intl.DateTimeFormat("en-GB", {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: timezone,
    }).formatToParts(date);
    const part = (type: string) =>
      parts.find((item) => item.type === type)?.value ?? "";
    const thisYear =
      part("year") ===
      new Intl.DateTimeFormat("en-GB", {
        year: "numeric",
        timeZone: timezone,
      }).format(new Date());
    return `${part("weekday")} ${part("day")} ${part("month")}${
      thisYear ? "" : ` ${part("year")}`
    }`;
  } catch {
    return "";
  }
}

// "09:00": a moment's wall time in the schedule's timezone.
export function timeLabel(value: number, timezone: string) {
  try {
    return new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone: timezone,
    }).format(new Date(value * 1000));
  } catch {
    return "";
  }
}

// The editor's summary: "Every Monday · 09:00 digest · next Mon 2 Nov" (Q12).
export function summaryLine(
  rule: Rule,
  digestTime: string,
  nextAt: number | null | undefined,
) {
  const next = dateLabel(nextAt, rule.timezone);
  return `${repeatLine(rule, digestTime)}${next ? ` · next ${next}` : ""}`;
}

// The Message Idea's first line, as rows and the Reminder Bar show it.
export function ideaLine(idea: string) {
  return (
    idea
      .split("\n")
      .map((line) => line.trim())
      .find(Boolean) ?? ""
  );
}

// "Keeps 12 messages from 25 Oct" (Q11).
export function keptLine(kept: Kept | null | undefined) {
  if (!kept) return "";
  const day = shortDay(kept.day);
  if (kept.state === "waiting") return `Will keep messages from ${day}`;
  if (kept.state === "expired")
    return `Nothing to keep: ${day} is older than the 30-day cache`;
  if (!kept.count) return `No messages from ${day} to keep`;
  return `Keeps ${kept.count} message${kept.count === 1 ? "" : "s"} from ${day}`;
}

// The Reminders tab's first line, "Daily digest · 09:00" (Q7).
export function digestLine(time: string) {
  return `Daily digest · ${time}`;
}

// A row's one date: when a pending reminder fell due, else the next delivery.
export function rowDate(
  pending: { due_at: number } | null | undefined,
  nextAt: number | null | undefined,
  timezone: string,
) {
  if (pending) return `Due ${dateLabel(pending.due_at, timezone)}`;
  const next = dateLabel(nextAt, timezone);
  return next ? `Next ${next}` : "No next delivery";
}

// The Reminder Bar's line for one pending occurrence (Q13).
export function barLine(idea: string) {
  return ideaLine(idea) || "Check-in reminder";
}

// Remove reminder asks first (Q4).
export const REMOVE_REMINDER = {
  title: "Remove reminder?",
  message:
    "Its schedule, Message Idea and Kept Messages are deleted. Your drafts stay as they are.",
  cancel: "Cancel",
  confirm: "Remove",
};
