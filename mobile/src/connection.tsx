import * as SecureStore from "expo-secure-store";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AppState } from "react-native";
import { Revoked, Unreachable, request, type Schemas } from "./api";
import { clearCached, readCached, writeCached } from "./cache";
import {
  clearCredential,
  loadCredential,
  saveCredential,
  type Credential,
} from "./credential";
import { openEvents, type ServerChange, type StreamState } from "./events";
import { serverName } from "./pairingLink";
import { dropRegistration } from "./pushTask";

export type Connection =
  | { status: "loading" }
  | { status: "unpaired"; notice: string }
  // A Join Request (ADR 48 Q19): paired, but waiting for the owner's approval.
  | { status: "waiting"; credential: Credential }
  | {
      status: "paired";
      credential: Credential;
      stream: StreamState;
      accounts: Schemas["Accounts"] | null;
      // The last read failed to reach the server; what was loaded stays shown
      // read-only (ADR 38 Q20).
      unreachable: boolean;
    };

type Request = <T>(
  path: string,
  options?: {
    body?: unknown;
    signal?: AbortSignal;
    // Keeps the answer under this key for reading offline (Q20).
    keep?: string;
  },
) => Promise<T>;

type Value = {
  connection: Connection;
  // Where the phone stands once its new credential is checked: listing its
  // accounts, waiting for the owner's approval (ADR 48), or unpaired again.
  paired: (credential: Credential) => Promise<Connection["status"]>;
  refresh: () => Promise<void>;
  // The signed-in account this phone shows, or null while there is none.
  account: number | null;
  selectAccount: (account: number) => void;
  // A request with this phone's credential; a revoked one unpairs the app.
  api: Request;
  // Screens reread what they show when the server reports a change.
  subscribe: (listener: (change: ServerChange) => void) => () => void;
  // Leaves the server (ADR 43 Q2): it revokes this phone's credential, and the
  // phone forgets it, its push registration and its offline copy.
  disconnect: () => Promise<void>;
  // A waiting request's withdrawal (ADR 48 Q19).
  withdraw: () => Promise<void>;
  // A changed Member Agreement this member hasn't accepted yet (ADR 48 Q3).
  agreement: Schemas["Agreement"] | null;
  acceptAgreement: () => Promise<void>;
  // The owner sees People and the AI settings; a member their own budgets
  // and Leave (ADR 48). Null until the server says, or while offline.
  role: "owner" | "member" | null;
  // A member leaves (ADR 48 Q9); false when Telegram didn't confirm every
  // sign-out.
  leave: () => Promise<boolean>;
};

const Context = createContext<Value | null>(null);

export const REVOKED_NOTICE = "This phone is no longer trusted. Pair it again.";
export const DECLINED_NOTICE =
  "Your request to join wasn't approved, or it expired. You can ask again.";
// How often a waiting phone asks whether the owner decided.
const WAITING_POLL = 5000;

// The phone's own Remembered Account (ADR 24 keeps it per client).
const ACCOUNT_KEY = "enloq.account";

