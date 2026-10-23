import React, { useEffect, useRef, useState } from "react";
import { LoaderCircle, Play, RotateCw } from "lucide-react";
import type { components } from "./api-schema";
import { chatApi, type NativeMessage } from "./conversation-api";
import { FILE_LIMIT, fileSize, mediaUrl } from "./MediaFiles";
import { useGallery } from "./MediaCarousel";
import { claimPlayback, positions, releasePlayback } from "./playback";
import { clock } from "./VoiceNote";

type PreparedVideo = components["schemas"]["PreparedVideo"];

// A video message plays in place in its circle; a tap pauses it (ADR 46 CU1).
export function VideoMessage({
  account,
  peer,
  message,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
}) {
  const key = `circle:${account}/${peer}/${message.message_id}@${message.revision}`;
  const video = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => () => releasePlayback(key), [key]);
  function toggle() {
    const element = video.current;
    if (!element) return;
    if (!element.paused) return element.pause();
    setFailed(false);
    claimPlayback(key, () => element.pause());
    void element.play().catch(() => setFailed(true));
  }
  return (
    <div className="media-block">
      <button
        type="button"
        className="video-circle"
        aria-label={playing ? "Pause video message" : "Play video message"}
        onClick={toggle}
      >
        <video
          ref={video}
          src={mediaUrl(account, peer, message)}
          poster={mediaUrl(account, peer, message, { variant: "poster" })}
          preload="none"
          playsInline
          disablePictureInPicture
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={(event) => {
            releasePlayback(key);
            event.currentTarget.currentTime = 0;
          }}
          onError={() => setFailed(true)}
        />
        {!playing && (
          <span className="video-overlay" aria-hidden>
            <Play size={30} />
          </span>
        )}
      </button>
      {failed && (
        <small className="bubble-media">
          Video message unavailable. Tap to try again.
        </small>
      )}
    </div>
  );
}

type VideoState = "idle" | "preparing" | "ready" | "unsupported" | "failed";

// A video shows its poster, prepares on Play (ADR 33, up to 50 MiB) and plays
// in place with the browser's controls and full screen. It keeps its
// position while the chat is open; preparing offers Cancel, a failure Retry.
export function VideoCard({
  account,
  peer,
  message,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
}) {
  const key = `video:${account}/${peer}/${message.message_id}@${message.revision}`;
  const info = message.media_info;
  const tooLarge = (info?.size ?? 0) > FILE_LIMIT;
  const [state, setState] = useState<VideoState>("idle");
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
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
  const ratio =
    info?.width && info.height
      ? Math.min(2, Math.max(0.6, info.width / info.height))
      : 16 / 9;
  const poster = mediaUrl(account, peer, message, { variant: "poster" });

  async function play() {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setState("preparing");
    setError("");
    try {
      const answer = await chatApi<PreparedVideo>(
        account,
        peer,
        `/originals/${message.message_id}/media/prepare-video`,
        { revision: message.revision, version: null },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setState(answer.state === "ready" ? "ready" : "unsupported");
    } catch (problem) {
      if (controller.signal.aborted) return;
      setError(
        (problem as Error).message ||
          "Video is unavailable. Retry loading or open Telegram.",
      );
      setState("failed");
    }
  }

  if (state === "ready")
    return (
      <video
        className="video-card"
        style={{ aspectRatio: ratio }}
        src={mediaUrl(account, peer, message)}
        poster={poster}
        controls
        autoPlay
        playsInline
        disablePictureInPicture
        controlsList="nodownload noremoteplayback"
        onLoadedMetadata={(event) => {
          const kept = positions.get(key);
          if (kept) event.currentTarget.currentTime = kept;
        }}
        onTimeUpdate={(event) =>
          positions.set(key, event.currentTarget.currentTime)
        }
        onPlay={(event) => {
          const element = event.currentTarget;
          claimPlayback(key, () => element.pause());
        }}
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
    <div className="media-block">
      <div className="video-card" style={{ aspectRatio: ratio }}>
        <img src={poster} alt="" loading="lazy" />
        <span className="video-overlay">
          {state === "preparing" ? (
            <LoaderCircle size={34} className="spin" aria-hidden />
          ) : tooLarge || state === "unsupported" ? null : (
            <button
              type="button"
              className="video-play"
              aria-label={state === "failed" ? "Retry loading" : "Play video"}
              onClick={() => (gallery ? gallery.open(message) : void play())}
            >
              {state === "failed" ? (
                <RotateCw size={28} aria-hidden />
              ) : (
                <Play size={28} aria-hidden />
              )}
            </button>
          )}
        </span>
        {details && <small className="video-badge">{details}</small>}
      </div>
      {state === "preparing" ? (
        <span className="media-line">
          <small className="bubble-media">Loading video</small>
          <button
            type="button"
            className="bubble-link"
            onClick={() => {
              request.current?.abort();
              setState("idle");
            }}
          >
            Cancel
          </button>
        </span>
      ) : tooLarge ? (
        <small className="bubble-media">
          Video is over the 50 MiB limit. Open Telegram to view it.
        </small>
      ) : state === "unsupported" ? (
        <small className="bubble-media">
          This video can't be played here. Use Download or open Telegram.
        </small>
      ) : state === "failed" ? (
        <small className="bubble-media">{error}</small>
      ) : null}
    </div>
  );
}
