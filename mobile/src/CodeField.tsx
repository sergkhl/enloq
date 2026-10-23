import React from "react";
import { Platform, StyleSheet, TextInput } from "react-native";
import { usePalette } from "./theme";

// A one-time code's single field (ADR 47 Q3): digits only, with the phone's
// code autofill, and the last digit, typed or pasted, submits the code. The
// field keeps no maximum length, so a pasted code with spaces keeps all its
// digits. A field whose length is unknown submits only from its button or
// the keyboard's return. A disabled field, such as one whose code is being
// checked, never submits again.
export function CodeField({
  label,
  value,
  length,
  disabled = false,
  secure = false,
  large = false,
  onChange,
  onComplete,
}: {
  label: string;
  value: string;
  length: number | null;
  disabled?: boolean;
  // Telegram's login code stays masked, as before.
  secure?: boolean;
  // The pairing code is shown large, as the code the phone compares.
  large?: boolean;
  onChange: (value: string) => void;
  onComplete: (value: string) => void;
}) {
  const palette = usePalette();
  return (
    <TextInput
      accessibilityLabel={label}
      autoFocus
      editable={!disabled}
      inputMode="numeric"
      textContentType="oneTimeCode"
      autoComplete={Platform.OS === "android" ? "sms-otp" : "one-time-code"}
      placeholder={length ? "•".repeat(length) : undefined}
      placeholderTextColor={palette.muted}
      secureTextEntry={secure}
      value={value}
      onChangeText={(text) => {
        const digits = text.replace(/[^0-9]/g, "");
        const next = length ? digits.slice(0, length) : digits;
        onChange(next);
        if (length && next.length === length && !disabled) onComplete(next);
      }}
      onSubmitEditing={() => {
        if (value && !disabled && (!length || value.length === length))
          onComplete(value);
      }}
      style={[
        styles.input,
        large ? styles.large : null,
        {
          color: palette.text,
          borderColor: palette.line,
          backgroundColor: palette.panel,
        },
      ]}
    />
  );
}

const styles = StyleSheet.create({
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
  },
  large: { fontSize: 28, letterSpacing: 6, textAlign: "center" },
});
