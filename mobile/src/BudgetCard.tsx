import Ionicons from "@expo/vector-icons/Ionicons";
import React, { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import {
  messageDate,
  messageTime,
  type HourCycle,
} from "../../shared/messageTime";
import {
  budgetAmount,
  dollars,
  message,
  resetLabel,
  type BudgetFamily,
  type BudgetState,
} from "./settings";
import { BUDGETS } from "../../shared/settings";
import { Explanation, RowNote, settingsStyles } from "./settingsUi";
import { usePalette, type Palette } from "./theme";

const COPY = BUDGETS;

// One budget (ADR 14, ADR 34): today's counted spending against its limit,
// Change limit, and its recent changes.
export function BudgetCard({
  family,
  state,
  disabled,
  outlined = false,
  timezone,
  hourCycle,
  write,
  readOnly = false,
}: {
  family: BudgetFamily;
  state: BudgetState;
  disabled: boolean;
  // A member sees their budgets; only the owner sets them (ADR 48 Q2).
  readOnly?: boolean;
  // Opened from a budget message (ADR 45 Q8).
  outlined?: boolean;
  timezone: string;
  hourCycle: HourCycle | undefined;
  write: (path: string, body: unknown) => Promise<void>;
}) {
  const palette = usePalette();
  const copy = COPY[family];
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState(false);
  const reset = resetLabel(state.resets_at, timezone, hourCycle);
  // A $0 limit is reached at once and shows a full meter.
  const share =
    state.limit > 0
      ? Math.min(1, state.spent / state.limit)
      : state.reached
        ? 1
        : 0;
  const typed = budgetAmount(text);

  async function save() {
    if (typed === null) {
      setProblem("$0 to $10, in whole cents.");
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      await write(copy.route, { limit_usd: typed });
      setEditing(false);
    } catch (error) {
      setProblem(message(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <View
      accessibilityLabel={copy.title}
      style={[
        styles.card,
        outlined ? [styles.outlined, { borderColor: palette.accent }] : null,
      ]}
    >
      <Text style={[styles.title, { color: palette.text }]}>{copy.title}</Text>
      <Explanation>{copy.explanation}</Explanation>
      <View
        accessibilityRole="progressbar"
        accessibilityLabel={`${copy.title} spent today`}
        accessibilityValue={{ min: 0, max: 100, now: Math.round(share * 100) }}
        style={[styles.meter, { backgroundColor: palette.line }]}
      >
        <View
          style={[
            styles.fill,
            {
              width: `${Math.round(share * 100)}%`,
              backgroundColor:
                state.reached || share >= 0.8
                  ? palette.warning
                  : palette.accent,
            },
          ]}
        />
      </View>
      <Text style={[styles.spent, { color: palette.text }]}>
        <Text style={styles.strong}>{dollars(state.spent, 4)}</Text> of{" "}
        {dollars(state.limit)} today · {state.calls}{" "}
        {state.calls === 1 ? "call" : "calls"}
      </Text>
      {state.reached ? (
        <RowNote tone="warning">{copy.reached(reset)}</RowNote>
      ) : (
        <Explanation>Resets {reset}</Explanation>
      )}
      {readOnly ? null : editing ? (
        <View style={styles.editor}>
          <View style={styles.inputRow}>
            <Text style={[styles.currency, { color: palette.text }]}>$</Text>
            <TextInput
              accessibilityLabel={`${copy.title} per day in dollars`}
              autoFocus
              inputMode="decimal"
              keyboardType="decimal-pad"
              value={text}
              editable={!busy}
              onChangeText={(value) => {
                setText(value);
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
          {typed === "0.00" ? (
            <RowNote tone="warning">{copy.zero}</RowNote>
          ) : null}
        </View>
      ) : (
        <View style={styles.actions}>
          <SmallButton
            label="Change limit"
            accessibilityLabel={`Change ${copy.title} limit`}
            disabled={disabled}
            palette={palette}
            onPress={() => {
              setText((state.limit / 1e9).toFixed(2));
              setProblem("");
              setEditing(true);
            }}
          />
        </View>
      )}
      {problem ? <RowNote>{problem}</RowNote> : null}
      {state.changes.length ? (
        <>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Recent changes to the ${copy.title}`}
            accessibilityState={{ expanded: history }}
            onPress={() => setHistory((value) => !value)}
            style={styles.historyToggle}
          >
            <Text style={[styles.historyLabel, { color: palette.muted }]}>
              Recent changes
            </Text>
            <Ionicons
              name={history ? "chevron-up" : "chevron-down"}
              size={16}
              color={palette.muted}
            />
          </Pressable>
          {history
            ? state.changes.map((change, index) => {
                const at = Number(change.at);
                return (
                  <Text
                    key={index}
                    style={[styles.change, { color: palette.muted }]}
                  >
                    {`${messageDate(at, timezone)} ${messageTime(at, timezone, hourCycle)} · ${dollars(Number(change.old))} → ${dollars(Number(change.new))}`}
                  </Text>
                );
              })
            : null}
        </>
      ) : null}
    </View>
  );
}

export function SmallButton({
  label,
  accessibilityLabel,
  primary,
  selected,
  disabled,
  palette,
  onPress,
}: {
  label: string;
  accessibilityLabel?: string;
  primary?: boolean;
  // One of a set of choices, such as a trait's levels (ADR 55).
  selected?: boolean;
  disabled?: boolean;
  palette: Palette;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ disabled: !!disabled, selected }}
      disabled={disabled}
      onPress={onPress}
      style={[
        settingsStyles.smallButton,
        {
          borderColor: primary ? palette.accent : palette.line,
          backgroundColor: primary ? palette.accent : palette.background,
          opacity: disabled ? 0.5 : 1,
        },
      ]}
    >
      <Text
        style={[
          settingsStyles.smallLabel,
          { color: primary ? palette.onAccent : palette.accent },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { paddingVertical: 12, gap: 6 },
  // Drawn inside the card's padding, so nothing moves.
  outlined: {
    borderWidth: 2,
    borderRadius: 10,
    marginHorizontal: -8,
    paddingHorizontal: 6,
    paddingVertical: 10,
  },
  title: { fontSize: 16, fontWeight: "600" },
  meter: { height: 8, borderRadius: 4, overflow: "hidden", marginTop: 4 },
  fill: { height: 8, borderRadius: 4 },
  spent: { fontSize: 15 },
  strong: { fontWeight: "700" },
  actions: { flexDirection: "row" },
  editor: { gap: 6 },
  inputRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  currency: { fontSize: 17, fontWeight: "600" },
  input: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
    fontSize: 17,
  },
  historyToggle: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingTop: 4,
  },
  historyLabel: { fontSize: 14, fontWeight: "600" },
  change: { fontSize: 13 },
});
