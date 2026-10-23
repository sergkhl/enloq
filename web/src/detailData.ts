import { useCallback, useEffect, useRef, useState } from "react";
import { workspaceApi } from "./accounts";
import { chatApi, type NativeMessage } from "./conversation-api";
import { forwardDetailsMissing, type Forward } from "../../shared/forwarding";
import type { ReactionOptions } from "../../shared/reactions";

// Message details' data (ADR 46 CU2), as the app reads it: the account's
// reaction palette, Telegram's earlier versions of an original and missing
// forward headers.

const palettes = new Map<number, Promise<ReactionOptions>>();

// The account's selectable emoji, read once per account.
export function useReactionOptions(account: number) {
  const [options, setOptions] = useState<
    ReactionOptions | "unavailable" | null
  >(null);
  const load = useCallback(() => {
    let request = palettes.get(account);
    if (!request) {
      request = workspaceApi<ReactionOptions>(
        `accounts/${account}/reactions/options`,
      );
      palettes.set(account, request);
      request.catch(() => palettes.delete(account));
    }
    request.then(setOptions, () => setOptions("unavailable"));
  }, [account]);
  return { options, load };
}

export type ObservedVersion = {
  id: number;
  message: NativeMessage;
  transcript: { text: string; language: string | null } | null;
  observed: number;
  reason: "edited" | "deleted";
  media_available: boolean;
};

// An original's captured versions, newest first, read page by page as the
// owner steps back (ADR 19). `index` -1 is the current message.
export function useVersions(
  account: number,
  peer: string,
  message: NativeMessage | null,
) {
  const [versions, setVersions] = useState<ObservedVersion[]>([]);
  const [index, setIndex] = useState(-1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const count = message?.history_count ?? 0;
  const identity = message?.message_id;
  // A new revision or a deletion returns to the current message.
  useEffect(() => {
    setVersions([]);
    setIndex(-1);
    setError("");
  }, [identity, message?.revision, message?.deleted, count]);
  async function earlier() {
    if (busy || identity === undefined) return;
    setBusy(true);
    setError("");
    try {
      let all = versions;
      if (index + 1 >= all.length && all.length < count) {
        const page = await chatApi<ObservedVersion[]>(
          account,
          peer,
          `/originals/${identity}/versions?before=${all.at(-1)?.id ?? 0}`,
        );
        all = [...all, ...page];
        setVersions(all);
      }
      if (all[index + 1]) setIndex(index + 1);
    } catch (problem) {
      setError(
        (problem as Error).message || "Earlier versions couldn't be loaded.",
      );
    } finally {
      setBusy(false);
    }
  }
  return {
    count,
    index,
    shown: index >= 0 ? (versions[index] ?? null) : null,
    busy,
    error,
    earlier: () => void earlier(),
    later: () => setIndex((value) => Math.max(-1, value - 1)),
  };
}

type ForwardRow = Forward & { message_id: number };

// Missing forward headers, read in batches of 100 and at most once a minute
// per message (ADR 27). The service keeps what it learns; a message's own
// values always win.
export function useForwardDetails(
  account: number,
  peer: string,
  messages: NativeMessage[],
) {
  const [found, setFound] = useState<ReadonlyMap<number, Forward>>(new Map());
  const asked = useRef(new Map<number, number>());
  const wanted = messages
    .filter(
      (message) =>
        !message.deleted &&
        !message.service &&
        forwardDetailsMissing({ ...message, ...found.get(message.message_id) }),
    )
    .map((message) => message.message_id);
  const key = wanted.join(",");
  useEffect(() => {
    const now = Date.now();
    const identities = wanted
      .filter((identity) => (asked.current.get(identity) ?? 0) + 60_000 <= now)
      .slice(0, 100);
    if (!identities.length) return;
    for (const identity of identities) asked.current.set(identity, now);
    let live = true;
    void chatApi<ForwardRow[]>(account, peer, "/originals/forwarding", {
      identities,
    })
      .then((rows) => {
        if (!live || !Array.isArray(rows)) return;
        setFound((previous) => {
          const next = new Map(previous);
          for (const row of rows) next.set(row.message_id, row);
          return next;
        });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
    // The batch follows the wanted list.
  }, [key, account, peer]);
  return useCallback(
    (message: NativeMessage): NativeMessage => {
      const extra = found.get(message.message_id);
      if (!extra) return message;
      const filled = { ...message };
      for (const field of [
        "forwarded",
        "forwarded_from",
        "forward_date",
        "forward_signature",
        "imported",
        "forward_read",
      ] as const)
        if (filled[field] == null && extra[field] != null)
          (filled as Record<string, unknown>)[field] = extra[field];
      return filled;
    },
    [found],
  );
}
