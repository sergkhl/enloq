import { albumRuns } from "../../shared/gallery";
import { useCallback, useEffect, useRef, useState } from "react";
import { unreadPlace, type GroupDay } from "../../shared/groupBriefings";
import type { Schemas } from "./api";
import { readCached } from "./cache";
import { useConnection, useServerChanges } from "./connection";
import type { DailyBriefing, NativeMessage } from "./conversation";
import { coalesce } from "./data";
import type { Operation } from "./drafts";

export type Day = GroupDay<NativeMessage, DailyBriefing>;

// Hide Bots (ADR 34): one global preference shared by every trusted client,
// initially On. Only a successful save changes it; a refusal throws.
export function useHideBots() {
  const { api } = useConnection();
  const [hideBots, setHideBots] = useState(true);
  const answered = useRef(false);
  const reader = useRef(
    coalesce(async () => {
      const answer = await api<Schemas["HideBots"]>("preferences/hide-bots", {
        keep: "hide-bots",
      });
      answered.current = true;
      setHideBots(answer.hide_bots);
    }),
  );
  useEffect(() => {
    void readCached<Schemas["HideBots"]>("hide-bots").then((kept) => {
      if (kept && !answered.current) setHideBots(kept.hide_bots);
    });
    void reader.current();
  }, []);
  useServerChanges((change) => {
    if (change.kind === "refresh" || change.shared.includes("hide_bots"))
      void reader.current();
  });
  const choose = useCallback(
    async (next: boolean) => {
      const answer = await api<Schemas["HideBots"]>("preferences/hide-bots", {
        body: { hide_bots: next },
        keep: "hide-bots",
      });
      answered.current = true;
      setHideBots(answer.hide_bots);
    },
    [api],
  );
  return { hideBots, choose };
}

// The rows of a group's reading, oldest first, as the inverted list draws
// them: each drawn day's band, then its citation tools, its briefing card,
// the originals listed after it and its "N new messages" spoiler, then the
// sends not yet among the messages. `day` names the band each row sits under.
export type GroupRow =
  | { kind: "band"; key: string; day: string; entry: Day }
  | { kind: "tools"; key: string; day: string; entry: Day }
  | { kind: "card"; key: string; day: string; entry: Day; unread: boolean }
  | {
      kind: "original";
      key: string;
      day: string;
      entry: Day;
      message: NativeMessage;
      unread: boolean;
      // A Telegram album this original begins, as one grid (ADR 51 Q12).
      album?: NativeMessage[];
    }
  | { kind: "fold"; key: string; day: string; entry: Day; unread: boolean }
  | { kind: "pending"; key: string; day: string; operation: Operation };

// Telegram albums among consecutive originals (ADR 51 Q12): an album's first
// row carries it, and the rest leave the list.
export function withGroupAlbums(rows: GroupRow[]): GroupRow[] {
  const result: GroupRow[] = [];
  let index = 0;
  while (index < rows.length) {
    const row = rows[index]!;
    if (row.kind !== "original") {
      result.push(row);
      index += 1;
      continue;
    }
    let end = index + 1;
    while (end < rows.length && rows[end]!.kind === "original") end += 1;
    const run = rows.slice(index, end) as Extract<
      GroupRow,
      { kind: "original" }
    >[];
    let at = 0;
    for (const album of albumRuns(run.map((value) => value.message))) {
      const first = run[at]!;
      result.push(album.length > 1 ? { ...first, album } : first);
      at += album.length;
    }
    index = end;
  }
  return result;
}

export function groupRows(
  days: Day[],
  {
    view,
    originalDays,
    unread,
  }: {
    view: "ai" | "originals";
    // Days whose briefing a citation swapped for its originals (AG4).
    originalDays: ReadonlySet<string>;
    unread: number | null;
  },
): GroupRow[] {
  const rows: GroupRow[] = [];
  for (const entry of days) {
    if (!entry.drawn) continue;
    const { day, brief } = entry;
    const place = unreadPlace(entry, unread);
    rows.push({ kind: "band", key: `band:${day}`, day, entry });
    if (entry.showingOriginals && originalDays.has(day) && view === "ai")
      rows.push({ kind: "tools", key: `tools:${day}`, day, entry });
    const wording = !entry.showingOriginals && entry.hasWording;
    const missing = !!brief && !entry.prefix && (brief.partial || brief.gap);
    if (wording || missing)
      rows.push({
        kind: "card",
        key: `card:${day}`,
        day,
        entry,
        unread: place === "card",
      });
    const original = (message: NativeMessage) => {
      if (!entry.visible.has(message.message_id)) return;
      rows.push({
        kind: "original",
        key: `m${message.message_id}`,
        day,
        entry,
        message,
        unread: place === "message" && message.message_id === unread,
      });
    };
    entry.listed.forEach(original);
    if (entry.spoiler) {
      rows.push({
        kind: "fold",
        key: `fold:${day}`,
        day,
        entry,
        unread: place === "fold",
      });
      if (entry.open) entry.fresh.forEach(original);
    }
  }
  return rows;
}
