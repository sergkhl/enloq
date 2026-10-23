import Ionicons from "@expo/vector-icons/Ionicons";
import React from "react";
import {
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { attachmentCard } from "../../shared/attachments";
import type { ForwardedLine } from "../../shared/forwarding";
import { chipLabel, chipName } from "../../shared/reactions";
import {
  canGoEarlier,
  canGoLater,
  DELETED_NAME,
  editedName,
  versionCounter,
} from "../../shared/versions";
import { useBubbleMenu } from "./bubbleMenu";
import type { NativeMessage } from "./conversation";
import type { ReadingView } from "./data";
import type { useVersions } from "./details";
import type { Palette } from "./theme";

// A message's details in the Enloq app's design (ADR 46 CU2): reaction
// chips, forwarded and imported lines and attachment cards.

// Chips under a message: not tappable; adding or removing the owner's own
// starts from the sheet. With several sources each names its message.
export function ReactionChips({
  sources,
  palette,
  inverse = false,
}: {
  sources: NativeMessage[];
  palette: Palette;
  inverse?: boolean;
}) {
  const shown = sources.flatMap((message) =>
    (message.reactions?.chips ?? []).map((chip) => ({ chip, message })),
  );
  if (!shown.length) return null;
  const several = sources.length > 1;
  const ink = inverse ? palette.onAccent : palette.text;
  return (
    <View accessibilityLabel="Reactions" style={styles.chips}>
      {shown.map(({ chip, message }, index) => (
        <View
          key={`${message.message_id}-${index}`}
          accessible
          accessibilityLabel={`${chipName(chip)}${several ? ` on message ${message.message_id}` : ""}`}
          style={[
            styles.chip,
            {
              backgroundColor: inverse
                ? "rgba(255,255,255,0.18)"
                : palette.background,
              borderColor: chip.mine
                ? inverse
                  ? palette.onAccent
                  : palette.accent
                : "transparent",
            },
          ]}
        >
          <Text style={[styles.chipLabel, { color: ink }]}>
            {chipLabel(chip)}
          </Text>
          {chip.count > 1 ? (
            <Text style={[styles.chipCount, { color: ink }]}>{chip.count}</Text>
          ) : null}
        </View>
      ))}
    </View>
  );
}

export function ForwardedLines({
  lines,
  palette,
  inverse = false,
}: {
  lines: ForwardedLine[];
  palette: Palette;
  inverse?: boolean;
}) {
  if (!lines.length) return null;
  const color = inverse ? palette.onAccent : palette.muted;
  return (
    <View style={styles.forwards}>
      {lines.map((line, index) => (
        <View key={index} style={styles.forward}>
          <Ionicons
            name={line.imported ? "download-outline" : "arrow-redo-outline"}
            size={14}
            color={color}
          />
          <Text style={[styles.forwardText, { color }]}>{line.text}</Text>
        </View>
      ))}
    </View>
  );
}

// In Originals, Telegram's earlier versions of an edited original: Previous,
// "2/3" and Next. Elsewhere, or with none captured, a pencil says it was
// edited (ADR 46 CU2). A deleted message adds a trash (ADR 52 Q7).
export function VersionControls({
  message,
  versions,
  view,
  color,
}: {
  message: NativeMessage;
  versions: ReturnType<typeof useVersions>;
  view: ReadingView;
  color: string;
}) {
  const state = { count: versions.count, index: versions.index };
  const trash = message.deleted ? (
    <Ionicons
      accessible
      accessibilityLabel={DELETED_NAME}
      name="trash-outline"
      size={12}
      color={color}
    />
  ) : null;
  if (view === "originals" && versions.count > 0)
    return (
      <>
        <View
          accessibilityLabel={`Versions of message ${message.message_id}`}
          style={styles.versions}
        >
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Previous version"
            accessibilityState={{ disabled: !canGoEarlier(state) }}
            disabled={versions.busy || !canGoEarlier(state)}
            hitSlop={6}
            onPress={versions.earlier}
            style={{ opacity: canGoEarlier(state) ? 1 : 0.35 }}
          >
            <Ionicons name="chevron-back" size={16} color={color} />
          </Pressable>
          <Text style={[styles.counter, { color }]}>
            {versionCounter(state)}
          </Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Next version"
            accessibilityState={{ disabled: !canGoLater(state) }}
            disabled={!canGoLater(state)}
            hitSlop={6}
            onPress={versions.later}
            style={{ opacity: canGoLater(state) ? 1 : 0.35 }}
          >
            <Ionicons name="chevron-forward" size={16} color={color} />
          </Pressable>
        </View>
        {trash}
      </>
    );
  if (!message.native_edited && versions.count === 0) return trash;
  return (
    <>
      <Ionicons
        accessible
        accessibilityLabel={editedName(
          view === "originals" ? 0 : versions.count,
        )}
        name="pencil"
        size={12}
        color={color}
      />
      {trash}
    </>
  );
}

// The maps app's search for a venue: Apple Maps on iPhones, the default
// maps app on Android.
const mapsSearch = (place: string) =>
  Platform.OS === "ios"
    ? `https://maps.apple.com/?q=${encodeURIComponent(place)}`
    : `geo:0,0?q=${encodeURIComponent(place)}`;

const ICONS = {
  poll: "stats-chart-outline",
  location: "location-outline",
  contact: "person-circle-outline",
  link: "link-outline",
} as const;

const KINDS = {
  poll: "Poll",
  location: "Location",
  contact: "Contact",
  link: "Link",
} as const;

// A poll, a location, a contact or a link preview (ADR 46 Q9).
export function AttachmentCardView({
  message,
  palette,
  inverse,
}: {
  message: NativeMessage;
  palette: Palette;
  inverse: boolean;
}) {
  const menu = useBubbleMenu();
  const card = attachmentCard(message);
  if (!card) return null;
  const color = inverse ? palette.onAccent : palette.text;
  const muted = inverse ? palette.onAccent : palette.muted;
  const target =
    card.kind === "location" && card.place
      ? mapsSearch(card.place)
      : card.kind === "link"
        ? card.url
        : null;
  const content = (
    <>
      <Ionicons
        name={ICONS[card.kind]}
        size={24}
        color={inverse ? palette.onAccent : palette.accent}
      />
      <View style={styles.cardText}>
        <Text style={[styles.cardKind, { color: muted }]}>
          {card.kind === "link" && card.site ? card.site : KINDS[card.kind]}
        </Text>
        <Text style={[styles.cardTitle, { color }]}>{card.title}</Text>
        {card.kind === "poll"
          ? card.options.map((option, index) => (
              <Text key={index} style={[styles.cardOption, { color }]}>
                {`• ${option}`}
              </Text>
            ))
          : null}
      </View>
    </>
  );
  const style = [
    styles.card,
    { borderColor: inverse ? palette.onAccent : palette.line },
  ];
  if (!target)
    return (
      <View accessible style={style}>
        {content}
      </View>
    );
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityHint={
        card.kind === "location" ? "Opens maps" : "Opens the link"
      }
      onPress={() => void Linking.openURL(target)}
      {...menu}
      style={style}
    >
      {content}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 4 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    borderWidth: 1.5,
    borderRadius: 12,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  chipLabel: { fontSize: 15 },
  chipCount: { fontSize: 12, fontWeight: "600" },
  forwards: { gap: 2 },
  forward: { flexDirection: "row", alignItems: "center", gap: 4 },
  forwardText: { fontSize: 13, flexShrink: 1 },
  card: {
    flexDirection: "row",
    gap: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    padding: 10,
    minWidth: 200,
  },
  cardText: { flexShrink: 1, gap: 2 },
  cardKind: { fontSize: 12 },
  cardTitle: { fontSize: 15, fontWeight: "600" },
  cardOption: { fontSize: 14 },
  versions: { flexDirection: "row", alignItems: "center", gap: 4 },
  counter: { fontSize: 12 },
});
