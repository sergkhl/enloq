import React from "react";
import { StyleSheet, Text, View } from "react-native";
import type { Schemas } from "./api";
import { usePalette } from "./theme";
import { Body, Button } from "./ui";

// The Member Agreement (ADR 48 Q3), in the words the server serves: read
// before asking to join, and again when it changes.
export function Agreement({
  agreement,
  action,
  busy,
  onAgree,
  onCancel,
  cancelLabel = "Cancel",
}: {
  agreement: Schemas["Agreement"];
  action: string;
  busy?: boolean;
  onAgree: () => void;
  onCancel?: () => void;
  cancelLabel?: string;
}) {
  const palette = usePalette();
  return (
    <View style={styles.box} accessibilityLabel="Member agreement">
      <Text
        accessibilityRole="header"
        style={[styles.title, { color: palette.text }]}
      >
        {agreement.title}
      </Text>
      {agreement.paragraphs.map((paragraph, index) => (
        <Body key={index}>{paragraph}</Body>
      ))}
      <Button label={action} primary disabled={busy} onPress={onAgree} />
      {onCancel ? (
        <Button label={cancelLabel} disabled={busy} onPress={onCancel} />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { gap: 12 },
  title: { fontSize: 20, fontWeight: "600" },
});
