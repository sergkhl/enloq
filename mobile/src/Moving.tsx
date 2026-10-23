import { Image } from "expo-image";
import { VideoView, useVideoPlayer } from "expo-video";
import LottieView, { type AnimationObject } from "lottie-react-native";
import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import type { Schemas } from "./api";
import { useConnection } from "./connection";
import type { NativeMessage } from "./conversation";
import { FILE_LIMIT, mediaSource, originalPath, type Source } from "./Media";
import { useMoving, useOnScreen } from "./motion";
import type { Palette } from "./theme";

type Asset = Schemas["ReceivedAssetView"];
type Loaded =
  | { state: "idle" | "loading" }
  | { state: "ready" | "unsupported"; asset: Asset }
  | { state: "failed"; error: string };

const STICKER_LIMIT = 5 * 1024 * 1024;

// The service's checked preview of a GIF or sticker: its description first,
// then its bytes fetched once when they are missing. It starts only on
// screen, as the web's did (ADR 32).
function useAsset(
  account: number,
  peer: string,
  message: NativeMessage,
  enabled: boolean,
) {
  const { api } = useConnection();
  const [loaded, setLoaded] = useState<Loaded>({ state: "idle" });
  const [attempt, setAttempt] = useState(0);
  // Once described, a preview is never asked for again while shown.
  const settled = useRef(false);
  const { revision } = message;
  const path = `${originalPath(account, peer, message)}/media`;
  useEffect(() => {
    if (!enabled || settled.current) return;
    const controller = new AbortController();
    setLoaded({ state: "loading" });
    void (async () => {
      try {
        let asset = await api<Asset>(
          `${path}/representation?revision=${revision}`,
          {
            signal: controller.signal,
          },
        );
        if (asset.state === "missing")
          asset = await api<Asset>(`${path}/prepare-preview`, {
            body: { revision, version: null },
            signal: controller.signal,
          });
        if (asset.revision !== revision)
          throw new Error("Media source changed; refresh its original");
        settled.current = true;
        setLoaded(
          asset.state === "ready"
            ? { state: "ready", asset }
            : { state: "unsupported", asset },
        );
      } catch (problem) {
        if (controller.signal.aborted) return;
        setLoaded({
          state: "failed",
          error:
            problem instanceof Error ? problem.message : "Preview unavailable",
        });
      }
    })();
    // Scrolled away before it answered: it asks again once shown.
    return () => controller.abort();
  }, [api, enabled, attempt, path, revision]);
  return { loaded, retry: () => setAttempt((value) => value + 1) };
}

function useSources(account: number, peer: string, message: NativeMessage) {
  const { connection } = useConnection();
  if (connection.status !== "paired") return null;
  const credential = connection.credential;
  return {
    preview: mediaSource(credential, account, peer, message, {
      path: "media/preview",
    }),
    poster: mediaSource(credential, account, peer, message, {
      variant: "poster",
    }),
  };
}

