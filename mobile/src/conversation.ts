import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { peerTab } from "../../shared/chatRoute";
import type { GroupFilter } from "../../shared/groupBriefings";
import type { OlderState, Progress } from "../../shared/aiControls";
import type { DeletionOperation } from "../../shared/deletion";
import type { ReactionView } from "../../shared/reactions";
import type { Schemas } from "./api";
import { chatKey, readCached } from "./cache";
import { useConnection, useServerChanges } from "./connection";
import { coalesce, type ReadingView } from "./data";
import type { Draft, Operation, ReplyHold } from "./drafts";

// The conversation snapshot (`GET …/chats/{peer}`), as far as MC2 reads it.
// The server declares no response model for it; the web's
// `conversation-api.ts` is the reference.
export type NativeMessage = Schemas["Message"] & {
  history_count?: number;
  // A group author's current photo, for their avatar (ADR 51 Q13).
  author_photo_id?: string | null;
  // Telegram's album (`grouped_id`), drawn as one grid (ADR 51 Q12).
  album?: string | null;
  // Native reactions (ADR 46 CU2), on every message the service annotates.
  reactions?: ReactionView | null;
  viewed_content?: Schemas["ViewedContent"] | null;
  // A group original's filtering (ADR 34): an ad, an ignored bot, or a bot
  // the owner exempted from Hide Bots.
  group_filter?: GroupFilter | null;
  // A group original's fingerprint for own-only deletion (ADR 34).
  deletion_fingerprint?: string | null;
  // A deleted message's content is what it last said (ADR 52 Q7): the
  // version captured at its deletion, and whether Enloq still holds its media.
  deleted_version?: number | null;
  media_available?: boolean | null;
};

export type Block = {
  id: string;
  revision: string;
  sources: number[];
  rendered: string | null;
  rendered_revision: string | null;
  stream?: { attempt: string; text: string; revision: string } | null;
  state: string;
  stage: string | null;
  error: string | null;
  // What the AI skipped in it (ADR 46 CU3).
  unanalysed?: { id: number; kind: string; reason?: string | null }[];
  // Its rewrite was calmed (ADR 53 Q5).
  calmed?: boolean;
};

// A group's Daily Briefing for one day (ADR 34), as the web's
// `conversation-api.ts` types it.
export type DailyBriefing = {
  day: string;
  start: number;
  end: number;
  source_ids: number[];
  covered_ids: number[];
  uncovered_ids: number[];
  partial: boolean;
  gap: boolean;
  cached: boolean;
  omissions: { message_id: number; reason: string }[];
  intake_coverage: { native_omitted: number; range_complete: boolean };
  final: {
    id: string;
    text: string;
    outcome: "briefing" | "no_substance";
    local_outcome?: "ads_skipped" | "bots_skipped" | null;
    topics: {
      text: string;
      citations: number[];
      title?: string | null;
      participants?: { peer: string; name: string }[];
    }[];
    covered_ids: number[];
    covered_start: number;
    covered_end: number;
  } | null;
  attempt: { id: string; state: string; error: string | null } | null;
  stream: { attempt: string; text: string } | null;
  analyzing: boolean;
  retry: { attempt: string; enabled: boolean; reason: string | null } | null;
};

// Today's Briefing Slot state (ADR 34).
export type BriefingUpdate = {
  enabled: boolean;
  status: string;
  reason: string | null;
  day: string;
  attempt?: string;
  transient?: boolean;
};

export type Snapshot = {
  reminder_occurrence?: string | null;
  timezone: string;
  chat: Schemas["Chat"];
  messages: NativeMessage[];
  blocks: Block[];
  opening_source?: number | null;
  unread_sources?: number[];
  emotion?: Schemas["EmotionView"] | null;
  suggestions?: Schemas["SuggestionsView"] | null;
  // The chat's service-held draft and its saved submissions (MC3).
  draft?: Draft;
  operations?: Operation[];
  reply_holds?: Record<string, ReplyHold>;
  // Group Chats only (ADR 34, ADR 42).
  briefings?: DailyBriefing[];
  briefing_update?: BriefingUpdate | null;
  group_processing?: Schemas["GroupProcessing"] | null;
  // Whether this private chat calms frustrated or angry messages (ADR 53).
  calming?: boolean | null;
  // A retained group (ADR 34): cached history, read-only.
  retained?: boolean;
  // The chat's recent deletions (ADR 31, ADR 46 CU4).
  deletions?: DeletionOperation[];
  // Rewrite earlier messages (ADR 28, ADR 46 CU3): a run's progress and
  // what is left of the loaded history.
  progress?: Progress | null;
  older?: OlderState | null;
};

