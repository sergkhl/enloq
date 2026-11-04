import { Redirect } from "expo-router";
import React, { useCallback, useEffect, useState } from "react";
import { FlatList, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import type { Schemas } from "../../src/api";
import { useConnection } from "../../src/connection";
import {
  logEntries,
  sendLogs,
  subscribeLogs,
  type LogEntry,
} from "../../src/logs";
import { usePalette } from "../../src/theme";
import { BackHeader, Button } from "../../src/ui";

// Settings → Logs (ADR 61): this phone's log, which Send to server sends in
// full, and for the owner the service's last lines, in the order they happened
// from the newest down. Everything can be selected and copied.
export default function LogsRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return <LogsPage />;
}

const moment = (time: number) =>
  new Date(time).toISOString().replace("T", " ").slice(0, 19);

function LogsPage() {
  const { api, role } = useConnection();
  const palette = usePalette();
  const [mine, setMine] = useState(logEntries);
  const [server, setServer] = useState<string[] | null>(null);
  const [showServer, setShowServer] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => subscribeLogs(() => setMine(logEntries())), []);

  const readServer = useCallback(async () => {
    setNote("");
    try {
      const value = await api<Schemas["ServerLogs"]>("logs?limit=500");
      setServer([...value.lines].reverse());
    } catch (problem) {
      setNote((problem as Error).message);
    }
  }, [api]);

  async function send() {
    setNote("");
    setNote(
      (await sendLogs(true))
        ? "Sent to the server's log."
        : "Couldn't send them; they stay on this phone.",
    );
  }

  return (
    <SafeAreaView
      style={[styles.fill, { backgroundColor: palette.background }]}
    >
      <View style={styles.header}>
        <BackHeader title="Logs" />
      </View>
      <View style={styles.actions}>
        {role === "owner" ? (
          <>
            <Button
              label="This phone"
              primary={!showServer}
              onPress={() => setShowServer(false)}
            />
            <Button
              label="Server"
              primary={showServer}
              onPress={() => {
                setShowServer(true);
                void readServer();
              }}
            />
          </>
        ) : null}
        <Button label="Send to server" onPress={() => void send()} />
      </View>
      {note ? (
        <Text style={[styles.note, { color: palette.muted }]}>{note}</Text>
      ) : null}
      {showServer ? (
        <FlatList
          data={server ?? []}
          keyExtractor={(_, index) => String(index)}
          ListEmptyComponent={
            <Text style={[styles.note, { color: palette.muted }]}>
              {server === null ? "Reading the server's log…" : "Nothing yet."}
            </Text>
          }
          renderItem={({ item }) => (
            <Text selectable style={[styles.line, { color: palette.text }]}>
              {item}
            </Text>
          )}
        />
      ) : (
        <FlatList
          data={mine}
          keyExtractor={(item, index) => `${item.time}/${index}`}
          ListEmptyComponent={
            <Text style={[styles.note, { color: palette.muted }]}>
              Nothing yet.
            </Text>
          }
          renderItem={({ item }) => <Entry item={item} />}
        />
      )}
    </SafeAreaView>
  );
}

function Entry({ item }: { item: LogEntry }) {
  const palette = usePalette();
  const color =
    item.level === "error"
      ? palette.error
      : item.level === "warn"
        ? palette.warning
        : palette.text;
  return (
    <View style={[styles.entry, { borderColor: palette.line }]}>
      <Text selectable style={[styles.line, { color }]}>
        {moment(item.time)} {item.source}: {item.message}
      </Text>
      {item.stack ? (
        <Text selectable style={[styles.line, { color: palette.muted }]}>
          {item.stack}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 4 },
  actions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    paddingHorizontal: 16,
    paddingBottom: 8,
  },
  note: { paddingHorizontal: 16, paddingVertical: 6 },
  entry: {
    paddingHorizontal: 16,
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  line: { fontFamily: "monospace", fontSize: 11 },
});
