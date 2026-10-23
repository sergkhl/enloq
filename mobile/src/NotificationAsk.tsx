import Ionicons from "@expo/vector-icons/Ionicons";
import * as SecureStore from "expo-secure-store";
import React, { useEffect, useRef, useState } from "react";
import { Modal, Platform, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import type { Schemas } from "./api";
import { useConnection } from "./connection";
import { usePhonePush } from "./push";
import { usePalette } from "./theme";
import { Button } from "./ui";

// The pairing this ask belongs to: a new pairing has a new trusted entry, so
// the phone asks again (ADR 47 Q4).
const ASKED = "enloq.notifications-asked";
// Asks claim their pairing one at a time: pairing again over an earlier list
// leaves two lists mounted, and only one of them may ask.
let claims: Promise<unknown> = Promise.resolve();

// Asking for notifications on opening (ADR 47 Q4), on Android only, since
// iOS has no push yet: once per pairing, while this phone's notifications
// are off and the server sends them. Android 13 and later asks with its own
// dialog at once, through the Settings switch's Turn on. Android 12 and older
// has no dialog, so this sheet asks first and Google is contacted only on a
// yes (ADR 38 Q42). Declining leaves Settings → Notifications as the way
// back.
export function NotificationAsk() {
  const { connection, api } = useConnection();
  const palette = usePalette();
  const push = usePhonePush();
  const [sheet, setSheet] = useState(false);
  const started = useRef(false);
  const unreachable = connection.status !== "paired" || connection.unreachable;

  useEffect(() => {
    const status = push.status;
    if (!status || unreachable || started.current) return;
    started.current = true;
    if (!status.available || status.push) return;
    void (async () => {
      const rows = await api<Schemas["TrustedBrowser"][]>("auth/browsers");
      const current = rows.find((row) => row.current)?.id;
      if (!current) return;
      const claim = claims.then(async () => {
        if ((await SecureStore.getItemAsync(ASKED)) === current) return false;
        await SecureStore.setItemAsync(ASKED, current);
        return true;
      });
      claims = claim.catch(() => undefined);
      if (!(await claim)) return;
      if (Platform.OS === "android" && Number(Platform.Version) >= 33)
        await push.turnOn();
      else setSheet(true);
    })().catch(() => undefined);
  }, [api, push, unreachable]);

  return (
    <Modal
      visible={sheet}
      transparent
      animationType="fade"
      onRequestClose={() => setSheet(false)}
    >
      <View style={styles.backdrop}>
        <SafeAreaView
          edges={["bottom"]}
          accessibilityLabel="Turn on notifications?"
          style={[styles.sheet, { backgroundColor: palette.background }]}
        >
          <Ionicons
            name="notifications-outline"
            size={28}
            color={palette.accent}
          />
          <Text
            accessibilityRole="header"
            style={[styles.title, { color: palette.text }]}
          >
            Turn on notifications?
          </Text>
          <Text style={[styles.body, { color: palette.muted }]}>
            New private messages alert this phone. Groups never alert.
          </Text>
          <View style={styles.buttons}>
            <View style={styles.button}>
              <Button label="Not now" onPress={() => setSheet(false)} />
            </View>
            <View style={styles.button}>
              <Button
                label="Turn on"
                primary
                disabled={push.busy}
                onPress={() =>
                  void push.turnOn().finally(() => setSheet(false))
                }
              />
            </View>
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  sheet: {
    alignItems: "center",
    gap: 8,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: 12,
  },
  title: { fontSize: 18, fontWeight: "600" },
  body: { fontSize: 15, lineHeight: 21, textAlign: "center" },
  buttons: { flexDirection: "row", gap: 10, marginTop: 8, width: "100%" },
  button: { flex: 1 },
});
