import Ionicons from "@expo/vector-icons/Ionicons";
import React from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
  type LayoutChangeEvent,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { usePalette } from "./theme";
import { BackHeader } from "./ui";

// The pieces Settings is built from (ADR 45): cards of rows, each explaining
// itself in a line, since the app has no Help (Q7). Since ADR 47 Q6 Settings
// is a menu of four rows with icons, each opening a page of its own.

// A Settings page: its title with Back fixed above what scrolls.
export function SettingsPage({
  title,
  children,
  scrollRef,
}: {
  title: string;
  children: React.ReactNode;
  scrollRef?: React.Ref<ScrollView>;
}) {
  const palette = usePalette();
  return (
    <SafeAreaView
      style={[styles.fill, { backgroundColor: palette.background }]}
    >
      <View style={styles.pageHeader}>
        <BackHeader title={title} />
      </View>
      <ScrollView
        ref={scrollRef}
        contentContainerStyle={styles.pageContent}
        keyboardShouldPersistTaps="handled"
      >
        {children}
      </ScrollView>
    </SafeAreaView>
  );
}

// A row of the Settings menu (ADR 47 Q6): its icon on a soft accent tile,
// its name, what it holds on the right and, when one needs attention, an
// amber word such as Paused. Never sparkles.
export function MenuRow({
  icon,
  label,
  detail,
  badge,
  onPress,
}: {
  icon: React.ComponentProps<typeof Ionicons>["name"];
  label: string;
  detail?: string;
  badge?: string;
  onPress: () => void;
}) {
  const palette = usePalette();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={[label, badge, detail].filter(Boolean).join(", ")}
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed ? styles.pressed : null]}
    >
      <View style={[styles.icon, { backgroundColor: palette.band }]}>
        <Ionicons name={icon} size={19} color={palette.accent} />
      </View>
      <Text style={[styles.label, { color: palette.text }]}>{label}</Text>
      {badge ? (
        <Text
          style={[
            styles.badge,
            { color: palette.onRetry, backgroundColor: palette.retry },
          ]}
        >
          {badge}
        </Text>
      ) : null}
      {detail ? (
        <Text style={[styles.detail, { color: palette.muted }]}>{detail}</Text>
      ) : null}
      <Ionicons name="chevron-forward" size={18} color={palette.muted} />
    </Pressable>
  );
}

export function Section({
  title,
  children,
  outlined = false,
  onLayout,
}: {
  // A page with one card needs no title over it.
  title?: string;
  children: React.ReactNode;
  // A link into Settings outlines the section it opened (Q8).
  outlined?: boolean;
  onLayout?: (event: LayoutChangeEvent) => void;
}) {
  const palette = usePalette();
  return (
    <View onLayout={onLayout} style={styles.section}>
      {title ? (
        <Text
          accessibilityRole="header"
          style={[styles.sectionTitle, { color: palette.muted }]}
        >
          {title}
        </Text>
      ) : null}
      <View
        style={[
          styles.card,
          {
            backgroundColor: palette.panel,
            borderColor: outlined ? palette.accent : palette.line,
            borderWidth: outlined ? 2 : 1,
          },
        ]}
      >
        {children}
      </View>
    </View>
  );
}

// A card's rows are divided by a hairline.
export function Divider() {
  const palette = usePalette();
  return <View style={[styles.divider, { backgroundColor: palette.line }]} />;
}

export function Explanation({ children }: { children: React.ReactNode }) {
  const palette = usePalette();
  return (
    <Text style={[styles.explanation, { color: palette.muted }]}>
      {children}
    </Text>
  );
}

export function RowNote({
  children,
  tone = "error",
}: {
  children: React.ReactNode;
  tone?: "error" | "warning" | "info";
}) {
  const palette = usePalette();
  const color =
    tone === "error"
      ? palette.error
      : tone === "warning"
        ? palette.warning
        : palette.text;
  return (
    <Text
      accessibilityRole={tone === "info" ? "text" : "alert"}
      style={[styles.explanation, { color }]}
    >
      {children}
    </Text>
  );
}

