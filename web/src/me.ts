import { useEffect, useState } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";

export type Me = components["schemas"]["Me"];

// This browser's member (ADR 48): the owner sees People and the AI settings,
// a member their own budgets and Leave.
export function useMe() {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    let live = true;
    void workspaceApi<Me>("members/me")
      .then((value) => {
        if (live) setMe(value);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);
  return me;
}
