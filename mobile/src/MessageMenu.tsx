import Ionicons from "@expo/vector-icons/Ionicons";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";
import React, { useEffect, useState } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  ownReactions,
  quickReactions,
  type ReactionOptions,
} from "../../shared/reactions";
import { mediaLabel, type NativeMessage } from "./conversation";
import type { MediaAction } from "./Media";
import { usePalette } from "./theme";

export type MenuTarget = {
  // The message a reply answers: a group's latest source in AI reading, as
  // the web's group menu does (ADR 27).
  reply: number | null;
  // The visible wording: the AI text in AI reading, else the original.
  copy: string;
  // A group bot's author peer, for a bot whose posts Hide Bots or its
  // remembered ignore hides and that is not exempt yet (ADR 42 Q3).
  bot?: string | null;
  // The row's voice and video notes and its files (ADR 46 CU1).
  transcripts?: MediaAction[];
  share?: MediaAction[];
  // A private chat's current sources the owner may react to (ADR 46 CU2).
  react?: NativeMessage[];
  // A failed block's Retry, and showing one block's AI text or originals
  // against the reading view (CU3).
  retry?: () => void;
  toggleView?: { label: string; run: () => void };
  // Starts Message Selection with this original (CU4).
  select?: () => void;
  // Hides a deleted message for good (ADR 52 Q11).
  hide?: () => void;
};

// What the reaction row shows: the palette as it loads, or why it can't.
export type ReactionPalette = ReactionOptions | "unavailable" | null;

// A source in the "which message" list: its text, media or plainly Message.
function excerpt(message: NativeMessage) {
  const text = message.text.trim();
  if (text) return text.length > 60 ? `${text.slice(0, 59)}…` : text;
  return mediaLabel(message) ?? "Message";
}

