import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ChevronLeft, ChevronRight, LoaderCircle, X } from "lucide-react";
import type { components } from "./api-schema";
import { chatApi, type NativeMessage } from "./conversation-api";
import { FILE_LIMIT, mediaUrl } from "./MediaFiles";
import { claimPlayback, releasePlayback } from "./playback";
import { galleryItems, step } from "../../shared/gallery";
import "./carousel.css";

type PreparedVideo = components["schemas"]["PreparedVideo"];

// The Media Carousel (ADR 51 Q10, Q11): a tap on a photo or video opens every
// photo and video the chat has loaded, in date order, at the tapped one.
export const Gallery = createContext<{
  open: (message: NativeMessage) => void;
} | null>(null);

export function useGallery() {
  return useContext(Gallery);
}

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
  // The open carousel's items, kept while it is open, and its place.
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
      {shown && (
        <MediaCarousel
          account={account}
          peer={peer}
          items={shown.items}
          index={shown.index}
          onIndex={(index) => setShown({ ...shown, index })}
          onClose={() => setShown(null)}
        />
      )}
    </Gallery.Provider>
  );
}

type Pointer = { x: number; y: number };
const distance = (a: Pointer, b: Pointer) => Math.hypot(a.x - b.x, a.y - b.y);

// One item at a time: swipe or the arrows move between them, pinch or a
// double tap zooms a photo, a zoomed photo pans, and a swipe down closes.
function MediaCarousel({
  account,
  peer,
  items,
  index,
  onIndex,
  onClose,
}: {
  account: number;
  peer: string;
  items: NativeMessage[];
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
}) {
  const item = items[index]!;
  const close = useRef<HTMLButtonElement>(null);
  const [note, setNote] = useState("");
  const [zoom, setZoom] = useState({ scale: 1, x: 0, y: 0 });
  const [drag, setDrag] = useState({ x: 0, y: 0 });
  const pointers = useRef(new Map<number, Pointer>());
  const gesture = useRef<{
    start: Pointer;
    zoom: typeof zoom;
    pinch: number | null;
  } | null>(null);
  const tapped = useRef(0);
  const move = (by: number) => {
    const next = step(index, items.length, by);
    if (next !== index) onIndex(next);
  };
  useEffect(() => {
    setZoom({ scale: 1, x: 0, y: 0 });
    setDrag({ x: 0, y: 0 });
    setNote("");
  }, [index]);
  useEffect(() => {
    close.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowLeft") move(-1);
      if (event.key === "ArrowRight") move(1);
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  });

  const photo = item.media === "image";
  const handlers = {
    onPointerDown(event: React.PointerEvent) {
      pointers.current.set(event.pointerId, {
        x: event.clientX,
        y: event.clientY,
      });
      // A video's own controls keep their taps.
      if (photo)
        try {
          event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
          // A synthetic pointer keeps no capture.
        }
      const points = [...pointers.current.values()];
      gesture.current = {
        start: points[0]!,
        zoom,
        pinch: points.length === 2 ? distance(points[0]!, points[1]!) : null,
      };
    },
    onPointerMove(event: React.PointerEvent) {
      if (!pointers.current.has(event.pointerId) || !gesture.current) return;
      pointers.current.set(event.pointerId, {
        x: event.clientX,
        y: event.clientY,
      });
      const points = [...pointers.current.values()];
      const { start, zoom: from, pinch } = gesture.current;
      if (photo && pinch && points.length === 2) {
        const scale = Math.min(
          4,
          Math.max(1, (from.scale * distance(points[0]!, points[1]!)) / pinch),
        );
        setZoom({ ...from, scale });
        return;
      }
      const dx = event.clientX - start.x;
      const dy = event.clientY - start.y;
      if (from.scale > 1) setZoom({ ...from, x: from.x + dx, y: from.y + dy });
      else setDrag({ x: dx, y: Math.max(0, dy) });
    },
    onPointerUp(event: React.PointerEvent) {
      pointers.current.delete(event.pointerId);
      const was = gesture.current;
      if (pointers.current.size) return;
      gesture.current = null;
      if (!was || was.zoom.scale > 1 || zoom.scale > 1 || was.pinch) {
        setDrag({ x: 0, y: 0 });
        return;
      }
      const dx = event.clientX - was.start.x;
      const dy = event.clientY - was.start.y;
      setDrag({ x: 0, y: 0 });
      if (dy > 110 && dy > Math.abs(dx)) return onClose();
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy))
        return move(dx < 0 ? 1 : -1);
      // A double tap zooms a photo in, or back out.
      if (photo && Math.abs(dx) < 10 && Math.abs(dy) < 10) {
        const now = Date.now();
        if (now - tapped.current < 300) {
          tapped.current = 0;
          setZoom((current) =>
            current.scale > 1
              ? { scale: 1, x: 0, y: 0 }
              : { scale: 2.5, x: 0, y: 0 },
          );
        } else tapped.current = now;
      }
    },
    onPointerCancel(event: React.PointerEvent) {
      pointers.current.delete(event.pointerId);
      gesture.current = null;
      setDrag({ x: 0, y: 0 });
    },
  };

  const url = mediaUrl(account, peer, item);
  // Share where the browser can share files, as the app's sheet does.
  async function share() {
    setNote("Preparing…");
    try {
      const blob = await (await fetch(url)).blob();
      const name = photo ? "photo.jpg" : "video.mp4";
      const file = new File([blob], name, { type: blob.type });
      await navigator.share({ files: [file] });
      setNote("");
    } catch {
      setNote(`The ${photo ? "photo" : "video"} couldn't be shared.`);
    }
  }
  const sharing =
    typeof navigator.canShare === "function" &&
    navigator.canShare({ files: [new File([], "photo.jpg")] });
  return (
    <div
      className="media-carousel"
      role="dialog"
      aria-modal="true"
      aria-label="Media"
    >
      <div className="carousel-bar">
        <span className="carousel-count" aria-live="polite">
          {index + 1} / {items.length}
        </span>
        <button
          ref={close}
          type="button"
          className="carousel-icon"
          aria-label="Close"
          onClick={onClose}
        >
          <X size={22} aria-hidden />
        </button>
      </div>
      <div
        className="carousel-stage"
        style={{
          transform: `translate(${drag.x}px, ${drag.y}px)`,
          opacity: drag.y ? Math.max(0.4, 1 - drag.y / 400) : 1,
        }}
        {...handlers}
      >
        {photo ? (
          <img
            key={item.message_id}
            src={url}
            alt="Photo"
            draggable={false}
            style={{
              transform: `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.scale})`,
            }}
          />
        ) : (
          <CarouselVideo
            key={item.message_id}
            account={account}
            peer={peer}
            message={item}
          />
        )}
      </div>
      {index > 0 && (
        <button
          type="button"
          className="carousel-icon carousel-previous"
          aria-label="Previous"
          onClick={() => move(-1)}
        >
          <ChevronLeft size={28} aria-hidden />
        </button>
      )}
      {index < items.length - 1 && (
        <button
          type="button"
          className="carousel-icon carousel-next"
          aria-label="Next"
          onClick={() => move(1)}
        >
          <ChevronRight size={28} aria-hidden />
        </button>
      )}
      {note && <p role="status">{note}</p>}
      {sharing && (
        <div className="photo-viewer-actions">
          <button
            type="button"
            className="secondary"
            onClick={() => void share()}
          >
            Share
          </button>
        </div>
      )}
    </div>
  );
}

