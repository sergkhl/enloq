import Ionicons from "@expo/vector-icons/Ionicons";
import * as Clipboard from "expo-clipboard";
import { Directory, File } from "expo-file-system";
import * as Sharing from "expo-sharing";
import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { cacheFolder } from "./cache";
import { useBubbleMenu } from "./bubbleMenu";
import { useConnection } from "./connection";
import type { Credential } from "./credential";
import type { NativeMessage } from "./conversation";
import type { VoiceSource } from "./playback";
import { usePalette, type Palette } from "./theme";
import {
  showTranscript,
  type TranscriptStatus,
  type TranscriptView,
} from "../../shared/transcripts";
import { Button } from "./ui";
import { useGallery } from "./gallery";

// The service keeps files up to 50 MiB for playback, Share and Download.
export const FILE_LIMIT = 50 * 1024 * 1024;

export const originalPath = (
  account: number,
  peer: string,
  message: NativeMessage,
) =>
  `accounts/${account}/chats/${encodeURIComponent(peer)}/originals/${message.message_id}`;

export type Source = { uri: string; headers: Record<string, string> };

// A message's media bytes from the service, with this phone's credential:
// the file itself, its poster frame, a voice note's AAC copy for iPhones, or
// a GIF's or sticker's checked preview.
export function mediaSource(
  credential: Credential,
  account: number,
  peer: string,
  message: NativeMessage,
  {
    variant,
    path = "media",
    version,
  }: { variant?: "poster" | "aac"; path?: string; version?: number } = {},
): Source {
  // An earlier version's media is read from the cache only (ADR 19).
  const query = `?revision=${message.revision}${variant ? `&variant=${variant}` : ""}${version ? `&version=${version}` : ""}`;
  return {
    uri: `${credential.origin}/api/${originalPath(account, peer, message)}/${path}${query}`,
    headers: { Authorization: `Bearer ${credential.token}` },
  };
}

export function useMediaSource(
  account: number,
  peer: string,
  message: NativeMessage,
  options?: { variant?: "poster" | "aac"; path?: string; version?: number },
) {
  const { connection } = useConnection();
  if (connection.status !== "paired") return null;
  return mediaSource(connection.credential, account, peer, message, options);
}

// Telegram's voice notes: audio marked as voice, or audio with no details.
export const isVoice = (message: NativeMessage) =>
  message.media === "audio" &&
  (!message.media_info || !!message.media_info.voice);

// A voice note for the shared player. iPhones play the service's AAC copy,
// since iOS reads neither Ogg nor Opus (ADR 46 CU1).
export function voiceSource(
  credential: Credential,
  account: number,
  peer: string,
  message: NativeMessage,
  chat: string,
): VoiceSource {
  return {
    key: `${account}/${peer}/${message.message_id}@${message.revision}`,
    ...mediaSource(credential, account, peer, message, {
      variant: Platform.OS === "ios" ? "aac" : undefined,
    }),
    chat,
  };
}

// The chat's voice notes in order, for playing on.
export function voiceQueue(
  credential: Credential | null,
  account: number,
  peer: string,
  messages: NativeMessage[],
  chat: string,
) {
  if (!credential) return [];
  return messages
    .filter((message) => isVoice(message) && !message.deleted)
    .sort((a, b) => a.date - b.date || a.message_id - b.message_id)
    .map((message) => voiceSource(credential, account, peer, message, chat));
}

const SHARED = new Set(["image", "audio", "video_note", "video", "document"]);
const KINDS: Record<string, string> = {
  image: "photo",
  video_note: "video message",
  video: "video",
  document: "file",
};

export const shareable = (message: NativeMessage) =>
  !message.deleted &&
  SHARED.has(message.media ?? "") &&
  (message.media_info?.size ?? 0) <= FILE_LIMIT;

export const spoken = (message: NativeMessage) =>
  !message.deleted && (isVoice(message) || message.media === "video_note");

const kind = (message: NativeMessage) =>
  message.media === "audio"
    ? isVoice(message)
      ? "voice message"
      : "music file"
    : (KINDS[message.media ?? ""] ?? "file");

export type MediaAction = { label: string; message: NativeMessage };

