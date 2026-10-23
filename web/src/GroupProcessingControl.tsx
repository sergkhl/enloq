import React, { useEffect, useRef, useState } from "react";
import { chatApi, type GroupProcessing } from "./conversation-api";
import { touchesChat, useChanges } from "./events";

// Group opt-in is separate from the account master and global Reading View.
// Each response keeps its exact account/chat, and a successful Save reads the
// latest shared state before presenting it, including edits by another browser.
export function useGroupProcessing(
  account: number,
  peer: string,
  group: boolean,
) {
  const [value, setValue] = useState<GroupProcessing | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const version = useRef(0);
  const alive = useRef(false);
  const changing = useRef(false);
  const refreshRef = useRef<() => void>(() => {});
  useEffect(() => {
    alive.current = true;
    setValue(null);
    setError("");
    if (!group) return;
    const refresh = async () => {
      if (changing.current) return;
      const read = ++version.current;
      try {
        const next = await chatApi<GroupProcessing>(
          account,
          peer,
          "/processing",
        );
        if (alive.current && read === version.current) {
          setValue(next);
          setError("");
        }
      } catch (e) {
        if (alive.current && read === version.current)
          setError((e as Error).message);
      }
    };
    void refresh();
    refreshRef.current = () => void refresh();
    return () => {
      alive.current = false;
      ++version.current;
      refreshRef.current = () => {};
    };
  }, [account, peer, group]);
  useChanges((change) => {
    if (
      touchesChat(change, account, `${account}/${peer}`) ||
      change.shared.includes("accounts")
    )
      refreshRef.current();
  }, 15000);

  async function toggle() {
    if (!group || value === null || changing.current) return;
    changing.current = true;
    setBusy(true);
    setError("");
    const change = ++version.current;
    try {
      await chatApi<GroupProcessing>(account, peer, "/processing", {
        enabled: !value.processing_enabled,
      });
      const next = await chatApi<GroupProcessing>(account, peer, "/processing");
      if (alive.current && change === version.current) setValue(next);
    } catch (e) {
      if (alive.current && change === version.current)
        setError((e as Error).message);
    } finally {
      changing.current = false;
      if (alive.current) setBusy(false);
    }
  }
  return { value, busy, error, toggle, refresh: () => refreshRef.current() };
}
