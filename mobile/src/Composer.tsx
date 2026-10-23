import Ionicons from "@expo/vector-icons/Ionicons";
import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import type { Schemas } from "./api";
import { useConnection } from "./connection";
import { participantLabel } from "../../shared/participantLabel";
import { replyQuoteText } from "../../shared/replyQuote";
import type { NativeMessage, Snapshot } from "./conversation";
import { TONES, useTone, type Tone } from "./data";
import type { Composer } from "./drafts";
import { MediaPicker, type PickerKind } from "./MediaPicker";
import { ReplyQuote } from "./ReplyQuote";
import { SelectedMediaPreview } from "./SelectedMediaPreview";
import { usePalette, type Palette } from "./theme";
import { SettingsLink } from "./settingsLinks";

const PURPOSES: Record<string, string> = {
  acknowledge: "Acknowledge",
  support: "Support",
  clarify: "Clarify",
  celebrate: "Celebrate",
  // Answer stances, always offered side by side (ADR 49 Q4, Q8).
  accept: "Accept",
  decline: "Decline",
  later: "Later",
};

// The web's suggestion statuses (`Suggestions.tsx`).
const SUGGESTION_STATUS: Record<string, string> = {
  queued: "Preparing suggestions…",
  running: "Preparing suggestions…",
  budget: "Suggestions wait for budget",
  paused: "Suggestions wait for AI processing",
  retrying: "Suggestions wait for the provider",
  format: "Suggestions unavailable: format check failed",
  provider: "Suggestions unavailable: provider error",
  refused: "Suggestions unavailable: provider refused",
  interrupted: "Suggestions unavailable: interrupted",
  too_large: "Suggestions unavailable: conversation too long",
};

