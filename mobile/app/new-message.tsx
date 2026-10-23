import { Redirect, router, useLocalSearchParams } from "expo-router";
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  newMessagePath,
  openPath,
  peopleSections,
  SEARCH_PAUSE_MS,
  type People,
  type Person,
} from "../../shared/newMessage";
import { Avatar } from "../src/Avatar";
import { useConnection } from "../src/connection";
import { RowNote } from "../src/settingsUi";
import { usePalette } from "../src/theme";
import { BackHeader } from "../src/ui";

// New Message (ADR 51 Q7, Q15-Q18), opened from the pencil on the Private
// tab: the account's private chats of any age, then its contacts, and for
// three typed characters Telegram's people search. Choosing someone opens
// their private chat, ready to write.
export default function NewMessageRoute() {
  const params = useLocalSearchParams<{ account: string }>();
  const account = Number(params.account);
  const { connection, api } = useConnection();
  const palette = usePalette();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<People | null>(null);
  const [problem, setProblem] = useState("");
  const [opening, setOpening] = useState<string | null>(null);
  const paired = connection.status === "paired";
  useEffect(() => {
    if (!paired) return;
    let live = true;
    const timer = setTimeout(
      () => {
        api<People>(newMessagePath(account, query)).then(
          (answer) => {
            if (!live) return;
            setFound(answer);
            setProblem("");
          },
          (error) =>
            live &&
            setProblem(error instanceof Error ? error.message : String(error)),
        );
      },
      query ? SEARCH_PAUSE_MS : 0,
    );
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [api, account, query, paired]);
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (!paired) return null;

  async function open(person: Person) {
    if (opening) return;
    setOpening(person.peer);
    setProblem("");
    try {
      await api(openPath(account, person.peer), { body: {} });
      router.replace({
        pathname: "/chat/[account]/[peer]",
        params: { account: String(account), peer: person.peer },
      });
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setOpening(null);
    }
  }

  const sections = peopleSections(found);
  return (
    <SafeAreaView
      style={[styles.fill, { backgroundColor: palette.background }]}
    >
      <View style={styles.header}>
        <BackHeader title="New message" />
        <TextInput
          accessibilityLabel="Search people"
          placeholder="Name or @username"
          placeholderTextColor={palette.muted}
          autoFocus
          autoCapitalize="none"
          autoCorrect={false}
          value={query}
          onChangeText={setQuery}
          style={[
            styles.search,
            { color: palette.text, borderColor: palette.line },
          ]}
        />
      </View>
      <ScrollView keyboardShouldPersistTaps="handled">
        {problem ? <RowNote>{problem}</RowNote> : null}
        {!found && !problem ? (
          <ActivityIndicator color={palette.accent} style={styles.loading} />
        ) : null}
        {found && !sections.length ? (
          <Text style={[styles.empty, { color: palette.muted }]}>
            {query.trim() ? "No one found." : "No chats or contacts yet."}
          </Text>
        ) : null}
        {sections.map((section) => (
          <View key={section.title}>
            <Text
              accessibilityRole="header"
              style={[styles.section, { color: palette.muted }]}
            >
              {section.title}
            </Text>
            {section.people.map((person) => (
              <Pressable
                key={person.peer}
                accessibilityRole="button"
                accessibilityLabel={`${person.name}${person.username ? `, @${person.username}` : ""}`}
                disabled={!!opening}
                onPress={() => void open(person)}
                style={({ pressed }) => [
                  styles.row,
                  pressed ? { backgroundColor: palette.panel } : null,
                ]}
              >
                <Avatar
                  account={account}
                  peer={person.peer}
                  photoId={person.photo_id}
                  name={person.name}
                  size={44}
                />
                <View style={styles.fill}>
                  <Text
                    numberOfLines={1}
                    style={[styles.name, { color: palette.text }]}
                  >
                    {person.name}
                  </Text>
                  {person.username ? (
                    <Text numberOfLines={1} style={{ color: palette.muted }}>
                      @{person.username}
                    </Text>
                  ) : null}
                </View>
                {opening === person.peer ? (
                  <ActivityIndicator color={palette.accent} />
                ) : null}
              </Pressable>
            ))}
          </View>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  header: { paddingHorizontal: 12, paddingTop: 4, gap: 8 },
  search: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 16,
  },
  loading: { marginTop: 24 },
  empty: { textAlign: "center", marginTop: 24 },
  section: {
    fontSize: 13,
    fontWeight: "600",
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 4,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  name: { fontSize: 16, fontWeight: "600" },
});