export type ReadingItem =
  | {
      kind: "group";
      key: string;
      block: Block;
      messages: NativeMessage[];
      // Provisional while streaming; null until a rewrite is ready.
      text: string | null;
      streaming: boolean;
    }
  | {
      kind: "message";
      key: string;
      message: NativeMessage;
      // A Telegram album this message begins, as one grid (ADR 51 Q12).
      album?: NativeMessage[];
    };

// The status the web shows for a block that has no rewrite to show yet.
export const STAGES: Record<string, string> = {
  quiet: "Waiting for a quiet moment…",
  queued: "Queued",
  preparing: "Preparing…",
  transcribing: "Transcribing…",
  reading_images: "Reading images…",
  rewriting: "Analyzing…",
  budget: "Waiting for budget",
  retrying: "Provider busy, retrying shortly",
  format_paused: "Paused after repeated format failures",
  paused: "Paused: AI processing is Off for this account",
  nothing: "Nothing to rewrite",
};

export function blockText(block: Block) {
  if (block.stream && block.stream.revision === block.revision)
    return { text: block.stream.text, streaming: true };
  if (block.rendered !== null && block.rendered_revision === block.revision)
    return { text: block.rendered, streaming: false };
  return { text: null, streaming: false };
}

// Messages oldest first. In AI reading a Message Group shows once, at its
// first loaded source (ADR 24); everything else shows as its original. A
// block's own choice overrides the reading view for it alone (ADR 46 CU3).
export function readingItems(
  snapshot: Snapshot,
  view: ReadingView,
  blockViews: Readonly<Record<string, ReadingView>> = {},
): ReadingItem[] {
  const messages = [...snapshot.messages].sort(
    (a, b) => a.message_id - b.message_id,
  );
  const owner = new Map<number, Block>();
  for (const block of snapshot.blocks)
    for (const source of block.sources) owner.set(source, block);
  const shown = new Set<string>();
  const items: ReadingItem[] = [];
  for (const message of messages) {
    const block = owner.get(message.message_id);
    if (!block || (blockViews[block.id] ?? view) === "originals") {
      items.push({ kind: "message", key: `m${message.message_id}`, message });
      continue;
    }
    if (shown.has(block.id)) continue;
    shown.add(block.id);
    const sources = new Set(block.sources);
    items.push({
      kind: "group",
      key: `b${block.id}`,
      block,
      messages: messages.filter((item) => sources.has(item.message_id)),
      ...blockText(block),
    });
  }
  return items;
}

// ADR 28: the Unread messages line sits above the first captured unseen
// source that is loaded and not deleted, above its whole group in AI reading.
export function unreadAnchor(items: ReadingItem[], captured: number[]) {
  for (const id of captured)
    for (const item of items) {
      const messages = item.kind === "group" ? item.messages : [item.message];
      if (messages.some((m) => m.message_id === id && !m.deleted))
        return item.key;
    }
  return null;
}

export function mediaLabel(message: NativeMessage) {
  switch (message.media) {
    case "image":
      return "Photo";
    case "audio":
      return "Voice message";
    case "video_note":
      return "Video message";
    case "video":
      return "Video";
    case "gif":
      return "GIF";
    case "sticker":
      return "Sticker";
    case "document":
      return "File";
    case "poll":
      return "Poll";
    case "location":
      return "Location";
    case "contact":
      return "Contact";
    case "other":
    case "unsupported":
      return "Unsupported message";
    default:
      return null;
  }
}

// Merge by message ID, keeping the higher revision (the web's rule).
export function mergeMessages(
  kept: NativeMessage[],
  incoming: NativeMessage[],
) {
  const merged = new Map(kept.map((m) => [m.message_id, m]));
  for (const message of incoming) {
    const old = merged.get(message.message_id);
    if (!old || message.revision >= old.revision)
      merged.set(message.message_id, message);
  }
  return [...merged.values()].sort((a, b) => a.message_id - b.message_id);
}

