import Ionicons from "@expo/vector-icons/Ionicons";
import * as Clipboard from "expo-clipboard";
import React, { useState } from "react";
import { Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { messageTime, type HourCycle } from "../../shared/messageTime";
import type { ReplyQuoteText } from "../../shared/replyQuote";
import { useConnection } from "./connection";
import type { Operation } from "./drafts";
import { ReplyQuote } from "./ReplyQuote";
import { SendResumeReview } from "./SendResumeReview";
import { runCorners, Tail } from "./Tail";
import type { RunPlace } from "../../shared/runs";
import { usePalette, type Palette } from "./theme";

const SENDING = ["queued", "prepared", "dispatching"];

// The web's status words (`PendingSends.tsx`).
export function sendStatus(operation: Operation) {
  if (operation.state === "queued" && operation.cause === "reply_deletion")
    return "Send held";
  // Proven unsent when group access was lost: Not sent until it is
  // reviewed and resumed (ADR 34, ADR 46 CU6).
  if (operation.resumable) return "Not sent";
  if (SENDING.includes(operation.state)) return "Sending";
  return {
    confirmed: "Sent",
    refused: "Not sent",
    unknown: "Delivery unknown",
    cancelled: "Cancelled",
  }[operation.state as "confirmed" | "refused" | "unknown" | "cancelled"];
}

// The next whole minute, when a refused send may be retried.
function when(seconds: number, timezone: string, hourCycle?: HourCycle) {
  return messageTime(Math.ceil(seconds / 60) * 60, timezone, hourCycle);
}

// A saved submission without its native message yet (ADR 26). It is never
// evidence of a Telegram message; Retry, Check delivery and Cancel are the
// service's own guarded actions. It looks like a sent message, its state an
// icon in the time line (ADR 51 Q3); a long press, or a tap on the "!" of a
// send that needs the owner, opens a sheet with exactly the actions the
// service allows (Q4).
export function PendingSend({
  account,
  peer,
  operation,
  timezone,
  hourCycle,
  reply,
  onJump,
  onChange,
  run = { first: true, last: true },
}: {
  account: number;
  peer: string;
  operation: Operation;
  // Its place in its Message Run (ADR 51 Q13).
  run?: RunPlace;
  timezone: string;
  hourCycle: HourCycle | undefined;
  // The quote of the message it replies to (ADR 41).
  reply?: ReplyQuoteText | null;
  onJump?: (target: number) => void;
  onChange: () => Promise<void>;
}) {
  const palette = usePalette();
  const { api, connection } = useConnection();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [sheet, setSheet] = useState(false);
  // Each request opens the paused send's review (ADR 46 CU6).
  const [review, setReview] = useState(0);
  const local = operation.id === "local";
  const sending = SENDING.includes(operation.state);
  const held =
    operation.state === "queued" && operation.cause === "reply_deletion";
  const status = sendStatus(operation);
  const needs =
    !!operation.resumable ||
    operation.state === "refused" ||
    operation.state === "unknown";

  async function act(action: "retry" | "cancel" | "recheck") {
    if (busy || local) return;
    setBusy(true);
    setNote("");
    try {
      const result = await api<Operation>(
        `accounts/${account}/operations/${operation.id}/${action}`,
        { body: {} },
      );
      if (result.check) setNote(result.check);
      await onChange();
    } catch (error) {
      setNote(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  const actions: SheetAction[] = local
    ? []
    : [
        ...(operation.state === "refused" && !operation.resumable
          ? [
              {
                label: "Retry",
                icon: "refresh" as const,
                disabled: busy || !operation.retryable,
                run: () => void act("retry"),
              },
            ]
          : []),
        ...(operation.resumable
          ? [
              {
                label: "Review and resume",
                icon: "play" as const,
                disabled: busy,
                run: () => setReview((count) => count + 1),
              },
            ]
          : []),
        ...(operation.checkable
          ? [
              {
                label: "Check delivery",
                icon: "search" as const,
                disabled: busy,
                run: () => void act("recheck"),
              },
            ]
          : []),
        ...(operation.cancellable
          ? [
              {
                label: "Cancel send",
                icon: "close" as const,
                disabled: busy,
                run: () => void act("cancel"),
              },
            ]
          : []),
        ...(!sending && operation.state !== "confirmed" && operation.text
          ? [
              {
                label: "Copy",
                icon: "copy-outline" as const,
                disabled: false,
                run: () =>
                  void Clipboard.setStringAsync(operation.text).then(() =>
                    setNote("Copied"),
                  ),
              },
            ]
          : []),
      ];
  const reason =
    (operation.state === "refused" || held) && operation.reason
      ? `${operation.reason}${
          operation.retry_after && !operation.retryable
            ? ` Retry after ${when(operation.retry_after, timezone, hourCycle)}.`
            : ""
        }`
      : "";
  const muted = palette.onAccent;
  const credential =
    connection.status === "paired" ? connection.credential : null;
  return (
    <>
      <Pressable
        accessibilityLabel={`${status} message`}
        accessibilityHint={local ? undefined : "Long press for message actions"}
        delayLongPress={350}
        onLongPress={
          local || !(actions.length || reason)
            ? undefined
            : () => setSheet(true)
        }
        style={[
          styles.bubble,
          { backgroundColor: palette.accent },
          run.first ? null : styles.follow,
          runCorners(run, "right"),
          operation.state === "cancelled" ? styles.cancelled : null,
        ]}
      >
        {run.last ? <Tail side="right" color={palette.accent} /> : null}
        {operation.reply_to && reply ? (
          <ReplyQuote quote={reply} palette={palette} onJump={onJump} />
        ) : null}
        {credential && operation.images.length ? (
          <View style={styles.images}>
            {operation.images.map((id, index) => (
              <Image
                key={id}
                accessibilityLabel={`Submitted image ${index + 1}`}
                // An array: React Native's Android Image sends headers only from one.
                source={[
                  {
                    uri: `${credential.origin}/api/accounts/${account}/chats/${encodeURIComponent(peer)}/images/${id}`,
                    headers: { Authorization: `Bearer ${credential.token}` },
                  },
                ]}
                style={[styles.image, { backgroundColor: palette.line }]}
              />
            ))}
          </View>
        ) : null}
        {/* A GIF or sticker names itself until Telegram's message shows. */}
        {operation.media ? (
          <Text style={[styles.small, { color: muted }]}>
            {operation.media.kind === "sticker" ? "Sticker" : "GIF"}
          </Text>
        ) : null}
        {operation.text ? (
          <Text style={[styles.text, { color: palette.onAccent }]}>
            {operation.text}
          </Text>
        ) : null}
        <View style={styles.meta}>
          <Text style={[styles.time, { color: muted }]}>
            {messageTime(operation.created, timezone, hourCycle)}
          </Text>
          {needs ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`${status}: message actions`}
              hitSlop={8}
              onPress={() => setSheet(true)}
              style={[styles.alert, { backgroundColor: palette.background }]}
            >
              <Ionicons name="alert-circle" size={14} color={palette.error} />
              <Text style={[styles.alertLabel, { color: palette.error }]}>
                {status}
              </Text>
            </Pressable>
          ) : sending ? (
            <>
              <Ionicons
                accessibilityLabel={status}
                name="time-outline"
                size={14}
                color={muted}
              />
              {held ? (
                <Text style={[styles.time, { color: muted }]}>Held</Text>
              ) : null}
            </>
          ) : operation.state === "confirmed" ? (
            <Ionicons
              accessibilityLabel="Sent"
              name="checkmark"
              size={14}
              color={muted}
            />
          ) : (
            <Text style={[styles.time, { color: muted }]}>{status}</Text>
          )}
        </View>
        {note ? (
          <Text
            accessibilityRole="alert"
            style={[styles.small, { color: muted }]}
          >
            {note}
          </Text>
        ) : null}
      </Pressable>
      {operation.resumable ? (
        <SendResumeReview
          account={account}
          peer={peer}
          operation={operation}
          reply={reply}
          palette={palette}
          onChange={onChange}
          request={review}
        />
      ) : null}
      <PendingSheet
        visible={sheet}
        title={reason || status}
        actions={actions}
        palette={palette}
        onClose={() => setSheet(false)}
      />
    </>
  );
}

type SheetAction = {
  label: string;
  icon: React.ComponentProps<typeof Ionicons>["name"];
  disabled: boolean;
  run: () => void;
};

// The pending send's own sheet, laid out as the message sheet is.
function PendingSheet({
  visible,
  title,
  actions,
  palette,
  onClose,
}: {
  visible: boolean;
  title: string;
  actions: SheetAction[];
  palette: Palette;
  onClose: () => void;
}) {
  return (
    <Modal
      visible={visible}
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
          <View accessibilityLabel="Message actions">
            <Text style={[styles.sheetTitle, { color: palette.muted }]}>
              {title}
            </Text>
            {actions.map((item) => (
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
                <Ionicons name={item.icon} size={20} color={palette.accent} />
                <Text style={[styles.label, { color: palette.text }]}>
                  {item.label}
                </Text>
              </Pressable>
            ))}
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  // The sent bubble's own look (`app/chat/[account]/[peer].tsx`).
  bubble: {
    alignSelf: "flex-end",
    maxWidth: "88%",
    borderRadius: 14,
    padding: 10,
    gap: 4,
  },
  cancelled: { opacity: 0.6 },
  follow: { marginTop: -6 },
  small: { fontSize: 13 },
  text: { fontSize: 16, lineHeight: 22 },
  time: { fontSize: 12 },
  meta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    alignSelf: "flex-end",
  },
  alert: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    borderRadius: 10,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  alertLabel: { fontSize: 12, fontWeight: "600" },
  images: { flexDirection: "row", flexWrap: "wrap", gap: 4 },
  image: { width: 96, height: 96, borderRadius: 8 },
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
  sheetTitle: { fontSize: 14, paddingHorizontal: 20, paddingVertical: 8 },
  item: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingHorizontal: 20,
    paddingVertical: 14,
  },
  label: { fontSize: 17 },
});
