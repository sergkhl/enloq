import React, {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { chatApi, type ViewedContent } from "./conversation-api";
import { viewable } from "../../shared/groupBriefings";

type Viewing = {
  account: number;
  peer: string;
  active: boolean;
  stream: React.RefObject<HTMLDivElement | null>;
  version: unknown;
  readyOnly?: boolean;
  // Ready messages still unacknowledged when focused viewing resumes.
  onReturn?: (sources: number[]) => void;
};
const Context = createContext<
  | (Viewing & {
      held: boolean;
      hold: (held: boolean) => void;
      // Originals folded under a closed spoiler, which are not viewed.
      withheld: ReadonlySet<number>;
      withhold: (sources: ReadonlySet<number>) => void;
    })
  | null
>(null);

export function LocalViewing({
  children,
  ...value
}: Viewing & { children: React.ReactNode }) {
  // A return to focused viewing holds every acknowledgement in this
  // conversation until its read of ready sources completes (ADR 28).
  const [held, hold] = useState(false);
  const [withheld, withhold] = useState<ReadonlySet<number>>(new Set());
  return (
    <Context.Provider value={{ ...value, held, hold, withheld, withhold }}>
      <FocusedSources />
      {children}
    </Context.Provider>
  );
}

// Folded messages are not counted as viewed (ADR 34): the group
// reading names the originals under a closed "N new messages" spoiler, and
// focused viewing leaves them out until it opens.
export function useWithheldViewing(sources: number[]) {
  const withhold = useContext(Context)?.withhold;
  const identity = sources.join(" ");
  useLayoutEffect(() => {
    if (!withhold) return;
    withhold(new Set(identity ? identity.split(" ").map(Number) : []));
    return () => withhold(new Set());
  }, [withhold, identity]);
}

// Focused viewing: a visible, focused page without an open dialog, with the
// conversation stream on screen.
function streamFocused(root: HTMLElement | null) {
  if (
    !root ||
    document.visibilityState !== "visible" ||
    !document.hasFocus() ||
    document.querySelector("dialog[open]")
  )
    return false;
  const r = root.getBoundingClientRect();
  return !(
    r.width <= 0 ||
    r.height <= 0 ||
    r.bottom <= 0 ||
    r.top >= innerHeight ||
    r.right <= 0 ||
    r.left >= innerWidth
  );
}

function useAcknowledgement(
  content: ViewedContent[],
  end?: React.RefObject<HTMLSpanElement | null>,
) {
  const viewing = useContext(Context);
  const acknowledged = useRef(new Set<string>());
  const observations = useRef(
    new Map<string, { since: number; attempts: number; retryAt: number }>(),
  );
  const tokenKey = (token: ViewedContent) => JSON.stringify(token);
  const identity = JSON.stringify(content);
  useEffect(() => {
    if (!content.length || !viewing?.active || viewing.held) {
      observations.current.clear();
      return;
    }
    const current = new Set(content.map(tokenKey));
    for (const key of observations.current.keys())
      if (!current.has(key)) observations.current.delete(key);
    const root = viewing.stream.current;
    const node = end?.current;
    if (!root || (end && !node)) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false,
      inFlight = false;
    let controller: AbortController | undefined;
    function visible() {
      if (!streamFocused(root)) return false;
      if (!node) return true;
      const r = root!.getBoundingClientRect();
      const n = node.getBoundingClientRect();
      return (
        n.width > 0 &&
        n.height > 0 &&
        n.top >= Math.max(r.top, 0) &&
        n.bottom <= Math.min(r.bottom, innerHeight) &&
        n.left >= Math.max(r.left, 0) &&
        n.right <= Math.min(r.right, innerWidth)
      );
    }
    function observe() {
      if (cancelled) return;
      if (!visible()) {
        clearTimeout(timer);
        timer = undefined;
        observations.current.clear();
        controller?.abort();
        return;
      }
      const now = performance.now();
      for (const token of content) {
        const key = tokenKey(token);
        if (!acknowledged.current.has(key) && !observations.current.has(key))
          observations.current.set(key, {
            since: now,
            attempts: 0,
            retryAt: 0,
          });
      }
      if (timer || inFlight) return;
      const deadlines = content.flatMap((token) => {
        const entry = observations.current.get(tokenKey(token));
        return entry &&
          entry.attempts < 3 &&
          !acknowledged.current.has(tokenKey(token))
          ? [Math.max(entry.since + 1000, entry.retryAt)]
          : [];
      });
      if (!deadlines.length) return;
      timer = setTimeout(
        () => {
          timer = undefined;
          if (cancelled || !visible()) {
            observe();
            return;
          }
          const qualified = content.filter((token) => {
            const entry = observations.current.get(tokenKey(token));
            return (
              entry &&
              entry.attempts < 3 &&
              !acknowledged.current.has(tokenKey(token)) &&
              Math.max(entry.since + 1000, entry.retryAt) <= performance.now()
            );
          });
          if (!qualified.length) {
            observe();
            return;
          }
          inFlight = true;
          controller = new AbortController();
          let attempted: ViewedContent[] = [];
          void (async () => {
            // The captured tokens bind this dwell to exact sources, even when a
            // newer source becomes ready or navigation occurs while posting.
            for (let offset = 0; offset < qualified.length; offset += 100) {
              if (cancelled || !visible() || controller!.signal.aborted) return;
              attempted = qualified.slice(offset, offset + 100);
              for (const token of attempted)
                ++observations.current.get(tokenKey(token))!.attempts;
              await chatApi(
                viewing!.account,
                viewing!.peer,
                "/viewed",
                { content: attempted },
                controller!.signal,
              );
              for (const token of attempted)
                acknowledged.current.add(tokenKey(token));
              attempted = [];
            }
          })()
            .catch(() => {
              // Three attempts per token and continuous observation. A new ready
              // token inherits no earlier dwell and cannot reset older deadlines.
              for (const token of attempted) {
                const entry = observations.current.get(tokenKey(token));
                if (entry)
                  entry.retryAt = performance.now() + 1000 * entry.attempts;
              }
            })
            .finally(() => {
              inFlight = false;
              observe();
            });
        },
        Math.max(0, Math.min(...deadlines) - now),
      );
    }
    const intersection = new IntersectionObserver(observe, {
      root,
      threshold: 1,
    });
    if (node) intersection.observe(node);
    const resize = new ResizeObserver(observe);
    resize.observe(root);
    root.addEventListener("scroll", observe, { passive: true });
    window.addEventListener("focus", observe);
    window.addEventListener("blur", observe);
    document.addEventListener("visibilitychange", observe);
    const dialogs = new MutationObserver(observe);
    dialogs.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["open"],
    });
    observe();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller?.abort();
      intersection.disconnect();
      resize.disconnect();
      dialogs.disconnect();
      root.removeEventListener("scroll", observe);
      window.removeEventListener("focus", observe);
      window.removeEventListener("blur", observe);
      document.removeEventListener("visibilitychange", observe);
    };
  }, [
    identity,
    viewing?.account,
    viewing?.peer,
    viewing?.active,
    viewing?.held,
  ]);
}