// A conversation's snapshot, reread when the server changes this chat. The
// last snapshot read on this phone shows until the server answers, or while
// it can't (Q20). A group also keeps the originals a citation or its opening
// brought in, rechecked against the service's cache on every reread, as the
// web does (ADR 42 AG3, AG4).
export function useConversation(account: number, peer: string) {
  const { api } = useConnection();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  // The server answered since the chat opened; until then the snapshot may be
  // the offline copy.
  const [live, setLive] = useState(false);
  // When the read behind the shown snapshot started.
  const [readAt, setReadAt] = useState(0);
  const [earlier, setEarlier] = useState<NativeMessage[]>([]);
  const kept = useRef(earlier);
  kept.current = earlier;
  const [error, setError] = useState("");
  // The unread sources captured when the chat opened (ADR 28).
  const [captured, setCaptured] = useState<number[] | null>(null);
  const opened = useRef(false);
  const path = `accounts/${account}/chats/${encodeURIComponent(peer)}`;
  const keep = chatKey(account, peer);
  const group = peerTab(peer) === "group";
  const reader = useRef(
    coalesce(async () => {
      try {
        const started = Date.now();
        const next = await api<Snapshot>(path, { keep });
        if (group) await recheck(next);
        setSnapshot(next);
        setLive(true);
        setReadAt(started);
        setCaptured((kept) => kept ?? next.unread_sources ?? []);
        setError("");
      } catch (problem) {
        setError(problem instanceof Error ? problem.message : String(problem));
      }
    }),
  );

  // A group opens at its earliest unseen ready message, read from the cache
  // around it when the page lacks it; kept originals outside the page are
  // read again, dropping those the cache no longer holds.
  async function recheck(next: Snapshot) {
    const loaded = new Set(next.messages.map((m) => m.message_id));
    if (!opened.current) {
      opened.current = true;
      const source = next.opening_source;
      if (source && !loaded.has(source)) {
        try {
          const window = await api<NativeMessage[]>(
            `${path}/window?around=${source}`,
          );
          setEarlier((shown) => mergeMessages(shown, window));
          for (const message of window) loaded.add(message.message_id);
        } catch {
          // Without it the chat opens at its latest message.
        }
      }
    }
    const identities = kept.current
      .map((message) => message.message_id)
      .filter((identity) => !loaded.has(identity));
    const current: NativeMessage[] = [];
    const missing = new Set<number>();
    for (let offset = 0; offset < identities.length; offset += 100) {
      const page = await api<{ messages: NativeMessage[]; missing: number[] }>(
        `${path}/originals/current`,
        { body: { identities: identities.slice(offset, offset + 100) } },
      );
      current.push(...page.messages);
      for (const identity of page.missing) missing.add(identity);
    }
    if (current.length || missing.size)
      setEarlier((shown) =>
        mergeMessages(
          shown.filter((message) => !missing.has(message.message_id)),
          current,
        ),
      );
  }

  useEffect(() => {
    void readCached<Snapshot>(keep).then((kept) => {
      if (kept) setSnapshot((shown) => shown ?? kept);
    });
    void reader.current();
    const timer = setInterval(() => void reader.current(), 30_000);
    return () => clearInterval(timer);
  }, [keep]);
  useServerChanges((change) => {
    if (
      change.kind === "refresh" ||
      change.chats[String(account)]?.includes(`${account}/${peer}`)
    )
      void reader.current();
  });

  const loadEarlier = useCallback(async () => {
    const loaded = mergeMessages(earlier, snapshot?.messages ?? []);
    const oldest = loaded[0]?.message_id;
    if (!oldest) return 0;
    const page = await api<NativeMessage[]>(`${path}/history?before=${oldest}`);
    setEarlier((kept) => mergeMessages(kept, page));
    return page.length;
  }, [api, earlier, path, snapshot]);

  // Originals a citation brought in (ADR 42 AG4).
  const merge = useCallback((messages: NativeMessage[]) => {
    setEarlier((kept) => mergeMessages(kept, messages));
  }, []);

  // One object per read and page of earlier messages, so screens can tell a
  // new read from a render.
  const merged = useMemo<Snapshot | null>(
    () =>
      snapshot && {
        ...snapshot,
        messages: mergeMessages(earlier, snapshot.messages),
      },
    [snapshot, earlier],
  );
  return {
    snapshot: merged,
    live,
    readAt,
    captured: captured ?? [],
    error,
    reload: () => reader.current(),
    loadEarlier,
    merge,
  };
}
