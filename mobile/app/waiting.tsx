import { Redirect } from "expo-router";
import React, { useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { Refused, Unreachable } from "../src/api";
import { useConnection } from "../src/connection";
import { serverName } from "../src/pairingLink";
import { usePalette } from "../src/theme";
import { Body, Button, Notice, Screen, Title } from "../src/ui";

// A Join Request's phone (ADR 48 Q19): paired, waiting for the server's
// owner to approve or decline in People. It moves on by itself.
export default function Waiting() {
  const { connection, withdraw } = useConnection();
  const palette = usePalette();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  if (connection.status === "paired") return <Redirect href="/chats" />;
  if (connection.status !== "waiting") return <Redirect href="/pair" />;
  const server = serverName(connection.credential.origin);
  return (
    <Screen>
      <Title>Waiting for approval</Title>
      <View style={styles.row}>
        <ActivityIndicator color={palette.accent} />
        <Body>{`You asked to join Enloq on ${server}.`}</Body>
      </View>
      <Body>
        The server's owner gets a notification and decides in their Settings.
        This screen moves on by itself once they approve, and nothing of yours
        is processed until then.
      </Body>
      {note ? <Notice tone="error">{note}</Notice> : null}
      <Button
        label={busy ? "Withdrawing…" : "Withdraw request"}
        disabled={busy}
        onPress={() => {
          setBusy(true);
          setNote("");
          void withdraw()
            .catch((error) =>
              setNote(
                error instanceof Refused || error instanceof Unreachable
                  ? error.message
                  : "Withdrawing didn't finish. Try again.",
              ),
            )
            .finally(() => setBusy(false));
        }}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
});