// The compact composer (ADR 13): Rewrite with its tone, Stop while it
// streams, Undo, and a separate Send. Return inserts a newline, since a
// phone keyboard has no Shift for the web's Enter shortcuts (ADR 22). It
// offers Neutral, Professional and Friendly casual (Adaptive left with ADR
// 53); in a group (ADR 34, ADR 42 Q1) it acts only while the group's native
// access is verified.
export function ComposerBar({
  composer,
  snapshot,
  account,
  peer,
  aiEnabled,
  group = false,
  nativeAvailable = true,
  rewrite = true,
  onSent,
}: {
  composer: Composer;
  snapshot: Snapshot;
  account: number;
  peer: string;
  aiEnabled: boolean;
  group?: boolean;
  nativeAvailable?: boolean;
  // Saved Messages has no AI for now (ADR 50 Q17): no Rewrite, tone or Undo.
  rewrite?: boolean;
  onSent: () => void;
}) {
  const palette = usePalette();
  const { tone, choose } = useTone();
  const tones = Object.keys(TONES) as Tone[];
  const [choosing, setChoosing] = useState(false);
  const [attachMenu, setAttachMenu] = useState(false);
  const [toneNote, setToneNote] = useState("");
  const input = useRef<TextInput>(null);
  // GIFs and Stickers (ADR 46 CU5).
  const [picker, setPicker] = useState<PickerKind | null>(null);
  // iOS presents the photo picker, or another sheet, only once the sheet has
  // closed, so the pick starts from its dismissal, with a timer in case none
  // comes.
  const picking = useRef<"library" | "camera" | PickerKind | null>(null);
  const draft = composer.draft;
  if (!draft) return null;
  const streaming = composer.locked;
  const sticker = draft.selected_media?.kind === "sticker";
  const editable =
    !streaming &&
    !sticker &&
    composer.action !== "send" &&
    composer.action !== "undo";
  const status = !aiEnabled
    ? "AI processing is Off for this account"
    : !nativeAvailable
      ? "Native group access is unavailable"
      : `Rewrite · ${TONES[tone]}`;
  const undoLabel =
    draft.state === "inserted" ? "Undo suggestion" : "Undo rewrite";
  const busy = !!composer.action || streaming;
  const rewritable = composer.canRewrite && aiEnabled && nativeAvailable;
  const sendable = composer.canSend && nativeAvailable;

  function pickAfterSheet() {
    const source = picking.current;
    picking.current = null;
    if (source === "gif" || source === "sticker") setPicker(source);
    else if (source) void composer.attach(source);
  }
  function startPick(source: "library" | "camera" | PickerKind) {
    picking.current = source;
    setAttachMenu(false);
    setTimeout(pickAfterSheet, Platform.OS === "ios" ? 700 : 0);
  }

  async function insert(index: number) {
    await composer.insert(index);
    const caret = composer.caret();
    if (caret !== null) input.current?.setSelection?.(caret, caret);
  }

  return (
    <View
      style={[
        styles.wrap,
        { borderColor: palette.line, backgroundColor: palette.background },
      ]}
    >
      <SuggestionArea
        view={snapshot.suggestions}
        folded={composer.folded}
        disabled={!composer.canInsert}
        palette={palette}
        onChoose={(index) => void insert(index)}
        onFold={(folded) => void composer.fold(folded)}
      />
      {composer.replyTo ? (
        <ReplyTarget
          account={account}
          peer={peer}
          identity={composer.replyTo}
          messages={snapshot.messages}
          title={snapshot.chat.title}
          group={group}
          timezone={snapshot.timezone}
          hold={composer.hold}
          disabled={busy}
          onRemove={() => composer.reply(null)}
          palette={palette}
        />
      ) : null}
      {draft.selected_media ? (
        <SelectedMediaPreview
          media={draft.selected_media}
          keptText={!!composer.text.trim()}
          disabled={busy}
          palette={palette}
          onRemove={() => void composer.discardMedia()}
          onRetry={() => void composer.retryMedia()}
        />
      ) : null}
      {draft.attachments.length ? (
        <Attachments
          ids={draft.attachments}
          account={account}
          peer={peer}
          disabled={busy}
          onRemove={(id) => void composer.removeImage(id)}
          palette={palette}
        />
      ) : null}
      {/* A sticker sends on its own; the text waits as Draft kept. */}
      <TextInput
        ref={input}
        multiline
        accessibilityLabel="Message"
        placeholder="Write your message…"
        placeholderTextColor={palette.muted}
        value={composer.text}
        editable={editable}
        onChangeText={(text) => composer.edit({ text })}
        onSelectionChange={(event) =>
          composer.setCaret(event.nativeEvent.selection.end)
        }
        style={[
          styles.input,
          {
            color: palette.text,
            backgroundColor: palette.panel,
            borderColor: palette.line,
          },
          sticker ? styles.hidden : null,
        ]}
      />
      <View style={styles.row}>
        {composer.action === "attach" ? (
          <ActivityIndicator style={styles.icon} color={palette.accent} />
        ) : (
          <IconButton
            label="Attach"
            icon="attach"
            disabled={busy || !!draft.selected_media || !nativeAvailable}
            onPress={() => setAttachMenu(true)}
            palette={palette}
          />
        )}
        {rewrite ? (
          <>
            <View style={[styles.split, { borderColor: palette.line }]}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Rewrite"
                accessibilityHint={status}
                accessibilityState={{ disabled: !rewritable }}
                disabled={!rewritable}
                onPress={() => void composer.rewrite()}
                style={({ pressed }) => [
                  styles.rewrite,
                  {
                    opacity: rewritable ? 1 : 0.5,
                    backgroundColor: pressed ? palette.panel : "transparent",
                  },
                ]}
              >
                {streaming || composer.action === "rewrite" ? (
                  <ActivityIndicator size="small" color={palette.accent} />
                ) : (
                  <Ionicons name="pencil" size={16} color={palette.accent} />
                )}
                <Text
                  numberOfLines={1}
                  style={[styles.label, { color: palette.accent }]}
                >
                  Rewrite · {TONES[tone]}
                </Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Rewrite tone"
                disabled={busy}
                hitSlop={6}
                onPress={() => setChoosing(true)}
                style={[
                  styles.tone,
                  { borderColor: palette.line, opacity: busy ? 0.5 : 1 },
                ]}
              >
                <Ionicons
                  name="chevron-down"
                  size={16}
                  color={palette.accent}
                />
              </Pressable>
            </View>
            {streaming ? (
              <IconButton
                label="Stop rewrite"
                icon="stop"
                disabled={!!composer.action}
                onPress={() => void composer.stop()}
                palette={palette}
              />
            ) : null}
          </>
        ) : null}
        {rewrite ? (
          <IconButton
            label={undoLabel}
            icon="arrow-undo"
            disabled={!composer.canUndo}
            onPress={() => void composer.undo()}
            palette={palette}
          />
        ) : null}
        <View style={styles.fill} />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Send"
          accessibilityHint={
            nativeAvailable ? undefined : "Native group access is unavailable"
          }
          accessibilityState={{ disabled: !sendable }}
          disabled={!sendable}
          onPress={() => void composer.send().then(onSent)}
          style={[
            styles.send,
            {
              backgroundColor: palette.accent,
              opacity: sendable ? 1 : 0.4,
            },
          ]}
        >
          {composer.action === "send" ? (
            <ActivityIndicator size="small" color={palette.onAccent} />
          ) : (
            <Ionicons name="send" size={18} color={palette.onAccent} />
          )}
        </Pressable>
      </View>
      {!nativeAvailable ? (
        <Text style={[styles.small, { color: palette.muted }]}>
          Native group access is unavailable
        </Text>
      ) : null}
      {streaming || draft.state === "stopped" ? (
        <Text
          accessibilityRole="text"
          style={[styles.small, { color: palette.muted }]}
        >
          {streaming ? "Processing…" : "Stopped · incomplete draft"}
        </Text>
      ) : null}
      {composer.note || toneNote ? (
        <Text
          accessibilityRole="alert"
          style={[styles.small, { color: palette.error }]}
        >
          {composer.note || toneNote}
        </Text>
      ) : null}
      <ChoiceSheet
        visible={choosing}
        title="Rewrite tone"
        palette={palette}
        onClose={() => setChoosing(false)}
        choices={tones.map((option) => ({
          key: option,
          label: TONES[option],
          role: "radio",
          selected: option === tone,
          onPress: async () => {
            setChoosing(false);
            setToneNote("");
            try {
              await choose(option);
            } catch (error) {
              setToneNote(
                error instanceof Error ? error.message : String(error),
              );
            }
          },
        }))}
      />
      <ChoiceSheet
        visible={attachMenu}
        title="Attach"
        palette={palette}
        onClose={() => setAttachMenu(false)}
        onDismiss={pickAfterSheet}
        choices={[
          {
            key: "library",
            label: "Photo library",
            icon: "images-outline",
            onPress: () => startPick("library"),
          },
          {
            key: "camera",
            label: "Take photo",
            icon: "camera-outline",
            onPress: () => startPick("camera"),
          },
          {
            key: "gif",
            label: "GIFs",
            icon: "film-outline",
            onPress: () => startPick("gif"),
          },
          {
            key: "sticker",
            label: "Stickers",
            icon: "happy-outline",
            onPress: () => startPick("sticker"),
          },
        ]}
      />
      <MediaPicker
        account={account}
        peer={peer}
        open={picker}
        disabled={busy}
        onTab={setPicker}
        onClose={() => setPicker(null)}
        onSelect={(item, current) => {
          setPicker(null);
          if (current()) void composer.selectMedia(item);
        }}
      />
    </View>
  );
}

