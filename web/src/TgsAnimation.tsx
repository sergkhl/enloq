import React, { useEffect, useRef } from "react";
import type {
  AnimationConfigWithData,
  AnimationItem,
  SVGRendererConfig,
} from "lottie-web";
import { animationText } from "./mediaFetch";

type SafeConfig = AnimationConfigWithData<"svg"> & {
  autoloadSegments: false;
  rendererSettings: SVGRendererConfig & { runExpressions: false };
};

function nonempty(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (value && typeof value === "object") return Object.keys(value).length > 0;
  return !!value;
}

// The backend has bounded and validated this vector-only representation. Parse
// a fresh rendering clone: Lottie completes/mutates data, and its optional CSS
// names must never become application IDs or classes.
function renderingClone(raw: string) {
  const data = JSON.parse(raw);
  if (!data || Array.isArray(data) || typeof data !== "object")
    throw new Error("Sticker animation is unavailable");
  if (
    nonempty(data.fonts?.list) ||
    nonempty(data.chars) ||
    nonempty(data.segments)
  )
    throw new Error("Sticker animation is unavailable");
  delete data.fonts;
  delete data.chars;
  if (
    !Array.isArray(data.layers) ||
    (data.assets || []).some(
      (asset: Record<string, unknown>) =>
        !asset || !Array.isArray(asset.layers) || "p" in asset || "u" in asset,
    )
  )
    throw new Error("Sticker animation is unavailable");
  const pending: { value: unknown; depth: number }[] = [
    { value: data, depth: 0 },
  ];
  let nodes = 0;
  while (pending.length) {
    const { value, depth } = pending.pop()!;
    if (++nodes > 100_000 || depth > 64)
      throw new Error("Sticker animation exceeds the rendering limit");
    if (!value || typeof value !== "object") continue;
    if (Array.isArray(value)) {
      for (const child of value)
        pending.push({ value: child, depth: depth + 1 });
      continue;
    }
    const object = value as Record<string, unknown>;
    if (
      typeof object.x === "string" ||
      nonempty(object.expression) ||
      nonempty(object.expressions) ||
      nonempty(object.ef) ||
      nonempty(object.ddd) ||
      nonempty(object.segments)
    )
      throw new Error("Sticker animation is unavailable");
    delete object.ln;
    delete object.cl;
    delete object.expression;
    delete object.expressions;
    delete object.ef;
    delete object.ddd;
    delete object.segments;
    for (const child of Object.values(object))
      pending.push({ value: child, depth: depth + 1 });
  }
  return data;
}

export function TgsAnimation({
  url,
  animate,
  onReady,
  onError,
}: {
  url: string;
  animate: boolean;
  onReady: () => void;
  onError: (message: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const instance = useRef<AnimationItem | null>(null);
  const motion = useRef(animate);
  motion.current = animate;
  const callbacks = useRef({ onReady, onError });
  callbacks.current = { onReady, onError };
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    let animation: AnimationItem | null = null;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const fail = (message: string) => {
      if (!current) return;
      current = false;
      controller.abort();
      clearTimeout(timeout);
      instance.current = null;
      animation?.destroy();
      animation = null;
      callbacks.current.onError(message);
    };
    timeout = setTimeout(
      () => fail("Sticker preview could not be loaded"),
      10_000,
    );
    void (async () => {
      try {
        const [raw, module] = await Promise.all([
          animationText(url, controller.signal),
          import("lottie-web/build/player/esm/lottie_light.min.js"),
        ]);
        if (!current || controller.signal.aborted || !host.current) return;
        const config: SafeConfig = {
          container: host.current,
          renderer: "svg",
          animationData: renderingClone(raw),
          autoplay: false,
          loop: true,
          autoloadSegments: false,
          rendererSettings: {
            runExpressions: false,
            focusable: false,
            preserveAspectRatio: "xMidYMid meet",
          },
        };
        animation = module.default.loadAnimation<"svg">(config);
        instance.current = animation;
        animation.addEventListener("DOMLoaded", () => {
          if (!current || !animation) return;
          clearTimeout(timeout);
          animation.goToAndStop(0, true);
          callbacks.current.onReady();
          if (motion.current) animation.play();
        });
        animation.addEventListener("data_failed", () =>
          fail("Sticker preview could not be loaded"),
        );
        animation.addEventListener("error", () =>
          fail("Sticker preview could not be shown"),
        );
      } catch (error) {
        if (current && !controller.signal.aborted)
          fail(
            (error as Error).message || "Sticker preview could not be shown",
          );
      }
    })();
    return () => {
      current = false;
      controller.abort();
      clearTimeout(timeout);
      instance.current = null;
      animation?.destroy();
    };
  }, [url]);
  useEffect(() => {
    const animation = instance.current;
    if (!animation?.isLoaded) return;
    if (animate) animation.play();
    else animation.goToAndStop(0, true);
  }, [animate]);
  return <div className="tgs-animation" ref={host} aria-hidden="true" />;
}
