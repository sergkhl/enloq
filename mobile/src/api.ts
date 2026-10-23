import type { components } from "./api-schema";

export type Schemas = components["schemas"];

// The server cannot be reached: no answer, or no network.
export class Unreachable extends Error {}
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

// Every request names its credential explicitly. The app sends no Origin and
// no cookies; the server accepts a bearer request without Origin (ADR 38).
export async function request<T>(
  origin: string,
  path: string,
  options: Options = {},
): Promise<T> {
  const post = options.body !== undefined;
  let response: Response;
  try {
    response = await fetch(`${origin}/api/${path}`, {
      method: post ? "POST" : "GET",
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
    if (options.signal?.aborted) throw error;
    throw new Unreachable("The server can't be reached.");
  }
  const data: unknown = await response.json().catch(() => null);
  if (response.status === 401 && options.token)
    throw new Revoked(detail(data) ?? "This device is no longer trusted.");
  // The edge answers 502 while the service restarts; a 503 with a reason is
  // the service's own refusal.
  if (response.status >= 500 && !(response.status === 503 && detail(data)))
    throw new Unreachable("The server isn't answering.");
  if (!response.ok)
    throw new Refused(
      response.status,
      detail(data) ?? "The server refused the request.",
    );
  return data as T;
}
