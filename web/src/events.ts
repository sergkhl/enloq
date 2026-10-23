import { useEffect, useRef } from "react";

// Change notifications from /api/events. `all` marks the first message of a
// (re)connected stream, when anything may have changed while disconnected.
export type Change = {
  all: boolean;
  shared: string[];
  chats: Record<string, string[]>;
};
type Listener = (change: Change) => void;

const listeners = new Set<Listener>();
let source: EventSource | null = null;

function connect() {
  if (source) return;
  source = new EventSource("/api/events");
  source.addEventListener("refresh", () =>
    emit({ all: true, shared: [], chats: {} }),
  );
  source.addEventListener("change", (event) => {
    const data = JSON.parse((event as MessageEvent).data);
    emit({ all: false, shared: data.shared || [], chats: data.chats || {} });
  });
  source.addEventListener("revoked", () => {
    source?.close();
    source = null;
    window.dispatchEvent(new Event("browser-auth-lost"));
  });
}

function emit(change: Change) {
  for (const listener of listeners) listener(change);
}

/** Runs `listener` on each change; `fallback` ms adds a slow safety refresh. */
export function useChanges(listener: Listener, fallback?: number) {
  const current = useRef(listener);
  current.current = listener;
  useEffect(() => {
    const wrapped: Listener = (change) => current.current(change);
    listeners.add(wrapped);
    connect();
    const timer = fallback
      ? setInterval(
          () => current.current({ all: true, shared: [], chats: {} }),
          fallback,
        )
      : undefined;
    return () => {
      listeners.delete(wrapped);
      if (timer) clearInterval(timer);
      if (!listeners.size) {
        source?.close();
        source = null;
      }
    };
  }, [fallback]);
}

export function touchesChat(change: Change, account: number, key?: string) {
  if (change.all) return true;
  const scopes = change.chats[String(account)] || [];
  return key ? scopes.includes(key) : scopes.length > 0;
}
