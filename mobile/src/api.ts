import { SERVER_ERROR } from "../../shared/serverErrors";
import type { components } from "./api-schema";
import { record } from "./logs";

export type Schemas = components["schemas"];

// Where a client's log goes (ADR 61), itself never logged.
export const LOG_PATH = "logs/client";

// The server cannot be reached: no answer, or no network.
export class Unreachable extends Error {}
// The server answered with an error of its own. Whatever was asked may or may
// not have happened, as with no answer, but the server is there.
export class ServerFailed extends Unreachable {}
// Whether the failure means the phone is offline, not one request's error.
export const offline = (error: unknown) =>
  error instanceof Unreachable && !(error instanceof ServerFailed);
// The server no longer trusts this device's credential.
export class Revoked extends Error {}
// The server answered and refused the request.
export class Refused extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type Options = {
  method?: "DELETE";
  token?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
};

function detail(data: unknown) {
  if (data && typeof data === "object" && "detail" in data) {
    const value = (data as { detail: unknown }).detail;
    if (typeof value === "string") return value;
  }
  return null;
}

// What a request's log line names: pairing routes name their attempts after
// the operation, as the service's own line leaves them out.
const logged = (path: string) =>
  path.startsWith("auth/") ? path.split("/").slice(0, 2).join("/") : path;

// Every request names its credential explicitly. The app sends no Origin and
// no cookies; the server accepts a bearer request without Origin (ADR 38).
export async function request<T>(
  origin: string,
  path: string,
  options: Options = {},
): Promise<T> {
  const post = options.body !== undefined;
  const method = options.method || (post ? "POST" : "GET");
  const started = Date.now();
  const note = (level: "info" | "warn" | "error", outcome: string) => {
    if (path !== LOG_PATH)
      record(
        level,
        "api",
        `${method} ${logged(path)} ${outcome} ${Date.now() - started} ms`,
      );
  };
  let response: Response;
  try {
    response = await fetch(`${origin}/api/${path}`, {
      method,
      headers: {
        Accept: "application/json",
        ...(post ? { "Content-Type": "application/json" } : {}),
        ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
        ...options.headers,
      },
      body: post ? JSON.stringify(options.body) : undefined,
      credentials: "omit",
      signal: options.signal,
    });
  } catch (error) {
    if (options.signal?.aborted) {
      note("info", "aborted");
      throw error;
    }
    note("warn", `no answer (${String(error)})`);
    throw new Unreachable("The server can't be reached.");
  }
  const data: unknown = await response.json().catch(() => null);
  const reason = detail(data);
  note(
    response.status >= 500 ? "error" : response.status >= 400 ? "warn" : "info",
    `${response.status}${reason ? `: ${reason}` : ""}`,
  );
  if (response.status === 401 && options.token)
    throw new Revoked(reason ?? "This device is no longer trusted.");
  if (response.status === 500) throw new ServerFailed(SERVER_ERROR);
  // The edge answers 502 while the service restarts; a 503 with a reason is
  // the service's own refusal.
  if (response.status >= 500 && !(response.status === 503 && reason))
    throw new Unreachable("The server isn't answering.");
  if (!response.ok)
    throw new Refused(
      response.status,
      reason ?? "The server refused the request.",
    );
  return data as T;
}
