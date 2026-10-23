import Ionicons from "@expo/vector-icons/Ionicons";
import React, { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { EXPLAIN } from "../../shared/settings";
import { SmallButton } from "./BudgetCard";
import { characterCount, message, type Settings } from "./settings";
import { Divider, Explanation, RowNote, SwitchRow } from "./settingsUi";
import { usePalette } from "./theme";

type Write = (path: string, body: unknown) => Promise<void>;

// The processing controls (ADR 45 Q6), each applied for every account: the
// Processing Character Threshold (ADR 35), the Concurrency Limit, and the
// Memory and suggestions switches (ADR 21, ADR 25); Memory covers both
// memories and traits (ADR 55 Q7).
export function ProcessingSettings({
  settings,
  disabled,
  write,
}: {
  settings: Settings;
  disabled: boolean;
  write: Write;
}) {
  return (
    <>
      <MinimumCharacters
        value={settings.processing_threshold}
        disabled={disabled}
        write={write}
      />
      <Divider />
      <ModelCalls
        value={settings.concurrency}
        disabled={disabled}
        write={write}
      />
      <Divider />
      <SettingSwitch
        label="Memory"
        explanation={EXPLAIN.memory}
        route="memory"
        testID="memory-switch"
        value={settings.memory}
        disabled={disabled}
        write={write}
      />
      <Divider />
      <SettingSwitch
        label="Suggestions"
        explanation={EXPLAIN.suggestions}
        route="suggestions"
        testID="suggestions-switch"
        value={settings.suggestions}
        disabled={disabled}
        write={write}
      />
    </>
  );
}

function MinimumCharacters({
  value,
  disabled,
  write,
}: {
  value: number;
  disabled: boolean;
  write: Write;
}) {
  const palette = usePalette();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  async function save() {
    const limit = characterCount(text);
    if (limit === null) {
      setProblem("A whole number from 0 to 10,000.");
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      await write("processing-threshold", { limit });
      setEditing(false);
    } catch (error) {
      setProblem(message(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.block}>
      {editing ? (
        <View style={styles.row}>
          <Text style={[styles.label, { color: palette.text }]}>
            Minimum characters
          </Text>
          <TextInput
            accessibilityLabel="Minimum characters"
            autoFocus
            inputMode="numeric"
            keyboardType="number-pad"
            value={text}
            editable={!busy}
            onChangeText={(next) => {
              setText(next);
              setProblem("");
            }}
            onSubmitEditing={() => void save()}
            style={[
              styles.input,
              {
                color: palette.text,
                borderColor: palette.line,
                backgroundColor: palette.background,
              },
            ]}
          />
        </View>
      ) : (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Minimum characters, ${value}. Change`}
          disabled={disabled}
          onPress={() => {
            setText(String(value));
            setProblem("");
            setEditing(true);
          }}
          style={styles.row}
        >
          <Text style={[styles.label, { color: palette.text }]}>
            Minimum characters
          </Text>
          <Text style={[styles.value, { color: palette.muted }]}>
            {value.toLocaleString("en-US")}
          </Text>
          <Ionicons name="create-outline" size={18} color={palette.muted} />
        </Pressable>
      )}
      {editing ? (
        <View style={styles.buttons}>
          <SmallButton
            label="Cancel"
            disabled={busy}
            palette={palette}
            onPress={() => {
              setEditing(false);
              setProblem("");
            }}
          />
          <SmallButton
            label="Save"
            primary
            disabled={busy || disabled}
            palette={palette}
            onPress={() => void save()}
          />
        </View>
      ) : null}
      <Explanation>{EXPLAIN.minimum}</Explanation>
      {problem ? <RowNote>{problem}</RowNote> : null}
    </View>
  );
}

function ModelCalls({
  value,
  disabled,
  write,
}: {
  value: number;
  disabled: boolean;
  write: Write;
}) {
  const palette = usePalette();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  async function change(limit: number) {
    setBusy(true);
    setProblem("");
    try {
      await write("concurrency", { limit });
    } catch (error) {
      setProblem(message(error));
    } finally {
      setBusy(false);
    }
  }

  const step = (label: string, icon: "remove" | "add", next: number) => {
    const off = disabled || busy || next < 1 || next > 8;
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityState={{ disabled: off }}
        disabled={off}
        hitSlop={6}
        onPress={() => void change(next)}
        style={[
          styles.step,
          { borderColor: palette.line, opacity: off ? 0.4 : 1 },
        ]}
      >
        <Ionicons name={icon} size={18} color={palette.accent} />
      </Pressable>
    );
  };

  return (
    <View style={styles.block}>
      <View style={styles.row}>
        <Text style={[styles.label, { color: palette.text }]}>
          Model calls at once
        </Text>
        {step("Fewer model calls at once", "remove", value - 1)}
        <Text
          accessibilityLabel={`${value} model calls at once`}
          style={[styles.count, { color: palette.text }]}
        >
          {value}
        </Text>
        {step("More model calls at once", "add", value + 1)}
      </View>
      <Explanation>{EXPLAIN.calls}</Explanation>
      {problem ? <RowNote>{problem}</RowNote> : null}
    </View>
  );
}

function SettingSwitch({
  label,
  explanation,
  route,
  testID,
  value,
  disabled,
  write,
}: {
  label: string;
  explanation: string;
  route: string;
  testID: string;
  value: boolean;
  disabled: boolean;
  write: Write;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  return (
    <>
      <SwitchRow
        label={label}
        explanation={explanation}
        testID={testID}
        value={value}
        disabled={disabled || busy}
        onChange={async (enabled) => {
          setBusy(true);
          setProblem("");
          try {
            await write(route, { enabled });
          } catch (error) {
            setProblem(message(error));
          } finally {
            setBusy(false);
          }
        }}
      />
      {problem ? <RowNote>{problem}</RowNote> : null}
    </>
  );
}

const styles = StyleSheet.create({
  block: { paddingVertical: 10, gap: 4 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: 44,
  },
  label: { flex: 1, fontSize: 16 },
  value: { fontSize: 16 },
  input: {
    width: 110,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
    fontSize: 17,
    textAlign: "right",
  },
  buttons: { flexDirection: "row", gap: 8, justifyContent: "flex-end" },
  step: {
    borderWidth: 1,
    borderRadius: 8,
    width: 36,
    height: 32,
    alignItems: "center",
    justifyContent: "center",
  },
  count: { fontSize: 17, fontWeight: "600", minWidth: 20, textAlign: "center" },
});
