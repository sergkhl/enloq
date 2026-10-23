import probeUrl from "./assets/webm-alpha-probe.webm?url&no-inline";

export type WebmAlphaCapability = "supported" | "unsupported" | "unavailable";
type ConclusiveCapability = Exclude<WebmAlphaCapability, "unavailable">;
type Attempt = {
  promise: Promise<WebmAlphaCapability>;
  cancel: () => void;
  users: number;
  readonly finished: boolean;
};
type DocumentState = {
  cached: ConclusiveCapability | null;
  attempt: Attempt | null;
};
const documents = new WeakMap<Document, DocumentState>();

// Authored public canary: one 32x32 VP9-alpha frame, transparent outside the
// central [8,24) green square (36,184,72,255), no audio, one-second duration.
// 718 bytes, SHA-256 04ddf4f598d4d20243529bc15260a951d93339d441dd2e8dd51207fac847bd5a.
// Vite keeps it as a hashed self-origin asset; SHA-256 binds the pixel verdict to
// those exact bytes. Nothing from the owner's media is sampled or analyzed.
const DIGEST =
  "04ddf4f598d4d20243529bc15260a951d93339d441dd2e8dd51207fac847bd5a";
const BYTE_LIMIT = 16 * 1024;
const DEADLINE_MS = 5_000;
const SIZE = 32;

async function probeBytes(
  signal: AbortSignal,
): Promise<Uint8Array<ArrayBuffer>> {
  const url = new URL(probeUrl, location.href);
  if (url.origin !== location.origin)
    throw new Error("Sticker preview capability is unavailable");
  const response = await fetch(url, {
    signal,
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
  });
  if (
    !response.ok ||
    Number(response.headers.get("content-length")) > BYTE_LIMIT
  )
    throw new Error("Sticker preview capability is unavailable");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Sticker preview capability is unavailable");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > BYTE_LIMIT)
        throw new Error("Sticker preview capability is unavailable");
      chunks.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  signal.throwIfAborted();
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  signal.throwIfAborted();
  if (
    [...new Uint8Array(digest)]
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("") !== DIGEST
  )
    throw new Error("Sticker preview capability is unavailable");
  return bytes;
}

function startProbe(): Attempt {
  const controller = new AbortController();
  const video = document.createElement("video");
  const canvas = document.createElement("canvas");
  let current = true;
  let objectUrl: string | null = null;
  let resolve!: (value: WebmAlphaCapability) => void;
  const promise = new Promise<WebmAlphaCapability>((done) => {
    resolve = done;
  });
  const finish = (result: WebmAlphaCapability) => {
    if (!current) return;
    current = false;
    controller.abort();
    clearTimeout(timeout);
    video.onloadedmetadata = video.onloadeddata = video.onerror = null;
    video.pause();
    video.removeAttribute("src");
    video.load();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    canvas.width = canvas.height = 0;
    resolve(result);
  };
  const timeout = setTimeout(() => finish("unavailable"), DEADLINE_MS);
  video.muted = video.defaultMuted = true;
  video.setAttribute("muted", "");
  video.playsInline = true;
  video.preload = "auto";
  video.loop = video.autoplay = video.controls = false;
  video.disablePictureInPicture = video.disableRemotePlayback = true;
  video.onloadedmetadata = () => {
    if (
      current &&
      (video.videoWidth !== SIZE ||
        video.videoHeight !== SIZE ||
        !Number.isFinite(video.duration) ||
        video.duration > 1.1)
    )
      finish("unavailable");
  };
  video.onloadeddata = () => {
    if (!current || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA)
      return;
    try {
      if (video.videoWidth !== SIZE || video.videoHeight !== SIZE)
        return finish("unavailable");
      canvas.width = canvas.height = SIZE;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) return finish("unavailable");
      // HAVE_CURRENT_DATA guarantees a decoded current frame. No play(), loop,
      // DOM insertion, ordinary playback claim or private file is needed.
      context.drawImage(video, 0, 0, SIZE, SIZE);
      const { data } = context.getImageData(0, 0, SIZE, SIZE);
      const pixel = (x: number, y: number) =>
        data.subarray((y * SIZE + x) * 4, (y * SIZE + x) * 4 + 4);
      const center = pixel(16, 16);
      if (
        center[3] < 250 ||
        [36, 184, 72].some(
          (expected, index) => Math.abs(center[index] - expected) > 32,
        )
      )
        return finish("unavailable");
      const corners = [
        pixel(1, 1)[3],
        pixel(30, 1)[3],
        pixel(1, 30)[3],
        pixel(30, 30)[3],
      ];
      finish(
        corners.every((alpha) => alpha <= 4)
          ? "supported"
          : corners.every((alpha) => alpha >= 250)
            ? "unsupported"
            : "unavailable",
      );
    } catch {
      finish("unavailable");
    }
  };
  video.onerror = () => finish("unavailable");
  void probeBytes(controller.signal)
    .then((bytes) => {
      if (!current || controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(
        new Blob([bytes], { type: "video/webm" }),
      );
      video.src = objectUrl;
      video.load();
    })
    .catch(() => finish("unavailable"));
  return {
    promise,
    cancel: () => finish("unavailable"),
    users: 0,
    get finished() {
      return !current;
    },
  };
}

// Simultaneous stickers share one bounded probe. Only a verified pixel verdict
// is cached per document. A cancelled/failed attempt can be retried by a fresh,
// deliberate preview attempt; dropping the last owner aborts and cleans it up.
export function webmAlphaCapability(
  signal: AbortSignal,
): Promise<WebmAlphaCapability> {
  signal.throwIfAborted();
  let state = documents.get(document);
  if (!state) {
    state = { cached: null, attempt: null };
    documents.set(document, state);
  }
  if (state.cached) return Promise.resolve(state.cached);
  if (!state.attempt) {
    const attempt = startProbe();
    state.attempt = attempt;
    const owner = state;
    void attempt.promise.then((result) => {
      if (owner.attempt !== attempt) return;
      owner.attempt = null;
      if (result !== "unavailable") owner.cached = result;
    });
  }
  const attempt = state.attempt;
  const owner = state;
  attempt.users++;
  return new Promise((resolve, reject) => {
    let listening = true;
    const detach = () => {
      listening = false;
      signal.removeEventListener("abort", abort);
      attempt.users--;
    };
    const abort = () => {
      if (!listening) return;
      detach();
      if (!attempt.users && !attempt.finished) {
        if (owner.attempt === attempt) owner.attempt = null;
        attempt.cancel();
      }
      reject(new DOMException("Sticker preview cancelled", "AbortError"));
    };
    signal.addEventListener("abort", abort, { once: true });
    void attempt.promise.then((result) => {
      if (!listening) return;
      detach();
      resolve(result);
    });
    if (signal.aborted) abort();
  });
}
