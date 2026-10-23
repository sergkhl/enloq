import React, { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import type { components } from "./api-schema";
import { chatApi, type NativeMessage } from "./conversation-api";
import { FILE_LIMIT, mediaUrl } from "./MediaFiles";
import { useOnScreen, useReducedMotion } from "./motion";
import { TgsAnimation } from "./TgsAnimation";
import { webmAlphaCapability } from "./webmAlphaCapability";

type Asset = components["schemas"]["ReceivedAssetView"];
type Loaded =
  | { state: "idle" | "loading" }
  | { state: "ready" | "unsupported"; asset: Asset }
  | { state: "failed"; error: string };

const STICKER_LIMIT = 5 * 1024 * 1024;

// The service's checked preview of a GIF or sticker: its description first,
// then its bytes fetched once when they are missing. It starts only on
// screen (ADR 32).
function useAsset(
  account: number,
  peer: string,
  message: NativeMessage,
  enabled: boolean,
) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "idle" });
  const [attempt, setAttempt] = useState(0);
  // Once described, a preview is never asked for again while shown.
  const settled = useRef(false);
  const { message_id: identity, revision } = message;
  useEffect(() => {
    if (!enabled || settled.current) return;
    const controller = new AbortController();
    const path = `/originals/${identity}/media`;
    setLoaded({ state: "loading" });
    void (async () => {
      try {
        let asset = await chatApi<Asset>(
          account,
          peer,
          `${path}/representation?revision=${revision}`,
          undefined,
          controller.signal,
        );
        if (asset.state === "missing")
          asset = await chatApi<Asset>(
            account,
            peer,
            `${path}/prepare-preview`,
            { revision, version: null },
            controller.signal,
          );
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
          error: (problem as Error).message || "Preview unavailable",
        });
      }
    })();
    // Scrolled away before it answered: it asks again once shown.
    return () => controller.abort();
  }, [enabled, attempt, account, peer, identity, revision]);
  return { loaded, retry: () => setAttempt((value) => value + 1) };
}

// A muted loop that plays only while it may move.
function Loop({
  src,
  poster,
  moving,
}: {
  src: string;
  poster: string;
  moving: boolean;
}) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const element = video.current;
    if (!element) return;
    if (moving) void element.play().catch(() => {});
    else element.pause();
  }, [moving]);
  return (
    <video
      ref={video}
      src={src}
      poster={poster}
      muted
      loop
      playsInline
      disablePictureInPicture
      aria-hidden
    />
  );
}

// A GIF loops silently while on screen (ADR 46 CU1).
export function GifView({
  account,
  peer,
  message,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
}) {
  const box = useRef<HTMLDivElement>(null);
  const onScreen = useOnScreen(box);
  const reduce = useReducedMotion();
  const moving = onScreen && !reduce;
  const info = message.media_info;
  const tooLarge = (info?.size ?? 0) > FILE_LIMIT;
  const { loaded, retry } = useAsset(
    account,
    peer,
    message,
    onScreen && !tooLarge,
  );
  const asset = loaded.state === "ready" ? loaded.asset : null;
  const width = asset?.width ?? info?.width;
  const height = asset?.height ?? info?.height;
  const ratio =
    width && height ? Math.min(2, Math.max(0.6, width / height)) : 4 / 3;
  const preview = mediaUrl(account, peer, message, { path: "media/preview" });
  const poster = mediaUrl(account, peer, message, { variant: "poster" });
  return (
    <div className="media-block">
      <div
        ref={box}
        className="gif-box"
        role="img"
        aria-label="GIF"
        style={{ aspectRatio: ratio }}
      >
        {asset?.format === "mp4" || asset?.format === "webm" ? (
          <Loop src={preview} poster={poster} moving={moving} />
        ) : (
          // A still GIF shows Telegram's poster frame.
          <img src={asset && moving ? preview : poster} alt="" />
        )}
        {loaded.state === "loading" && (
          <span className="video-overlay">
            <LoaderCircle size={28} className="spin" aria-hidden />
          </span>
        )}
      </div>
      <Status
        label="GIF"
        limit={tooLarge ? "50 MiB" : null}
        loaded={loaded}
        retry={retry}
      />
    </div>
  );
}

// Static stickers are images and animated (TGS) ones play the service's
// Lottie JSON. Video (WebM) stickers animate where the browser decodes their
// transparency, else show their still image (ADR 46 Q10).
export function StickerView({
  account,
  peer,
  message,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
}) {
  const box = useRef<HTMLDivElement>(null);
  const onScreen = useOnScreen(box);
  const reduce = useReducedMotion();
  const moving = onScreen && !reduce;
  const info = message.media_info;
  const video = info?.sticker === "video";
  const tooLarge = (info?.size ?? 0) > STICKER_LIMIT;
  const [alpha, setAlpha] = useState<boolean | null>(video ? null : false);
  useEffect(() => {
    if (!video || !onScreen || alpha !== null) return;
    const controller = new AbortController();
    void webmAlphaCapability(controller.signal)
      .then((result) => setAlpha(result === "supported"))
      .catch(() => {});
    return () => controller.abort();
  }, [video, onScreen, alpha]);
  const { loaded, retry } = useAsset(
    account,
    peer,
    message,
    onScreen && !tooLarge && (!video || alpha === true),
  );
  const [failure, setFailure] = useState("");
  const asset = loaded.state === "ready" ? loaded.asset : null;
  const preview = mediaUrl(account, peer, message, { path: "media/preview" });
  const poster = mediaUrl(account, peer, message, { variant: "poster" });
  return (
    <div className="media-block">
      <div
        ref={box}
        className="sticker-box"
        role="img"
        aria-label={`Sticker${info?.emoji ? ` ${info.emoji}` : ""}`}
      >
        {asset?.format === "tgs" && !failure ? (
          <TgsAnimation
            url={mediaUrl(account, peer, message, { path: "media/animation" })}
            animate={moving}
            onReady={() => setFailure("")}
            onError={setFailure}
          />
        ) : asset?.format === "webm" ? (
          <Loop src={preview} poster={poster} moving={moving} />
        ) : (
          <img
            src={asset && asset.format !== "tgs" ? preview : poster}
            alt=""
          />
        )}
      </div>
      {failure ? (
        <small className="bubble-media">{failure}</small>
      ) : video && alpha === false ? null : (
        <Status
          label="Sticker"
          limit={tooLarge ? "5 MiB" : null}
          loaded={loaded}
          retry={retry}
        />
      )}
    </div>
  );
}

function Status({
  label,
  limit,
  loaded,
  retry,
}: {
  label: string;
  limit: string | null;
  loaded: Loaded;
  retry: () => void;
}) {
  if (limit)
    return (
      <small className="bubble-media">{`${label} exceeds the ${limit} limit`}</small>
    );
  if (loaded.state === "unsupported")
    return (
      <small className="bubble-media">{`${label} can't be shown here`}</small>
    );
  if (loaded.state !== "failed") return null;
  return (
    <span className="media-line">
      <small className="bubble-media">Preview unavailable</small>
      <button type="button" className="bubble-link" onClick={retry}>
        Retry loading
      </button>
    </span>
  );
}
