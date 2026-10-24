import { useCallback, useEffect, useRef, useState } from "react";
import type { Schemas } from "./api";
import { readCached } from "./cache";
import { useConnection, useServerChanges } from "./connection";

export type ConversationRow = Schemas["ConversationRow"];
export type ReadingView = Schemas["ReadingView"]["reading_view"];
// One read at a time: a request made while one runs is answered by a single
// read started after it, so bursts of changes (a streaming rewrite) cost one
// extra read rather than one each.
export function coalesce(read: () => Promise<void>) {
  let running: Promise<void> | null = null;
  let again = false;
  const run = (): Promise<void> => {
    if (running) {
      again = true;
      return running;
    }
    running = read()
      .catch(() => undefined)
      .finally(() => {
        running = null;
        if (again) {
          again = false;
          void run();
        }
      });
    return running;
  };
  return run;
}

// The account's two lists (ADR 34): private chats and Group Chats.
export type ChatKind = "private" | "group";

// What each list keeps for reading offline (Q20).
export const listKey = (account: number, kind: ChatKind) =>
  kind === "private" ? `list:${account}` : `groups:${account}`;

// The account's seven-day Private or Groups list (ADR 38 Q6, ADR 42 AG2),
// reread when the server changes one of its chats, when the app returns and
// once a minute. The last list read shows until the server answers, or while
// it can't (Q20). No list is read while the Saved tab shows (ADR 50).
export function useChatList(
  account: number | null,
  kind: ChatKind | null = "private",
) {
  const { api } = useConnection();
  const [rows, setRows] = useState<ConversationRow[] | null>(null);
  const shown = useRef(`${account}/${kind}`);
  const reader = useRef<() => Promise<void>>(async () => undefined);

  useEffect(() => {
    const current = `${account}/${kind}`;
    shown.current = current;
    setRows(null);
    if (account === null || kind === null) return;
    const keep = listKey(account, kind);
    void readCached<ConversationRow[]>(keep).then((kept) => {
      if (kept && shown.current === current) setRows((rows) => rows ?? kept);
    });
    reader.current = coalesce(async () => {
      const next = await api<ConversationRow[]>(
        `accounts/${account}/chats?kind=${kind}`,
        { keep },
      );
      if (shown.current === current) setRows(next);
    });
    void reader.current();
    const timer = setInterval(() => void reader.current(), 60_000);
    return () => clearInterval(timer);
  }, [account, api, kind]);

  useServerChanges((change) => {
    if (account === null || kind === null) return;
    if (change.kind === "refresh" || change.chats[String(account)]?.length)
      void reader.current();
  });

  const reload = useCallback(() => reader.current(), []);
  return { rows, reload };
}

// The global AI or Originals reading choice, shared by every trusted client.
export function useReadingView() {
  const { api } = useConnection();
  const [view, setView] = useState<ReadingView>("ai");
  const answered = useRef(false);
  // A read that started before a choice must not undo it.
  const version = useRef(0);
  const reader = useRef(
    coalesce(async () => {
      const started = version.current;
      const answer = await api<Schemas["ReadingView"]>(
        "preferences/reading-view",
        { keep: "reading-view" },
      );
      if (started !== version.current) return;
      answered.current = true;
      setView(answer.reading_view);
    }),
  );
  useEffect(() => {
    void readCached<Schemas["ReadingView"]>("reading-view").then((kept) => {
      if (kept && !answered.current) setView(kept.reading_view);
    });
    void reader.current();
  }, []);
  useServerChanges((change) => {
    if (change.kind === "refresh" || change.shared.includes("reading_view"))
      void reader.current();
  });
  const choose = useCallback(
    async (next: ReadingView) => {
      const previous = view;
      const mine = ++version.current;
      answered.current = true;
      setView(next);
      try {
        const answer = await api<Schemas["ReadingView"]>(
          "preferences/reading-view",
          { body: { reading_view: next }, keep: "reading-view" },
        );
        if (mine === version.current) setView(answer.reading_view);
      } catch {
        // Only a successful save changes the displayed choice.
        if (mine === version.current) setView(previous);
      }
    },
    [api, view],
  );
  return { view, choose };
}
