import React, { useEffect, useRef, useState } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import {
  acceptSoundSettings,
  audioReady,
  beginSoundMutation,
  cancelSound,
  chime,
  finishSoundMutation,
  resetNotificationSounds,
  soundRead,
  soundReadActive,
  soundReadCurrent,
  soundSettingsChanged,
  unlockSound,
} from "./notificationSounds";
import { Explanation, Note, SwitchRow } from "./screenUi";

type Status = components["schemas"]["NotificationStatus"];
type Attention = components["schemas"]["NotificationAttentionStatus"];
export function supported() {
  return (
    "Notification" in window &&
    "PushManager" in window &&
    "serviceWorker" in navigator
  );
}
export function permission() {
  return "Notification" in window ? Notification.permission : "unsupported";
}
let registration: Promise<ServiceWorkerRegistration> | null = null;
function worker() {
  registration ??= navigator.serviceWorker
    .register("/notifications-sw.js")
    .then(() => navigator.serviceWorker.ready)
    .catch((error) => {
      registration = null;
      throw error;
    });
  return registration;
}

// Turning this browser's notifications on (ADR 20), from the Settings switch
// or the ask after pairing (ADR 47 Q5): the browser's prompt, which only a
// click may start, then the push subscription the server sends to. Without
// permission there is no subscription and no status.
export async function turnOnBrowserPush(status: Status): Promise<{
  permission: NotificationPermission;
  status?: Status;
}> {
  const result = await Notification.requestPermission();
  if (result !== "granted") return { permission: result };
  const registered = await worker();
  let subscription = await registered.pushManager.getSubscription();
  if (subscription && status.failure === "expired") {
    await subscription.unsubscribe();
    subscription = null;
  }
  if (!subscription) {
    const key = status.public_key.replace(/-/g, "+").replace(/_/g, "/");
    const applicationServerKey = Uint8Array.from(
      atob(key + "=".repeat((4 - (key.length % 4)) % 4)),
      (c) => c.charCodeAt(0),
    );
    subscription = await registered.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey,
    });
  }
  return {
    permission: result,
    status: await workspaceApi<Status>(
      "notifications/subscription",
      subscription.toJSON(),
    ),
  };
}

export function useNotificationPresence(account: number, peer: string | null) {
  const target = useRef({ account, peer });
  target.current = { account, peer };
  const refresh = useRef<() => void>(() => {});
  useEffect(() => {
    let alive = true,
      busy = false;
    let controller = new AbortController();
    const tab = crypto.randomUUID();
    if (supported()) void worker().catch(() => {});
    const poll = async () => {
      if (!alive || busy) return;
      busy = true;
      const read = soundRead();
      try {
        const result = await workspaceApi<Attention>(
          "notifications/attention",
          {
            tab,
            account_id: target.current.account || null,
            peer: target.current.peer,
            focused:
              document.visibilityState === "visible" && document.hasFocus(),
            audio: audioReady(),
            push_permission: permission(),
          },
          controller.signal,
        );
        if (!alive || !soundReadActive(read)) return;
        acceptSoundSettings(result, read);
        for (const id of result.events) {
          if (!alive || !soundReadActive(read)) return;
          const claimed = await workspaceApi<{
            account_id: number;
            peer: string;
          } | null>("notifications/claim", { id, tab }, controller.signal);
          if (!alive || !claimed) continue;
          if (
            document.visibilityState === "visible" &&
            document.hasFocus() &&
            target.current.account === claimed.account_id &&
            target.current.peer === claimed.peer
          )
            continue;
          await chime();
        }
      } catch {
        /* Existing API/auth owner handles connection loss. */
      } finally {
        busy = false;
      }
    };
    const update = () => {
      void poll();
    };
    refresh.current = update;
    const gesture = () => {
      void unlockSound();
    };
    const hide = () => {
      alive = false;
      controller.abort();
      cancelSound();
    };
    const show = () => {
      if (!alive) {
        controller = new AbortController();
        alive = true;
        update();
      }
    };
    document.addEventListener("pointerdown", gesture);
    document.addEventListener("keydown", gesture);
    document.addEventListener("visibilitychange", update);
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", show);
    for (const name of ["focus", "blur", "notification-settings-changed"])
      window.addEventListener(name, update);
    const timer = setInterval(update, 2500);
    update();
    return () => {
      hide();
      resetNotificationSounds();
      refresh.current = () => {};
      clearInterval(timer);
      document.removeEventListener("pointerdown", gesture);
      document.removeEventListener("keydown", gesture);
      document.removeEventListener("visibilitychange", update);
      window.removeEventListener("pagehide", hide);
      window.removeEventListener("pageshow", show);
      for (const name of ["focus", "blur", "notification-settings-changed"])
        window.removeEventListener(name, update);
    };
  }, []);
  useEffect(() => refresh.current(), [account, peer]);
}

