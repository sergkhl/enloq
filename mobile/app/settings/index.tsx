import Ionicons from "@expo/vector-icons/Ionicons";
import { Redirect, router } from "expo-router";
import React, { useCallback, useEffect, useState } from "react";
import { Alert, Pressable, StyleSheet, Text, View } from "react-native";
import { PEOPLE, waitingLabel } from "../../../shared/people";
import { dollars } from "../../../shared/settings";
import type { Schemas } from "../../src/api";
import { thisBuild } from "../../src/build";
import { useConnection, useServerChanges } from "../../src/connection";
import { serverName } from "../../src/pairingLink";
import { usePeople } from "../../src/people";
import { phonePush } from "../../src/phonePush";
import { usePhonePush } from "../../src/push";
import { useSettings } from "../../src/settings";
import {
  Divider,
  MenuRow,
  RowNote,
  Section,
  SettingsPage,
  settingsStyles,
} from "../../src/settingsUi";
import { usePalette } from "../../src/theme";

// Settings behind the list header's gear (ADR 45 Q3), as a menu since ADR 47
// Q6: Notifications (Android), Budget, AI and Privacy, each with its icon and
// what it holds, then Disconnect and the build at the foot (ADR 45 Q4). Since
// ADR 48 the owner also has People, and a member has no AI row but Leave.
// Nothing here is kept offline: while the server can't be reached, the last
// values read stay shown, and the pages can't change them.
export default function SettingsRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return <SettingsMenu />;
}

function SettingsMenu() {
  const { connection, api, role } = useConnection();
  const owner = role !== "member";
  const palette = usePalette();
  const { settings } = useSettings();
  const [trusted, setTrusted] = useState<number | null>(null);
  const unreachable = connection.status === "paired" && connection.unreachable;

  const loadTrusted = useCallback(async () => {
    try {
      setTrusted(
        (await api<Schemas["TrustedBrowser"][]>("auth/browsers")).length,
      );
    } catch {
      // The count waits for the server; Privacy says why.
    }
  }, [api]);
  useEffect(() => void loadTrusted(), [loadTrusted]);
  useServerChanges((change) => {
    if (change.kind === "refresh" || change.shared.length) void loadTrusted();
  });

  return (
    <SettingsPage title="Settings">
      {unreachable ? (
        <RowNote tone="warning">
          Settings can be changed once the server answers.
        </RowNote>
      ) : null}
      <Section>
        {phonePush ? (
          <>
            <NotificationsRow />
            <Divider />
          </>
        ) : null}
        <MenuRow
          icon="wallet-outline"
          label="Budget"
          detail={
            settings
              ? `${dollars(settings.budget.spent)} of ${dollars(settings.budget.limit)}`
              : undefined
          }
          onPress={() => router.push("/settings/budget")}
        />
        <Divider />
        {owner ? (
          <>
            <MenuRow
              icon="hardware-chip-outline"
              label="AI"
              badge={settings?.guidance?.paused.length ? "Paused" : undefined}
              onPress={() => router.push("/settings/ai")}
            />
            <Divider />
          </>
        ) : null}
        <MenuRow
          icon="lock-closed-outline"
          label="Privacy"
          detail={trusted === null ? undefined : String(trusted)}
          onPress={() => router.push("/settings/privacy")}
        />
        {role === "owner" ? (
          <>
            <Divider />
            <PeopleRow />
          </>
        ) : null}
      </Section>
      {role === "member" ? <LeaveRow /> : null}
      <DisconnectRow />
      <Text style={[styles.build, { color: palette.muted }]}>
        {thisBuild()}
      </Text>
    </SettingsPage>
  );
}

// The owner's People (ADR 48 Q19), with the requests waiting.
function PeopleRow() {
  const { people } = usePeople();
  return (
    <MenuRow
      icon="people-outline"
      label={PEOPLE.title}
      badge={waitingLabel(people?.requests.length ?? 0) || undefined}
      detail={people ? String(people.members.length) : undefined}
      onPress={() => router.push("/settings/people")}
    />
  );
}

