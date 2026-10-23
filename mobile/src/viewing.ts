import { useFocusEffect } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState } from "react-native";
import { viewable } from "../../shared/groupBriefings";
import type { Schemas } from "./api";
import { useConnection } from "./connection";

type Ready = {
  content: Schemas["ViewedContent"][];
  next_cursor: number | null;
};

// Whether this screen is on top and the app is in the foreground.
export function useFocused() {
  const [screen, setScreen] = useState(false);
  // iOS can report "unknown" until its first change; only the background and
  // transitions count as away.
  const [app, setApp] = useState(
    !["background", "inactive"].includes(AppState.currentState ?? ""),
  );
  useFocusEffect(
    useCallback(() => {
      setScreen(true);
      return () => setScreen(false);
    }, []),
  );
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) =>
      setApp(state === "active"),
    );
    return () => subscription.remove();
  }, []);
  return screen && app;
}

// ADR 24: one focused second acknowledges every ready incoming source in the
// open conversation, offscreen ones included. Viewing is the app's own state
// shared by trusted clients; it sends no Telegram read receipt. `version`
// changes when the conversation does, so newly ready content is acknowledged
// while the screen stays focused. A group's originals folded under a closed
// spoiler are `withheld` until it opens (ADR 34).
export function useFocusedViewing(
  account: number,
  peer: string,
  focused: boolean,
  version: unknown,
  withheld: ReadonlySet<number> = NONE,
) {
  const { api } = useConnection();
  const path = `accounts/${account}/chats/${encodeURIComponent(peer)}`;
  const folded = useRef(withheld);
  folded.current = withheld;
  const unfolded = [...withheld].sort((a, b) => a - b).join(" ");
  useEffect(() => {
    if (!focused) return;
    let live = true;
    const timer = setTimeout(async () => {
      try {
        let after = 0;
        for (let page = 0; page < 20 && live; page++) {
          const ready = await api<Ready>(
            `${path}/ready-sources?after=${after}&limit=100`,
          );
          // Rewrite tokens describe AI wording, not a source to acknowledge.
          const content = viewable(
            ready.content.filter((item) => item.kind !== "rewrite"),
            folded.current,
          );
          if (content.length && live)
            await api(`${path}/viewed`, { body: { content } });
          if (!ready.next_cursor) break;
          after = ready.next_cursor;
        }
      } catch {
        // A stale token or lost connection waits for the next change.
      }
    }, 1000);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [api, focused, path, version, unfolded]);
}

const NONE: ReadonlySet<number> = new Set();