// The long-press message menu (ADR 26): in a private chat a reaction row
// (ADR 46 CU2), Reply and Copy, Copy transcript and Share for a row's media
// (CU1), and in a group Stop ignoring this bot on a bot's original.
export function MessageMenu({
  target,
  replyAllowed,
  palette: reactions = null,
  onReply,
  onCopy,
  onCopyTranscript,
  onShare,
  onReact,
  onStopIgnoring,
  onClose,
}: {
  target: MenuTarget | null;
  replyAllowed: boolean;
  palette?: ReactionPalette;
  onReply: (identity: number) => void;
  onCopy: (text: string) => void;
  onCopyTranscript: (message: NativeMessage) => void;
  onShare: (message: NativeMessage) => void;
  onReact?: (message: NativeMessage, emoji: string) => void;
  onStopIgnoring?: (author: string) => void;
  onClose: () => void;
}) {
  const palette = usePalette();
  const [more, setMore] = useState(false);
  // An emoji waiting for its message, when the row has several.
  const [choosing, setChoosing] = useState<string | null>(null);
  useEffect(() => {
    if (target) return;
    setMore(false);
    setChoosing(null);
  }, [target]);
  const sources = target?.react ?? [];
  const reacting = !!onReact && sources.length > 0;
  const own = sources.length === 1 ? ownReactions(sources[0]!.reactions) : [];
  const emoji =
    reactions && reactions !== "unavailable"
      ? quickReactions(reactions.emoji, own, more)
      : own;
  function react(choice: string) {
    if (sources.length > 1) return setChoosing(choice);
    onReact?.(sources[0]!, choice);
    onClose();
  }
  const items: {
    label: string;
    // The reading view's robot (ADR 52 Q15), never sparkles.
    icon: React.ComponentProps<typeof Ionicons>["name"] | "robot";
    disabled: boolean;
    run: () => void;
  }[] = target
    ? [
        {
          label: "Reply",
          icon: "arrow-undo-outline",
          disabled: !replyAllowed || target.reply === null,
          run: () => target.reply !== null && onReply(target.reply),
        },
        {
          label: "Copy",
          icon: "copy-outline",
          disabled: !target.copy,
          run: () => onCopy(target.copy),
        },
        ...(target.select
          ? [
              {
                label: "Select",
                icon: "checkmark-circle-outline" as const,
                disabled: false,
                run: target.select,
              },
            ]
          : []),
        ...(target.toggleView
          ? [
              {
                label: target.toggleView.label,
                icon: "robot" as const,
                disabled: false,
                run: target.toggleView.run,
              },
            ]
          : []),
        ...(target.hide
          ? [
              {
                label: "Hide",
                icon: "eye-off-outline" as const,
                disabled: false,
                run: target.hide,
              },
            ]
          : []),
        ...(target.retry
          ? [
              {
                label: "Retry",
                icon: "refresh" as const,
                disabled: false,
                run: target.retry,
              },
            ]
          : []),
        ...(target.transcripts ?? []).map((item) => ({
          label: item.label,
          icon: "document-text-outline" as const,
          disabled: false,
          run: () => onCopyTranscript(item.message),
        })),
        ...(target.share ?? []).map((item) => ({
          label: item.label,
          icon: "share-outline" as const,
          disabled: false,
          run: () => onShare(item.message),
        })),
        ...(target.bot && onStopIgnoring
          ? [
              {
                label: "Stop ignoring this bot",
                icon: "eye-outline" as const,
                disabled: false,
                run: () => target.bot && onStopIgnoring(target.bot),
              },
            ]
          : []),
      ]
    : [];
  return (
    <Modal
      visible={!!target}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={styles.backdrop}>
        {/* Behind the sheet, so its items stay separate for screen readers. */}
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
          {choosing ? (
            <View accessibilityLabel="Message actions">
              <View style={styles.heading}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Back to message actions"
                  hitSlop={8}
                  onPress={() => setChoosing(null)}
                >
                  <Ionicons
                    name="chevron-back"
                    size={22}
                    color={palette.accent}
                  />
                </Pressable>
                <Text style={[styles.label, { color: palette.text }]}>
                  {`React ${choosing} to which message?`}
                </Text>
              </View>
              {sources.map((source) => {
                const mine = ownReactions(source.reactions).includes(choosing);
                return (
                  <Pressable
                    key={source.message_id}
                    accessibilityRole="menuitem"
                    accessibilityLabel={`${mine ? "Remove" : "React"} ${choosing}: ${excerpt(source)}`}
                    onPress={() => {
                      onReact?.(source, choosing);
                      onClose();
                    }}
                    style={styles.source}
                  >
                    <Text
                      numberOfLines={2}
                      style={[styles.sourceText, { color: palette.text }]}
                    >
                      {excerpt(source)}
                    </Text>
                    {mine ? (
                      <Text style={[styles.small, { color: palette.muted }]}>
                        {`Remove your ${choosing}`}
                      </Text>
                    ) : null}
                  </Pressable>
                );
              })}
            </View>
          ) : (
            <View accessibilityLabel="Message actions">
              {reacting ? (
                <View
                  accessibilityLabel="React"
                  style={[styles.reactions, { borderColor: palette.line }]}
                >
                  {reactions === "unavailable" ? (
                    <Text style={[styles.small, { color: palette.muted }]}>
                      Reactions are unavailable right now.
                    </Text>
                  ) : (
                    emoji.map((choice) => {
                      const chosen = own.includes(choice);
                      return (
                        <Pressable
                          key={choice}
                          accessibilityRole="menuitem"
                          accessibilityLabel={`${chosen ? "Remove reaction" : "React"} ${choice}`}
                          accessibilityState={{ selected: chosen }}
                          onPress={() => react(choice)}
                          style={[
                            styles.emoji,
                            chosen ? { backgroundColor: palette.panel } : null,
                          ]}
                        >
                          <Text style={styles.emojiText}>{choice}</Text>
                        </Pressable>
                      );
                    })
                  )}
                  {reactions &&
                  reactions !== "unavailable" &&
                  !more &&
                  reactions.emoji.length > 6 ? (
                    <Pressable
                      accessibilityRole="menuitem"
                      accessibilityLabel="More reactions"
                      onPress={() => setMore(true)}
                      style={styles.emoji}
                    >
                      <Ionicons name="add" size={24} color={palette.accent} />
                    </Pressable>
                  ) : null}
                </View>
              ) : null}
              {items.map((item) => (
                <Pressable
                  key={item.label}
                  accessibilityRole="menuitem"
                  accessibilityLabel={item.label}
                  accessibilityState={{ disabled: item.disabled }}
                  disabled={item.disabled}
                  onPress={() => {
                    item.run();
                    onClose();
                  }}
                  style={[styles.item, { opacity: item.disabled ? 0.4 : 1 }]}
                >
                  {item.icon === "robot" ? (
                    <MaterialCommunityIcons
                      name="robot-outline"
                      size={20}
                      color={palette.accent}
                    />
                  ) : (
                    <Ionicons
                      name={item.icon}
                      size={20}
                      color={palette.accent}
                    />
                  )}
                  <Text style={[styles.label, { color: palette.text }]}>
                    {item.label}
                  </Text>
                </Pressable>
              ))}
            </View>
          )}
        </SafeAreaView>
      </View>
    </Modal>
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
  reactions: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 14,
    paddingBottom: 8,
    marginBottom: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  emoji: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: "center",
    justifyContent: "center",
  },
  emojiText: { fontSize: 24 },
  heading: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  source: { paddingHorizontal: 20, paddingVertical: 12, gap: 2 },
  sourceText: { fontSize: 16 },
  small: { fontSize: 13 },
  item: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingHorizontal: 20,
    paddingVertical: 14,
  },
  label: { fontSize: 17, flexShrink: 1 },
});
