import React from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { usePhonePush } from "./push";
import { Divider, Explanation, RowNote, SwitchRow } from "./settingsUi";
import { usePalette } from "./theme";
import { Button } from "./ui";

const FAILURES: Record<string, string> = {
  expired:
    "Google retired this phone's registration. Turn notifications on again.",
  delivery_failed: "The last notification couldn't be delivered.",
};

// Notifications on this phone (ADR 38 Q5), the
// first section of Settings on Android (ADR 45 Q3): this phone's own
// destination, with its own sound choice, delivered through FCM.
export function PhoneNotifications() {
  const palette = usePalette();
  const push = usePhonePush();
  const status = push.status;
  return (
    <View style={styles.body}>
      {!status ? (
        push.note ? null : (
          <ActivityIndicator style={styles.loading} color={palette.accent} />
        )
      ) : (
        <>
          {status.available ? (
            <SwitchRow
              label="Notifications"
              testID="push-switch"
              explanation="New private messages alert this phone. Groups never alert."
              value={status.push}
              disabled={push.busy}
              onChange={(on) => void (on ? push.turnOn() : push.turnOff())}
            />
          ) : (
            <Explanation>
              {"This server doesn't send phone notifications."}
            </Explanation>
          )}
          {status.push ? (
            <>
              <Divider />
              <SwitchRow
                label="Sound"
                testID="sound-switch"
                value={status.sound}
                disabled={push.busy}
                onChange={(on) => void push.setSound(on)}
              />
              <View style={styles.test}>
                <Button
                  label="Send a test notification"
                  disabled={push.busy}
                  onPress={() => void push.test()}
                />
              </View>
            </>
          ) : null}
          {status.failure && FAILURES[status.failure] ? (
            <RowNote tone="warning">{FAILURES[status.failure]}</RowNote>
          ) : null}
        </>
      )}
      {push.note ? <RowNote tone="info">{push.note}</RowNote> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  body: { paddingBottom: 8 },
  loading: { paddingVertical: 12 },
  test: { paddingBottom: 6 },
});
