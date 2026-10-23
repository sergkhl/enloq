import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { useConnection } from "./connection";
import { serverName } from "./pairingLink";
import { usePalette } from "./theme";
import { Notice } from "./ui";

const STREAM = {
  connecting: "Connecting…",
  reconnecting: "Reconnecting…",
} as const;

// The connection's state, shown only when it is not simply live: the stream
// reconnecting, or the last read failing to reach the server (ADR 38 Q20).
export function ConnectionStatus() {
  const { connection } = useConnection();
  const palette = usePalette();
  if (connection.status !== "paired") return null;
  const server = serverName(connection.credential.origin);
  return (
    <>
      {connection.stream !== "open" ? (
        <View style={styles.row}>
          <View style={[styles.dot, { backgroundColor: palette.muted }]} />
          <Text style={[styles.text, { color: palette.muted }]}>
            {server} · {STREAM[connection.stream]}
          </Text>
        </View>
      ) : null}
      {connection.unreachable ? (
        <Notice tone="warning">
          {`${server} can't be reached. Showing what was last loaded.`}
        </Notice>
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  text: { fontSize: 14 },
});
