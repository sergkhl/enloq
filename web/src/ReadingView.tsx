import React, { useEffect, useRef, useState } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { useChanges } from "./events";

type Preference = components["schemas"]["ReadingView"];
type View = Preference["reading_view"];

// Reading preferences share one durable service value across accounts and
// trusted browsers. Only a successful save changes the displayed choice.
export function useReadingPreference<Key extends string, Value>(
  key: Key,
  path: string,
  initial: Value,
) {
  const [value, setValue] = useState<Value>(initial);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const version = useRef(0),
    changing = useRef(false),
    reread = useRef<() => void>(() => {}),
    pendingRead = useRef(false),
    mounted = useRef(false);
  useChanges((change) => {
    if (change.all || change.shared.includes(key)) reread.current();
  }, 30000);
  useEffect(() => {
    let alive = true;
    mounted.current = true;
    const read = async () => {
      if (changing.current) {
        pendingRead.current = true;
        return;
      }
      const current = ++version.current;
      try {
        const saved = await workspaceApi<Record<Key, Value>>(path);
        if (alive && current === version.current) {
          setValue(saved[key]);
          setError("");
          setBusy(false);
        }
      } catch (e) {
        if (alive && current === version.current) {
          setError((e as Error).message);
          setBusy(false);
        }
      }
    };
    void read();
    reread.current = () => void read();
    return () => {
      alive = false;
      mounted.current = false;
      ++version.current;
      reread.current = () => {};
    };
  }, [key, path]);
  async function choose(next: Value) {
    if (changing.current || next === value) return;
    const current = ++version.current;
    changing.current = true;
    setBusy(true);
    setError("");
    try {
      const saved = await workspaceApi<Record<Key, Value>>(path, {
        [key]: next,
      });
      if (mounted.current && current === version.current) setValue(saved[key]);
    } catch (e) {
      if (mounted.current && current === version.current)
        setError((e as Error).message);
    } finally {
      changing.current = false;
      if (mounted.current) {
        setBusy(false);
        // A remote update received during the save still needs its own read.
        if (pendingRead.current) {
          pendingRead.current = false;
          reread.current();
        }
      }
    }
  }
  return { value, busy, error, choose };
}

// The global Reading View (ADR 14), shown as the Conversation actions menu's
// Show AI text switch (ADR 28).
export function useReadingView() {
  const { value, ...state } = useReadingPreference<"reading_view", View>(
    "reading_view",
    "preferences/reading-view",
    "ai",
  );
  return { view: value, ...state };
}
