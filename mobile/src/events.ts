import EventSource from "react-native-sse";

export type StreamState = "connecting" | "open" | "reconnecting";

// What changed on the server: everything after a (re)connection, otherwise
// shared settings scopes and chat keys grouped by account.
export type ServerChange =
  | { kind: "refresh" }
  | { kind: "change"; shared: string[]; chats: Record<string, string[]> };

export type StreamHandlers = {
  onState: (state: StreamState) => void;
  onChange: (change: ServerChange) => void;
  onRevoked: () => void;
};

type ServerEvent = "refresh" | "change" | "revoked";

// Waits between attempts after a failure; the last one repeats.
export const RETRY_DELAYS = [5000, 10000, 20000, 40000, 60000];

// The service's change stream (ADR 37 source findings: refresh, change and
// revoked events with keepalives). The library reopens a stream that ended
// normally, but not one that failed on the network, so every failure here
// drops the source and opens a fresh one after a growing delay. A revoked
// device stops it for good.
export function openEvents(
  origin: string,
  token: string,
  handlers: StreamHandlers,
): () => void {
  let closed = false;
  let failures = 0;
  let source: EventSource<ServerEvent> | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const discard = () => {
    source?.removeAllEventListeners();
    source?.close();
    source = null;
  };
  const stop = () => {
    if (closed) return;
    closed = true;
    if (timer) clearTimeout(timer);
    discard();
  };
  const retry = () => {
    if (closed || timer) return;
    discard();
    handlers.onState("reconnecting");
    const delay = RETRY_DELAYS[Math.min(failures++, RETRY_DELAYS.length - 1)];
    timer = setTimeout(() => {
      timer = null;
      connect();
    }, delay);
  };
  const connect = () => {
    const current = new EventSource<ServerEvent>(`${origin}/api/events`, {
      headers: { Authorization: `Bearer ${token}` },
      pollingInterval: 5000,
    });
    source = current;
    current.addEventListener("open", () => {
      failures = 0;
      handlers.onState("open");
    });
    current.addEventListener("refresh", () => {
      handlers.onState("open");
      handlers.onChange({ kind: "refresh" });
    });
    current.addEventListener("change", (event) => {
      const change = parseChange(event.data);
      // An unreadable change can only be answered by rereading everything.
      handlers.onChange(change ?? { kind: "refresh" });
    });
    current.addEventListener("revoked", () => {
      stop();
      handlers.onRevoked();
    });
    current.addEventListener("error", (event) => {
      if (event.type === "error" && event.xhrStatus === 401) {
        stop();
        handlers.onRevoked();
        return;
      }
      retry();
    });
  };

  handlers.onState("connecting");
  connect();
  return stop;
}

const strings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");

function parseChange(data: string | null): ServerChange | null {
  let value: unknown;
  try {
    value = JSON.parse(data ?? "");
  } catch {
    return null;
  }
  if (!value || typeof value !== "object") return null;
  const { shared = [], chats = {} } = value as Record<string, unknown>;
  if (!strings(shared) || !chats || typeof chats !== "object") return null;
  const grouped: Record<string, string[]> = {};
  for (const [account, keys] of Object.entries(chats)) {
    if (!strings(keys)) return null;
    grouped[account] = keys;
  }
  return { kind: "change", shared, chats: grouped };
}
