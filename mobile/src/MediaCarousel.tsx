import Ionicons from "@expo/vector-icons/Ionicons";
import { useEventListener } from "expo";
import { VideoView, useVideoPlayer } from "expo-video";
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  FlatList,
  Image,
  Modal,
  PanResponder,
  Pressable,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
  type GestureResponderEvent,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { galleryItems } from "../../shared/gallery";
import type { Schemas } from "./api";
import { useConnection } from "./connection";
import { Gallery } from "./gallery";
import type { NativeMessage } from "./conversation";
import {
  FILE_LIMIT,
  mediaSource,
  originalPath,
  useMediaActions,
} from "./Media";
import { claimPlayback, releasePlayback } from "./playback";

// The Media Carousel (ADR 51 Q10, Q11): a tap on a photo or video opens every
// photo and video the chat has loaded, in date order, at the tapped one.
export function GalleryProvider({
  account,
  peer,
  messages,
  children,
}: {
  account: number;
  peer: string;
  messages: NativeMessage[];
  children: React.ReactNode;
}) {
  const items = useMemo(() => galleryItems(messages), [messages]);
  // The open carousel's items, kept while it is open, and where it opened.
  const [shown, setShown] = useState<{
    items: NativeMessage[];
    index: number;
  } | null>(null);
  const value = useMemo(
    () => ({
      open(message: NativeMessage) {
        const index = items.findIndex(
          (item) => item.message_id === message.message_id,
        );
        setShown(
          index >= 0 ? { items, index } : { items: [message], index: 0 },
        );
      },
    }),
    [items],
  );
  return (
    <Gallery.Provider value={value}>
      {children}
      <Modal
        visible={!!shown}
        animationType="fade"
        onRequestClose={() => setShown(null)}
      >
        {shown ? (
          <MediaCarousel
            account={account}
            peer={peer}
            items={shown.items}
            start={shown.index}
            onClose={() => setShown(null)}
          />
        ) : null}
      </Modal>
    </Gallery.Provider>
  );
}

// One page per item: swiping between them, pinch or a double tap zooming a
// photo, a zoomed photo panning, and a swipe down closing.
function MediaCarousel({
  account,
  peer,
  items,
  start,
  onClose,
}: {
  account: number;
  peer: string;
  items: NativeMessage[];
  start: number;
  onClose: () => void;
}) {
  const { width } = useWindowDimensions();
  const [index, setIndex] = useState(start);
  const [zoomed, setZoomed] = useState(false);
  const [note, setNote] = useState("");
  const { share } = useMediaActions(account, peer, setNote);
  const list = useRef<FlatList<NativeMessage>>(null);
  const item = items[index]!;
  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.bar}>
        <Text style={styles.count} accessibilityLiveRegion="polite">
          {index + 1} / {items.length}
        </Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close"
          hitSlop={8}
          onPress={onClose}
          style={styles.icon}
        >
          <Ionicons name="close" size={24} color="#fff" />
        </Pressable>
      </View>
      <FlatList
        ref={list}
        data={items}
        horizontal
        pagingEnabled
        scrollEnabled={!zoomed}
        initialScrollIndex={start}
        getItemLayout={(_, at) => ({
          length: width,
          offset: width * at,
          index: at,
        })}
        showsHorizontalScrollIndicator={false}
        keyExtractor={(value) => String(value.message_id)}
        onMomentumScrollEnd={(event) =>
          setIndex(
            Math.round(event.nativeEvent.contentOffset.x / Math.max(1, width)),
          )
        }
        renderItem={({ item: value, index: at }) => (
          <View style={{ width }}>
            {value.media === "image" ? (
              <PhotoPage
                account={account}
                peer={peer}
                message={value}
                onZoom={setZoomed}
                onClose={onClose}
              />
            ) : (
              <VideoPage
                account={account}
                peer={peer}
                message={value}
                current={at === index}
              />
            )}
          </View>
        )}
      />
      {note ? <Text style={styles.note}>{note}</Text> : null}
      <View style={styles.actions}>
        <Pressable
          accessibilityRole="button"
          onPress={() => void share(item)}
          style={styles.button}
        >
          <Ionicons name="share-outline" size={20} color="#fff" />
          <Text style={styles.label}>Share</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

