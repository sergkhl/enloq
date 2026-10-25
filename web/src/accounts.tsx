import { ScreenFrame } from "./screenUi";
import React, { useEffect, useRef, useState } from "react";
import { Plus, LogOut, X } from "lucide-react";
import type { components } from "./api-schema";
import { AboutYou } from "./AboutYou";
import { Avatar } from "./Avatar";
import { CodeInput } from "./CodeInput";
import { servicePlace } from "./serviceHost";

export type Account = components["schemas"]["AccountSummary"];
export type Overview = components["schemas"]["Accounts"];
type Login = components["schemas"]["LoginState"];

export function clearAccountCache(mode: string, id: number) {
  if (sessionStorage.getItem("selectedChat")?.startsWith(`${id}/`))
    sessionStorage.removeItem("selectedChat");
  for (const storage of [sessionStorage, localStorage]) {
    for (const key of Object.keys(storage)) {
      if (
        key.startsWith(`reply:${mode}:${id}:`) ||
        key.startsWith(`draft:${id}/`) ||
        key.startsWith(`classification-review:${mode}:${id}:`) ||
        key.startsWith(`combined-review:${mode}:${id}:`)
      )
        storage.removeItem(key);
    }
  }
}

export function applyRemovalMarkers() {
  for (const key of Object.keys(localStorage)) {
    const match = /^reply:([^:]+):removed:(\d+)$/.exec(key);
    if (match && sessionStorage.getItem(key) !== localStorage.getItem(key)) {
      clearAccountCache(match[1], Number(match[2]));
      sessionStorage.setItem(key, localStorage.getItem(key)!);
    }
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function workspaceApi<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
  options?: { recoverableUnavailable?: readonly string[]; method?: "DELETE" },
): Promise<T> {
  const response = await fetch(
    `/api/${path}`,
    body === undefined
      ? { cache: "no-store", signal }
      : {
          method: options?.method || "POST",
          signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  ).catch((error) => {
    if (signal?.aborted) throw error;
    window.dispatchEvent(new Event("browser-service-unavailable"));
    throw new Error("Enloq is unavailable. Start the service and try again.");
  });
  if (response.status === 401)
    window.dispatchEvent(new Event("browser-auth-lost"));
  // A media endpoint may report a recoverable source/verification refusal as
  // 503 while the application is healthy. Only a caller's exact documented
  // details opt into local handling; other server/network failures stay global.
  const inspectUnavailable =
    response.status === 503 && !!options?.recoverableUnavailable?.length;
  if (response.status >= 500 && !inspectUnavailable)
    window.dispatchEvent(new Event("browser-service-unavailable"));
  const data = await response.json().catch((error) => {
    if (inspectUnavailable && !signal?.aborted)
      window.dispatchEvent(new Event("browser-service-unavailable"));
    throw error;
  });
  if (
    inspectUnavailable &&
    !options?.recoverableUnavailable?.includes(data?.detail)
  )
    window.dispatchEvent(new Event("browser-service-unavailable"));
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof data?.detail === "string"
        ? data.detail
        : "Please check this action and try again.",
    );
  return data;
}

export function Modal({
  title,
  children,
  onClose,
  busy = false,
  closeLabel = "Close account dialog",
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  busy?: boolean;
  closeLabel?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="account-dialog"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="account-dialog-heading">
        <h2>{title}</h2>
        <button aria-label={closeLabel} disabled={busy} onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function LoginDialog({
  expected,
  onClose,
  onAdded,
  offline,
}: {
  expected?: Account;
  onClose: () => void;
  onAdded: (id: number) => void;
  offline: boolean;
}) {
  const [method, setMethod] = useState<"qr" | "phone">("qr");
  const [flow, setFlow] = useState<Login | null>(null);
  const flowRef = useRef<Login | null>(null);
  const [phone, setPhone] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);
  const alive = useRef(true),
    completed = useRef(false);
  const generation = useRef(0);
  function update(value: Login) {
    flowRef.current = value;
    if (!alive.current) return;
    setFlow(value);
    if (value.stage === "complete" && value.account_id && !completed.current) {
      completed.current = true;
      onAdded(value.account_id);
    }
  }
  async function start(choice: "qr" | "phone") {
    generation.current += 1;
    setBusy(true);
    setError("");
    setSecret("");
    try {
      const value = await workspaceApi<Login>("logins", {
        method: choice,
        phone: choice === "phone" ? phone : "",
        expected_id: expected?.account_id,
      });
      flowRef.current = value;
      if (!alive.current) {
        await workspaceApi(`logins/${value.id}/cancel`, {});
        return;
      }
      update(value);
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  useEffect(() => {
    alive.current = true;
    const startup = window.setTimeout(() => void start("qr"), 0);
    const cancel = () => {
      const pending = flowRef.current;
      if (pending && pending.stage !== "complete")
        void fetch(`/api/logins/${pending.id}/cancel`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
          keepalive: true,
        });
    };
    window.addEventListener("pagehide", cancel);
    return () => {
      clearTimeout(startup);
      alive.current = false;
      window.removeEventListener("pagehide", cancel);
      cancel();
    };
  }, []);
  useEffect(() => {
    if (
      !flow ||
      ["complete", "cancelled", "expired", "error"].includes(flow.stage)
    )
      return;
    const timer = window.setInterval(() => {
      setNow(Date.now() / 1000);
      if (!busy) {
        const current = generation.current;
        void workspaceApi<Login>(`logins/${flow.id}`)
          .then((value) => {
            if (
              current === generation.current &&
              flowRef.current?.id === value.id
            )
              update(value);
          })
          .catch((e) => {
            if (alive.current) setError(e.message);
          });
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [flow?.id, flow?.stage, busy]);
  async function cancel() {
    generation.current += 1;
    if (flowRef.current && flowRef.current.stage !== "complete")
      await workspaceApi(`logins/${flowRef.current.id}/cancel`, {});
    flowRef.current = null;
  }
  async function close() {
    setBusy(true);
    try {
      await cancel();
      onClose();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  async function change(choice: "qr" | "phone") {
    setBusy(true);
    setError("");
    try {
      await cancel();
      setFlow(null);
      setSecret("");
      setMethod(choice);
      if (choice === "qr") await start(choice);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  // Telegram's code submits itself on its last digit when its length is
  // known (ADR 47 Q3); the two-step password only from Sign in.
  async function submit(action: string, value = secret) {
    if (!flow || busy) return;
    generation.current += 1;
    setBusy(true);
    setError("");
    setSecret("");
    try {
      update(
        await workspaceApi<Login>(
          `logins/${flow.id}/${action}`,
          action === "resend" ? {} : { value },
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const ended = flow && ["error", "expired", "cancelled"].includes(flow.stage);
  const seconds = Math.max(0, Math.ceil((flow?.retry_at ?? 0) - now));
  return (
    <ScreenFrame
      title={
        expected ? `Sign in again · ${expected.name}` : "Add Telegram account"
      }
      onBack={() => void (busy ? undefined : close())}
    >
      {offline && (
        <p className="synthetic-login-note">
          Synthetic login for this offline demo. Do not enter real credentials.
        </p>
      )}
      <div aria-live="polite" className="login-status">
        {flow?.message ||
          (method === "qr"
            ? "Preparing a private QR code…"
            : "Enter the phone number of an existing Telegram account.")}
      </div>
      {flow?.stage === "qr" && (
        <div className="qr-frame">
          <img
            width="260"
            height="260"
            alt="Scan this QR code with Telegram to log in"
            src={`/api/logins/${flow.id}/qr?v=${flow.qr_revision}`}
          />
        </div>
      )}
      {method === "phone" && (!flow || ended) && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void start("phone");
          }}
        >
          <label htmlFor="login-phone">Phone number</label>
          <input
            autoFocus
            id="login-phone"
            type="tel"
            autoComplete="off"
            placeholder="+44…"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            required
          />
          <button className="primary" disabled={busy || !phone.trim()}>
            Continue
          </button>
        </form>
      )}
      {(flow?.stage === "code" || flow?.stage === "password") && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit(flow.stage);
          }}
        >
          <label htmlFor="login-secret">
            {flow.stage === "password"
              ? "Telegram two-step password"
              : "Login code"}
          </label>
          {flow.stage === "code" ? (
            <CodeInput
              key="code"
              id="login-secret"
              value={secret}
              length={flow.code_length ?? null}
              disabled={busy}
              secure
              onChange={setSecret}
              onComplete={(value) => void submit("code", value)}
            />
          ) : (
            <input
              key="password"
              autoFocus
              id="login-secret"
              type="password"
              autoComplete="off"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              required
            />
          )}
          {flow.delivery && flow.stage === "code" && (
            <p className="small">
              Telegram selected: {flow.delivery.replaceAll("_", " ")}
            </p>
          )}
          <button className="primary" disabled={busy || !secret.trim()}>
            {busy ? "Checking…" : "Sign in"}
          </button>
          {flow.can_resend && (
            <button
              type="button"
              className="secondary"
              disabled={busy || seconds > 0}
              onClick={() => void submit("resend")}
            >
              {seconds ? `Resend available in ${seconds}s` : "Resend code"}
            </button>
          )}
        </form>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {method === "qr" && (ended || (error && !flow)) && (
        <button
          className="primary"
          disabled={busy}
          onClick={() => void start("qr")}
        >
          Try QR login again
        </button>
      )}
      <button
        className="secondary login-method"
        disabled={busy}
        onClick={() => void change(method === "qr" ? "phone" : "qr")}
      >
        {method === "qr" ? "Log in by phone number" : "Log in with QR code"}
      </button>
      <p className="small">
        Your other accounts stay connected. Each account has its own inbox and
        drafts.
      </p>
    </ScreenFrame>
  );
}

export function ManageAccounts({
  overview,
  onClose,
  onRefresh,
  onLogin,
}: {
  overview: Overview;
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onLogin: (account?: Account) => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<{
    account: Account;
    action: "logout" | "remove-local-data";
  } | null>(null);
  // About you (ADR 55 Q21): an account's own memory and traits.
  const [about, setAbout] = useState<Account | null>(null);
  async function perform() {
    if (!confirm) return;
    setBusy(true);
    setError("");
    try {
      await workspaceApi(
        `accounts/${confirm.account.account_id}/${confirm.action}`,
        { confirm: true },
      );
      if (confirm.action === "remove-local-data") {
        clearAccountCache(overview.mode, confirm.account.account_id);
        const marker = `reply:${overview.mode}:removed:${confirm.account.account_id}`;
        const value = String(Date.now());
        localStorage.setItem(marker, value);
        sessionStorage.setItem(marker, value);
      }
      setConfirm(null);
      await onRefresh();
    } catch (e) {
      setError((e as Error).message);
      await onRefresh();
    } finally {
      setBusy(false);
    }
  }
  if (about)
    return (
      <AboutYou
        account={about.account_id}
        name={about.name}
        onBack={() => setAbout(null)}
      />
    );
  return (
    <ScreenFrame title="Manage accounts" onBack={() => !busy && onClose()}>
      {confirm ? (
        <div className="account-confirm">
          <h3>
            {confirm.action === "logout"
              ? "Log out of"
              : "Remove local data for"}{" "}
            {confirm.account.name}?
          </h3>
          <p>
            {confirm.action === "logout"
              ? `Only this app will sign out. Your drafts and conversation cache stay on ${servicePlace()} under the normal retention policy, and return when you sign in again.`
              : `This deletes this account’s local conversation cache, unsent drafts and images from ${servicePlace()}. It does not delete Telegram messages. Budget records remain.`}
          </p>
          <div className="account-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setConfirm(null)}
            >
              Keep account
            </button>
            <button
              className="primary danger"
              disabled={busy}
              onClick={() => void perform()}
            >
              {busy
                ? "Working…"
                : confirm.action === "logout"
                  ? "Confirm logout"
                  : "Remove local data"}
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className="small">
            Each account has its own conversations and drafts. AI usage limits
            are shared.
          </p>
          <div className="managed-accounts">
            {overview.accounts.map((account) => (
              <article key={account.account_id} className="managed-account">
                <Avatar
                  account={account.account_id}
                  peer={`user:${account.account_id}`}
                  name={account.name}
                  photoId={
                    account.state === "authorized" ? account.photo_id : null
                  }
                />
                <div>
                  <h3>{account.name}</h3>
                  <p>
                    {account.username
                      ? `@${account.username}`
                      : account.phone || `Account ${account.account_id}`}
                  </p>
                  <small>{account.status}</small>
                </div>
                {/* Each signed-in account's AI processing (ADR 46 CU3). */}
                {account.state === "authorized" && (
                  <button
                    type="button"
                    role="switch"
                    className="switch-line account-ai"
                    aria-checked={account.ai_enabled}
                    aria-label={`AI processing for ${account.name}`}
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      setError("");
                      try {
                        await workspaceApi(
                          `accounts/${account.account_id}/processing`,
                          { ai_enabled: !account.ai_enabled },
                        );
                        await onRefresh();
                      } catch (e) {
                        setError((e as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <span>AI processing</span>
                    <span className="switch-knob" aria-hidden />
                  </button>
                )}
                <div className="account-actions">
                  {account.state === "authorized" && (
                    <button
                      className="secondary"
                      disabled={busy}
                      aria-label={`About you: ${account.name}`}
                      onClick={() => setAbout(account)}
                    >
                      About you
                    </button>
                  )}
                  {account.state === "authorized" && !account.connected && (
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={async () => {
                        setBusy(true);
                        setError("");
                        try {
                          await workspaceApi(
                            `accounts/${account.account_id}/reconnect`,
                            {},
                          );
                          await onRefresh();
                        } catch (e) {
                          setError((e as Error).message);
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Reconnect
                    </button>
                  )}
                  {account.state === "authorized" ||
                  account.state === "logout_pending" ? (
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => setConfirm({ account, action: "logout" })}
                    >
                      <LogOut size={15} />
                      {account.state === "logout_pending"
                        ? "Retry logout"
                        : "Log out"}
                    </button>
                  ) : (
                    <button
                      className="secondary"
                      disabled={!overview.login_available}
                      onClick={() => onLogin(account)}
                    >
                      Sign in again
                    </button>
                  )}
                  {account.state === "requires_login" && (
                    <button
                      className="secondary"
                      onClick={() => setConfirm({ account, action: "logout" })}
                    >
                      Log out
                    </button>
                  )}
                  {account.state === "logged_out" && (
                    <button
                      className="text-danger"
                      onClick={() =>
                        setConfirm({ account, action: "remove-local-data" })
                      }
                    >
                      Remove local data
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
          <button
            className="primary"
            disabled={!overview.login_available}
            onClick={() => onLogin()}
          >
            <Plus size={17} />
            Add account
          </button>
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </ScreenFrame>
  );
}
