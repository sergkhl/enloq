import React, { useEffect, useState } from "react";
import { Image, StyleSheet, Text, View } from "react-native";
import { useConnection } from "./connection";
import { usePalette } from "./theme";

export function initials(name: string) {
  const letters = name
    .split(/\s+/)
    // Words only: separators such as "·" in "Anna · design" are not names.
    .filter((word) => {
      const first = Array.from(word)[0] ?? "";
      return first.toUpperCase() !== first.toLowerCase();
    })
    .slice(0, 2)
    .map((word) => Array.from(word)[0]?.toUpperCase() ?? "");
  return letters.join("") || "?";
}

// A Telegram profile photo for an account or chat, read with this phone's
// credential, with initials until it loads or when there is none.
export function Avatar({
  account,
  peer,
  photoId,
  name,
  size = 40,
  tint,
}: {
  account: number;
  peer: string;
  photoId: string | null | undefined;
  name: string;
  size?: number;
  // A group member's own colour behind white initials (ADR 51 Q13).
  tint?: string;
}) {
  const { connection } = useConnection();
  const palette = usePalette();
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [photoId]);
  const round = { width: size, height: size, borderRadius: size / 2 };
  if (connection.status !== "paired" || !photoId || failed)
    return (
      <View
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
        style={[
          styles.initials,
          round,
          { backgroundColor: tint ?? palette.panel },
        ]}
      >
        <Text
          style={{
            color: tint ? "#ffffff" : palette.accent,
            fontSize: size * 0.38,
            fontWeight: "600",
          }}
        >
          {initials(name)}
        </Text>
      </View>
    );
  const { origin, token } = connection.credential;
  return (
    <Image
      accessibilityIgnoresInvertColors
      // An array: React Native's Android Image sends headers only from one.
      source={[
        {
          uri: `${origin}/api/accounts/${account}/avatars/${encodeURIComponent(peer)}?photo_id=${encodeURIComponent(photoId)}`,
          headers: { Authorization: `Bearer ${token}` },
        },
      ]}
      onError={() => setFailed(true)}
      style={[round, { backgroundColor: palette.panel }]}
    />
  );
}

const styles = StyleSheet.create({
  initials: { alignItems: "center", justifyContent: "center" },
});