type Touch = { pageX: number; pageY: number };
const spread = (touches: readonly Touch[]) =>
  Math.hypot(
    touches[0]!.pageX - touches[1]!.pageX,
    touches[0]!.pageY - touches[1]!.pageY,
  );

// A photo zooms with two fingers or a double tap, pans while zoomed, and
// closes the carousel on a swipe down while not.
function PhotoPage({
  account,
  peer,
  message,
  onZoom,
  onClose,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  onZoom: (zoomed: boolean) => void;
  onClose: () => void;
}) {
  const { connection } = useConnection();
  const scale = useRef(new Animated.Value(1)).current;
  const shift = useRef(new Animated.ValueXY()).current;
  const state = useRef({ scale: 1, x: 0, y: 0, tapped: 0 });
  const gesture = useRef<{ scale: number; spread: number | null } | null>(null);
  const latest = useRef({ onZoom, onClose });
  latest.current = { onZoom, onClose };
  const responder = useMemo(() => {
    const set = (next: { scale: number; x: number; y: number }) => {
      Object.assign(state.current, next);
      scale.setValue(next.scale);
      shift.setValue({ x: next.x, y: next.y });
      latest.current.onZoom(next.scale > 1);
    };
    const touches = (event: GestureResponderEvent) =>
      event.nativeEvent.touches as unknown as Touch[];
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      // Two fingers, a zoomed photo or a downward drag: horizontal swipes
      // otherwise stay the carousel's.
      onMoveShouldSetPanResponder: (event, move) =>
        touches(event).length === 2 ||
        state.current.scale > 1 ||
        (move.dy > 12 && move.dy > Math.abs(move.dx) * 2),
      onPanResponderGrant: (event) => {
        const fingers = touches(event);
        gesture.current = {
          scale: state.current.scale,
          spread: fingers.length === 2 ? spread(fingers) : null,
        };
      },
      onPanResponderMove: (event, move) => {
        const fingers = touches(event);
        const from = gesture.current;
        if (!from) return;
        if (fingers.length === 2) {
          if (from.spread === null) from.spread = spread(fingers);
          const next = Math.min(
            4,
            Math.max(1, (from.scale * spread(fingers)) / from.spread),
          );
          scale.setValue(next);
          state.current.scale = next;
          return;
        }
        if (state.current.scale > 1)
          shift.setValue({
            x: state.current.x + move.dx,
            y: state.current.y + move.dy,
          });
        else shift.setValue({ x: 0, y: Math.max(0, move.dy) });
      },
      onPanResponderRelease: (_, move) => {
        gesture.current = null;
        const current = state.current;
        if (current.scale > 1) {
          set({
            scale: current.scale,
            x: current.x + move.dx,
            y: current.y + move.dy,
          });
          if (Math.abs(move.dx) + Math.abs(move.dy) > 10) return;
        } else if (move.dy > 110) return latest.current.onClose();
        if (Math.abs(move.dx) < 10 && Math.abs(move.dy) < 10) {
          // A double tap zooms in, or back out.
          const now = Date.now();
          if (now - current.tapped < 300) {
            current.tapped = 0;
            set(
              current.scale > 1
                ? { scale: 1, x: 0, y: 0 }
                : { scale: 2.5, x: 0, y: 0 },
            );
            return;
          }
          current.tapped = now;
        }
        if (current.scale <= 1) set({ scale: 1, x: 0, y: 0 });
      },
      onPanResponderTerminate: () => {
        gesture.current = null;
        if (state.current.scale <= 1) set({ scale: 1, x: 0, y: 0 });
      },
    });
  }, [scale, shift]);
  if (connection.status !== "paired") return null;
  const source = mediaSource(connection.credential, account, peer, message);
  return (
    <Animated.View
      {...responder.panHandlers}
      style={[
        styles.page,
        {
          transform: [
            { translateX: shift.x },
            { translateY: shift.y },
            { scale },
          ],
        },
      ]}
    >
      <Image
        // An array: React Native's Android Image sends headers only from one.
        source={[source]}
        resizeMode="contain"
        accessibilityLabel="Photo"
        style={styles.full}
      />
    </Animated.View>
  );
}

