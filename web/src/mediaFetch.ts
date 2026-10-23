// Private preview reads stay on the authenticated application origin. These
// routes serve prepared bytes; reading them never starts a native download.
export function privateMediaUrl(value?: string | null): string | null {
  if (!value?.startsWith("/api/accounts/")) return null;
  const url = new URL(value, location.origin);
  return url.origin === location.origin ? value : null;
}

export async function animationText(url: string, signal: AbortSignal) {
  const source = privateMediaUrl(url);
  if (!source) throw new Error("Sticker preview unavailable");
  const response = await fetch(source, {
    signal,
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
  });
  if (response.status === 401)
    window.dispatchEvent(new Event("browser-auth-lost"));
  if (!response.ok) throw new Error("Sticker preview could not be loaded");
  const limit = 4 * 1024 * 1024;
  if (Number(response.headers.get("content-length")) > limit)
    throw new Error("Sticker preview exceeds the animation limit");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Sticker preview could not be loaded");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit)
        throw new Error("Sticker preview exceeds the animation limit");
      chunks.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  signal.throwIfAborted();
  const data = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    data.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(data);
}