function IconButton({
  label,
  icon,
  disabled,
  onPress,
  palette,
}: {
  label: string;
  icon: React.ComponentProps<typeof Ionicons>["name"];
  disabled: boolean;
  onPress: () => void;
  palette: Palette;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled }}
      disabled={disabled}
      hitSlop={6}
      onPress={onPress}
      style={[styles.icon, { opacity: disabled ? 0.35 : 1 }]}
    >
      <Ionicons name={icon} size={20} color={palette.accent} />
    </Pressable>
  );
}

type Choice = {
  key: string;
  label: string;
  role?: "radio" | "button";
  selected?: boolean;
  icon?: React.ComponentProps<typeof Ionicons>["name"];
  onPress: () => void;
};

// A bottom sheet of choices over a dimmed backdrop that closes it.
function ChoiceSheet({
  visible,
  title,
  choices,
  palette,
  onClose,
  onDismiss,
}: {
  visible: boolean;
  title: string;
  choices: Choice[];
  palette: Palette;
  onClose: () => void;
  onDismiss?: () => void;
}) {
  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
      onDismiss={onDismiss}
    >
      <View style={styles.backdrop}>
        {/* Behind the sheet, so its options stay separate for screen readers. */}
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
          <View>
            <Text style={[styles.heading, { color: palette.muted }]}>
              {title}
            </Text>
            {choices.map((choice) => (
              <Pressable
                key={choice.key}
                accessibilityRole={choice.role ?? "button"}
                accessibilityLabel={choice.label}
                accessibilityState={
                  choice.role === "radio"
                    ? { selected: !!choice.selected }
                    : undefined
                }
                onPress={choice.onPress}
                style={styles.option}
              >
                {choice.icon ? (
                  <Ionicons
                    name={choice.icon}
                    size={20}
                    color={palette.accent}
                  />
                ) : null}
                <Text
                  style={[
                    styles.fill,
                    styles.optionText,
                    { color: palette.text },
                  ]}
                >
                  {choice.label}
                </Text>
                {choice.selected ? (
                  <Ionicons name="checkmark" size={20} color={palette.accent} />
                ) : null}
              </Pressable>
            ))}
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

// The draft's photos, read with this phone's credential, each removable.
function Attachments({
  ids,
  account,
  peer,
  disabled,
  onRemove,
  palette,
}: {
  ids: string[];
  account: number;
  peer: string;
  disabled: boolean;
  onRemove: (id: string) => void;
  palette: Palette;
}) {
  const { connection } = useConnection();
  if (connection.status !== "paired") return null;
  const { origin, token } = connection.credential;
  return (
    <ScrollView
      horizontal
      keyboardShouldPersistTaps="handled"
      contentContainerStyle={styles.thumbs}
    >
      {ids.map((id, index) => (
        <View key={id}>
          <Image
            accessibilityLabel={`Attached image ${index + 1}`}
            // An array: React Native's Android Image sends headers only from one.
            source={[
              {
                uri: `${origin}/api/accounts/${account}/chats/${encodeURIComponent(peer)}/images/${encodeURIComponent(id)}`,
                headers: { Authorization: `Bearer ${token}` },
              },
            ]}
            style={[styles.thumb, { backgroundColor: palette.panel }]}
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Remove image ${index + 1}`}
            disabled={disabled}
            hitSlop={6}
            onPress={() => onRemove(id)}
            style={[
              styles.thumbRemove,
              {
                backgroundColor: palette.background,
                borderColor: palette.line,
              },
            ]}
          >
            <Ionicons name="close" size={14} color={palette.text} />
          </Pressable>
        </View>
      ))}
    </ScrollView>
  );
}

// The draft's reply target as a Reply Quote (ADR 41): "Reply to" its sender
// with one line of it, loaded or read from the service.
function ReplyTarget({
  account,
  peer,
  identity,
  messages,
  title,
  group,
  timezone,
  hold,
  disabled,
  onRemove,
  palette,
}: {
  account: number;
  peer: string;
  identity: number;
  messages: NativeMessage[];
  title: string;
  group: boolean;
  timezone: string;
  hold: string | null;
  disabled: boolean;
  onRemove: () => void;
  palette: Palette;
}) {
  const { api } = useConnection();
  const loaded = messages.find((message) => message.message_id === identity);
  // Undefined while reading; null when the service has no such message.
  const [fetched, setFetched] = useState<NativeMessage | null | undefined>();
  useEffect(() => {
    if (loaded) return;
    let live = true;
    setFetched(undefined);
    api<NativeMessage | null>(
      `accounts/${account}/chats/${encodeURIComponent(peer)}/originals/${identity}`,
    )
      .then((original) => live && setFetched(original ?? null))
      .catch(() => live && setFetched(null));
    return () => {
      live = false;
    };
  }, [account, api, identity, loaded, peer]);
  const quote = replyQuoteText(
    { message_id: 0, date: Date.now() / 1000, reply_to: identity },
    loaded ?? fetched ?? null,
    {
      timezone,
      own: `user:${account}`,
      sender: (target) =>
        participantLabel(target as NativeMessage, title, account, group),
      pending: !loaded && fetched === undefined,
    },
  );
  return (
    <View style={styles.reply}>
      <View style={styles.fill}>
        <ReplyQuote
          quote={quote}
          palette={palette}
          heading={
            quote.sender
              ? `Reply to ${quote.sender}${quote.date ? ` · ${quote.date}` : ""}`
              : undefined
          }
        />
        {hold ? (
          <Text style={[styles.small, { color: palette.warning }]}>{hold}</Text>
        ) : null}
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Remove reply target"
        disabled={disabled}
        hitSlop={8}
        onPress={onRemove}
      >
        <Ionicons name="close" size={18} color={palette.muted} />
      </Pressable>
    </View>
  );
}

// Up to three response options for the latest incoming block (ADR 25);
// choosing one inserts it at the caret. A folded set stays folded on every
// client until the next one arrives (ADR 56 Q12 B).
function SuggestionArea({
  view,
  folded,
  disabled,
  palette,
  onChoose,
  onFold,
}: {
  view: Schemas["SuggestionsView"] | null | undefined;
  folded: boolean;
  disabled: boolean;
  palette: Palette;
  onChoose: (index: number) => void;
  onFold: (folded: boolean) => void;
}) {
  if (!view || view.state === "none") return null;
  if (view.expires_at && view.expires_at * 1000 < Date.now()) return null;
  if (view.state !== "ready" || !view.options?.length) {
    const status = SUGGESTION_STATUS[view.reason ?? ""];
    // Waiting for budget opens the Daily Budget (ADR 45 Q8).
    if (status && view.reason === "budget")
      return (
        <SettingsLink target="daily" style={styles.small}>
          {status}
        </SettingsLink>
      );
    return status ? (
      <Text style={[styles.small, { color: palette.muted }]}>{status}</Text>
    ) : null;
  }
  if (folded)
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Show ${view.options.length} suggestions`}
        accessibilityState={{ expanded: false }}
        onPress={() => onFold(false)}
        style={styles.suggestionsHead}
      >
        <Text style={[styles.purpose, { color: palette.accent }]}>
          {`Suggestions (${view.options.length})`}
        </Text>
        <Ionicons name="chevron-up" size={15} color={palette.accent} />
      </Pressable>
    );
  return (
    <View accessibilityLabel="Suggestions" style={styles.suggestions}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Hide suggestions"
        accessibilityState={{ expanded: true }}
        onPress={() => onFold(true)}
        style={styles.suggestionsHead}
      >
        <Text style={[styles.purpose, { color: palette.muted }]}>
          Suggestions
        </Text>
        <Ionicons name="chevron-down" size={15} color={palette.muted} />
      </Pressable>
      {view.options.map((option, index) => (
        <Pressable
          key={index}
          accessibilityRole="button"
          accessibilityLabel={`${PURPOSES[option.purpose] ?? "Response"}: ${option.text}`}
          accessibilityState={{ disabled }}
          disabled={disabled}
          onPress={() => onChoose(index)}
          style={[
            styles.suggestion,
            {
              backgroundColor: palette.panel,
              borderColor: palette.line,
              opacity: disabled ? 0.5 : 1,
            },
          ]}
        >
          <Text style={[styles.purpose, { color: palette.accent }]}>
            {PURPOSES[option.purpose] ?? "Response"}
          </Text>
          <Text style={[styles.suggestionText, { color: palette.text }]}>
            {option.text}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  hidden: { display: "none" },
  wrap: {
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingTop: 8,
    paddingBottom: 6,
    gap: 6,
  },
  small: { fontSize: 13 },
  input: {
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 8,
    fontSize: 16,
    maxHeight: 140,
  },
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
  split: {
    flexDirection: "row",
    alignItems: "stretch",
    borderWidth: 1,
    borderRadius: 18,
    overflow: "hidden",
    flexShrink: 1,
  },
  rewrite: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingLeft: 10,
    paddingRight: 8,
    paddingVertical: 7,
    flexShrink: 1,
  },
  label: { fontSize: 14, fontWeight: "600", flexShrink: 1 },
  tone: {
    justifyContent: "center",
    paddingHorizontal: 8,
    borderLeftWidth: 1,
  },
  icon: { padding: 4 },
  send: {
    width: 44,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
  },
  quote: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderRadius: 10,
    padding: 8,
  },
  reply: { flexDirection: "row", alignItems: "center", gap: 8 },
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
  heading: { fontSize: 13, paddingHorizontal: 20, paddingVertical: 8 },
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingHorizontal: 20,
    paddingVertical: 14,
  },
  thumbs: { gap: 8, paddingTop: 6, paddingRight: 6 },
  thumb: { width: 64, height: 64, borderRadius: 8 },
  thumbRemove: {
    position: "absolute",
    top: -6,
    right: -6,
    borderWidth: 1,
    borderRadius: 11,
    width: 22,
    height: 22,
    alignItems: "center",
    justifyContent: "center",
  },
  optionText: { fontSize: 17 },
  suggestions: { gap: 6 },
  suggestionsHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: 32,
  },
  suggestion: { borderWidth: 1, borderRadius: 10, padding: 8, gap: 2 },
  purpose: { fontSize: 12, fontWeight: "600" },
  suggestionText: { fontSize: 15 },
});
