import React, { useEffect, useRef, useState } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { useChanges } from "./events";

type Processing = components["schemas"]["AccountProcessing"];

// The current account's AI processing (ADR 14). The account picker switches
// it; the conversation's AI off tag and processing actions follow it (ADR 28).
export function useAccountProcessing(account: number) {
  const [value, setValue] = useState<{
    account: number;
    enabled: boolean;
  } | null>(null);
  // The account whose change is being saved; a save keeps its own account.
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");
  const version = useRef(0);
  const alive = useRef(true);
  const current = useRef(account);
  current.current = account;
  const changing = useRef<number | null>(null);
  const refreshRef = useRef<() => void>(() => {});
  useChanges((change) => {
    if (change.all || change.shared.includes("accounts")) refreshRef.current();
  }, 30000);
  useEffect(() => {
    alive.current = true;
    setError("");
    if (!account) return;
    const refresh = async () => {
      if (changing.current === account) return;
      const read = ++version.current;
      try {
        const next = await workspaceApi<Processing>(
          `accounts/${account}/processing`,
        );
        if (alive.current && read === version.current)
          setValue({ account, enabled: next.ai_enabled });
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
  }, [account]);
  const enabled = value?.account === account ? value.enabled : null;

  async function toggle() {
    if (enabled === null || changing.current !== null) return;
    const target = account;
    changing.current = target;
    setBusy(target);
    setError("");
    ++version.current;
    // A result or refusal applies only while its account is still current.
    try {
      const next = await workspaceApi<Processing>(
        `accounts/${target}/processing`,
        { ai_enabled: !enabled },
      );
      if (alive.current && current.current === target)
        setValue({ account: target, enabled: next.ai_enabled });
    } catch (e) {
      if (alive.current && current.current === target)
        setError((e as Error).message);
    } finally {
      changing.current = null;
      setBusy(null);
    }
  }
  return { enabled, busy: busy === account, error, toggle };
}

// The picker's switch for the current account; it flips in place.
