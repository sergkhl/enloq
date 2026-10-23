import Constants from "expo-constants";
import { Refused, Unreachable, request, type Schemas } from "./api";
import { serverOrigin, type PairingTarget } from "./pairingLink";

// ADR 38 Q3: the app claims the pairing without cookies, shows the code, waits
// for the owner's approval and receives its own credential once.
const PROOF = "X-Pairing-Proof";

export function claimPairing(target: PairingTarget, name: string) {
  return request<Schemas["DeviceClaimStatus"]>(
    target.origin,
    `auth/pairings/${target.id}/device`,
    { body: { name } },
  );
}

export async function awaitApproval(
  target: PairingTarget,
  proof: string,
  { signal, interval = 1000 }: { signal?: AbortSignal; interval?: number } = {},
) {
  for (;;) {
    await delay(interval, signal);
    const status = await request<Schemas["PairingStatus"]>(
      target.origin,
      `auth/pairings/${target.id}`,
      { headers: { [PROOF]: proof }, signal },
    );
    if (status.state === "approved") return;
    if (status.state === "cancelled")
      throw new Refused(400, "Pairing was cancelled.");
    if (status.state === "expired")
      throw new Refused(400, "The pairing code expired. Start again.");
  }
}

export async function finishPairing(target: PairingTarget, proof: string) {
  const credential = await request<Schemas["DeviceCredential"]>(
    target.origin,
    `auth/pairings/${target.id}/device/complete`,
    { body: {}, headers: { [PROOF]: proof } },
  );
  return credential.token;
}

function delay(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(new Error("Pairing stopped"));
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new Error("Pairing stopped"));
      },
      { once: true },
    );
  });
}

// The release server this build connects to (ADR 43 Q8), set at build time.
export function releaseServer() {
  const extra = Constants.expoConfig?.extra as { server?: string } | undefined;
  return serverOrigin(extra?.server ?? "") ?? "https://enloq.globesoul.com";
}

// ADR 43 Q8: the server posts a code to the Owner Account's Saved Messages,
// which the owner types here; the proof binds every step to this phone.
export type CodeAttempt = Schemas["CodeAttempt"];

export function startCode(origin: string, name: string) {
  return request<CodeAttempt>(origin, "auth/code-pairings", {
    body: { name },
  });
}

export async function resendCode(origin: string, attempt: CodeAttempt) {
  const sent = await request<CodeAttempt>(
    origin,
    `auth/code-pairings/${attempt.id}/resend`,
    { body: {}, headers: { [PROOF]: attempt.proof } },
  );
  return { ...attempt, expires_at: sent.expires_at };
}

export async function enterCode(
  origin: string,
  attempt: CodeAttempt,
  code: string,
) {
  const credential = await request<Schemas["DeviceCredential"]>(
    origin,
    `auth/code-pairings/${attempt.id}/code`,
    { body: { code }, headers: { [PROOF]: attempt.proof } },
  );
  return credential.token;
}

// ADR 47 Q11 and ADR 48 Q11: anyone pairs by signing in with Telegram
// through the service's own login; the account Telegram verifies decides
// what pairs, and an unknown one reads the Member Agreement first. Connect
// answers 409 while the Owner Account is signed out. The proof binds every
// step to this phone.
export type SignInState = Schemas["SignInPairingState"];
export type SignInAttempt = { id: string; proof: string };

export function ownerSignedOut(error: unknown) {
  return error instanceof Refused && error.status === 409;
}

export function startSignIn(origin: string, phone: string, name: string) {
  return request<SignInState>(origin, "auth/sign-in-pairings", {
    body: { phone, name },
  });
}

export function signInStep(
  origin: string,
  attempt: SignInAttempt,
  action: "code" | "password" | "resend" | "agree" | "cancel",
  value = "",
) {
  return request<SignInState>(
    origin,
    `auth/sign-in-pairings/${attempt.id}/${action}`,
    {
      body:
        action === "code" || action === "password"
          ? { value }
          : action === "agree"
            ? { version: value }
            : {},
      headers: { [PROOF]: attempt.proof },
    },
  );
}

// Enloq Dev's Use the local test server (ADR 43 Q9): the fixture starts a
// pairing whose claim it approves itself.
export async function localPairing(origin: string): Promise<PairingTarget> {
  let response: Response;
  try {
    response = await fetch(`${origin}/fixture/pairing`, {
      headers: { Accept: "application/json" },
      credentials: "omit",
    });
  } catch {
    throw new Unreachable(
      "The local test server can't be reached. Start it and reverse its port.",
    );
  }
  const data = (await response.json().catch(() => null)) as {
    id?: unknown;
  } | null;
  if (!response.ok || typeof data?.id !== "string")
    throw new Refused(response.status, "The local test server refused.");
  return { origin, id: data.id };
}
