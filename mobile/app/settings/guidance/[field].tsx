import Ionicons from "@expo/vector-icons/Ionicons";
import { Redirect, Stack, router, useLocalSearchParams } from "expo-router";
import React, { useEffect, useState } from "react";
import {
  BackHandler,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { SmallButton } from "../../../src/BudgetCard";
import { useConnection } from "../../../src/connection";
import { GUIDANCE_FIELDS, GUIDANCE_LIMIT } from "../../../../shared/guidance";
import { message, useSettings } from "../../../src/settings";
import { Explanation, RowNote } from "../../../src/settingsUi";
import { usePalette } from "../../../src/theme";

// One Processing Guidance field's editor (ADR 45): the text with its count,
// Save, Reset to default after a confirmation, and the default text. Leaving
// with unsaved text asks first, from Back, Android's back or iOS's swipe.
export default function GuidanceFieldRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return <Editor />;
}

function leave() {
  if (router.canGoBack()) router.back();
  else router.replace("/settings/guidance");
}

function Editor() {
  const params = useLocalSearchParams<{ field?: string }>();
  const field = GUIDANCE_FIELDS.find((item) => item.name === params.field);
  const { connection } = useConnection();
  const palette = usePalette();
  const { settings, error, write } = useSettings();
  const current = field ? settings?.guidance?.fields[field.name] : undefined;
  const fallback = (field && settings?.guidance?.defaults[field.name]) ?? "";
  // The owner's unsaved text; null shows the current text, so an untouched
  // field follows changes from other clients.
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [asking, setAsking] = useState<"leave" | "reset" | null>(null);
  const [defaultShown, setDefaultShown] = useState(false);
  const unreachable = connection.status === "paired" && connection.unreachable;
  const value = text ?? current ?? "";
  const dirty = text !== null && current !== undefined && text !== current;

  useEffect(() => {
    if (!dirty) return;
    const subscription = BackHandler.addEventListener(
      "hardwareBackPress",
      () => {
        setAsking("leave");
        return true;
      },
    );
    return () => subscription.remove();
  }, [dirty]);

  async function save(fields: Record<string, string | null>) {
    setBusy(true);
    setProblem("");
    try {
      await write("guidance", { fields });
      setText(null);
      setAsking(null);
    } catch (failure) {
      setProblem(message(failure));
    } finally {
      setBusy(false);
    }
  }

  const title = field?.label ?? "Guidance";
  return (
    <SafeAreaView
      style={[styles.fill, { backgroundColor: palette.background }]}
    >
      <Stack.Screen options={{ gestureEnabled: !dirty }} />
      <View style={styles.header}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Back"
          hitSlop={8}
          onPress={() => (dirty ? setAsking("leave") : leave())}
        >
          <Ionicons name="chevron-back" size={26} color={palette.accent} />
        </Pressable>
        <Text
          accessibilityRole="header"
          numberOfLines={1}
          style={[styles.title, { color: palette.text }]}
        >
          {title}
        </Text>
      </View>
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        {!field ? (
          <RowNote>This guidance field doesn&apos;t exist.</RowNote>
        ) : current === undefined ? (
          <Explanation>
            {error ? "Settings show once the server answers." : "Loading…"}
          </Explanation>
        ) : (
          <>
            <Explanation>{field.explanation}</Explanation>
            {asking === "leave" ? (
              <View style={[styles.ask, { borderColor: palette.line }]}>
                <Text style={[styles.body, { color: palette.text }]}>
                  {`Discard your changes to ${field.label}?`}
                </Text>
                <View style={styles.buttons}>
                  <SmallButton
                    label="Keep editing"
                    palette={palette}
                    onPress={() => setAsking(null)}
                  />
                  <SmallButton
                    label="Discard"
                    primary
                    palette={palette}
                    onPress={() => {
                      setText(null);
                      setAsking(null);
                      leave();
                    }}
                  />
                </View>
              </View>
            ) : null}
            <TextInput
              testID="guidance-text"
              accessibilityLabel={field.label}
              multiline
              maxLength={GUIDANCE_LIMIT}
              editable={!busy}
              value={value}
              onChangeText={(next) => {
                setText(next);
                setProblem("");
              }}
              textAlignVertical="top"
              style={[
                styles.input,
                {
                  color: palette.text,
                  borderColor: palette.line,
                  backgroundColor: palette.panel,
                },
              ]}
            />
            <Text style={[styles.count, { color: palette.muted }]}>
              {`${value.length}/${GUIDANCE_LIMIT}`}
            </Text>
            {field.name === "faithfulness" && !value.trim() ? (
              <RowNote tone="warning">
                Without faithfulness rules, rewrites may drop or change facts,
                requests, refusals or conditions.
              </RowNote>
            ) : null}
            {asking === "reset" ? (
              <View style={[styles.ask, { borderColor: palette.line }]}>
                <Text style={[styles.body, { color: palette.text }]}>
                  {`Reset ${field.label} to its default? Your text is replaced by the shipped default.`}
                </Text>
                <View style={styles.buttons}>
                  <SmallButton
                    label="Cancel"
                    disabled={busy}
                    palette={palette}
                    onPress={() => setAsking(null)}
                  />
                  <SmallButton
                    label="Reset"
                    accessibilityLabel="Confirm reset"
                    primary
                    disabled={busy || unreachable}
                    palette={palette}
                    onPress={() => void save({ [field.name]: null })}
                  />
                </View>
              </View>
            ) : (
              <View style={styles.buttons}>
                <SmallButton
                  label="Reset to default"
                  disabled={busy || unreachable || current === fallback}
                  palette={palette}
                  onPress={() => setAsking("reset")}
                />
                <SmallButton
                  label="Save"
                  primary
                  disabled={busy || unreachable || !dirty}
                  palette={palette}
                  onPress={() => void save({ [field.name]: value })}
                />
              </View>
            )}
            {unreachable ? (
              <RowNote tone="warning">
                Settings can be changed once the server answers.
              </RowNote>
            ) : null}
            {problem ? <RowNote>{problem}</RowNote> : null}
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ expanded: defaultShown }}
              onPress={() => setDefaultShown((shown) => !shown)}
              style={styles.toggle}
            >
              <Text style={[styles.toggleLabel, { color: palette.muted }]}>
                Default text
              </Text>
              <Ionicons
                name={defaultShown ? "chevron-up" : "chevron-down"}
                size={16}
                color={palette.muted}
              />
            </Pressable>
            {defaultShown ? (
              <Text
                selectable
                style={[
                  styles.fallback,
                  { color: palette.muted, borderColor: palette.line },
                ]}
              >
                {fallback || "Empty"}
              </Text>
            ) : null}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 16,
    paddingTop: 8,
  },
  title: { fontSize: 22, fontWeight: "700", flexShrink: 1 },
  content: { padding: 16, gap: 10, paddingBottom: 32 },
  body: { fontSize: 15, lineHeight: 21 },
  ask: { borderWidth: 1, borderRadius: 12, padding: 12, gap: 10 },
  buttons: { flexDirection: "row", gap: 8, justifyContent: "flex-end" },
  input: {
    minHeight: 260,
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    fontSize: 15,
    lineHeight: 21,
  },
  count: { fontSize: 12, textAlign: "right" },
  toggle: { flexDirection: "row", alignItems: "center", gap: 4 },
  toggleLabel: { fontSize: 14, fontWeight: "600" },
  fallback: {
    fontSize: 13,
    lineHeight: 19,
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
  },
});