export function ConnectionProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [connection, setConnection] = useState<Connection>({
    status: "loading",
  });
  const [remembered, setRemembered] = useState<number | null>(null);
  const [agreement, setAgreement] = useState<Schemas["Agreement"] | null>(null);
  const [role, setRole] = useState<"owner" | "member" | null>(null);
  const current = useRef<Credential | null>(null);
  const waitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // A waiting phone reads nothing but its own member until the owner decides.
  const waiting = useRef(false);
  const stopStream = useRef<(() => void) | null>(null);
  const stream = useRef<StreamState>("connecting");
  const listeners = useRef(new Set<(change: ServerChange) => void>());
  // The last read failed on the network; the next answer reopens the stream.
  const offline = useRef(false);
  const relisten = useRef<() => void>(() => undefined);

  const update = useCallback(
    (
      change: (state: Extract<Connection, { status: "paired" }>) => Connection,
    ) =>
      setConnection((state) =>
        state.status === "paired" ? change(state) : state,
      ),
    [],
  );

  const forget = useCallback(async (notice = REVOKED_NOTICE) => {
    stopStream.current?.();
    stopStream.current = null;
    if (waitTimer.current) clearTimeout(waitTimer.current);
    waitTimer.current = null;
    waiting.current = false;
    setAgreement(null);
    current.current = null;
    clearCached();
    // Its FCM registration goes with it (ADR 38).
    void dropRegistration();
    await clearCredential();
    setConnection({ status: "unpaired", notice });
  }, []);

  const disconnect = useCallback(async () => {
    const credential = current.current;
    if (!credential) return;
    const server = serverName(credential.origin);
    try {
      await request(credential.origin, "auth/disconnect", {
        token: credential.token,
        body: {},
      });
    } catch (error) {
      if (error instanceof Unreachable) {
        // The phone leaves anyway; the server still lists it.
        await forget(
          `Disconnected on this phone. ${server} can't be reached, so it lists this phone as trusted until it is forgotten there.`,
        );
        return;
      }
      if (!(error instanceof Revoked)) throw error;
    }
    await forget(`Disconnected from ${server}.`);
  }, [forget]);

  const api = useCallback<Request>(
    async (path, { keep, ...options } = {}) => {
      const credential = current.current;
      if (!credential) throw new Revoked(REVOKED_NOTICE);
      try {
        const answer = await request<never>(credential.origin, path, {
          ...options,
          token: credential.token,
        });
        // Only while this credential stands: forgetting it clears the copy.
        if (current.current === credential) {
          update((state) =>
            state.unreachable ? { ...state, unreachable: false } : state,
          );
          if (keep) writeCached(keep, answer);
          // The server is back: a stream still waiting out its retry delay
          // reopens now, and its first refresh rereads every screen.
          if (offline.current) {
            offline.current = false;
            if (stream.current !== "open") relisten.current();
          }
        }
        return answer;
      } catch (error) {
        if (current.current === credential) {
          if (error instanceof Revoked) await forget();
          else if (error instanceof Unreachable) {
            offline.current = true;
            update((state) => ({ ...state, unreachable: true }));
          }
        }
        throw error;
      }
    },
    [forget, update],
  );

  const loadAccounts = useCallback(async () => {
    const credential = current.current;
    try {
      const accounts = await api<Schemas["Accounts"]>("accounts", {
        keep: "accounts",
      });
      if (current.current === credential)
        update((state) => ({ ...state, accounts }));
    } catch {
      // `api` already recorded an unreachable server or a revoked device.
    }
  }, [api, update]);

  const broadcast = useCallback((change: ServerChange) => {
    for (const listener of listeners.current) listener(change);
  }, []);

  const subscribe = useCallback((listener: (change: ServerChange) => void) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  const listen = useCallback(() => {
    const credential = current.current;
    if (!credential) return;
    stopStream.current?.();
    stopStream.current = openEvents(credential.origin, credential.token, {
      onState: (state) => {
        stream.current = state;
        update((connection) => ({ ...connection, stream: state }));
      },
      onChange: (change) => {
        if (change.kind === "refresh" || change.shared.includes("accounts"))
          void loadAccounts();
        broadcast(change);
      },
      onRevoked: () => void forget(),
    });
  }, [broadcast, forget, loadAccounts, update]);
  useEffect(() => {
    relisten.current = listen;
  }, [listen]);

  const start = useCallback(
    (credential: Credential) => {
      current.current = credential;
      setConnection({
        status: "paired",
        credential,
        stream: "connecting",
        accounts: null,
        unreachable: false,
      });
      listen();
      void loadAccounts();
      // The last accounts show until the server answers, or while it can't.
      void readCached<Schemas["Accounts"]>("accounts").then((accounts) => {
        if (accounts && current.current === credential)
          update((state) => (state.accounts ? state : { ...state, accounts }));
      });
    },
    [listen, loadAccounts, update],
  );

  // ADR 48: the server says whether this phone's member waits for the
  // owner's approval, and whether they owe a changed agreement. Offline, the
  // phone shows what it kept (ADR 38 Q20).
  const begin = useCallback(
    async (credential: Credential): Promise<Connection["status"]> => {
      current.current = credential;
      let me: Schemas["Me"] | null = null;
      try {
        me = await request<Schemas["Me"]>(credential.origin, "members/me", {
          token: credential.token,
        });
      } catch (error) {
        if (current.current !== credential) return "loading";
        if (error instanceof Revoked) {
          await forget();
          return "unpaired";
        }
      }
      if (current.current !== credential) return "loading";
      setRole(me?.role ?? null);
      waiting.current = me?.state === "waiting";
      if (me?.state === "waiting") {
        setConnection({ status: "waiting", credential });
        const poll = () => {
          waitTimer.current = setTimeout(() => void check(), WAITING_POLL);
        };
        const check = async () => {
          if (current.current !== credential) return;
          try {
            const next = await request<Schemas["Me"]>(
              credential.origin,
              "members/me",
              { token: credential.token },
            );
            if (current.current !== credential) return;
            if (next.state === "active") {
              waiting.current = false;
              setAgreement(next.agreement ?? null);
              start(credential);
              return;
            }
          } catch (error) {
            if (current.current !== credential) return;
            if (error instanceof Revoked) {
              await forget(DECLINED_NOTICE);
              return;
            }
          }
          poll();
        };
        poll();
        return "waiting";
      }
      setAgreement(me?.agreement ?? null);
      start(credential);
      return "paired";
    },
    [forget, start],
  );

  useEffect(() => {
    let live = true;
    void Promise.all([
      loadCredential(),
      SecureStore.getItemAsync(ACCOUNT_KEY).catch(() => null),
    ]).then(([credential, saved]) => {
      if (!live) return;
      const account = Number(saved);
      if (Number.isSafeInteger(account) && account > 0) setRemembered(account);
      if (credential) void begin(credential);
      else setConnection({ status: "unpaired", notice: "" });
    });
    return () => {
      live = false;
      stopStream.current?.();
      if (waitTimer.current) clearTimeout(waitTimer.current);
    };
  }, [begin]);

  // Returning to the app rereads and reopens a stream the phone may have lost.
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state !== "active" || !current.current || waiting.current) return;
      if (stream.current !== "open") listen();
      void loadAccounts();
      broadcast({ kind: "refresh" });
    });
    return () => subscription.remove();
  }, [broadcast, listen, loadAccounts]);

  // The remembered account while it is signed in, otherwise the first one
  // that is, as the web app's plain startup does.
  const signedIn =
    connection.status === "paired"
      ? (connection.accounts?.accounts ?? []).filter(
          (item) => item.state === "authorized",
        )
      : [];
  const account =
    signedIn.find((item) => item.account_id === remembered)?.account_id ??
    signedIn[0]?.account_id ??
    null;

  const value = useMemo<Value>(
    () => ({
      connection,
      paired: async (credential) => {
        // Nothing read or registered for an earlier pairing stays.
        clearCached();
        void dropRegistration();
        await saveCredential(credential);
        setConnection({ status: "loading" });
        return begin(credential);
      },
      refresh: async () => {
        if (stream.current !== "open") listen();
        await loadAccounts();
        broadcast({ kind: "refresh" });
      },
      account,
      selectAccount: (next) => {
        setRemembered(next);
        void SecureStore.setItemAsync(ACCOUNT_KEY, String(next)).catch(
          () => undefined,
        );
      },
      api,
      subscribe,
      disconnect,
      withdraw: async () => {
        const credential = current.current;
        if (!credential) return;
        try {
          await request(credential.origin, "auth/disconnect", {
            token: credential.token,
            body: {},
          });
        } catch (error) {
          if (!(error instanceof Revoked)) throw error;
        }
        await forget("You withdrew your request to join.");
      },
      agreement,
      acceptAgreement: async () => {
        if (!agreement) return;
        const me = await api<Schemas["Me"]>("members/me/agreement", {
          body: { version: agreement.version },
        });
        setAgreement(me.agreement ?? null);
      },
      role,
      leave: async () => {
        const left = await api<Schemas["Removed"]>("members/me/leave", {
          body: { confirm: true },
        });
        await forget(
          left.confirmed
            ? "You left Enloq. Everything it kept for you is deleted."
            : "You left Enloq. Telegram didn't confirm every sign-out: end the Enloq session in Telegram → Settings → Devices.",
        );
        return left.confirmed;
      },
    }),
    [
      account,
      agreement,
      role,
      api,
      begin,
      broadcast,
      connection,
      disconnect,
      forget,
      listen,
      loadAccounts,
      subscribe,
    ],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useConnection() {
  const value = useContext(Context);
  if (!value) throw new Error("useConnection needs ConnectionProvider");
  return value;
}

// Calls `listener` on every server change while the screen is mounted.
export function useServerChanges(listener: (change: ServerChange) => void) {
  const { subscribe } = useConnection();
  const latest = useRef(listener);
  latest.current = listener;
  useEffect(() => subscribe((change) => latest.current(change)), [subscribe]);
}
