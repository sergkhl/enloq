import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";
import React from "react";
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { linkSegments } from "../../shared/links";
import { mediaLabel, type NativeMessage } from "./conversation";
import type { ReadingView } from "./data";
import {
  FileCard,
  isVoice,
  MessageImage,
  spoken,
  TranscriptButton,
} from "./Media";
import type { ObservedVersion } from "./details";
import { AttachmentCardView } from "./MessageDetails";
import { GifView, StickerView } from "./Moving";
import type { Palette } from "./theme";
import { VideoCard, VideoMessage } from "./VideoMedia";
import { VoiceNote } from "./VoiceNote";

// Reading pieces both conversation screens share: the header's AI toggle, the
// Unread messages line and a message's own content.

// The Reading View as one robot in the header (ADR 52 Q13): pressed shows
// the AI view, unpressed Originals.
export function ReadingToggle({
  view,
  choose,
  palette,
}: {
  view: ReadingView;
  choose: (view: ReadingView) => Promise<void>;
  palette: Palette;
}) {
  const ai = view === "ai";
  return (
    <Pressable
      accessibilityRole="togglebutton"
      accessibilityLabel="AI view"
      accessibilityState={{ checked: ai }}
      hitSlop={8}
      onPress={() => void choose(ai ? "originals" : "ai")}
      style={[
        styles.toggle,
        ai
          ? { backgroundColor: palette.accent, borderColor: palette.accent }
          : { borderColor: palette.line },
      ]}
    >
      <MaterialCommunityIcons
        name={ai ? "robot" : "robot-outline"}
        size={20}
        color={ai ? palette.onAccent : palette.muted}
      />
    </Pressable>
  );
}

// ADR 28: the Unread messages line above the earliest unseen message.
export function UnreadLine({ palette }: { palette: Palette }) {
  return (
    <View accessibilityRole="text" style={styles.unread}>
      <View style={[styles.unreadRule, { backgroundColor: palette.accent }]} />
      <Text style={[styles.small, { color: palette.accent }]}>
        Unread messages
      </Text>
      <View style={[styles.unreadRule, { backgroundColor: palette.accent }]} />
    </View>
  );
}

export function MessageBody({
  message,
  account,
  peer,
  palette,
  inverse,
  hideText = false,
  version = null,
}: {
  message: NativeMessage;
  account: number;
  peer: string;
  palette: Palette;
  inverse: boolean;
  // Under an AI rewrite only the attachment shows; the text is rewritten.
  hideText?: boolean;
  // An earlier version shown in its place (ADR 46 CU2).
  version?: ObservedVersion | null;
}) {
  const color = inverse ? palette.onAccent : palette.text;
  if (version)
    return (
      <VersionBody
        version={version}
        account={account}
        peer={peer}
        palette={palette}
        inverse={inverse}
      />
    );
  const label = mediaLabel(message);
  const media = { account, peer, message, palette };
  // A deleted message shows what it last said (ADR 52 Q7); media Enloq no
  // longer holds says so, since Telegram serves it no more.
  const gone = !!message.deleted && !!message.media && !message.media_available;
  return (
    <View style={styles.body}>
      {gone ? (
        <Text
          style={[
            styles.small,
            { color: inverse ? palette.onAccent : palette.muted },
          ]}
        >
          {`${label || "Attachment"} · no longer available`}
        </Text>
      ) : message.media === "image" ? (
        <MessageImage
          account={account}
          peer={peer}
          message={message}
          version={message.deleted_version ?? undefined}
        />
      ) : isVoice(message) ? (
        <VoiceNote {...media} inverse={inverse} />
      ) : message.media === "video_note" ? (
        <VideoMessage {...media} />
      ) : message.media === "video" ? (
        <VideoCard {...media} />
      ) : message.media === "gif" ? (
        <GifView {...media} />
      ) : message.media === "sticker" ? (
        <StickerView {...media} />
      ) : message.media === "document" || message.media === "audio" ? (
        <FileCard {...media} inverse={inverse} />
      ) : ["poll", "location", "contact", "link"].includes(
          message.media ?? "",
        ) ? (
        <AttachmentCardView
          message={message}
          palette={palette}
          inverse={inverse}
        />
      ) : label ? (
        <Text
          style={[
            styles.small,
            { color: inverse ? palette.onAccent : palette.muted },
          ]}
        >
          {label}
          {message.media_info?.duration
            ? ` · ${Math.round(message.media_info.duration)} s`
            : ""}
        </Text>
      ) : null}
      {spoken(message) ? (
        <TranscriptButton
          account={account}
          peer={peer}
          message={message}
          inverse={inverse}
        />
      ) : null}
      {message.text && !hideText ? (
        <Text selectable style={[styles.text, { color }]}>
          {/* Links open in the phone's browser (ADR 46 Q9). */}
          {linkSegments(message.text, message.links ?? []).map(
            (segment, index) =>
              segment.url ? (
                <Text
                  key={index}
                  accessibilityRole="link"
                  onPress={() => void Linking.openURL(segment.url!)}
                  style={[
                    styles.link,
                    { color: inverse ? palette.onAccent : palette.accent },
                  ]}
                >
                  {segment.text}
                </Text>
              ) : (
                segment.text
              ),
          )}
        </Text>
      ) : null}
    </View>
  );
}

// An earlier version: its text, and its photo while the cache still has it;
// other media say whether they are still kept, with a captured transcript.
function VersionBody({
  version,
  account,
  peer,
  palette,
  inverse,
}: {
  version: ObservedVersion;
  account: number;
  peer: string;
  palette: Palette;
  inverse: boolean;
}) {
  const message = version.message;
  const color = inverse ? palette.onAccent : palette.text;
  const muted = inverse ? palette.onAccent : palette.muted;
  const label = mediaLabel(message);
  return (
    <View style={styles.body}>
      {message.media === "image" && version.media_available ? (
        <MessageImage
          account={account}
          peer={peer}
          message={message}
          version={version.id}
        />
      ) : label && message.media !== "link" ? (
        <Text style={[styles.small, { color: muted }]}>
          {version.media_available
            ? label
            : `${label} · no longer available locally`}
        </Text>
      ) : null}
      {version.transcript?.text ? (
        <Text style={[styles.small, { color: muted }]}>
          {`Transcript: ${version.transcript.text}`}
        </Text>
      ) : null}
      {message.deleted ? (
        <Text style={[styles.text, styles.deleted, { color: muted }]}>
          Message deleted
        </Text>
      ) : message.text ? (
        <Text selectable style={[styles.text, { color }]}>
          {message.text}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  toggle: {
    width: 34,
    height: 34,
    borderRadius: 17,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  small: { fontSize: 13 },
  unread: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginVertical: 6,
  },
  unreadRule: { flex: 1, height: 1 },
  body: { gap: 6 },
  text: { fontSize: 16, lineHeight: 22 },
  deleted: { fontStyle: "italic" },
  link: { textDecorationLine: "underline" },
});
