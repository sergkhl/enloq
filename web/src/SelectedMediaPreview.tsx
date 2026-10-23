import React, { useState } from "react";
import { LoaderCircle, RotateCw, X } from "lucide-react";
import type { SelectedMedia } from "./conversation-api";
import { stagedStatus } from "../../shared/catalog";
import { useReducedMotion } from "./motion";
import { TgsAnimation } from "./TgsAnimation";

// The staged GIF or sticker above the composer (ADR 32, ADR 46 CU5): its
// picture, Preparing… or why it failed with Retry, Draft kept beside a
// sticker, and remove.
export function SelectedMediaPreview({
  media,
  keptText,
  disabled,
  onRemove,
  onRetry,
}: {
  media: SelectedMedia;
  keptText: boolean;
  disabled: boolean;
  onRemove: () => void;
  onRetry: () => void;
}) {
  const name = media.kind === "sticker" ? "sticker" : "GIF";
  const status = stagedStatus(media);
  return (
    <div
      className={`selected-media selected-${media.kind}`}
      role="group"
      aria-label={`Selected ${name}`}
    >
      <div className="selected-media-visual">
        {media.status === "ready" ? (
          <Visual key={media.id} media={media} name={name} />
        ) : media.status === "preparing" ? (
          <LoaderCircle size={22} className="work-spinner" aria-hidden />
        ) : (
          <span aria-hidden>{media.emoji || name}</span>
        )}
      </div>
      <div className="selected-media-detail">
        <span>{media.kind === "sticker" ? "Sticker" : "GIF"}</span>
        {status && (
          <small
            className={media.status === "failed" ? "failed" : undefined}
            role={media.status === "failed" ? "alert" : "status"}
          >
            {status}
          </small>
        )}
        {media.kind === "sticker" && keptText && (
          <small role="status">Draft kept</small>
        )}
        {media.status === "failed" && media.retryable && (
          <button
            type="button"
            disabled={disabled}
            aria-label={`Retry the ${name}`}
            onClick={onRetry}
          >
            <RotateCw size={14} aria-hidden /> Retry
          </button>
        )}
      </div>
      <button
        type="button"
        className="selected-media-remove"
        aria-label={`Remove the ${name}`}
        disabled={disabled}
        onClick={onRemove}
      >
        <X size={16} aria-hidden />
      </button>
    </div>
  );
}

// A ready GIF loops silently; a sticker shows as Telegram draws it.
function Visual({ media, name }: { media: SelectedMedia; name: string }) {
  const reduce = useReducedMotion();
  const [failed, setFailed] = useState(false);
  if (!media.content_url || failed)
    return <span aria-hidden>{media.emoji || name}</span>;
  if (media.format === "tgs" && media.animation_url)
    return (
      <TgsAnimation
        url={media.animation_url}
        animate={!reduce}
        onReady={() => undefined}
        onError={() => setFailed(true)}
      />
    );
  if (media.format === "mp4" || media.format === "webm")
    return (
      <video
        src={media.content_url}
        muted
        loop
        playsInline
        autoPlay={!reduce}
        aria-label={`Selected ${name}`}
        onError={() => setFailed(true)}
      />
    );
  return (
    <img
      src={media.content_url}
      alt={`Selected ${name}`}
      onError={() => setFailed(true)}
    />
  );
}
