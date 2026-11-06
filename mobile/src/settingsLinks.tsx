import { router } from "expo-router";
import React from "react";
import { Text, type StyleProp, type TextStyle } from "react-native";
import { usePalette } from "./theme";

import type { SettingsTarget } from "../../shared/settings";

export { STAGE_TARGETS, type SettingsTarget } from "../../shared/settings";

// Since ADR 47 Q6 a budget opens Settings → Budget, and a format pause
// Settings → AI's notice (ADR 62 Q18).
export function openSettings(target: SettingsTarget) {
  if (target === "paused")
    router.push({ pathname: "/settings/ai", params: { section: "paused" } });
  else
    router.push({ pathname: "/settings/budget", params: { section: target } });
}

// A status line that opens the Settings section able to change it.
export function SettingsLink({
  target,
  style,
  children,
}: {
  target: SettingsTarget;
  style?: StyleProp<TextStyle>;
  children: string;
}) {
  const palette = usePalette();
  return (
    <Text
      accessibilityRole="link"
      accessibilityHint="Opens Settings"
      onPress={() => openSettings(target)}
      style={[
        style,
        { color: palette.accent, textDecorationLine: "underline" },
      ]}
    >
      {children}
    </Text>
  );
}
