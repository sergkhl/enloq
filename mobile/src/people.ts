import { useCallback, useEffect, useRef, useState } from "react";
import type { Schemas } from "./api";
import { useConnection, useServerChanges } from "./connection";

export type People = Schemas["People"];

// The owner's People (ADR 48 Q19), as the web's usePeople: a change to
// members, their spending or accounts rereads it.
export function usePeople() {
  const { api } = useConnection();
  const [people, setPeople] = useState<People | null>(null);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const load = useCallback(async () => {
    const mine = ++generation.current;
    try {
      const value = await api<People>("people");
      if (mine === generation.current) {
        setPeople(value);
        setError("");
      }
    } catch (problem) {
      if (mine === generation.current)
        setError(problem instanceof Error ? problem.message : String(problem));
    }
  }, [api]);
  useEffect(() => {
    void load();
    return () => {
      ++generation.current;
    };
  }, [load]);
  useServerChanges((change) => {
    if (
      change.kind === "refresh" ||
      ["people", "spending", "accounts"].some((scope) =>
        change.shared.includes(scope),
      )
    )
      void load();
  });
  return { people, error, setPeople, load };
}