// A GIF loops silently while on screen (ADR 46 CU1).
export function GifView({
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
  const onScreen = useOnScreen();
  const moving = useMoving();
  const info = message.media_info;
  const tooLarge = (info?.size ?? 0) > FILE_LIMIT;
  const { loaded, retry } = useAsset(
    account,
    peer,
    message,
    onScreen && !tooLarge,
  );
  if (!sources) return null;
  const asset = loaded.state === "ready" ? loaded.asset : null;
  const width = asset?.width ?? info?.width;
  const height = asset?.height ?? info?.height;
  const ratio =
    width && height ? Math.min(2, Math.max(0.6, width / height)) : 4 / 3;
  return (
    <View style={styles.block}>
      <View
        accessible
        accessibilityRole="image"
        accessibilityLabel="GIF"
        style={[
          styles.gif,
          { aspectRatio: ratio, backgroundColor: palette.line },
        ]}
      >
        {asset?.format === "mp4" ? (
          <Loop source={sources.preview} moving={moving} />
        ) : asset ? (
          <Image
            source={sources.preview}
            autoplay={moving}
            contentFit="contain"
            style={StyleSheet.absoluteFill}
          />
        ) : (
          <Image
            source={sources.poster}
            contentFit="cover"
            style={StyleSheet.absoluteFill}
          />
        )}
        {loaded.state === "loading" ? (
          <View style={styles.center}>
            <ActivityIndicator color="#fff" />
          </View>
        ) : null}
      </View>
      <Status
        label="GIF"
        limit={tooLarge ? "50 MiB" : null}
        loaded={loaded}
        retry={retry}
        palette={palette}
      />
    </View>
  );
}

function Loop({ source, moving }: { source: Source; moving: boolean }) {
  const player = useVideoPlayer(source, (created) => {
    created.loop = true;
    created.muted = true;
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

// Static stickers are images and animated (TGS) ones play the service's
// Lottie JSON; video (WebM) stickers show their still image (ADR 46 Q10).
export function StickerView({
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
  const onScreen = useOnScreen();
  const moving = useMoving();
  const info = message.media_info;
  const still = info?.sticker === "video";
  const tooLarge = (info?.size ?? 0) > STICKER_LIMIT;
  const { loaded, retry } = useAsset(
    account,
    peer,
    message,
    onScreen && !tooLarge && !still,
  );
  if (!sources) return null;
  const asset = loaded.state === "ready" ? loaded.asset : null;
  const label = `Sticker${info?.emoji ? ` ${info.emoji}` : ""}`;
  return (
    <View style={styles.block}>
      <View
        accessible
        accessibilityRole="image"
        accessibilityLabel={label}
        style={styles.sticker}
      >
        {asset?.format === "tgs" ? (
          <Animation
            account={account}
            peer={peer}
            message={message}
            moving={moving}
          />
        ) : (
          <Image
            source={asset ? sources.preview : sources.poster}
            contentFit="contain"
            style={StyleSheet.absoluteFill}
          />
        )}
      </View>
      {still ? null : (
        <Status
          label="Sticker"
          limit={tooLarge ? "5 MiB" : null}
          loaded={loaded}
          retry={retry}
          palette={palette}
        />
      )}
    </View>
  );
}

function Animation({
  account,
  peer,
  message,
  moving,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  moving: boolean;
}) {
  const { api } = useConnection();
  const [json, setJson] = useState<AnimationObject | null>(null);
  const view = useRef<LottieView>(null);
  const url = `${originalPath(account, peer, message)}/media/animation?revision=${message.revision}`;
  useEffect(() => {
    let live = true;
    void api<AnimationObject>(url)
      .then((value) => live && setJson(value))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, url]);
  useEffect(() => {
    if (moving) view.current?.play();
    else view.current?.pause();
  }, [moving, json]);
  if (!json) return null;
  return (
    <LottieView
      ref={view}
      source={json}
      autoPlay={moving}
      loop
      style={StyleSheet.absoluteFill}
    />
  );
}

function Status({
  label,
  limit,
  loaded,
  retry,
  palette,
}: {
  label: string;
  limit: string | null;
  loaded: Loaded;
  retry: () => void;
  palette: Palette;
}) {
  if (limit)
    return (
      <Text style={[styles.note, { color: palette.muted }]}>
        {`${label} exceeds the ${limit} limit`}
      </Text>
    );
  if (loaded.state === "unsupported")
    return (
      <Text style={[styles.note, { color: palette.muted }]}>
        {`${label} can't be shown here`}
      </Text>
    );
  if (loaded.state !== "failed") return null;
  return (
    <View style={styles.line}>
      <Text style={[styles.note, { color: palette.muted }]}>
        Preview unavailable
      </Text>
      <Pressable accessibilityRole="button" onPress={retry} hitSlop={8}>
        <Text style={[styles.action, { color: palette.accent }]}>
          Retry loading
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  block: { gap: 4 },
  gif: { width: 240, borderRadius: 10, overflow: "hidden" },
  sticker: { width: 128, height: 128 },
  center: {
    position: "absolute",
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    alignItems: "center",
    justifyContent: "center",
  },
  note: { fontSize: 14 },
  line: { flexDirection: "row", alignItems: "center", gap: 12 },
  action: { fontSize: 15, fontWeight: "600" },
});
