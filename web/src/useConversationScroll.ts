import { useEffect, useLayoutEffect, useRef, useState } from "react";

type Anchor = { source: string; offset: number; bubble?: boolean };

export function useConversationScroll(
  contentVersion: string,
  opening?: { source: number | null; separator?: boolean },
) {
  const stream = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const anchor = useRef<Anchor | null>(null);
  const expectedScroll = useRef<number | null>(null);
  // The last scroll position this hook positioned or observed.
  const observed = useRef<number | null>(null);
  const opened = useRef(false);
  const [away, setAway] = useState(false);

  function rows() {
    return Array.from(
      list.current?.querySelectorAll<HTMLElement>("[data-source-ids]") || [],
    );
  }
  function remember() {
    const element = stream.current;
    if (!element) return;
    const top = element.getBoundingClientRect().top;
    const candidates = Array.from(
      list.current?.querySelectorAll<HTMLElement>(
        "[data-source-id], [data-source-ids]",
      ) || [],
    ).filter(
      (node) =>
        !!node.dataset.sourceId || !node.querySelector("[data-source-id]"),
    );
    // A group's pinned date band covers the stream's top edge; a row hidden
    // under it is not the reader's position.
    const band = Array.from(
      list.current?.querySelectorAll<HTMLElement>(".briefing-day-band") || [],
    )
      .map((node) => node.getBoundingClientRect())
      .find((box) => box.top <= top + 1 && box.bottom > top);
    const edge = band ? band.bottom : top;
    const row = candidates.find(
      (node) => node.getBoundingClientRect().bottom > edge + 1,
    );
    anchor.current = row
      ? {
          source: row.dataset.sourceId || row.dataset.sourceIds!.split(" ")[0],
          offset: row.getBoundingClientRect().top - top,
        }
      : null;
  }
  function position(value: number) {
    if (!stream.current) return;
    stream.current.scrollTop = value;
    expectedScroll.current = stream.current.scrollTop;
    observed.current = stream.current.scrollTop;
  }
  function restore() {
    const element = stream.current;
    if (!element) return;
    // A layout change can arrive before the reader's scroll event. That
    // position is still the reader's: adopt it before restoring an anchor.
    if (
      observed.current !== null &&
      Math.abs(element.scrollTop - observed.current) >= 1 &&
      (expectedScroll.current === null ||
        Math.abs(element.scrollTop - expectedScroll.current) >= 1)
    )
      readerScrolled(element);
    if (!opened.current && opening) {
      opened.current = true;
      // Opening shows the Unread Separator at the top when it marks the
      // opening message; it is never an anchor afterwards (ADR 28).
      const target =
        opening.source === null
          ? null
          : (opening.separator &&
              list.current?.querySelector<HTMLElement>(
                "[data-unread-separator]",
              )) ||
            list.current?.querySelector<HTMLElement>(
              `[data-source-id="${opening.source}"]`,
            ) ||
            rows().find((node) =>
              node.dataset
                .sourceIds!.split(" ")
                .includes(String(opening.source)),
            );
      if (target) {
        // A group's date band stays pinned above its day; open below it.
        const band = target
          .closest(".briefing-day")
          ?.querySelector<HTMLElement>(".briefing-day-band");
        position(
          element.scrollTop +
            target.getBoundingClientRect().top -
            element.getBoundingClientRect().top -
            (band && !band.contains(target)
              ? band.getBoundingClientRect().height
              : 0),
        );
        following.current =
          element.scrollHeight - element.clientHeight - element.scrollTop <= 32;
        setAway(!following.current);
        remember();
        return;
      }
    }
    if (following.current) {
      position(element.scrollHeight);
    } else {
      const saved = anchor.current;
      if (saved) {
        const row =
          (!saved.bubble &&
            list.current?.querySelector<HTMLElement>(
              `[data-source-id="${saved.source}"]`,
            )) ||
          rows().find((node) =>
            node.dataset.sourceIds!.split(" ").includes(saved.source),
          );
        if (row) {
          // A combined rewrite can shrink into a much shorter native message.
          // Keep some of that source visible when its old offset cannot fit.
          const offset = Math.min(
            element.clientHeight - 1,
            Math.max(saved.offset, 1 - row.getBoundingClientRect().height),
          );
          position(
            element.scrollTop +
              row.getBoundingClientRect().top -
              element.getBoundingClientRect().top -
              offset,
          );
        }
      }
      remember();
    }
  }
  function readerScrolled(element: HTMLDivElement) {
    expectedScroll.current = null;
    observed.current = element.scrollTop;
    following.current =
      element.scrollHeight - element.clientHeight - element.scrollTop <= 32;
    setAway(!following.current);
    remember();
  }
  function onScroll() {
    const element = stream.current;
    if (!element) return;
    if (
      expectedScroll.current !== null &&
      Math.abs(element.scrollTop - expectedScroll.current) < 1
    ) {
      expectedScroll.current = null;
      return;
    }
    readerScrolled(element);
  }
  function latest() {
    following.current = true;
    anchor.current = null;
    setAway(false);
    restore();
  }
  function pause(source?: number) {
    following.current = false;
    setAway(true);
    const element = stream.current;
    const row =
      source === undefined
        ? null
        : rows().find((node) =>
            node.dataset.sourceIds!.split(" ").includes(String(source)),
          );
    if (row && element) {
      // A local reading switch replaces native source nodes inside this
      // bubble. Anchor its shared outer box so the inner padding cannot be
      // mistaken for a change in the source's screen position.
      anchor.current = {
        source: String(source),
        offset:
          row.getBoundingClientRect().top - element.getBoundingClientRect().top,
        bubble: true,
      };
    } else remember();
  }
  useLayoutEffect(restore, [contentVersion]);
  useEffect(() => {
    const observer = new ResizeObserver(restore);
    if (stream.current) observer.observe(stream.current);
    if (list.current) observer.observe(list.current);
    return () => observer.disconnect();
  }, []);
  return { stream, list, away, onScroll, latest, pause };
}