function FocusedSources() {
  const viewing = useContext(Context)!;
  const [content, setContent] = useState<ViewedContent[]>([]);
  const refresh = useRef<() => void>(() => {});
  const onReturn = useRef(viewing.onReturn);
  onReturn.current = viewing.onReturn;
  const hold = viewing.hold;
  // Leaving focused viewing after it first began, and the read due on return.
  const activated = useRef(false),
    departed = useRef(false),
    returning = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    // Reads started in this effect; only one started after a return resolves it.
    let loads = 0,
      mark = 0;
    let attempts = 0,
      inFlight = false,
      again = false;
    if (!viewing.active) {
      if (activated.current) departed.current = true;
      setContent([]);
      return;
    }
    activated.current = true;
    async function load() {
      const read = ++loads;
      inFlight = true;
      again = false;
      ++attempts;
      let succeeded = false;
      try {
        const snapshot: ViewedContent[] = [];
        let after = 0;
        do {
          const page = await chatApi<{
            content: ViewedContent[];
            next_cursor: number | null;
          }>(
            viewing.account,
            viewing.peer,
            `/ready-sources?after=${after}&limit=100`,
            undefined,
            controller.signal,
          );
          snapshot.push(...page.content);
          if (page.next_cursor === null) break;
          if (page.next_cursor <= after)
            throw new Error("Ready sources could not be loaded.");
          after = page.next_cursor;
        } while (!controller.signal.aborted);
        if (!controller.signal.aborted) {
          setContent((old) =>
            JSON.stringify(old) === JSON.stringify(snapshot) ? old : snapshot,
          );
          // Messages that became ready while away move the Unread Separator,
          // before the held acknowledgements resume.
          if (returning.current && read > mark) {
            returning.current = false;
            const sources = snapshot
              .filter((c) => c.kind === "original")
              .map((c) => Number(c.identity))
              .sort((a, b) => a - b);
            if (sources.length) onReturn.current?.(sources);
            hold(false);
          }
        }
        succeeded = true;
        attempts = 0;
      } catch {
        if (!controller.signal.aborted && attempts < 3)
          timer = setTimeout(() => {
            timer = undefined;
            void load();
          }, 1000 * attempts);
        else if (!controller.signal.aborted && returning.current) {
          // No read to wait for: acknowledgement resumes without a move.
          returning.current = false;
          hold(false);
        }
      } finally {
        inFlight = false;
        if (succeeded && again && !controller.signal.aborted) void load();
      }
    }
    refresh.current = () => {
      again = true;
      if (!inFlight && !timer && attempts < 3) void load();
    };
    // A return detected on activation holds acknowledgement before its read.
    focus();
    refresh.current();
    function focus() {
      if (!streamFocused(viewing.stream.current)) {
        departed.current = true;
        return false;
      }
      if (!departed.current) return false;
      departed.current = false;
      returning.current = true;
      mark = loads;
      hold(true);
      return true;
    }
    const resume = () => {
      focus();
      if (document.visibilityState !== "visible" || !document.hasFocus())
        return;
      if (!inFlight && !timer) attempts = 0;
      refresh.current();
    };
    const dialogs = new MutationObserver(() => {
      if (focus()) resume();
    });
    dialogs.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["open"],
    });
    window.addEventListener("focus", resume);
    window.addEventListener("blur", focus);
    document.addEventListener("visibilitychange", resume);
    return () => {
      refresh.current = () => {};
      controller.abort();
      clearTimeout(timer);
      dialogs.disconnect();
      window.removeEventListener("focus", resume);
      window.removeEventListener("blur", focus);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [viewing.account, viewing.peer, viewing.active]);
  // Complete a captured paginated read even during frequent draft/stream
  // refreshes. Coalesce new reads; only navigation or pane lifetime aborts it.
  useEffect(() => refresh.current(), [viewing.version]);
  useAcknowledgement(viewable(content, viewing.withheld));
  return null;
}

// Before readiness, current original content still qualifies at its visible end.
// Ready sources use the independent conversation-focused snapshot above.
export function ViewedEnd({ content }: { content?: ViewedContent | null }) {
  const end = useRef<HTMLSpanElement>(null);
  const viewing = useContext(Context);
  const original =
    !viewing?.readyOnly && content?.kind === "original" ? content : null;
  useAcknowledgement(original ? [original] : [], end);
  return original ? (
    <span
      ref={end}
      className="viewed-end"
      aria-hidden="true"
      data-view-kind={original.kind}
      data-view-identity={original.identity}
    />
  ) : null;
}