// The sheet's Share and Copy transcript items for a row's sources (ADR 46
// CU1). With several, each names its kind and time.
export function mediaActions(
  messages: NativeMessage[],
  time: (message: NativeMessage) => string,
) {
  const label = (verb: string, list: NativeMessage[], item: NativeMessage) =>
    list.length === 1 ? verb : `${verb} · ${kind(item)} ${time(item)}`;
  const files = messages.filter(shareable);
  const voices = messages.filter(spoken);
  return {
    share: files.map((message) => ({
      label: label("Share", files, message),
      message,
    })),
    transcripts: voices.map((message) => ({
      label: label("Copy transcript", voices, message),
      message,
    })),
  };
}

// Share hands the file to the system share sheet; Copy transcript copies the
// machine transcript. `notify` shows how it went beside the reading bar.
export function useMediaActions(
  account: number,
  peer: string,
  notify: (text: string) => void,
) {
  const { connection, api } = useConnection();
  async function share(message: NativeMessage) {
    if (connection.status !== "paired") return;
    const source = mediaSource(connection.credential, account, peer, message);
    notify("Preparing…");
    try {
      // Beside the offline copy, so forgetting the phone deletes it too.
      const folder = new Directory(
        cacheFolder("shared"),
        `${message.message_id}-${message.revision}`,
      );
      folder.create({ intermediates: true, idempotent: true });
      const photo = message.media === "image";
      const file = await File.downloadFileAsync(
        photo ? source.uri : `${source.uri}&download=1`,
        // The service names other files, as Telegram Desktop does.
        photo ? new File(folder, `photo-${message.message_id}.jpg`) : folder,
        { headers: source.headers, idempotent: true },
      );
      notify("");
      await Sharing.shareAsync(file.uri);
    } catch {
      notify("The file couldn't be shared.");
    }
  }
  async function copyTranscript(message: NativeMessage) {
    try {
      const answer = await api<{ text: string } | null>(
        `${originalPath(account, peer, message)}/transcript`,
      );
      if (!answer?.text) return notify("No transcript yet.");
      await Clipboard.setStringAsync(answer.text);
      notify("Transcript copied");
    } catch {
      notify("The transcript couldn't be loaded.");
    }
  }
  return { share, copyTranscript };
}

// A photo in the conversation opens the chat's Media Carousel (ADR 51 Q10);
// an earlier version's photo opens alone, full screen with Share (ADR 38 Q6).
export function MessageImage({
  account,
  peer,
  message,
  version,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  // An earlier version's photo, from the cache (ADR 46 CU2).
  version?: number;
}) {
  const palette = usePalette();
  const source = useMediaSource(account, peer, message, { version });
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const [sharing, setSharing] = useState("");
  const { share } = useMediaActions(account, peer, setSharing);
  const menu = useBubbleMenu();
  const gallery = useGallery();
  if (!source) return null;
  const { width, height } = message.media_info ?? {};
  const ratio = width && height ? width / height : 4 / 3;
  if (failed)
    return (
      <Text style={[styles.note, { color: palette.muted }]}>
        Photo unavailable
      </Text>
    );

  return (
    <>
      <Pressable
        accessibilityRole="imagebutton"
        accessibilityLabel="Photo, opens full screen"
        onPress={() =>
          gallery && !version ? gallery.open(message) : setOpen(true)
        }
        {...menu}
      >
        <Image
          // An array: React Native's Android Image sends headers only from one.
          source={[source]}
          onError={() => setFailed(true)}
          style={[
            styles.image,
            { aspectRatio: ratio, backgroundColor: palette.panel },
          ]}
        />
      </Pressable>
      <Modal
        visible={open}
        animationType="fade"
        onRequestClose={() => setOpen(false)}
      >
        <SafeAreaView style={styles.viewer}>
          <Image
            source={[source]}
            resizeMode="contain"
            style={styles.full}
            accessibilityLabel="Photo"
          />
          {sharing ? <Text style={styles.viewerNote}>{sharing}</Text> : null}
          <View style={styles.actions}>
            <View style={styles.action}>
              <Button label="Share" onPress={() => void share(message)} />
            </View>
            <View style={styles.action}>
              <Button label="Close" primary onPress={() => setOpen(false)} />
            </View>
          </View>
        </SafeAreaView>
      </Modal>
    </>
  );
}

