import Ionicons from "@expo/vector-icons/Ionicons";
import { useEventListener } from "expo";
import { Image } from "expo-image";
import { VideoView, useVideoPlayer, type VideoPlayer } from "expo-video";
import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import type { Schemas } from "./api";
import { useBubbleMenu } from "./bubbleMenu";
import { useConnection } from "./connection";
import type { NativeMessage } from "./conversation";
import {
  FILE_LIMIT,
  fileSize,
  mediaSource,
  originalPath,
  type Source,
} from "./Media";
import { useGallery } from "./gallery";
import { claimPlayback, positions, releasePlayback } from "./playback";
import type { Palette } from "./theme";
import { clock } from "./VoiceNote";

function useSources(account: number, peer: string, message: NativeMessage) {
  const { connection } = useConnection();
  if (connection.status !== "paired") return null;
  const credential = connection.credential;
  return {
    file: mediaSource(credential, account, peer, message),
    poster: mediaSource(credential, account, peer, message, {
      variant: "poster",
    }),
  };
}

// A video message plays in place in its circle; a tap pauses it (ADR 46 CU1).
// Its player exists only once it has been played.
export function VideoMessage({
  account,
  peer,
  message,
  palette,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  palette: Palette;
}) {
  const sources = useSources(account, peer, message);
  const key = `circle:${account}/${peer}/${message.message_id}@${message.revision}`;
  const [started, setStarted] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const control = useRef<VideoPlayer | null>(null);
  const menu = useBubbleMenu();
  useEffect(() => () => releasePlayback(key), [key]);
  if (!sources) return null;

  function toggle() {
    const player = control.current;
    if (!started || !player) {
      setFailed(false);
      setStarted(true);
    } else if (player.playing) player.pause();
    else {
      claimPlayback(key, () => player.pause());
      player.play();
    }
  }

  return (
    <View style={styles.circleBlock}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={
          playing ? "Pause video message" : "Play video message"
        }
        onPress={toggle}
        {...menu}
        style={[styles.circle, { backgroundColor: palette.line }]}
      >
        {started ? (
          <CirclePlayer
            source={sources.file}
            slot={key}
            control={control}
            onPlaying={setPlaying}
            onEnd={() => {
              releasePlayback(key);
              setPlaying(false);
              setStarted(false);
            }}
            onError={() => setFailed(true)}
          />
        ) : (
          <Image
            source={sources.poster}
            contentFit="cover"
            style={StyleSheet.absoluteFill}
          />
        )}
        {!playing ? (
          <View style={styles.overlay}>
            <Ionicons name="play" size={30} color="#fff" />
          </View>
        ) : null}
      </Pressable>
      {failed ? (
        <Text style={[styles.note, { color: palette.muted }]}>
          Video message unavailable. Tap to try again.
        </Text>
      ) : null}
    </View>
  );
}

function CirclePlayer({
  source,
  slot,
  control,
  onPlaying,
  onEnd,
  onError,
}: {
  source: Source;
  slot: string;
  control: React.RefObject<VideoPlayer | null>;
  onPlaying: (playing: boolean) => void;
  onEnd: () => void;
  onError: () => void;
}) {
  const player = useVideoPlayer(source);
  useEventListener(player, "playingChange", ({ isPlaying }) =>
    onPlaying(isPlaying),
  );
  useEventListener(player, "playToEnd", onEnd);
  useEventListener(player, "statusChange", ({ status }) => {
    if (status === "error") onError();
  });
  useEffect(() => {
    control.current = player;
    claimPlayback(slot, () => player.pause());
    player.play();
    return () => {
      control.current = null;
    };
  }, [player, slot, control]);
  return (
    <VideoView
      player={player}
      nativeControls={false}
      contentFit="cover"
      style={StyleSheet.absoluteFill}
    />
  );
}

type VideoState = "idle" | "preparing" | "ready" | "unsupported" | "failed";

