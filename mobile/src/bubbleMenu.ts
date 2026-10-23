import { createContext, useContext } from "react";

// A bubble's long press opens its message sheet (ADR 26). Media inside it
// with their own taps (a play button, a file card, a photo) pass the long
// press on, so holding anywhere on the message opens the sheet, as in
// Telegram (ADR 46 CU1).
export const BubbleMenu = createContext<(() => void) | undefined>(undefined);

export function useBubbleMenu() {
  const open = useContext(BubbleMenu);
  return open ? { onLongPress: open, delayLongPress: 350 } : {};
}
