import React, { useCallback, useEffect, useState } from "react";
import { QrCode } from "lucide-react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { useChanges } from "./events";
import {
  Explanation,
  MenuRow,
  Note,
  ScreenFrame,
  Section,
  SmallButton,
} from "./screenUi";
import { messageDate, messageTime } from "../../shared/messageTime";

type Trusted = components["schemas"]["TrustedBrowser"];

// Settings → Privacy (ADR 47 Q6), as the app's: Pair a phone, which shows a
// QR code for Enloq on a new phone (Q1), then the trusted browsers and
// devices (ADR 43 IB5, ADR 45 Q4): each entry's label, kind and last use,
// with this browser marked, and Forget after a confirmation. Forgetting this
// browser is Disconnect.
export function PrivacyScreen({
  timezone,
  onBack,
  onPair,
}: {
  timezone: string;
  onBack: () => void;
  onPair: () => void;
}) {
  const [entries, setEntries] = useState<Trusted[] | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setEntries(await workspaceApi<Trusted[]>("auth/browsers"));
      setError("");
    } catch (problem) {
      setError((problem as Error).message);
    }
  }, []);
  useEffect(() => void load(), [load]);
  useChanges((change) => {
    if (change.all || change.shared.length) void load();
  });
  async function forget(entry: Trusted) {
    setBusy(true);
    setError("");
    try {
      if (entry.current) {
        await workspaceApi("auth/disconnect", {});
        window.dispatchEvent(new Event("browser-auth-lost"));
        return;
      }
      await workspaceApi(`auth/browsers/${entry.id}/revoke`, {});
      setConfirm(null);
      await load();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <ScreenFrame title="Privacy" onBack={onBack}>
      <Section>
        <MenuRow icon={QrCode} label="Pair a phone" onOpen={onPair} />
      </Section>
      <h2 className="screen-subtitle">Trusted browsers and devices</h2>
      <Explanation>
        Each one can read and write in your connected Telegram accounts on this
        server. Forget one to end its access at once.
      </Explanation>
      {entries === null && !error && <Explanation>Loading…</Explanation>}
      {(entries ?? []).map((entry) => (
        <div
          key={entry.id}
          className="trusted-entry"
          role="group"
          aria-label={`${entry.label}${entry.current ? ", this browser" : ""}`}
        >
          <strong>{entry.label}</strong>
          <small>
            {entry.current
              ? "This browser"
              : entry.kind === "device"
                ? "Phone"
                : "Browser"}
            {` · Last used ${messageDate(entry.last_access, timezone)} ${messageTime(entry.last_access, timezone)}`}
          </small>
          {confirm === entry.id ? (
            <>
              <p>
                {entry.current
                  ? "Forget this browser? It disconnects from this server and pairs again to come back."
                  : `Forget ${entry.label}? It loses access at once and pairs again to come back.`}
              </p>
              <div className="button-row">
                <SmallButton
                  label="Keep"
                  disabled={busy}
                  onClick={() => setConfirm(null)}
                />
                <SmallButton
                  label="Forget"
                  primary
                  disabled={busy}
                  onClick={() => void forget(entry)}
                />
              </div>
            </>
          ) : (
            <div>
              <SmallButton
                label={`Forget ${entry.current ? "this browser" : entry.label}`}
                disabled={busy}
                onClick={() => setConfirm(entry.id)}
              />
            </div>
          )}
        </div>
      ))}
      {error && <Note>{error}</Note>}
    </ScreenFrame>
  );
}
