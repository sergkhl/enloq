// ADR 61, as the app has it (mobile/src/logs.ts): what this browser saw, for
// Settings → Logs and the service's log. Requests and their answers stay here;
// errors and warnings go to the service as they happen, and Send to server
// sends the rest. A browser keeps nothing of it beyond the open tab.
export type LogLevel = "info" | "warn" | "error";
export type LogEntry = {
  time: number;
  level: LogLevel;
  source: string;
  message: string;
  stack?: string;
};
type Kept = LogEntry & { sent?: boolean };

const KEPT = 500;
// The service's bounds on one post (backend/src/enloq/api/logs.py).
const LONGEST = 20000;
const BATCH = 200;
const BUILD = 200;
// After a failed send, the next automatic one waits this long.
const RETRY = 30_000;

let entries: Kept[] = [];
let sending = false;
let failedAt = 0;
const listeners = new Set<() => void>();

function changed() {
  for (const listener of listeners) listener();
}

export function record(
  level: LogLevel,
  source: string,
  message: string,
  stack?: string | null,
) {
  entries.push({
    time: Date.now(),
    level,
    source,
    message: message.slice(0, LONGEST),
    ...(stack ? { stack: stack.slice(0, LONGEST) } : {}),
  });
  if (entries.length > KEPT) entries = entries.slice(-KEPT);
  changed();
  if (level !== "info") void sendLogs();
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
export const logEntries = (): LogEntry[] => [...entries].reverse();

export function subscribeLogs(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// What a request's line names: pairing routes name their attempts after the
// operation, as the service's own line leaves them out.
export const loggedPath = (path: string) =>
  path.startsWith("auth/") ? path.split("/").slice(0, 2).join("/") : path;

// Sends what has not gone yet: warnings and errors, or with `all` everything.
export async function sendLogs(all = false) {
  if (sending) return false;
  if (!all && Date.now() - failedAt < RETRY) return false;
  sending = true;
  try {
    for (;;) {
      const batch = entries
        .filter((entry) => !entry.sent && (all || entry.level !== "info"))
        .slice(0, BATCH);
      if (!batch.length) return true;
      const response = await fetch("/api/logs/client", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          build: navigator.userAgent.slice(0, BUILD),
          entries: batch.map(({ sent: _sent, ...entry }) => entry),
        }),
      });
      if (!response.ok) throw new Error(String(response.status));
      for (const entry of batch) entry.sent = true;
      changed();
    }
  } catch {
    failedAt = Date.now();
    return false;
  } finally {
    sending = false;
  }
}

export function installLogging() {
  window.addEventListener("error", (event) =>
    recordError("uncaught", event.error ?? event.message),
  );
  window.addEventListener("unhandledrejection", (event) =>
    recordError("promise", event.reason),
  );
}