export function NotificationSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [localPermission, setPermission] = useState(permission);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingSound, setPendingSound] = useState<boolean | null>(null);
  const live = useRef(true);
  function present(value: Status) {
    if (live.current) setStatus(value);
  }
  function accept(value: Status, read: ReturnType<typeof soundRead>) {
    if (live.current && acceptSoundSettings(value, read)) present(value);
  }
  function complete(
    mutation: ReturnType<typeof beginSoundMutation>,
    value: Status,
  ) {
    if (finishSoundMutation(mutation, value)) present(value);
  }
  useEffect(() => {
    live.current = true;
    const refresh = () => {
      setPermission(permission());
      const read = soundRead();
      void workspaceApi<Status>("notifications")
        .then((value) => accept(value, read))
        .catch((e) => {
          if (live.current && soundReadCurrent(read)) setError(e.message);
        });
    };
    refresh();
    const timer = setInterval(refresh, 3000);
    window.addEventListener("notification-settings-changed", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      live.current = false;
      clearInterval(timer);
      window.removeEventListener("notification-settings-changed", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, []);
  async function changeSound(sound: boolean) {
    cancelSound();
    setPendingSound(sound);
    const mutation = beginSoundMutation({ sound });
    if (sound) {
      void unlockSound();
    }
    setError("");
    setBusy(true);
    try {
      complete(
        mutation,
        await workspaceApi<Status>("notifications/preferences", { sound }),
      );
      if (sound) await unlockSound();
    } catch (e) {
      finishSoundMutation(mutation);
      if (live.current) setError((e as Error).message);
    } finally {
      if (live.current) {
        setPendingSound(null);
        setBusy(false);
      }
      soundSettingsChanged();
    }
  }
  async function enablePush() {
    setError("");
    setNotice("");
    setBusy(true);
    const mutation = beginSoundMutation();
    try {
      // Permission starts directly in this explicit click, never during polling.
      const turned = await turnOnBrowserPush(status!);
      setPermission(turned.permission);
      if (!turned.status) {
        setNotice(
          turned.permission === "denied"
            ? "Push permission denied. Change it in browser settings to enable push."
            : "Push permission was not granted.",
        );
        return;
      }
      complete(mutation, turned.status);
    } catch (e) {
      setError(`Push could not be enabled: ${(e as Error).message}`);
    } finally {
      finishSoundMutation(mutation);
      setBusy(false);
      soundSettingsChanged();
    }
  }
  async function disablePush() {
    setBusy(true);
    setError("");
    const mutation = beginSoundMutation();
    try {
      complete(
        mutation,
        await workspaceApi<Status>("notifications/preferences", {
          push: false,
        }),
      );
      const registered = await worker();
      await (await registered.pushManager.getSubscription())?.unsubscribe();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      finishSoundMutation(mutation);
      setBusy(false);
      soundSettingsChanged();
    }
  }
  async function test() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (status?.push && localPermission === "granted") {
        const result = await workspaceApi<{ state: string }>(
          "notifications/test",
          {},
        );
        setNotice(
          result.state === "sent"
            ? "Push service accepted the test; check for a banner."
            : result.state === "retry"
              ? "Test queued for a bounded retry."
              : "Push test failed; enable push again to retry.",
        );
      } else {
        await unlockSound();
        setNotice(
          (await chime())
            ? "Test chime played."
            : "Sound is blocked by this browser.",
        );
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  // Notifications in this browser, as the app's section (ADR 45 Q3, ADR 44
  // MW4): on or off, Sound and a test; the chimes and custom sound are gone.
  const pushOn = !!status?.push && localPermission === "granted";
  return (
    <div className="browser-notifications">
      <SwitchRow
        label="Notifications"
        explanation="New private messages alert this browser. Groups never alert."
        checked={pushOn}
        disabled={busy || !status || (!pushOn && !supported())}
        onChange={(on) => void (on ? enablePush() : disablePush())}
      />
      <SwitchRow
        label="Sound"
        checked={pendingSound ?? status?.sound ?? true}
        disabled={!status || busy}
        onChange={(on) => void changeSound(on)}
      />
      <div className="screen-block">
        <button
          type="button"
          className="wide-button"
          disabled={busy || !status || (!status.sound && !pushOn)}
          onClick={() => void test()}
        >
          Send a test notification
        </button>
        {!supported() ? (
          <Explanation>This browser can't show notifications.</Explanation>
        ) : localPermission === "denied" ? (
          <Note tone="warning">
            Notifications are blocked for this site in the browser's settings.
          </Note>
        ) : null}
        {status?.failure && (
          <Note tone="warning">
            {status.failure === "expired"
              ? "This browser's notification registration expired. Turn notifications on again."
              : "The last notification couldn't be delivered."}
          </Note>
        )}
        {notice && <Note tone="info">{notice}</Note>}
        {error && <Note>{error}</Note>}
      </div>
    </div>
  );
}
