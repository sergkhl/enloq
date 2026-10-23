import { useCallback, useEffect, useRef, useState } from "react";
import type { Schemas } from "./api";
import { useConnection, useServerChanges } from "./connection";

export type Settings = Schemas["Settings"];
export type BudgetState = Schemas["BudgetState"];

export const message = (problem: unknown) =>
  problem instanceof Error ? problem.message : String(problem);

export {
  budgetAmount,
  characterCount,
  dollars,
  resetLabel,
  type BudgetFamily,
} from "../../shared/settings";

// The service-wide settings (ADR 45). Every write answers with a snapshot,
// but a late answer may predate another client's edit, so each write is
// reconciled through a fresh read, as on the web; a shared change from any
// client rereads them too.
export function useSettings() {
  const { api } = useConnection();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const generation = useRef(0);

  const load = useCallback(async () => {
    const mine = ++generation.current;
    try {
      const value = await api<Settings>("settings");
      if (mine !== generation.current) return;
      setSettings(value);
      setError("");
    } catch (problem) {
      if (mine === generation.current) setError(message(problem));
    }
  }, [api]);

  useEffect(() => {
    void load();
    return () => {
      ++generation.current;
    };
  }, [load]);
  useServerChanges((change) => {
    if (change.kind === "refresh" || change.shared.length) void load();
  });

  // Throws the service's refusal for the control to show.
  const write = useCallback(
    async (path: string, body: unknown) => {
      await api<Settings>(`settings/${path}`, { body });
      await load();
    },
    [api, load],
  );

  return { settings, error, write };
}