// A size as Telegram shows it: 48 KB, 3.1 MB.
export function fileSize(bytes: number | null | undefined) {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// A document or a music file: its name and size, opening Share (ADR 46 Q9).
export function FileCard({
  account,
  peer,
  message,
  palette,
  inverse,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  palette: Palette;
  inverse: boolean;
}) {
  const [status, setStatus] = useState("");
  const { share } = useMediaActions(account, peer, setStatus);
  const menu = useBubbleMenu();
  const info = message.media_info;
  const music = message.media === "audio";
  const name =
    (music && info?.title
      ? [info.performer, info.title].filter(Boolean).join(" – ")
      : info?.file_name) || (music ? "Music file" : "File");
  const size = fileSize(info?.size);
  const tooLarge = (info?.size ?? 0) > FILE_LIMIT;
  const color = inverse ? palette.onAccent : palette.text;
  const muted = inverse ? palette.onAccent : palette.muted;
  return (
    <View style={styles.fileBlock}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${name}${size ? `, ${size}` : ""}`}
        accessibilityHint={tooLarge ? undefined : "Opens Share"}
        disabled={tooLarge}
        onPress={() => void share(message)}
        {...menu}
        style={[styles.file, { borderColor: inverse ? color : palette.line }]}
      >
        <Ionicons
          name={music ? "musical-notes-outline" : "document-outline"}
          size={26}
          color={inverse ? color : palette.accent}
        />
        <View style={styles.fileText}>
          <Text numberOfLines={2} style={[styles.fileName, { color }]}>
            {name}
          </Text>
          <Text style={[styles.note, { color: muted }]}>
            {[size, tooLarge ? "Open in Telegram" : "Share"]
              .filter(Boolean)
              .join(" · ")}
          </Text>
        </View>
      </Pressable>
      {status ? (
        <Text style={[styles.note, { color: muted }]}>{status}</Text>
      ) : null}
    </View>
  );
}

// The machine transcript of a voice or video note; a missing one starts on
// the tap and shows while it runs (ADR 51 Q9).
export function TranscriptButton({
  account,
  peer,
  message,
  inverse = false,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  inverse?: boolean;
}) {
  const { api } = useConnection();
  const palette = usePalette();
  const menu = useBubbleMenu();
  const [view, setView] = useState<TranscriptView | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const path = `${originalPath(account, peer, message)}/transcript`;
  function show() {
    void showTranscript({
      read: () => api<TranscriptStatus>(`${path}/status`),
      start: () => api<TranscriptStatus>(path, { body: {} }),
      saved: peer === `user:${account}`,
      show: setView,
      alive: () => alive.current,
      wait: (ms) => new Promise((done) => setTimeout(done, ms)),
    });
  }
  const color = inverse ? palette.onAccent : palette.accent;
  if (view === null)
    return (
      <Pressable accessibilityRole="button" onPress={show} {...menu}>
        <Text style={[styles.link, { color }]}>Show transcript</Text>
      </Pressable>
    );
  if (view.kind === "loading") return <ActivityIndicator color={color} />;
  if (view.kind === "running")
    return (
      <View style={styles.transcribing}>
        <ActivityIndicator color={color} />
        <Text
          style={[
            styles.note,
            { color: inverse ? palette.onAccent : palette.muted },
          ]}
        >
          Transcribing…
        </Text>
      </View>
    );
  if (view.kind === "text")
    return (
      <Text
        style={[
          styles.note,
          { color: inverse ? palette.onAccent : palette.text },
        ]}
      >
        {view.text}
      </Text>
    );
  return (
    <View>
      <Text
        style={[
          styles.note,
          { color: inverse ? palette.onAccent : palette.muted },
        ]}
      >
        {view.note}
      </Text>
      {view.retry ? (
        <Pressable accessibilityRole="button" onPress={show} {...menu}>
          <Text style={[styles.link, { color }]}>Try again</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  image: { width: 220, borderRadius: 10 },
  note: { fontSize: 14 },
  transcribing: { flexDirection: "row", alignItems: "center", gap: 8 },
  link: { fontSize: 15, fontWeight: "600", paddingVertical: 4 },
  viewer: { flex: 1, backgroundColor: "#000" },
  full: { flex: 1 },
  viewerNote: { color: "#fff", textAlign: "center", padding: 8 },
  actions: { flexDirection: "row", gap: 12, padding: 16 },
  action: { flex: 1 },
  fileBlock: { gap: 4 },
  file: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    padding: 10,
    minWidth: 200,
  },
  fileText: { flexShrink: 1, gap: 2 },
  fileName: { fontSize: 15, fontWeight: "600" },
});
