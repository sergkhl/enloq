import { createContext, useContext, useEffect, useState } from "react";
import { AccessibilityInfo } from "react-native";
import { useSyncExternalStore } from "react";
import { useFocused } from "./viewing";

// GIFs and animated stickers move only while their row is on screen, the
// chat is focused and the phone's reduce motion setting is off (ADR 46 CU1).

// The row a piece of media sits in: `${account}/${peer}/${row key}`.
export const MediaRow = createContext<string | null>(null);

// Each chat's rows on screen, from its list's viewability.
const shown = new Map<string, Set<string>>();
const listeners = new Set<() => void>();

export function showRows(chat: string, rows: string[]) {
  shown.set(chat, new Set(rows.map((row) => `${chat}/${row}`)));
  for (const listener of listeners) listener();
}

export function forgetRows(chat: string) {
  shown.delete(chat);
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function onScreen(row: string | null) {
  // Outside a list (a test or a single bubble), media counts as shown.
  if (row === null) return true;
  for (const rows of shown.values()) if (rows.has(row)) return true;
  return false;
}

export function useReduceMotion() {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    let live = true;
    void AccessibilityInfo.isReduceMotionEnabled().then(
      (value) => live && setReduce(value),
    );
    const subscription = AccessibilityInfo.addEventListener(
      "reduceMotionChanged",
      setReduce,
    );
    return () => {
      live = false;
      subscription.remove();
    };
  }, []);
  return reduce;
}

// Whether this row's media may load: on screen in a focused chat.
export function useOnScreen() {
  const row = useContext(MediaRow);
  const visible = useSyncExternalStore(subscribe, () => onScreen(row));
  return useFocused() && visible;
}

// Whether this row's media may move as well.
export function useMoving() {
  const reduce = useReduceMotion();
  return useOnScreen() && !reduce;
}
