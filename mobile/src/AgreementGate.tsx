import React, { useState } from "react";
import { ScrollView, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Agreement } from "./Agreement";
import { Refused, Unreachable } from "./api";
import { useConnection } from "./connection";
import { usePalette } from "./theme";
import { Notice } from "./ui";

// A changed Member Agreement (ADR 48 Q3) covers the app until the member
// accepts it; their accounts' new AI work waits meanwhile.
export function AgreementGate() {
  const { connection, agreement, acceptAgreement, disconnect } =
    useConnection();
  const palette = usePalette();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  if (connection.status !== "paired" || !agreement) return null;
  return (
    <View
      style={[StyleSheet.absoluteFill, { backgroundColor: palette.background }]}
    >
      <SafeAreaView style={styles.fill}>
        <ScrollView contentContainerStyle={styles.content}>
          <Notice tone="warning">
            The agreement for using Enloq changed. Read it again to keep AI on.
          </Notice>
          <Agreement
            agreement={agreement}
            action={busy ? "Saving…" : "Agree"}
            busy={busy}
            onAgree={() => {
              setBusy(true);
              setNote("");
              void acceptAgreement()
                .catch((error) =>
                  setNote(
                    error instanceof Refused || error instanceof Unreachable
                      ? error.message
                      : "That didn't save. Try again.",
                  ),
                )
                .finally(() => setBusy(false));
            }}
            onCancel={() => void disconnect()}
            cancelLabel="Disconnect this phone"
          />
          {note ? <Notice tone="error">{note}</Notice> : null}
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  content: { padding: 20, gap: 16 },
});