// A video shows its poster, prepares on Play (ADR 33, up to 50 MiB) and plays
// in place with the system controls and full screen. It keeps its position
// while the chat is open; preparing offers Cancel, a failure Retry.
export function VideoCard({
  account,
  peer,
  message,
  palette,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  palette: Palette;
}) {
  const { api } = useConnection();
  const sources = useSources(account, peer, message);
  const key = `video:${account}/${peer}/${message.message_id}@${message.revision}`;
  const info = message.media_info;
  const tooLarge = (info?.size ?? 0) > FILE_LIMIT;
  const [state, setState] = useState<VideoState>("idle");
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const menu = useBubbleMenu();
  // In a conversation the video opens in its Media Carousel and plays there
  // (ADR 51 Q11).
  const gallery = useGallery();
  useEffect(
    () => () => {
      request.current?.abort();
      releasePlayback(key);
    },
    [key],
  );
  if (!sources) return null;
  const ratio =
    info?.width && info.height ? clampRatio(info.width / info.height) : 16 / 9;

  async function play() {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setState("preparing");
    setError("");
    try {
      const answer = await api<Schemas["PreparedVideo"]>(
        `${originalPath(account, peer, message)}/media/prepare-video`,
        {
          body: { revision: message.revision, version: null },
          signal: controller.signal,
        },
      );
      if (controller.signal.aborted) return;
      setState(answer.state === "ready" ? "ready" : "unsupported");
    } catch (problem) {
      if (controller.signal.aborted) return;
      setError(
        problem instanceof Error
          ? problem.message
          : "Video is unavailable. Retry loading or open Telegram.",
      );
      setState("failed");
    }
  }

  function cancel() {
    request.current?.abort();
    setState("idle");
  }

  if (state === "ready")
    return (
      <CardPlayer
        source={sources.file}
        slot={key}
        ratio={ratio}
        onError={() => {
          setError("Video is unavailable. Retry loading or open Telegram.");
          setState("failed");
        }}
      />
    );
  const details = [clock(info?.duration ?? 0), fileSize(info?.size)]
    .filter(Boolean)
    .join(" · ");
  return (
    <View style={styles.videoBlock}>
      <View
        style={[
          styles.video,
          { aspectRatio: ratio, backgroundColor: palette.line },
        ]}
      >
        <Poster source={sources.poster} />
        <View style={styles.overlay}>
          {state === "preparing" ? (
            <ActivityIndicator color="#fff" size="large" />
          ) : tooLarge || state === "unsupported" ? null : (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={
                state === "failed" ? "Retry loading" : "Play video"
              }
              onPress={() => (gallery ? gallery.open(message) : void play())}
              {...menu}
              style={styles.bigPlay}
            >
              <Ionicons
                name={state === "failed" ? "refresh" : "play"}
                size={30}
                color="#fff"
              />
            </Pressable>
          )}
        </View>
        {details ? <Text style={styles.badge}>{details}</Text> : null}
      </View>
      {state === "preparing" ? (
        <View style={styles.line}>
          <Text style={[styles.note, { color: palette.muted }]}>
            Loading video
          </Text>
          <Pressable accessibilityRole="button" onPress={cancel} hitSlop={8}>
            <Text style={[styles.action, { color: palette.accent }]}>
              Cancel
            </Text>
          </Pressable>
        </View>
      ) : tooLarge ? (
        <Text style={[styles.note, { color: palette.muted }]}>
          Video is over the 50 MiB limit. Open Telegram to view it.
        </Text>
      ) : state === "unsupported" ? (
        <Text style={[styles.note, { color: palette.muted }]}>
          This video can't be played here. Use Share or open Telegram.
        </Text>
      ) : state === "failed" ? (
        <Text style={[styles.note, { color: palette.muted }]}>{error}</Text>
      ) : null}
    </View>
  );
}

// A prepared video with the system's controls and full screen, resuming
// where it was while its chat stays open.
function CardPlayer({
  source,
  slot,
  ratio,
  onError,
}: {
  source: Source;
  slot: string;
  ratio: number;
  onError: () => void;
}) {
  const player = useVideoPlayer(source, (created) => {
    created.timeUpdateEventInterval = 1;
    const kept = positions.get(slot);
    if (kept) created.currentTime = kept;
  });
  useEventListener(player, "timeUpdate", ({ currentTime }) =>
    positions.set(slot, currentTime),
  );
  useEventListener(player, "playingChange", ({ isPlaying }) => {
    if (isPlaying) claimPlayback(slot, () => player.pause());
  });
  useEventListener(player, "statusChange", ({ status }) => {
    if (status === "error") onError();
  });
  useEffect(() => {
    claimPlayback(slot, () => player.pause());
    player.play();
  }, [player, slot]);
  return (
    <VideoView
      player={player}
      nativeControls
      contentFit="contain"
      fullscreenOptions={{ enable: true }}
      allowsPictureInPicture={false}
      style={[styles.video, { aspectRatio: ratio }]}
    />
  );
}

function Poster({ source }: { source: Source }) {
  return (
    <Image source={source} contentFit="cover" style={StyleSheet.absoluteFill} />
  );
}

// Very tall or wide videos keep a usable frame in the bubble.
const clampRatio = (ratio: number) => Math.min(2, Math.max(0.6, ratio));

const styles = StyleSheet.create({
  circleBlock: { gap: 4 },
  circle: {
    width: 200,
    height: 200,
    borderRadius: 100,
    overflow: "hidden",
  },
  overlay: {
    position: "absolute",
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    alignItems: "center",
    justifyContent: "center",
  },
  note: { fontSize: 14 },
  videoBlock: { gap: 6 },
  video: { width: 240, borderRadius: 10, overflow: "hidden" },
  bigPlay: {
    width: 54,
    height: 54,
    borderRadius: 27,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(0,0,0,0.45)",
  },
  badge: {
    position: "absolute",
    left: 8,
    bottom: 8,
    color: "#fff",
    fontSize: 12,
    backgroundColor: "rgba(0,0,0,0.45)",
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
    overflow: "hidden",
  },
  line: { flexDirection: "row", alignItems: "center", gap: 12 },
  action: { fontSize: 15, fontWeight: "600" },
});
