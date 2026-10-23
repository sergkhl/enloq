import { useCalendars } from "expo-localization";
import type { HourCycle } from "../../shared/messageTime";

// Hermes formats times from the locale alone, so an Android phone set to a
// 24-hour clock would still show "6:43 PM"; follow the device's setting.
export function useHourCycle(): HourCycle | undefined {
  const [calendar] = useCalendars();
  const uses24 = calendar?.uses24hourClock;
  if (uses24 == null) return undefined;
  return uses24 ? "h23" : "h12";
}