// A video in the carousel: prepared on opening (ADR 33, up to 50 MiB) and
// played there with the system's controls while it is the shown page
// (ADR 51 Q11).
function VideoPage({
  account,
  peer,
  message,
  current,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  current: boolean;
}) {
  const { api } = useConnection();
  const tooLarge = (message.media_info?.size ?? 0) > FILE_LIMIT;
  const [state, setState] = useState<
    "preparing" | "ready" | "unsupported" | "failed"
  >("preparing");
  const [error, setError] = useState("");
  useEffect(() => {
    if (tooLarge || !current) return;
    const controller = new AbortController();
    api<Schemas["PreparedVideo"]>(
      `${originalPath(account, peer, message)}/media/prepare-video`,
      {
        body: { revision: message.revision, version: null },
        signal: controller.signal,
      },
    ).then(
      (answer) =>
        !controller.signal.aborted &&
        setState(answer.state === "ready" ? "ready" : "unsupported"),
      (problem) => {
        if (controller.signal.aborted) return;
        setError(
          problem instanceof Error
            ? problem.message
            : "Video is unavailable. Open Telegram to view it.",
        );
        setState("failed");
      },
    );
    return () => controller.abort();
  }, [api, account, peer, message, tooLarge, current]);
  if (tooLarge)
    return (
      <Text style={styles.note}>
        Video is over the 50 MiB limit. Open Telegram to view it.
      </Text>
    );
  if (state === "preparing")
    return (
      <View style={styles.page}>
        <ActivityIndicator color="#fff" size="large" />
        <Text style={styles.note}>Loading video</Text>
      </View>
    );
  if (state !== "ready")
    return (
      <View style={styles.page}>
        <Text style={styles.note}>
          {state === "unsupported"
            ? "This video can't be played here. Use Share or open Telegram."
            : error}
        </Text>
      </View>
    );
  return (
    <CarouselPlayer
      account={account}
      peer={peer}
      message={message}
      current={current}
      onError={() => {
        setError("Video is unavailable. Open Telegram to view it.");
        setState("failed");
      }}
    />
  );
}

function CarouselPlayer({
  account,
  peer,
  message,
  current,
  onError,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  current: boolean;
  onError: () => void;
}) {
  const { connection } = useConnection();
  const slot = `carousel:${account}/${peer}/${message.message_id}@${message.revision}`;
  const source =
    connection.status === "paired"
      ? mediaSource(connection.credential, account, peer, message)
      : null;
  const player = useVideoPlayer(source, () => undefined);
  useEventListener(player, "playingChange", ({ isPlaying }) => {
    if (isPlaying) claimPlayback(slot, () => player.pause());
  });
  useEventListener(player, "statusChange", ({ status }) => {
    if (status === "error") onError();
  });
  useEffect(() => {
    if (!current) {
      player.pause();
      return;
    }
    claimPlayback(slot, () => player.pause());
    player.play();
  }, [player, slot, current]);
  useEffect(() => () => releasePlayback(slot), [slot]);
  return (
    <VideoView
      player={player}
      nativeControls
      contentFit="contain"
      fullscreenOptions={{ enable: true }}
      allowsPictureInPicture={false}
      style={styles.full}
    />
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#000" },
  bar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  count: { color: "#fff", fontSize: 16, fontVariant: ["tabular-nums"] },
  icon: { padding: 6 },
  page: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8 },
  full: { flex: 1, width: "100%" },
  note: { color: "#fff", textAlign: "center", padding: 16 },
  actions: { flexDirection: "row", justifyContent: "center", padding: 12 },
  button: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: 22,
    backgroundColor: "rgba(255,255,255,0.14)",
  },
  label: { color: "#fff", fontSize: 16, fontWeight: "600" },
});
