import React, { useEffect, useState } from "react";
import { Bell } from "lucide-react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { permission, supported, turnOnBrowserPush } from "./Notifications";
import { soundSettingsChanged } from "./notificationSounds";

type Status = components["schemas"]["NotificationStatus"];
type Trusted = components["schemas"]["TrustedBrowser"];

// The browser's trust this ask belongs to: a new pairing has a new entry, so
// it asks again (ADR 47 Q4, Q5).
const ASKED = "enloq.notifications-asked";

function asked() {
  try {
    return localStorage.getItem(ASKED);
  } catch {
    return null;
  }
}

function remember(entry: string) {
  try {
    localStorage.setItem(ASKED, entry);
  } catch {
    // Without storage the browser may ask again next time; nothing breaks.
  }
}

// Asking for notifications on opening (ADR 47 Q5), as the app asks (Q4): once
// per pairing, while this browser can show them, hasn't decided and is not
// already on. Browsers prompt only after a click, so a small sheet asks first
// and its Turn on starts the browser's prompt. Declining leaves Settings →
// Notifications as the way back.
export function NotificationAsk() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!supported() || permission() !== "default") return;
    let live = true;
    void Promise.all([
      workspaceApi<Trusted[]>("auth/browsers"),
      workspaceApi<Status>("notifications"),
    ])
      .then(([rows, value]) => {
        const current = rows.find((row) => row.current)?.id;
        if (!live || !current || value.push || !value.public_key) return;
        if (asked() === current) return;
        remember(current);
        setStatus(value);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);
  if (!status) return null;
  async function turnOn() {
    setBusy(true);
    try {
      await turnOnBrowserPush(status!);
    } catch {
      // Settings → Notifications says what went wrong when tried again.
    } finally {
      soundSettingsChanged();
      setStatus(null);
    }
  }
  return (
    <div className="sheet-scrim">
      <div
        className="sheet-bottom notification-ask"
        role="dialog"
        aria-modal="true"
        aria-label="Turn on notifications?"
      >
        <Bell size={26} aria-hidden />
        <h2>Turn on notifications?</h2>
        <p>New private messages alert this browser. Groups never alert.</p>
        <div className="notification-ask-buttons">
          <button
            type="button"
            className="sheet-button"
            disabled={busy}
            onClick={() => setStatus(null)}
          >
            Not now
          </button>
          <button
            type="button"
            className="sheet-button primary"
            disabled={busy}
            onClick={() => void turnOn()}
          >
            Turn on
          </button>
        </div>
      </div>
    </div>
  );
}
