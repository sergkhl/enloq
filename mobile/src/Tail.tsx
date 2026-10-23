import React from "react";
import { StyleSheet, View } from "react-native";
import type { RunPlace } from "../../shared/runs";

// The tail on a Message Run's last bubble (ADR 51 Q13), on the bubble's own
// side: a small wedge in the bubble's colour, drawn first inside the bubble
// so its content stays on top and nothing beside it (a member's avatar) is
// covered. The web draws the same wedge with a curve (`bubble.css`).
export function Tail({
  side,
  color,
}: {
  side: "left" | "right";
  color: string;
}) {
  return (
    <View
      pointerEvents="none"
      style={
        side === "right"
          ? [styles.tail, styles.right, { borderLeftColor: color }]
          : [styles.tail, styles.left, { borderRightColor: color }]
      }
    />
  );
}

// A run's corners on the bubble's own side: tighter between its bubbles, and
// nearly square where the tail joins.
export function runCorners(run: RunPlace, side: "left" | "right") {
  const top = run.first ? 14 : 6;
  const bottom = run.last ? 4 : 6;
  return side === "right"
    ? { borderTopRightRadius: top, borderBottomRightRadius: bottom }
    : { borderTopLeftRadius: top, borderBottomLeftRadius: bottom };
}

const styles = StyleSheet.create({
  tail: {
    position: "absolute",
    bottom: 0,
    width: 0,
    height: 0,
    borderTopWidth: 14,
    borderTopColor: "transparent",
  },
  right: { right: -7, borderLeftWidth: 10 },
  left: { left: -7, borderRightWidth: 10 },
});
