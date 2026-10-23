// The application's calendar is shared by grouping and presentation. Locale
// controls the format; the browser's timezone never controls the calendar day.
// A client that knows the device's 12/24-hour setting passes its hour cycle.
export type HourCycle = "h12" | "h23";

export function messageTime(
  value: number,
  timezone: string,
  hourCycle?: HourCycle,
) {
  return new Date(value * 1000).toLocaleTimeString([], {
    timeZone: timezone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle,
  });
}

export function messageDate(value: number, timezone: string) {
  return new Date(value * 1000).toLocaleDateString([], {
    timeZone: timezone,
    month: "short",
    day: "numeric",
  });
}

export function previewTime(
  value: number | null | undefined,
  timezone: string,
  hourCycle?: HourCycle,
) {
  if (!value) return "";
  const date = new Date(value * 1000),
    now = new Date(),
    calendar = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }),
    day = (stamp: Date) => {
      const parts = calendar.formatToParts(stamp);
      return ["year", "month", "day"]
        .map((name) => parts.find((part) => part.type === name)!.value)
        .join("-");
    };
  if (day(date) === day(now)) return messageTime(value, timezone, hourCycle);
  if (day(date) >= day(new Date(now.getTime() - 6 * 86400000)))
    return date.toLocaleDateString([], {
      timeZone: timezone,
      weekday: "short",
    });
  return date.toLocaleDateString([], {
    timeZone: timezone,
    month: "short",
    day: "numeric",
  });
}

export function formatDuration(seconds: number) {
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
