import React, { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import {
  olderStatus,
  type OlderState,
  type Progress,
} from "../../shared/aiControls";
import { useConnection } from "./connection";
import { usePalette } from "./theme";

// Rewrite earlier messages at the top of the loaded history (ADR 28, ADR 46
// CU3): how many earlier blocks wait with Rewrite, the run's progress with
// Stop, or how the last run ended.
export function EarlierBar({
  account,
  peer,
  older,
  progress,
  aiEnabled,
  onChange,
}: {
  account: number;
  peer: string;
  older: OlderState | null | undefined;
  progress: Progress | null | undefined;
  aiEnabled: boolean;
  onChange: () => void;
}) {
  const { api } = useConnection();
  const palette = usePalette();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const status = olderStatus(older, progress);
  if (!status) return null;
  async function run(suffix: string) {
    setBusy(true);
    setError("");
    try {
      await api(
        `accounts/${account}/chats/${encodeURIComponent(peer)}/${suffix}`,
        { body: {} },
      );
      onChange();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy(false);
    }
  }
  const action =
    status.kind === "running"
      ? {
          label: "Stop",
          name: "Stop rewriting earlier messages",
          suffix: "stop",
          disabled: busy,
        }
      : (older?.available ?? 0) > 0
        ? {
            label: "Rewrite",
            name: "Rewrite earlier messages",
            suffix: "process-older",
            disabled: busy || !aiEnabled,
          }
        : null;
  return (
    <View accessibilityLiveRegion="polite" style={styles.bar}>
      <Text style={[styles.text, { color: palette.muted }]}>{status.text}</Text>
      {status.kind === "running" ? (
        <View
          accessibilityLabel="Earlier messages analysed"
          accessibilityValue={{ min: 0, max: status.total, now: status.done }}
          style={[styles.track, { backgroundColor: palette.line }]}
        >
          <View
            style={[
              styles.fillBar,
              {
                backgroundColor: palette.accent,
                width: `${status.total ? (status.done / status.total) * 100 : 0}%`,
              },
            ]}
          />
        </View>
      ) : null}
      {action ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={action.name}
          accessibilityState={{ disabled: action.disabled }}
          accessibilityHint={
            !aiEnabled && action.suffix === "process-older"
              ? "AI processing is Off for this account"
              : undefined
          }
          disabled={action.disabled}
          hitSlop={8}
          onPress={() => void run(action.suffix)}
        >
          <Text
            style={[
              styles.action,
              { color: palette.accent, opacity: action.disabled ? 0.4 : 1 },
            ]}
          >
            {action.label}
          </Text>
        </Pressable>
      ) : null}
      {error ? (
        <Text style={[styles.text, { color: palette.error }]}>{error}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { alignItems: "center", gap: 6, paddingVertical: 8 },
  text: { fontSize: 14, textAlign: "center" },
  track: { width: 220, height: 4, borderRadius: 2, overflow: "hidden" },
  fillBar: { height: 4 },
  action: { fontSize: 15, fontWeight: "600" },
});
