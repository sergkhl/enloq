import React, { useCallback, useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { messageDate, messageTime } from "../../shared/messageTime";
import type { Schemas } from "./api";
import { useConnection, useServerChanges } from "./connection";
import { Explanation } from "./settingsUi";
import { usePalette } from "./theme";
import { useHourCycle } from "./time";
import { Body, Button, Notice } from "./ui";

type Trusted = Schemas["TrustedBrowser"];

// Trusted browsers and devices (ADR 43 Q8, IB5), under Settings → Privacy
// since ADR 47 Q6: each entry's label, kind and last access, with this phone
// marked, and Forget after a confirmation. Forgetting this phone is
// Disconnect.
export function TrustedList() {
  const { connection, api, disconnect } = useConnection();
  const palette = usePalette();
  const hourCycle = useHourCycle();
  const timezone =
    (connection.status === "paired" && connection.accounts?.timezone) || "UTC";
  const [entries, setEntries] = useState<Trusted[] | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setEntries(await api<Trusted[]>("auth/browsers"));
      setError("");
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    }
  }, [api]);
  useEffect(() => void load(), [load]);
  useServerChanges(() => void load());

  async function forget(entry: Trusted) {
    setBusy(true);
    setError("");
    try {
      if (entry.current) await disconnect();
      else {
        await api(`auth/browsers/${entry.id}/revoke`, { body: {} });
        setConfirm(null);
        await load();
      }
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.list}>
      <Explanation>
        Each one can read and write in your connected Telegram accounts on this
        server. Forget one to end its access at once.
      </Explanation>
      {entries === null && !error ? <Body muted>Loading…</Body> : null}
      {(entries ?? []).map((entry) => (
        <View
          key={entry.id}
          accessibilityLabel={`${entry.label}${entry.current ? ", this phone" : ""}`}
          style={[styles.entry, { borderColor: palette.line }]}
        >
          <Text style={[styles.label, { color: palette.text }]}>
            {entry.label}
          </Text>
          <Text style={{ color: palette.muted }}>
            {entry.current
              ? "This phone"
              : entry.kind === "device"
                ? "Phone"
                : "Browser"}
            {` · Last used ${messageDate(entry.last_access, timezone)} ${messageTime(entry.last_access, timezone, hourCycle)}`}
          </Text>
          {confirm === entry.id ? (
            <>
              <Body>
                {entry.current
                  ? "Forget this phone? It disconnects from this server and pairs again to come back."
                  : `Forget ${entry.label}? It loses access at once and pairs again to come back.`}
              </Body>
              <View style={styles.buttons}>
                <Button
                  label="Keep"
                  disabled={busy}
                  onPress={() => setConfirm(null)}
                />
                <Button
                  label="Forget"
                  primary
                  disabled={busy}
                  onPress={() => void forget(entry)}
                />
              </View>
            </>
          ) : (
            <Button
              label={`Forget ${entry.current ? "this phone" : entry.label}`}
              disabled={busy}
              onPress={() => setConfirm(entry.id)}
            />
          )}
        </View>
      ))}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  list: { gap: 10, paddingVertical: 10 },
  entry: { borderWidth: 1, borderRadius: 12, padding: 12, gap: 6 },
  label: { fontSize: 16, fontWeight: "600" },
  buttons: { gap: 8 },
});
