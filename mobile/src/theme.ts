import { useColorScheme } from "react-native";

// The web app's palette (web/src/rewriting.css) in both color schemes.
const light = {
  background: "#fbfcfb",
  panel: "#f0f3f1",
  text: "#22372f",
  muted: "#5f7167",
  line: "#dce5df",
  accent: "#237052",
  onAccent: "#ffffff",
  warning: "#8a5a00",
  error: "#a3302a",
  // A group's date band: the accent mixed 14% into the background, opaque so
  // a pinned band hides what scrolls under it (web/src/briefings.css).
  band: "#dde8e3",
  // The amber Briefing Slot Retry.
  retry: "#f6dfb0",
  onRetry: "#6b4500",
};

const dark: typeof light = {
  background: "#19221e",
  panel: "#202d26",
  text: "#e0e9e3",
  muted: "#9eafa5",
  line: "#34473b",
  accent: "#8cd3ad",
  onAccent: "#12201a",
  warning: "#e0b25a",
  error: "#f08a80",
  band: "#293b32",
  retry: "#4a3812",
  onRetry: "#f3cd78",
};

export type Palette = typeof light;

export function usePalette(): Palette {
  return useColorScheme() === "dark" ? dark : light;
}