// A member leaves (ADR 48 Q9) after a confirmation: Enloq signs out of their
// Telegram accounts here and deletes everything it keeps for them.
function LeaveRow() {
  const { leave } = useConnection();
  const palette = usePalette();
  const [note, setNote] = useState("");
  return (
    <View style={[styles.disconnect, { borderColor: palette.line }]}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={PEOPLE.leave}
        onPress={() =>
          Alert.alert(PEOPLE.leave, PEOPLE.leaveConfirm, [
            { text: "Cancel", style: "cancel" },
            {
              text: "Leave",
              style: "destructive",
              onPress: () => {
                setNote("");
                void leave().catch((error) =>
                  setNote(
                    error instanceof Error ? error.message : String(error),
                  ),
                );
              },
            },
          ])
        }
        style={styles.disconnectRow}
      >
        <Ionicons name="exit-outline" size={20} color={palette.error} />
        <Text style={[styles.disconnectLabel, { color: palette.error }]}>
          {PEOPLE.leave}
        </Text>
      </Pressable>
      {note ? <RowNote>{note}</RowNote> : null}
    </View>
  );
}

// Android only (ADR 38): its row says On or Off.
function NotificationsRow() {
  const { status } = usePhonePush();
  return (
    <MenuRow
      icon="notifications-outline"
      label="Notifications"
      detail={
        status && status.available ? (status.push ? "On" : "Off") : undefined
      }
      onPress={() => router.push("/settings/notifications")}
    />
  );
}

// Disconnect from the server after a confirmation (ADR 43 Q2), at the foot of
// Settings (ADR 45 Q4).
function DisconnectRow() {
  const { connection, disconnect } = useConnection();
  const palette = usePalette();
  const [confirming, setConfirming] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [note, setNote] = useState("");
  const server =
    connection.status === "paired"
      ? serverName(connection.credential.origin)
      : "";

  async function leave() {
    setLeaving(true);
    setNote("");
    try {
      await disconnect();
    } catch (error) {
      setNote(error instanceof Error ? error.message : String(error));
      setLeaving(false);
    }
  }

  return (
    <View style={[styles.disconnect, { borderColor: palette.line }]}>
      {confirming ? (
        <>
          <Text style={[styles.body, { color: palette.text }]}>
            {`Disconnect from ${server}? This phone forgets its pairing, its notifications and what it kept offline, and pairs again to come back.`}
          </Text>
          <View style={styles.buttons}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Cancel"
              disabled={leaving}
              onPress={() => setConfirming(false)}
              style={[
                settingsStyles.smallButton,
                { borderColor: palette.line, backgroundColor: palette.panel },
              ]}
            >
              <Text
                style={[settingsStyles.smallLabel, { color: palette.text }]}
              >
                Cancel
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Disconnect"
              disabled={leaving}
              onPress={() => void leave()}
              style={[
                settingsStyles.smallButton,
                {
                  borderColor: palette.error,
                  backgroundColor: palette.error,
                  opacity: leaving ? 0.5 : 1,
                },
              ]}
            >
              <Text
                style={[settingsStyles.smallLabel, { color: palette.onAccent }]}
              >
                Disconnect
              </Text>
            </Pressable>
          </View>
        </>
      ) : (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Disconnect from ${server}`}
          onPress={() => setConfirming(true)}
          style={styles.disconnectRow}
        >
          <Ionicons name="log-out-outline" size={20} color={palette.error} />
          <Text style={[styles.disconnectLabel, { color: palette.error }]}>
            Disconnect from {server}
          </Text>
        </Pressable>
      )}
      {note ? <RowNote>{note}</RowNote> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  body: { fontSize: 15, lineHeight: 21 },
  disconnect: { borderTopWidth: 1, paddingTop: 10, gap: 10 },
  disconnectRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: 44,
  },
  disconnectLabel: { fontSize: 16, fontWeight: "600" },
  buttons: { flexDirection: "row", gap: 10, justifyContent: "flex-end" },
  build: { fontSize: 12, textAlign: "center" },
});
