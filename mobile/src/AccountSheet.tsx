import Ionicons from "@expo/vector-icons/Ionicons";
import { router, type Href } from "expo-router";
import React from "react";
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import type { Schemas } from "./api";
import { Avatar } from "./Avatar";
import { usePalette, type Palette } from "./theme";

type Account = Schemas["AccountSummary"];

// The account sheet under the list's header (ADR 38, ADR 43): the signed-in
// accounts to switch between and Manage accounts (Q10). The trusted list,
// Disconnect and the build moved to Settings (ADR 45 Q4).
export function AccountSheet({
  visible,
  accounts,
  current,
  onSelect,
  onClose,
}: {
  visible: boolean;
  accounts: Account[];
  current: number | null;
  onSelect: (account: number) => void;
  onClose: () => void;
}) {
  const palette = usePalette();
  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={styles.scrim}>
        {/* Behind the sheet, so its rows stay separate for screen readers. */}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close accounts"
          style={StyleSheet.absoluteFill}
          onPress={onClose}
        />
        <View
          style={[
            styles.sheet,
            { backgroundColor: palette.background, borderColor: palette.line },
          ]}
        >
          <ScrollView>
            <Text style={[styles.title, { color: palette.text }]}>
              Accounts
            </Text>
            {accounts.map((item) => (
              <Pressable
                key={item.account_id}
                testID={`account-${item.account_id}`}
                accessibilityRole="button"
                accessibilityLabel={`${item.name || `Account ${item.account_id}`}${item.ai_enabled ? "" : ", AI off"}`}
                accessibilityState={{ selected: item.account_id === current }}
                onPress={() => {
                  onSelect(item.account_id);
                  onClose();
                }}
                style={styles.accountRow}
              >
                <Avatar
                  account={item.account_id}
                  peer={`user:${item.account_id}`}
                  photoId={item.photo_id}
                  name={item.name}
                  size={36}
                />
                <View style={styles.fill}>
                  <Text style={[styles.rowTitle, { color: palette.text }]}>
                    {item.name || `Account ${item.account_id}`}
                  </Text>
                  {item.ai_enabled ? null : (
                    <Text style={{ color: palette.muted }}>AI off</Text>
                  )}
                </View>
                {item.account_id === current ? (
                  <Ionicons name="checkmark" size={20} color={palette.accent} />
                ) : null}
              </Pressable>
            ))}
            <SheetLink
              label="Manage accounts"
              icon="people-outline"
              href="/accounts"
              palette={palette}
              onClose={onClose}
            />
            {/* Settings, with this phone's notifications inside (ADR 45 Q3),
                left the header for the sheet's last row (ADR 51 Q19, Q21). */}
            <View style={[styles.divider, { backgroundColor: palette.line }]} />
            <SheetLink
              label="Settings"
              icon="settings-outline"
              href="/settings"
              palette={palette}
              onClose={onClose}
            />
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

function SheetLink({
  label,
  icon,
  href,
  palette,
  onClose,
}: {
  label: string;
  icon: React.ComponentProps<typeof Ionicons>["name"];
  href: Href;
  palette: Palette;
  onClose: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={() => {
        onClose();
        router.push(href);
      }}
      style={styles.actionRow}
    >
      <Ionicons name={icon} size={20} color={palette.accent} />
      <Text style={[styles.rowTitle, { color: palette.text }]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  divider: { height: StyleSheet.hairlineWidth, marginVertical: 4 },
  fill: { flex: 1 },
  scrim: {
    flex: 1,
    justifyContent: "flex-start",
    paddingTop: 80,
    paddingHorizontal: 16,
    paddingBottom: 40,
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  sheet: { borderRadius: 14, borderWidth: 1, padding: 12, maxHeight: "100%" },
  title: { fontSize: 15, fontWeight: "600", padding: 4 },
  accountRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 8,
    paddingHorizontal: 4,
  },
  actionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: 4,
  },
  rowTitle: { fontSize: 16, fontWeight: "600" },
});
