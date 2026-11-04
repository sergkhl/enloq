import { Alert, AppState } from "react-native";
import { thisBuild } from "./build";
import { readCachedSync, writeCached } from "./cache";

// ADR 61: what this phone saw, for Settings → Logs and the service's log.
// Requests, their answers and any error stay here; errors and warnings go to
// the service as they happen, or once it answers again, and Send to server
// sends the rest. A crash's line is on disk before anything else can fail, and
// the next start sends it. The log leaves with the phone's other copies when
// it is forgotten.
export type LogLevel = "info" | "warn" | "error";
export type LogEntry = {
  time: number;
  level: LogLevel;
  source: string;
  message: string;
  stack?: string;
};
type Kept = LogEntry & { sent?: boolean };
export type LogPost = { build: string; entries: LogEntry[] };

const KEEP = "logs";
const KEPT = 500;
// The service's bounds on one post (backend/src/enloq/api/logs.py).
const LONGEST = 20000;
const BATCH = 200;
// After a failed send, the next automatic one waits this long.
const RETRY = 30_000;

let entries: Kept[] = [];
let loaded = false;
let sender: ((post: LogPost) => Promise<unknown>) | null = null;
let sending = false;
let failedAt = 0;
let alerted = 0;
const listeners = new Set<() => void>();

// A headless push task may log before anything else reads the file, so the
// first entry reads it: a write must not lose what an earlier run kept.
function load() {
  if (loaded) return;
  loaded = true;
  const kept = readCachedSync<Kept[]>(KEEP);
  if (kept?.length) entries = [...kept, ...entries].slice(-KEPT);
}

function persist() {
  writeCached(KEEP, entries);
}

function changed() {
  for (const listener of listeners) listener();
}

export function record(
  level: LogLevel,
  source: string,
  message: string,
  stack?: string | null,
) {
  load();
  entries.push({
    time: Date.now(),
    level,
    source,
    message: message.slice(0, LONGEST),
    ...(stack ? { stack: stack.slice(0, LONGEST) } : {}),
  });
  if (entries.length > KEPT) entries = entries.slice(-KEPT);
  changed();
  if (level !== "info") {
    persist();
    void sendLogs();
  }
}

export function recordError(source: string, error: unknown, stack?: string) {
  const known = error instanceof Error;
  record(
    "error",
    source,
    known ? `${error.name}: ${error.message}` : String(error),
    [known ? error.stack : undefined, stack].filter(Boolean).join("\n"),
  );
}

// Newest first, as Settings → Logs shows them.
export function logEntries(): LogEntry[] {
  load();
  return [...entries].reverse();
}

export function subscribeLogs(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setLogSender(send: typeof sender) {
  sender = send;
  failedAt = 0;
  if (send) void sendLogs();
}

export const hasUnsentLogs = () => {
  load();
  return entries.some((entry) => !entry.sent && entry.level !== "info");
};

// Sends what has not gone yet: warnings and errors, or with `all` everything.
export async function sendLogs(all = false) {
  load();
  if (!sender || sending) return false;
  if (!all && Date.now() - failedAt < RETRY) return false;
  sending = true;
  try {
    for (;;) {
      const batch = entries
        .filter((entry) => !entry.sent && (all || entry.level !== "info"))
        .slice(0, BATCH);
      if (!batch.length) return true;
      await sender({
        build: thisBuild(),
        entries: batch.map(({ sent: _sent, ...entry }) => entry),
      });
      for (const entry of batch) entry.sent = true;
      persist();
      changed();
    }
  } catch {
    failedAt = Date.now();
    return false;
  } finally {
    sending = false;
  }
}

// The phone was forgotten: its log goes with its other copies, whose folder
// the caller clears.
export function forgetLogs() {
  entries = [];
  loaded = true;
  failedAt = 0;
  changed();
}

// An error nothing caught outside a screen's render, such as in a press or a
// timer: logged and shown. Left to React Native, a release build would close.
function uncaught(error: unknown, fatal?: boolean) {
  recordError(fatal ? "fatal" : "uncaught", error);
  if (Date.now() - alerted < 10_000) return;
  alerted = Date.now();
  Alert.alert(
    "Something went wrong",
    `${error instanceof Error ? error.message : String(error)}\n\nIt's in Settings → Logs.`,
  );
}

type Handler = (error: unknown, fatal?: boolean) => void;
type Hermes = {
  enablePromiseRejectionTracker?: (options: {
    allRejections: boolean;
    onUnhandled: (id: number, rejection: unknown) => void;
    onHandled: (id: number) => void;
  }) => void;
};

// Once, as the app starts. A development build keeps React Native's own
// red screen as well.
export function installLogging() {
  const global = globalThis as unknown as {
    ErrorUtils?: {
      getGlobalHandler: () => Handler;
      setGlobalHandler: (handler: Handler) => void;
    };
    HermesInternal?: Hermes;
  };
  const previous = global.ErrorUtils?.getGlobalHandler();
  global.ErrorUtils?.setGlobalHandler((error, fatal) => {
    if (__DEV__) {
      recordError(fatal ? "fatal" : "uncaught", error);
      previous?.(error, fatal);
    } else uncaught(error, fatal);
  });
  if (!__DEV__)
    global.HermesInternal?.enablePromiseRejectionTracker?.({
      allRejections: true,
      onUnhandled: (_id, rejection) =>
        record(
          "warn",
          "promise",
          rejection instanceof Error
            ? `${rejection.name}: ${rejection.message}`
            : String(rejection),
          rejection instanceof Error ? rejection.stack : undefined,
        ),
      onHandled: () => undefined,
    });
  AppState.addEventListener("change", (state) => {
    if (state !== "active" && loaded) persist();
  });
}
