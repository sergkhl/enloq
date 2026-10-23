import Ionicons from "@expo/vector-icons/Ionicons";
import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import type { ReplyQuoteText } from "../../shared/replyQuote";
import type { Palette } from "./theme";

const ICONS: Record<string, keyof typeof Ionicons.glyphMap> = {
  image: "image-outline",
  audio: "mic-outline",
  video_note: "videocam-outline",
  video: "film-outline",
  gif: "film-outline",
  sticker: "happy-outline",
  document: "document-outline",
  poll: "list-outline",
  location: "location-outline",
  contact: "person-outline",
};

// What a screen reader announces for a quote.
export function quoteLabel(quote: ReplyQuoteText) {
  const body = quote.deleted ? `deleted message, ${quote.body}` : quote.body;
  if (!quote.sender) return body;
  const date = quote.date ? `, ${quote.date}` : "";
  return `Reply to ${quote.sender}${date}: ${body}`;
}

// The Reply Quote (ADR 41): a tinted box with an accent bar holding who wrote
// the replied-to message and one line of it. Only a message in this chat can
// be tapped; on the accent background of an outgoing bubble it inverts.
export function ReplyQuote({
  quote,
  palette,
  inverse = false,
  heading,
  onJump,
}: {
  quote: ReplyQuoteText;
  palette: Palette;
  inverse?: boolean;
  // The composer's own first line, such as "Reply to Anna".
  heading?: string;
  onJump?: (target: number) => void;
}) {
  const strong = inverse ? palette.onAccent : palette.accent;
  const color = inverse ? palette.onAccent : palette.text;
  const muted = quote.kind === "pending" || quote.kind === "unknown";
  const first =
    heading ??
    (quote.sender
      ? `${quote.sender}${quote.date ? ` · ${quote.date}` : ""}`
      : null);
  const icon = quote.media ? ICONS[quote.media] : undefined;
  const content = (
    <View
      style={[
        styles.quote,
        // The accent or the outgoing bubble's own text, faintly (#RRGGBBAA).
        { backgroundColor: `${strong}1f` },
      ]}
    >
      <View style={[styles.rule, { backgroundColor: strong }]} />
      <View style={styles.fill}>
        {first ? (
          <Text numberOfLines={1} style={[styles.sender, { color: strong }]}>
            {first}
          </Text>
        ) : null}
        <View style={styles.line}>
          {/* What a deleted message last said (ADR 52 Q10). */}
          {quote.deleted ? (
            <Ionicons name="trash-outline" size={14} color={color} />
          ) : null}
          {icon ? <Ionicons name={icon} size={14} color={color} /> : null}
          <Text
            numberOfLines={1}
            style={[
              styles.body,
              { color: muted && !inverse ? palette.muted : color },
              quote.quoted ? styles.quoted : null,
            ]}
          >
            {quote.body}
          </Text>
        </View>
      </View>
    </View>
  );
  const label = quoteLabel(quote);
  if (onJump && quote.target !== null) {
    const target = quote.target;
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        onPress={() => onJump(target)}
      >
        {content}
      </Pressable>
    );
  }
  return (
    <View accessible accessibilityLabel={label}>
      {content}
    </View>
  );
}

const styles = StyleSheet.create({
  quote: {
    flexDirection: "row",
    gap: 8,
    borderRadius: 8,
    paddingVertical: 4,
    paddingRight: 8,
    overflow: "hidden",
  },
  rule: { width: 3 },
  fill: { flex: 1, gap: 1 },
  line: { flexDirection: "row", alignItems: "center", gap: 4 },
  sender: { fontSize: 13, fontWeight: "600" },
  body: { fontSize: 13, flexShrink: 1 },
  quoted: { fontStyle: "italic" },
});
