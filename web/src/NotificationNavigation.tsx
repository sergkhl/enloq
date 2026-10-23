import { useEffect, useRef } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { rewriteQuery } from "./routeHistory";

type Opened = components["schemas"]["NotificationTarget"];
type Target = { account_id: number; peer: string };
const pendingKey = "reply-pending-notification";
const identity = /^[a-f0-9]{32}$/;
function remember(id: unknown) {
  if (typeof id !== "string" || !identity.test(id)) return;
  sessionStorage.setItem(pendingKey, id);
  window.dispatchEvent(new Event("reply-notification-open"));
}
// Retain only the opaque event ID while the browser reconnects/signs in.
if ("serviceWorker" in navigator)
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (
      event.origin === location.origin &&
      event.data?.type === "notification-open"
    )
      remember(event.data.id);
  });
const fromUrl = new URLSearchParams(location.search).get("notification");
if (fromUrl) remember(fromUrl);

// This document was opened by a notification whose locator is still pending.
// It then takes precedence over the accompanying route state (ADR 36).
export function openedByNotification() {
  try {
    return (
      !!fromUrl &&
      identity.test(fromUrl) &&
      sessionStorage.getItem(pendingKey) === fromUrl
    );
  } catch {
    return false;
  }
}

export function useNotificationNavigation(
  ready: boolean,
  open: (target: Target, current: () => boolean) => Promise<boolean | void>,
  // The locator opens no conversation: unknown, removed or unavailable.
  failed: (error: string) => void,
  // A valid Test notification, which deliberately names no conversation.
  test: () => void,
  capture?: () => () => boolean,
  // A Join Request's push, which opens People (ADR 48 Q19).
  people?: () => void,
  // A decision-model outage's push, which opens Settings → AI (ADR 49 Q14).
  ai?: () => void,
) {
  const latest = useRef({ open, failed, test, capture, people, ai });
  latest.current = { open, failed, test, capture, people, ai };
  useEffect(() => {
    if (!ready) return;
    let alive = true,
      busy = false;
    let received: { id: string; current: () => boolean } | null = null;
    const activate = async () => {
      const id = sessionStorage.getItem(pendingKey);
      if (!id || busy) return;
      busy = true;
      let opening = false;
      const receipt =
        received?.id === id
          ? received
          : { id, current: latest.current.capture?.() || (() => true) };
      received = receipt;
      const activationCurrent = receipt.current;
      const current = () =>
        alive &&
        received === receipt &&
        sessionStorage.getItem(pendingKey) === id;
      try {
        const target = await workspaceApi<Opened>("notifications/open", { id });
        if (!current() || !activationCurrent()) return;
        if (target.kind === "test") latest.current.test();
        else if (target.kind === "people")
          (latest.current.people ?? latest.current.test)();
        else if (target.kind === "ai")
          (latest.current.ai ?? latest.current.test)();
        else if (target.kind === "conversation" && target.account_id) {
          opening = true;
          await latest.current.open(
            { account_id: target.account_id, peer: target.peer || "" },
            current,
          );
        } else
          latest.current.failed(
            "This notification's conversation is unavailable.",
          );
      } catch (e) {
        if (current() && (opening || activationCurrent()))
          latest.current.failed((e as Error).message);
      } finally {
        // A newer click may arrive during either lookup or guarded navigation.
        // Only retire this ID; drain the newest pending event after it finishes.
        if (current()) sessionStorage.removeItem(pendingKey);
        // Consume this locator, and one a newer click superseded before it
        // could open; a still-pending one and the route stay.
        rewriteQuery((query) => {
          const locator = query.get("notification");
          if (locator === id || locator !== sessionStorage.getItem(pendingKey))
            query.delete("notification");
        });
        busy = false;
        if (alive && sessionStorage.getItem(pendingKey)) void activate();
      }
    };
    const listener = () => {
      const id = sessionStorage.getItem(pendingKey);
      if (id)
        received = {
          id,
          current: latest.current.capture?.() || (() => true),
        };
      void activate();
    };
    window.addEventListener("reply-notification-open", listener);
    listener();
    return () => {
      alive = false;
      window.removeEventListener("reply-notification-open", listener);
    };
  }, [ready]);
}
