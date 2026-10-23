import React, { useCallback, useEffect, useRef, useState } from "react";
import { MessageCircle } from "lucide-react";
import { workspaceApi, ApiError } from "./accounts";
import type { components } from "./api-schema";
import { CodeInput } from "./CodeInput";
import { rewriteQuery } from "./routeHistory";
import { onServer, servicePlace } from "./serviceHost";

type Status = components["schemas"]["BrowserStatus"];
type Pairing = components["schemas"]["PairingStatus"];
type Me = components["schemas"]["Me"];
type AgreementText = components["schemas"]["Agreement"];
// "waiting": a Join Request's browser, until the owner decides (ADR 48 Q19).
type Access = "checking" | "trusted" | "waiting" | "connect" | "unavailable";

const DECLINED =
  "Your request to join wasn't approved, or it expired. You can ask again.";
// How often a waiting browser asks whether the owner decided.
const WAITING_POLL = 5000;

export function browserAccessLost() {
  window.dispatchEvent(new Event("browser-auth-lost"));
}

type CodeAttempt = components["schemas"]["CodeAttempt"];
const PROOF = "X-Pairing-Proof";

// The code routes take the attempt's proof in a header (ADR 43 Q8).
async function codeApi<T>(path: string, body: unknown, proof = ""): Promise<T> {
  const response = await fetch(`/api/auth/${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(proof ? { [PROOF]: proof } : {}),
    },
    body: JSON.stringify(body),
  }).catch(() => {
    throw new Error("Enloq is unavailable. Try again in a moment.");
  });
  const data = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof data?.detail === "string" ? data.detail : "Please try again.",
    );
  return data as T;
}

// Pairing with the code the service posts to the Owner Account's Saved
// Messages, as the app pairs (ADR 44 Q10, MW4): the code sets this browser's
// trust cookie, so its credential never reaches the page. The code submits
// itself on its last digit (ADR 47 Q3), and while the Owner Account is signed
// out Connect turns into Sign in with Telegram (Q11, Q13).
function CodePairing({ onPaired }: { onPaired: () => Promise<void> }) {
  const host = window.location.host;
  const [attempt, setAttempt] = useState<CodeAttempt | null>(null);
  const [signIn, setSignIn] = useState(false);
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setNote("");
    try {
      await action();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setSignIn(true);
        return;
      }
      const message = (e as Error).message;
      setNote(message);
      // An attempt that ended starts again from the button.
      if (/Start again|ended/.test(message)) setAttempt(null);
    } finally {
      setBusy(false);
    }
  }
  function submit(value: string) {
    if (!attempt) return;
    void run(async () => {
      setCode("");
      await codeApi(
        `code-pairings/${attempt.id}/browser`,
        { code: value },
        attempt.proof,
      );
      await onPaired();
    });
  }
  if (signIn)
    return (
      <SignInPairing
        onPaired={onPaired}
        onCancel={() => {
          setSignIn(false);
          setNote("");
        }}
      />
    );
  if (!attempt)
    return (
      <>
        <p>Sign in with your Telegram account to use Enloq on {host}.</p>
        <button
          className="primary"
          disabled={busy}
          onClick={() => {
            setNote("");
            setSignIn(true);
          }}
        >
          Sign in with Telegram
        </button>
        <p className="small">
          If you own {host}, it can post a pairing code to your Saved Messages
          in Telegram instead.
        </p>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              setCode("");
              setAttempt(
                await codeApi<CodeAttempt>("code-pairings", { browser: true }),
              );
            })
          }
        >
          Connect to {host}
        </button>
        {note && (
          <div className="error" role="alert">
            {note}
          </div>
        )}
      </>
    );
  return (
    <>
      <p>
        Enter the code sent to your Saved Messages in Telegram. It expires after
        two minutes.
      </p>
      <CodeInput
        className="pairing-code-input"
        label="Pairing code"
        value={code}
        length={6}
        disabled={busy}
        onChange={setCode}
        onComplete={submit}
      />
      {note && (
        <div className="error" role="alert">
          {note}
        </div>
      )}
      <button
        className="primary"
        disabled={busy || code.length !== 6}
        onClick={() => submit(code)}
      >
        Pair
      </button>
      <button
        className="secondary"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            const sent = await codeApi<CodeAttempt>(
              `code-pairings/${attempt.id}/resend`,
              {},
              attempt.proof,
            );
            setAttempt({ ...attempt, expires_at: sent.expires_at });
            setNote("A new code is in your Saved Messages.");
          })
        }
      >
        Send a new code
      </button>
      <button
        className="secondary"
        disabled={busy}
        onClick={() => {
          setAttempt(null);
          setNote("");
        }}
      >
        Cancel
      </button>
    </>
  );
}

type Login = components["schemas"]["LoginState"];
type SignInState = components["schemas"]["SignInPairingState"];

// The Member Agreement (ADR 48 Q3), in the words the server serves.
function AgreementView({
  agreement,
  action,
  busy,
  onAgree,
}: {
  agreement: AgreementText;
  action: string;
  busy: boolean;
  onAgree: () => void;
}) {
  return (
    <section className="member-agreement" aria-label="Member agreement">
      <h2>{agreement.title}</h2>
      {agreement.paragraphs.map((paragraph, index) => (
        <p key={index}>{paragraph}</p>
      ))}
      <button
        type="button"
        className="primary"
        disabled={busy}
        onClick={onAgree}
      >
        {action}
      </button>
    </section>
  );
}

// Sign in with Telegram (ADR 47 Q11, ADR 48 Q11, Q12), as the app's
// (mobile/src/SignInPairing.tsx): this browser pairs by signing in with its
// number, the code Telegram sends and the two-step password. The owner's
// account or a member's first account pairs it to them; an account the
// server doesn't know reads the Member Agreement and asks to join, and the
// browser then waits for the owner. The code submits itself on its last
// digit (ADR 47 Q3).
function SignInPairing({
  onPaired,
  onCancel,
}: {
  onPaired: () => Promise<void>;
  onCancel: () => void;
}) {
  const host = window.location.host;
  const [phone, setPhone] = useState("");
  const [attempt, setAttempt] = useState<{ id: string; proof: string } | null>(
    null,
  );
  const [login, setLogin] = useState<Login | null>(null);
  const [agreement, setAgreement] = useState<AgreementText | null>(null);
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    if (!login?.retry_at) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [login?.retry_at]);
  async function begin() {
    if (busy || !phone.trim()) return;
    setBusy(true);
    setNote("");
    try {
      const state = await codeApi<SignInState>("sign-in-pairings", {
        phone: phone.trim(),
        browser: true,
      });
      setAttempt({ id: state.id, proof: state.proof });
      setLogin(state.login);
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function submit(
    action: "code" | "password" | "resend" | "agree",
    value = "",
  ) {
    if (busy || !attempt) return;
    setBusy(true);
    setNote("");
    setSecret("");
    try {
      const state = await codeApi<SignInState>(
        `sign-in-pairings/${attempt.id}/${action}`,
        action === "resend"
          ? {}
          : action === "agree"
            ? { version: value }
            : { value },
        attempt.proof,
      );
      if (state.authenticated) {
        await onPaired();
        return;
      }
      setLogin(state.login);
      setAgreement(state.agreement ?? null);
      if (action === "resend") setNote("Telegram sent a new code.");
    } catch (e) {
      const message = (e as Error).message;
      setNote(message);
      // An attempt that ended starts again from the number.
      if (/ended|Start again|start a new login|expired/i.test(message)) {
        setAttempt(null);
        setLogin(null);
        setAgreement(null);
      }
    } finally {
      setBusy(false);
    }
  }
  function cancel() {
    if (attempt)
      void codeApi(
        `sign-in-pairings/${attempt.id}/cancel`,
        {},
        attempt.proof,
      ).catch(() => undefined);
    onCancel();
  }
  const wait = Math.max(0, Math.ceil((login?.retry_at ?? 0) - now));
  return (
    <form
      className="sign-in-pairing"
      onSubmit={(event) => {
        event.preventDefault();
        if (!login) void begin();
        else if (login.stage === "password") void submit("password", secret);
        else if (secret) void submit("code", secret);
      }}
    >
      {agreement && login?.stage === "agreement" ? (
        <AgreementView
          agreement={agreement}
          action={busy ? "Asking…" : "Agree and ask to join"}
          busy={busy}
          onAgree={() => void submit("agree", agreement.version)}
        />
      ) : !login ? (
        <>
          <p>
            Sign in with your Telegram account to use Enloq on {host}. If you’re
            new here, you’ll read what joining means and ask the server’s owner.
          </p>
          <input
            aria-label="Phone number"
            autoFocus
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            placeholder="+44…"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
          />
          <button className="primary" disabled={busy || !phone.trim()}>
            {busy ? "Asking Telegram…" : "Continue"}
          </button>
        </>
      ) : login.stage === "password" ? (
        <>
          <p>{login.message}</p>
          <input
            key="password"
            aria-label="Telegram two-step password"
            autoFocus
            type="password"
            autoComplete="off"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
          />
          <button className="primary" disabled={busy || !secret}>
            {busy ? "Checking…" : "Sign in"}
          </button>
        </>
      ) : (
        <>
          <p>{login.message}</p>
          <CodeInput
            label="Login code"
            value={secret}
            length={login.code_length ?? null}
            disabled={busy}
            secure
            onChange={setSecret}
            onComplete={(value) => void submit("code", value)}
          />
          <button className="primary" disabled={busy || !secret}>
            {busy ? "Checking…" : "Sign in"}
          </button>
          {login.can_resend && (
            <button
              type="button"
              className="secondary"
              disabled={busy || wait > 0}
              onClick={() => void submit("resend")}
            >
              {wait ? `Resend available in ${wait}s` : "Resend code"}
            </button>
          )}
        </>
      )}
      {note && (
        <div className="error" role="alert">
          {note}
        </div>
      )}
      <button type="button" className="secondary" onClick={cancel}>
        Cancel
      </button>
    </form>
  );
}

export function BrowserGate({ children }: { children: React.ReactNode }) {
  const [access, setAccess] = useState<Access>("checking");
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [error, setError] = useState("");
  const initial = useRef<Promise<{
    status: Status;
    pairing: Pairing | null;
  }> | null>(null);
  const completion = useRef<Promise<Status> | null>(null);
  const revision = useRef(0);
  // Only the consumed pairing locator; route input and history stay (ADR 36).
  const clearLocator = () => rewriteQuery((query) => query.delete("connect"));
  const failed = useCallback((e: unknown) => {
    setError(e instanceof Error ? e.message : "Please try again.");
    setAccess(
      e instanceof ApiError && e.status < 500 ? "connect" : "unavailable",
    );
  }, []);
  const check = useCallback(async () => {
    const current = ++revision.current;
    try {
      const status = await workspaceApi<Status>("auth/session");
      if (current !== revision.current) return;
      setAccess(
        status.authenticated
          ? status.waiting
            ? "waiting"
            : "trusted"
          : "connect",
      );
      setError("");
    } catch (e) {
      if (current === revision.current) failed(e);
    }
  }, [failed]);

  useEffect(() => {
    let live = true;
    const current = revision.current;
    // One request chain also under React's development effect replay.
    initial.current ??= (async () => {
      const status = await workspaceApi<Status>("auth/session");
      const id = new URLSearchParams(window.location.search).get("connect");
      let pairing: Pairing | null = null;
      if (id && /^[a-f0-9]{32}$/.test(id)) {
        try {
          pairing = await workspaceApi<Pairing>(
            `auth/pairings/${id}/claim`,
            {},
          );
        } catch (e) {
          if (!status.authenticated) throw e;
        }
      }
      return { status, pairing };
    })();
    void initial.current
      .then(({ status, pairing }) => {
        if (!live || revision.current !== current) return;
        setPairing(pairing);
        setAccess(
          status.authenticated
            ? status.waiting
              ? "waiting"
              : "trusted"
            : "connect",
        );
        if (status.authenticated) clearLocator();
      })
      .catch((e) => {
        if (live && revision.current === current) failed(e);
      });
    return () => {
      live = false;
    };
  }, [failed]);

  useEffect(() => {
    if (
      !pairing ||
      access === "trusted" ||
      ["cancelled", "expired", "complete"].includes(pairing.state)
    )
      return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await workspaceApi<Pairing>(`auth/pairings/${pairing.id}`);
        if (!live) return;
        if (next.state === "approved") {
          completion.current ??= workspaceApi<Status>(
            `auth/pairings/${pairing.id}/complete`,
            {},
          );
          await completion.current;
          if (!live) return;
          clearLocator();
          setPairing(null);
          await check();
          return;
        }
        if (["expired", "cancelled"].includes(next.state)) {
          setPairing(next);
          return;
        }
        timer = setTimeout(() => void poll(), 1000);
      } catch (e) {
        if (live) {
          setPairing(null);
          failed(e);
        }
      }
    };
    void poll();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [pairing, access, check, failed]);

  useEffect(() => {
    const lost = () => {
      revision.current++;
      setPairing(null);
      setError("");
      setAccess("connect");
    };
    const unavailable = () => {
      revision.current++;
      setAccess("unavailable");
    };
    window.addEventListener("browser-auth-lost", lost);
    window.addEventListener("browser-service-unavailable", unavailable);
    return () => {
      window.removeEventListener("browser-auth-lost", lost);
      window.removeEventListener("browser-service-unavailable", unavailable);
    };
  }, []);

  // A Join Request's browser asks until the owner decides (ADR 48 Q19).
  useEffect(() => {
    if (access !== "waiting") return;
    let live = true;
    const timer = setInterval(() => {
      void workspaceApi<Status>("auth/session")
        .then((status) => {
          if (!live) return;
          if (!status.authenticated) {
            setError(DECLINED);
            setAccess("connect");
          } else if (!status.waiting) setAccess("trusted");
        })
        .catch(() => undefined);
    }, WAITING_POLL);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [access]);

  async function withdraw() {
    try {
      await workspaceApi("auth/disconnect", {});
    } catch {
      // Already gone; the screen returns to connecting either way.
    }
    revision.current++;
    setError("You withdrew your request to join.");
    setAccess("connect");
  }

  useEffect(() => {
    if (access !== "trusted") return;
    // Renew on visits/focus and daily while open. Event streams detect revocation.
    const focused = () => {
      if (!document.hidden) void check();
    };
    const timer = setInterval(() => void check(), 24 * 3600 * 1000);
    window.addEventListener("focus", focused);
    document.addEventListener("visibilitychange", focused);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", focused);
      document.removeEventListener("visibilitychange", focused);
    };
  }, [access, check]);

  if (access === "trusted") return <AgreementGate>{children}</AgreementGate>;
  return (
    <main className="unlock browser-connection">
      <div className="brand-mark">
        <MessageCircle size={28} />
      </div>
      <p className="eyebrow">ENLOQ</p>
      {access === "checking" ? (
        <h1>Checking connection…</h1>
      ) : access === "waiting" ? (
        <>
          <h1>Waiting for approval</h1>
          <p>You asked to join Enloq on {window.location.host}.</p>
          <p>
            The server’s owner gets a notification and decides in their
            Settings. This page moves on by itself once they approve, and
            nothing of yours is processed until then.
          </p>
          <button className="secondary" onClick={() => void withdraw()}>
            Withdraw request
          </button>
        </>
      ) : access === "unavailable" ? (
        <>
          <h1>Enloq isn’t running</h1>
          {onServer() ? (
            <p>The server isn’t answering. Try again in a moment.</p>
          ) : (
            <>
              <p>Start the service on this Mac, then try again.</p>
              <code>./scripts/service start</code>
            </>
          )}
          <button className="primary" onClick={() => void check()}>
            Retry
          </button>
        </>
      ) : (
        <>
          <h1>Pair this browser</h1>
          {pairing?.state === "pending" || pairing?.state === "approved" ? (
            <>
              {/* Only the Mac launcher pairs a browser by link: a server
                  with a public origin takes none, and a trusted client's QR
                  code pairs the Enloq app (ADR 47 Q1, Q10). */}
              <p>
                Match this code in the Mac confirmation dialog, then choose{" "}
                <strong>Trust browser</strong>.
              </p>
              <output
                className="pairing-code"
                aria-label="Browser matching code"
              >
                {pairing.code}
              </output>
              <p className="small">
                This connection attempt expires after two minutes.
              </p>
            </>
          ) : (
            <>
              {(pairing?.state === "cancelled" ||
                pairing?.state === "expired") && (
                <p>
                  {pairing.state === "cancelled"
                    ? "Connection cancelled."
                    : "Connection expired."}
                </p>
              )}
              <CodePairing onPaired={check} />
              {onServer() ? null : (
                <p className="small">
                  Or open Enloq from the Mac launcher with{" "}
                  <code>./scripts/open</code> to approve this browser once.
                </p>
              )}
              <button className="secondary" onClick={() => void check()}>
                Check connection
              </button>
            </>
          )}
          <p className="small">
            Your connected accounts and saved drafts stay on {servicePlace()}.
          </p>
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
        </>
      )}
    </main>
  );
}

// A changed Member Agreement (ADR 48 Q3) covers the app until the member
// accepts it; their accounts' new AI work waits meanwhile. The owner never
// sees it.
function AgreementGate({ children }: { children: React.ReactNode }) {
  const [agreement, setAgreement] = useState<AgreementText | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => {
    let live = true;
    void workspaceApi<Me>("members/me")
      .then((me) => {
        if (live) setAgreement(me.agreement ?? null);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);
  if (!agreement) return <>{children}</>;
  return (
    <main className="unlock browser-connection">
      <p className="eyebrow">ENLOQ</p>
      <h1>The agreement changed</h1>
      <p>Read it again to keep AI on for your accounts.</p>
      <AgreementView
        agreement={agreement}
        action={busy ? "Saving…" : "Agree"}
        busy={busy}
        onAgree={() => {
          setBusy(true);
          setNote("");
          void workspaceApi<Me>("members/me/agreement", {
            version: agreement.version,
          })
            .then((me) => setAgreement(me.agreement ?? null))
            .catch((e) => setNote((e as Error).message))
            .finally(() => setBusy(false));
        }}
      />
      {note && (
        <div className="error" role="alert">
          {note}
        </div>
      )}
    </main>
  );
}