// A video in the carousel: prepared on opening (ADR 33, up to 50 MiB), then
// played there with the browser's controls (ADR 51 Q11).
function CarouselVideo({
  account,
  peer,
  message,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
}) {
  const key = `video:${account}/${peer}/${message.message_id}@${message.revision}`;
  const tooLarge = (message.media_info?.size ?? 0) > FILE_LIMIT;
  const [state, setState] = useState<
    "preparing" | "ready" | "unsupported" | "failed"
  >("preparing");
  const [error, setError] = useState("");
  useEffect(() => {
    if (tooLarge) return;
    const controller = new AbortController();
    chatApi<PreparedVideo>(
      account,
      peer,
      `/originals/${message.message_id}/media/prepare-video`,
      { revision: message.revision, version: null },
      controller.signal,
    ).then(
      (answer) =>
        !controller.signal.aborted &&
        setState(answer.state === "ready" ? "ready" : "unsupported"),
      (problem) => {
        if (controller.signal.aborted) return;
        setError(
          (problem as Error).message ||
            "Video is unavailable. Open Telegram to view it.",
        );
        setState("failed");
      },
    );
    return () => {
      controller.abort();
      releasePlayback(key);
    };
  }, [account, peer, message.message_id, message.revision, key, tooLarge]);
  if (tooLarge)
    return (
      <p className="carousel-note">
        Video is over the 50 MiB limit. Open Telegram to view it.
      </p>
    );
  if (state === "preparing")
    return (
      <p className="carousel-note">
        <LoaderCircle size={28} className="spin" aria-hidden /> Loading video
      </p>
    );
  if (state !== "ready")
    return (
      <p className="carousel-note">
        {state === "unsupported"
          ? "This video can't be played here. Open Telegram to view it."
          : error}
      </p>
    );
  return (
    <video
      src={mediaUrl(account, peer, message)}
      poster={mediaUrl(account, peer, message, { variant: "poster" })}
      controls
      autoPlay
      playsInline
      disablePictureInPicture
      controlsList="nodownload noremoteplayback"
      onPlay={(event) => {
        const element = event.currentTarget;
        claimPlayback(key, () => element.pause());
      }}
      onError={() => {
        setError("Video is unavailable. Open Telegram to view it.");
        setState("failed");
      }}
    />
  );
}
