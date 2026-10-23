import { useReadingPreference } from "./ReadingView";

export function useHideBots() {
  const { value, ...state } = useReadingPreference<"hide_bots", boolean>(
    "hide_bots",
    "preferences/hide-bots",
    true,
  );
  return { hideBots: value, ...state };
}
