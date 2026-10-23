// The pairing link the server shows as text and as a QR code (ADR 37, ADR 38):
// `<origin>/?connect=<32 hex>`. Only the exact HTTPS origin a server prints is
// accepted; plain HTTP is for the offline fixture server on this machine.
export type PairingTarget = { origin: string; id: string };

const LINK =
  /^(https:\/\/[a-z0-9.-]+(?::\d{1,5})?|http:\/\/(?:127\.0\.0\.1|localhost)(?::\d{1,5})?)\/\?connect=([a-f0-9]{32})$/;

export function parsePairingLink(text: string): PairingTarget | null {
  const match = LINK.exec(text.trim());
  return match ? { origin: match[1]!, id: match[2]! } : null;
}

export function serverName(origin: string) {
  return origin.replace(/^https?:\/\//, "");
}

// A server the app connects to by its address (ADR 43 Q8, Q9): an exact
// HTTPS origin, or plain HTTP for the offline fixture on this machine.
const ORIGIN =
  /^(?:https:\/\/[a-z0-9.-]+(?::\d{1,5})?|http:\/\/(?:127\.0\.0\.1|localhost)(?::\d{1,5})?)$/;

export function serverOrigin(text: string): string | null {
  const origin = text.trim().replace(/\/+$/, "");
  return ORIGIN.test(origin) ? origin : null;
}

// Enloq Dev's one-tap test server: the authored fixture through adb's port
// reversal, which approves its own claims (ADR 43 Q9).
export const LOCAL_SERVER = "http://127.0.0.1:8776";
