import Ionicons from "@expo/vector-icons/Ionicons";
import { Image } from "expo-image";
import { VideoView, useVideoPlayer } from "expo-video";
import LottieView, { type AnimationObject } from "lottie-react-native";
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { stagedStatus } from "../../shared/catalog";
import { serviceImage, servicePath } from "./catalog";
import { useConnection } from "./connection";
import type { SelectedMedia } from "./drafts";
import { useMoving } from "./motion";
import type { Palette } from "./theme";

// The staged GIF or sticker above the composer (ADR 32, ADR 46 CU5): its
// picture, Preparing… or why it failed with Retry, Draft kept beside a
// sticker, and remove.
export function SelectedMediaPreview({
  media,
  keptText,
  disabled,
  palette,
  onRemove,
  onRetry,
}: {
  media: SelectedMedia;
  keptText: boolean;
  disabled: boolean;
  palette: Palette;
  onRemove: () => void;
  onRetry: () => void;
}) {
  const name = media.kind === "sticker" ? "sticker" : "GIF";
  const status = stagedStatus(media);
  return (
    <View
      accessibilityLabel={`Selected ${name}`}
      style={[styles.row, { borderColor: palette.line }]}
    >
      <View
        style={[
          styles.visual,
          {
            backgroundColor:
              media.kind === "sticker" ? "transparent" : palette.panel,
          },
        ]}
      >
        {media.status === "ready" ? (
          <Visual key={media.id} media={media} palette={palette} />
        ) : media.status === "preparing" ? (
          <ActivityIndicator color={palette.accent} />
        ) : (
          <Text style={[styles.emoji, { color: palette.muted }]}>
            {media.emoji || name}
          </Text>
        )}
      </View>
      <View style={styles.detail}>
        <Text style={{ color: palette.muted }}>
          {media.kind === "sticker" ? "Sticker" : "GIF"}
        </Text>
        {status ? (
          <Text
            accessibilityRole={media.status === "failed" ? "alert" : undefined}
            accessibilityLiveRegion="polite"
            style={[
              styles.small,
              {
                color:
                  media.status === "failed" ? palette.error : palette.muted,
              },
            ]}
          >
            {status}
          </Text>
        ) : null}
        {media.kind === "sticker" && keptText ? (
          <Text style={[styles.small, { color: palette.muted }]}>
            Draft kept
          </Text>
        ) : null}
        {media.status === "failed" && media.retryable ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Retry the ${name}`}
            disabled={disabled}
            hitSlop={6}
            onPress={onRetry}
          >
            <Text style={[styles.retry, { color: palette.accent }]}>Retry</Text>
          </Pressable>
        ) : null}
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Remove the ${name}`}
        disabled={disabled}
        hitSlop={8}
        onPress={onRemove}
        style={[styles.remove, { backgroundColor: palette.panel }]}
      >
        <Ionicons name="close" size={18} color={palette.text} />
      </Pressable>
    </View>
  );
}

// A ready GIF loops silently; a sticker shows as the phone draws it: video
// stickers stay still on the phones (ADR 46 Q10).
function Visual({
  media,
  palette,
}: {
  media: SelectedMedia;
  palette: Palette;
}) {
  const { connection } = useConnection();
  if (connection.status !== "paired" || !media.content_url)
    return (
      <Text style={[styles.emoji, { color: palette.muted }]}>
        {media.emoji || "GIF"}
      </Text>
    );
  if (media.format === "tgs" && media.animation_url)
    return <Animation path={media.animation_url} />;
  if (media.kind === "gif" && media.format === "mp4")
    return (
      <Loop source={serviceImage(connection.credential, media.content_url)} />
    );
  if (media.kind === "sticker" && media.format === "webm")
    return (
      <Text style={[styles.emoji, { color: palette.muted }]}>
        {media.emoji || "◇"}
      </Text>
    );
  return (
    <Image
      source={serviceImage(connection.credential, media.content_url)}
      contentFit="contain"
      style={StyleSheet.absoluteFill}
    />
  );
}

function Loop({
  source,
}: {
  source: { uri: string; headers: Record<string, string> };
}) {
  const moving = useMoving();
  const player = useVideoPlayer(source, (value) => {
    value.loop = true;
    value.muted = true;
  });
  useEffect(() => {
    if (moving) player.play();
    else player.pause();
  }, [moving, player]);
  return (
    <VideoView
      player={player}
      nativeControls={false}
      contentFit="contain"
      style={StyleSheet.absoluteFill}
    />
  );
}

function Animation({ path }: { path: string }) {
  const { api } = useConnection();
  const moving = useMoving();
  const [json, setJson] = useState<AnimationObject | null>(null);
  useEffect(() => {
    let live = true;
    void api<AnimationObject>(servicePath(path))
      .then((value) => live && setJson(value))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, path]);
  if (!json) return null;
  return (
    <LottieView
      source={json}
      autoPlay={moving}
      loop
      style={StyleSheet.absoluteFill}
    />
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  visual: {
    width: 88,
    height: 88,
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
    borderRadius: 10,
  },
  emoji: { fontSize: 22, fontWeight: "600" },
  detail: { flex: 1, gap: 4 },
  small: { fontSize: 13 },
  retry: { fontSize: 15, fontWeight: "600" },
  remove: {
    width: 32,
    height: 32,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 16,
  },
});
