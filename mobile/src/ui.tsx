import Ionicons from "@expo/vector-icons/Ionicons";
import { router } from "expo-router";
import React from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type ScrollViewProps,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { usePalette } from "./theme";

export function Screen({
  children,
  header,
  ...scroll
}: {
  children: React.ReactNode;
  // Stays above the scrolling content, so Back is always in reach.
  header?: React.ReactNode;
} & Pick<ScrollViewProps, "refreshControl">) {
  const palette = usePalette();
  return (
    <SafeAreaView
      style={[styles.fill, { backgroundColor: palette.background }]}
    >
      {header ? <View style={styles.header}>{header}</View> : null}
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        {...scroll}
      >
        {children}
      </ScrollView>
    </SafeAreaView>
  );
}

export function Title({ children }: { children: React.ReactNode }) {
  const palette = usePalette();
  return (
    <Text
      accessibilityRole="header"
      style={[styles.title, { color: palette.text }]}
    >
      {children}
    </Text>
  );
}

// A screen's title with Back to where it was opened from, or the list.
export function BackHeader({ title }: { title: string }) {
  const palette = usePalette();
  return (
    <View style={styles.back}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Back"
        hitSlop={8}
        onPress={() =>
          router.canGoBack() ? router.back() : router.replace("/chats")
        }
      >
        <Ionicons name="chevron-back" size={26} color={palette.accent} />
      </Pressable>
      <Text
        accessibilityRole="header"
        style={[styles.backTitle, { color: palette.text }]}
      >
        {title}
      </Text>
    </View>
  );
}

export function Body({
  children,
  muted,
}: {
  children: React.ReactNode;
  muted?: boolean;
}) {
  const palette = usePalette();
  return (
    <Text
      style={[styles.body, { color: muted ? palette.muted : palette.text }]}
    >
      {children}
    </Text>
  );
}

export function Button({
  label,
  onPress,
  primary,
  disabled,
  accessibilityLabel,
}: {
  label: string;
  onPress: () => void;
  primary?: boolean;
  disabled?: boolean;
  // A fuller name than the visible label, such as which entry it edits.
  accessibilityLabel?: string;
}) {
  const palette = usePalette();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: primary ? palette.accent : palette.panel,
          borderColor: primary ? palette.accent : palette.line,
          opacity: disabled ? 0.5 : pressed ? 0.8 : 1,
        },
      ]}
    >
      <Text
        style={[
          styles.buttonLabel,
          { color: primary ? palette.onAccent : palette.accent },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

export function Notice({
  children,
  tone = "info",
}: {
  children: React.ReactNode;
  tone?: "info" | "warning" | "error";
}) {
  const palette = usePalette();
  const color =
    tone === "error"
      ? palette.error
      : tone === "warning"
        ? palette.warning
        : palette.text;
  return (
    <View
      accessibilityRole={tone === "info" ? "text" : "alert"}
      style={[
        styles.notice,
        { backgroundColor: palette.panel, borderColor: palette.line },
      ]}
    >
      <Text style={[styles.body, { color }]}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  content: { padding: 20, gap: 14 },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 4 },
  title: { fontSize: 26, fontWeight: "700" },
  back: { flexDirection: "row", alignItems: "center", gap: 6 },
  backTitle: { fontSize: 22, fontWeight: "700", flexShrink: 1 },
  body: { fontSize: 16, lineHeight: 22 },
  button: {
    borderWidth: 1,
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 16,
    alignItems: "center",
  },
  buttonLabel: { fontSize: 16, fontWeight: "600" },
  notice: { borderWidth: 1, borderRadius: 10, padding: 12 },
});
