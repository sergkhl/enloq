import { useEffect, useState, type RefObject } from "react";

// GIFs and animated stickers move only while on screen, in a visible page,
// with reduced motion off (ADR 46 CU1, the app's rule).

// Whether the element is in view in a visible page; it loads only then.
export function useOnScreen(target: RefObject<Element | null>) {
  const [inView, setInView] = useState(false);
  const [visible, setVisible] = useState(
    () => document.visibilityState === "visible",
  );
  useEffect(() => {
    const element = target.current;
    if (!element) return;
    const observer = new IntersectionObserver(([entry]) =>
      setInView(!!entry?.isIntersecting),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [target]);
  useEffect(() => {
    const change = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", change);
    return () => document.removeEventListener("visibilitychange", change);
  }, []);
  return inView && visible;
}

export function useReducedMotion() {
  const [reduce, setReduce] = useState(
    () => matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const query = matchMedia("(prefers-reduced-motion: reduce)");
    const change = () => setReduce(query.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  return reduce;
}
