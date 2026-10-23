import { useEffect, useState } from "react";
import type { components } from "./api-schema";

export type Emotion = components["schemas"]["EmotionView"];
export function useEmotion(signal?: Emotion): Emotion {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const update = () => setNow(Date.now());
    update();
    const wait = (signal?.expires_at ?? 0) * 1000 - Date.now();
    const timer =
      wait > 0
        ? window.setTimeout(update, Math.min(wait + 1, 2147483647))
        : undefined;
    document.addEventListener("visibilitychange", update);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, [signal?.expires_at]);
  if (
    signal?.expires_at &&
    Math.max(now, Date.now()) >= signal.expires_at * 1000
  )
    return { ...signal, code: null, reason: "expired" };
  return (
    signal ?? {
      code: null,
      reason: "unavailable",
      source_time: null,
      expires_at: null,
      source: "incoming",
      reaction: null,
      target_id: null,
    }
  );
}
