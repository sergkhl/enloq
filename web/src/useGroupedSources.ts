import { useEffect, useState } from "react";
import {
  chatApi,
  type ConversationData,
  type NativeMessage,
} from "./conversation-api";

type Block = ConversationData["blocks"][number];
type Loaded = { messages: NativeMessage[]; complete: boolean; error?: string };
const key = (block: Block) =>
  JSON.stringify([block.id, block.revision, block.sources]);

// Only groups intersecting an already-open local reading page are expanded.
// Every request stays local, paginated and tied to that group's revision.
export function useGroupedSources(
  account: number,
  peer: string,
  blocks: Block[],
  native: NativeMessage[],
) {
  const [loaded, setLoaded] = useState<Record<string, Loaded>>({});
  const [retry, setRetry] = useState(0);
  const ids = new Set(native.map((message) => message.message_id));
  const nativeById = new Map(
    native.map((message) => [message.message_id, message]),
  );
  const snapshotComplete = (block: Block) =>
    !!block.source_revisions &&
    Object.keys(block.source_revisions).length === block.sources.length &&
    block.sources.every((id) => block.source_revisions![id] !== undefined);
  const current = (block: Block, messages: Map<number, NativeMessage>) =>
    snapshotComplete(block) &&
    block.sources.every((id) => {
      const message = messages.get(id);
      return (
        message &&
        !message.deleted &&
        block.source_revisions![id] === message.revision
      );
    });
  const displayed = blocks.filter((block) =>
    block.sources.some((id) => ids.has(id)),
  );
  const missing = displayed.filter((block) => !current(block, nativeById));
  const signature = JSON.stringify(
    missing.map((block) => [
      key(block),
      block.source_revisions,
      block.sources.map((id) => {
        const message = nativeById.get(id);
        return message ? [message.revision, !!message.deleted] : null;
      }),
    ]),
  );
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void (async () => {
      for (const block of missing) {
        const identity = key(block);
        const prior = loaded[identity];
        if (prior?.complete) {
          const retained = new Map(
            prior.messages.map((m) => [m.message_id, m]),
          );
          for (const message of native)
            if (
              (retained.get(message.message_id)?.revision || 0) <=
              message.revision
            )
              retained.set(message.message_id, message);
          if (current(block, retained)) continue;
        }
        const messages: NativeMessage[] = [];
        try {
          let cursor = 0;
          let unavailable = false;
          do {
            const page = await chatApi<{
              revision: string;
              messages: NativeMessage[];
              next_cursor: number | null;
              missing: number[];
            }>(
              account,
              peer,
              `/blocks/${block.id.split("/").pop()}/sources?revision=${encodeURIComponent(block.revision)}&cursor=${cursor}&limit=100`,
              undefined,
              controller.signal,
            );
            if (!active) return;
            if (page.revision !== block.revision)
              throw new Error(
                "Original sources changed; refresh the conversation.",
              );
            unavailable ||= page.missing.length > 0;
            messages.push(...page.messages);
            if (page.next_cursor === null) break;
            if (page.next_cursor <= cursor)
              throw new Error("Original sources could not be loaded.");
            cursor = page.next_cursor;
          } while (active);
          const found = new Set(messages.map((message) => message.message_id));
          unavailable ||= block.sources.some((id) => !found.has(id));
          setLoaded((prior) => ({
            ...prior,
            [identity]: {
              messages,
              complete: !unavailable,
              error: unavailable
                ? "Some original sources are no longer cached."
                : undefined,
            },
          }));
        } catch (error) {
          if (!active) return;
          setLoaded((prior) => ({
            ...prior,
            [identity]: {
              messages:
                "status" in (error as object) &&
                (error as { status: number }).status === 409
                  ? []
                  : messages,
              complete: false,
              error: (error as Error).message,
            },
          }));
        }
      }
    })();
    return () => {
      active = false;
      controller.abort();
    };
  }, [account, peer, signature, retry]);
  const all = new Map<number, NativeMessage>();
  for (const block of displayed)
    for (const message of loaded[key(block)]?.messages || [])
      if (block.sources.includes(message.message_id))
        all.set(message.message_id, message);
  // A loaded history page can be older than a refreshed group source. Local
  // revisions are monotonic; at equal revisions use the native page metadata.
  for (const message of native) {
    if ((all.get(message.message_id)?.revision || 0) <= message.revision)
      all.set(message.message_id, message);
  }
  return {
    retry: () => setRetry((value) => value + 1),
    messages: Array.from(all.values()).sort(
      (a, b) => a.message_id - b.message_id,
    ),
    status(block: Block) {
      if (current(block, nativeById)) return null;
      if (loaded[key(block)]?.error) return loaded[key(block)].error!;
      if (!snapshotComplete(block))
        return "Original source revisions unavailable; refresh the conversation.";
      if (current(block, all)) return null;
      return loaded[key(block)]?.error || "Loading original sources…";
    },
  };
}
