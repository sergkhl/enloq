import Ionicons from "@expo/vector-icons/Ionicons";
import { router } from "expo-router";
import React, { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { retainedLine, type RetainedGroup } from "../../shared/retained";
import { useConnection } from "./connection";
import { usePalette } from "./theme";

// Retained group work at the foot of the Groups list (ADR 34, ADR 46 CU6):
// folded, each group past the list's seven days that still holds a saved
// draft, an unresolved send or deletion, with its reason line. A group
// opens read-only, as the web's does.
export function RetainedGroups({
  account,
  version,
}: {
  account: number;
  // The list's own read, so the section rereads with it.
  version: unknown;
}) {
  const { api } = useConnection();
  const palette = usePalette();
  const [entries, setEntries] = useState<RetainedGroup[]>([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let live = true;
    void api<RetainedGroup[]>(`accounts/${account}/retained-groups`)
      .then((value) => live && setEntries(Array.isArray(value) ? value : []))
      .catch(() => live && setEntries([]));
    return () => {
      live = false;
    };
  }, [api, account, version]);
  if (!entries.length) return null;
  return (
    <View style={[styles.section, { borderColor: palette.line }]}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Retained group work, ${entries.length}`}
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen((value) => !value)}
        style={styles.toggle}
      >
        <Ionicons
          name={open ? "chevron-down" : "chevron-forward"}
          size={18}
          color={palette.muted}
        />
        <Text style={[styles.title, styles.fill, { color: palette.text }]}>
          Retained group work
        </Text>
        <Text style={{ color: palette.muted }}>{entries.length}</Text>
      </Pressable>
      {open
        ? entries.map((entry) => (
            <Pressable
              key={entry.chat.key}
              accessibilityRole="button"
              accessibilityLabel={`${entry.chat.title}, ${retainedLine(entry)}`}
              onPress={() =>
                router.push({
                  pathname: "/chat/[account]/[peer]",
                  params: { account: String(account), peer: entry.chat.peer },
                })
              }
              style={({ pressed }) => [
                styles.row,
                pressed ? { backgroundColor: palette.panel } : null,
              ]}
            >
              <Text
                numberOfLines={1}
                style={[styles.rowTitle, { color: palette.text }]}
              >
                {entry.chat.title}
              </Text>
              <Text style={[styles.small, { color: palette.muted }]}>
                {retainedLine(entry)}
              </Text>
            </Pressable>
          ))
        : null}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { marginTop: 8, borderTopWidth: StyleSheet.hairlineWidth },
  toggle: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    minHeight: 48,
    paddingHorizontal: 16,
  },
  fill: { flex: 1 },
  title: { fontSize: 16, fontWeight: "600" },
  row: { gap: 2, paddingHorizontal: 16, paddingVertical: 10 },
  rowTitle: { fontSize: 16, fontWeight: "600" },
  small: { fontSize: 13 },
});
