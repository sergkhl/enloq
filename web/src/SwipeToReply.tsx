import React, { createContext, useContext, useRef, useState } from "react";
import { CornerUpLeft } from "lucide-react";

// How far a bubble follows the finger, and how far it must go to reply
// (the app's `mobile/src/SwipeToReply.tsx`).
const LIMIT = 72;
const THRESHOLD = 56;

// The conversation's composer Reply while Reply is available, else null.
export const SwipeReply = createContext<((identity: number) => void) | null>(
  null,
);

type Handlers = Pick<
  React.HTMLAttributes<HTMLElement>,
  "onPointerDown" | "onPointerMove" | "onPointerUp" | "onPointerCancel"
>;

// Swipe to Reply (ADR 51 Q5): on a touch screen a bubble swiped from right
// to left past the threshold shows a reply arrow with a light vibration, and
// letting go does what its sheet's Reply does. A mouse keeps the long-press
// and right-click sheet; a vertical drag stays the page's scroll, which the
// bubble's `touch-action: pan-y` leaves to the browser.
export function useSwipeToReply(reply: number | null, off = false) {
  const swipe = useContext(SwipeReply);
  const enabled = !!swipe && reply !== null && !off;
  const start = useRef<{ x: number; y: number } | null>(null);
  const horizontal = useRef(false);
  const [offset, setOffset] = useState(0);
  const current = useRef(0);
  const move = (value: number) => {
    if (value <= -THRESHOLD && current.current > -THRESHOLD)
      navigator.vibrate?.(12);
    current.current = value;
    setOffset(value);
  };
  const reset = () => {
    start.current = null;
    horizontal.current = false;
    move(0);
  };
  const handlers: Handlers = enabled
    ? {
        onPointerDown: (event) => {
          if (event.pointerType === "mouse") return;
          start.current = { x: event.clientX, y: event.clientY };
          horizontal.current = false;
        },
        onPointerMove: (event) => {
          const from = start.current;
          if (!from) return;
          const dx = event.clientX - from.x;
          const dy = event.clientY - from.y;
          if (!horizontal.current) {
            if (dx < -12 && Math.abs(dx) > Math.abs(dy) * 2) {
              horizontal.current = true;
              try {
                event.currentTarget.setPointerCapture(event.pointerId);
              } catch {
                // A pointer the browser no longer tracks keeps no capture.
              }
            } else if (Math.abs(dy) > 12 || dx > 12) {
              start.current = null;
              return;
            } else return;
          }
          move(Math.max(Math.min(dx, 0), -LIMIT));
        },
        onPointerUp: () => {
          if (horizontal.current && current.current <= -THRESHOLD)
            swipe!(reply!);
          reset();
        },
        onPointerCancel: reset,
      }
    : {};
  const style: React.CSSProperties | undefined = offset
    ? { transform: `translateX(${offset}px)`, transition: "none" }
    : undefined;
  // Only while the bubble moves, so nothing at rest overflows the column.
  const arrow = offset ? (
    <span
      className="swipe-reply-arrow"
      aria-hidden
      style={{ opacity: Math.min(1, -offset / THRESHOLD) }}
    >
      <CornerUpLeft size={20} />
    </span>
  ) : null;
  return { handlers, style, arrow };
}

// The bubble's own long-press handlers and the swipe's, both called.
export function combined(first: Handlers, second: Handlers): Handlers {
  const result: Handlers = { ...first };
  for (const key of Object.keys(second) as (keyof Handlers)[]) {
    const a = first[key];
    const b = second[key];
    result[key] =
      a && b
        ? (event: React.PointerEvent<HTMLElement>) => {
            a(event);
            b(event);
          }
        : (a ?? b);
  }
  return result;
}