// A row that opens another screen, with what it holds on the right.
export function LinkRow({
  label,
  detail,
  badge,
  onPress,
}: {
  label: string;
  detail?: string;
  // An amber word that needs attention, such as Paused.
  badge?: string;
  onPress: () => void;
}) {
  const palette = usePalette();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={[label, badge, detail].filter(Boolean).join(", ")}
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed ? styles.pressed : null]}
    >
      <Text style={[styles.label, { color: palette.text }]}>{label}</Text>
      {badge ? (
        <Text
          style={[
            styles.badge,
            { color: palette.onRetry, backgroundColor: palette.retry },
          ]}
        >
          {badge}
        </Text>
      ) : null}
      {detail ? (
        <Text style={[styles.detail, { color: palette.muted }]}>{detail}</Text>
      ) : null}
      <Ionicons name="chevron-forward" size={18} color={palette.muted} />
    </Pressable>
  );
}

export function SwitchRow({
  label,
  explanation,
  value,
  disabled,
  testID,
  onChange,
}: {
  label: string;
  explanation?: string;
  value: boolean;
  disabled: boolean;
  testID?: string;
  onChange: (value: boolean) => void;
}) {
  const palette = usePalette();
  return (
    <View style={styles.block}>
      <View style={styles.row}>
        <Text style={[styles.label, { color: palette.text }]}>{label}</Text>
        <Switch
          testID={testID}
          accessibilityLabel={label}
          value={value}
          disabled={disabled}
          onValueChange={onChange}
          trackColor={{ true: palette.accent }}
        />
      </View>
      {explanation ? <Explanation>{explanation}</Explanation> : null}
    </View>
  );
}

// One choice of a few side by side, segmented as the GIFs and Stickers tabs
// are, with the chosen one's explanation beneath: a chat's Suggestions (ADR
// 56 Q12 A). Screen readers hear radios.
export function ChoiceRow<T extends string>({
  label,
  options,
  value,
  explanation,
  disabled,
  testID,
  onChange,
}: {
  // The group's name for screen readers; its section's title shows it.
  label: string;
  options: { value: T; label: string }[];
  // Null until the choice is known.
  value: T | null;
  explanation?: string;
  disabled: boolean;
  // Each option's is this and its value, such as suggestions-off.
  testID?: string;
  onChange: (value: T) => void;
}) {
  const palette = usePalette();
  return (
    <View style={styles.block}>
      <View
        accessibilityRole="radiogroup"
        accessibilityLabel={label}
        style={[
          styles.segments,
          { borderColor: palette.line, opacity: disabled ? 0.5 : 1 },
        ]}
      >
        {options.map((option, index) => {
          const chosen = option.value === value;
          return (
            <Pressable
              key={option.value}
              testID={testID ? `${testID}-${option.value}` : undefined}
              accessibilityRole="radio"
              accessibilityLabel={option.label}
              accessibilityState={{ checked: chosen, disabled }}
              disabled={disabled}
              onPress={() => {
                if (!chosen) onChange(option.value);
              }}
              style={[
                styles.segment,
                index
                  ? { borderLeftWidth: 1, borderLeftColor: palette.line }
                  : null,
                chosen ? { backgroundColor: palette.accent } : null,
              ]}
            >
              <Text
                style={[
                  styles.segmentLabel,
                  { color: chosen ? palette.onAccent : palette.text },
                ]}
              >
                {option.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
      {explanation ? <Explanation>{explanation}</Explanation> : null}
    </View>
  );
}

export const settingsStyles = StyleSheet.create({
  // Small buttons inside a card's rows.
  smallButton: {
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 6,
    paddingHorizontal: 12,
  },
  smallLabel: { fontSize: 15, fontWeight: "600" },
});

const styles = StyleSheet.create({
  fill: { flex: 1 },
  pageHeader: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 },
  pageContent: { padding: 16, gap: 18, paddingBottom: 32 },
  icon: {
    width: 30,
    height: 30,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
  },
  section: { gap: 6 },
  sectionTitle: {
    fontSize: 13,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    paddingHorizontal: 4,
  },
  card: { borderRadius: 12, paddingHorizontal: 14, paddingVertical: 4 },
  divider: { height: StyleSheet.hairlineWidth },
  block: { paddingVertical: 10, gap: 4 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: 44,
    paddingVertical: 6,
  },
  pressed: { opacity: 0.6 },
  label: { flex: 1, fontSize: 16 },
  detail: { fontSize: 15 },
  badge: {
    fontSize: 12,
    fontWeight: "700",
    borderRadius: 6,
    overflow: "hidden",
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  explanation: { fontSize: 13, lineHeight: 18 },
  segments: {
    flexDirection: "row",
    borderWidth: 1,
    borderRadius: 8,
    overflow: "hidden",
  },
  segment: {
    flex: 1,
    minHeight: 40,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12,
  },
  segmentLabel: { fontSize: 15, fontWeight: "600" },
});
