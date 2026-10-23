import React from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import type { Schemas } from "./api";
import type { Palette } from "./theme";

// Why Group AI processing can't brief now (the web's GroupProcessingControl).
export function processingNote(
  value: Schemas["GroupProcessing"] | null | undefined,
  accountEnabled: boolean,
) {
  if (value && !value.supported)
    return "This group is unavailable for AI processing.";
  if (value?.access === "lost")
    return "Group access was lost. Cached originals remain available.";
  if (value?.access === "unknown") return "Group access has not been verified.";
  if (!accountEnabled) return "AI processing is Off for this account.";
  return null;
}

// A group's ⋮ actions sheet (ADR 42 Q3): Group AI processing with its notes,
// and the global Hide Bots preference. Both wait for the server.
export function GroupSheet({
  visible,
  processing,
  accountEnabled,
  hideBots,
  busy,
  note,
  palette,
  onProcessing,
  onHideBots,
  onClose,
}: {
  visible: boolean;
  // Null for a retained group, which has no Group AI processing (ADR 34).
  processing: Schemas["GroupProcessing"] | null | undefined;
  accountEnabled: boolean;
  hideBots: boolean;
  busy: boolean;
  // The latest refusal.
  note: string;
  palette: Palette;
  onProcessing: (() => void) | null;
  onHideBots: () => void;
  onClose: () => void;
}) {
  const status = processingNote(processing, accountEnabled);
  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={styles.backdrop}>
        {/* Behind the sheet, so its rows stay separate for screen readers. */}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close"
          style={StyleSheet.absoluteFill}
          onPress={onClose}
        />
        <SafeAreaView
          edges={["bottom"]}
          style={[styles.sheet, { backgroundColor: palette.background }]}
        >
          <View accessibilityLabel="Conversation actions">
            {onProcessing ? (
              <>
                <SwitchRow
                  label="Group AI processing"
                  checked={processing?.processing_enabled ?? false}
                  disabled={!processing || busy}
                  palette={palette}
                  onPress={onProcessing}
                />
                {status ? (
                  <Text style={[styles.note, { color: palette.muted }]}>
                    {status}
                  </Text>
                ) : null}
              </>
            ) : null}
            <SwitchRow
              label="Hide bots"
              checked={hideBots}
              disabled={busy}
              palette={palette}
              onPress={onHideBots}
            />
            {note ? (
              <Text
                accessibilityRole="alert"
                style={[styles.note, { color: palette.error }]}
              >
                {note}
              </Text>
            ) : null}
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

function SwitchRow({
  label,
  checked,
  disabled,
  palette,
  onPress,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  palette: Palette;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityLabel={label}
      accessibilityState={{ checked, disabled }}
      disabled={disabled}
      onPress={onPress}
      style={[styles.row, { opacity: disabled ? 0.5 : 1 }]}
    >
      <Text style={[styles.label, { color: palette.text }]}>{label}</Text>
      <View
        style={[
          styles.track,
          { backgroundColor: checked ? palette.accent : palette.line },
        ]}
      >
        <View
          style={[
            styles.knob,
            { backgroundColor: palette.background },
            checked ? styles.knobOn : null,
          ]}
        />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  sheet: {
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingVertical: 8,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingHorizontal: 20,
    paddingVertical: 14,
  },
  label: { flex: 1, fontSize: 17 },
  note: { fontSize: 13, paddingHorizontal: 20, paddingBottom: 10 },
  track: { width: 44, height: 26, borderRadius: 13, padding: 3 },
  knob: { width: 20, height: 20, borderRadius: 10 },
  knobOn: { alignSelf: "flex-end" },
});
