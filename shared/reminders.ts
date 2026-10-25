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

export function reminderPath(account: number, peer: string) {
  return `accounts/${account}/chats/${encodeURIComponent(peer)}/reminder`;
}

export function reminderDefaults(timezone: string, language: string) {
  return {
    frequency: "weekly" as const,
    weekday: 0,
    month_day: 1,
    timezone,
    delivery: "digest" as const,
    time: "09:00",
    language,
  };
}

export function reminderTime(
  value: number | null | undefined,
  timezone: string,
) {
  if (!value) return "";
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: timezone,
    }).format(new Date(value * 1000));
  } catch {
    return "Choose a valid timezone";
  }
}
