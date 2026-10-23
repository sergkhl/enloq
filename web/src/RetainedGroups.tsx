import React, { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { workspaceApi } from "./accounts";
import type { RetainedGroup } from "./conversation-api";
import { retainedLine } from "../../shared/retained";
import "./retained.css";

// Retained group work at the foot of the Groups list (ADR 34, ADR 46 CU6):
// folded, each group past the list's seven days that still holds a saved
// draft, an unresolved send or deletion, with its reason line.
export function RetainedGroups({
  account,
  version,
  onOpen,
}: {
  account: number;
  // The list's own read, so the section rereads with it.
  version: unknown;
  onOpen: (peer: string) => void;
}) {
  const [entries, setEntries] = useState<RetainedGroup[]>([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let live = true;
    void workspaceApi<RetainedGroup[]>(`accounts/${account}/retained-groups`)
      .then((value) => live && setEntries(Array.isArray(value) ? value : []))
      .catch(() => live && setEntries([]));
    return () => {
      live = false;
    };
  }, [account, version]);
  if (!entries.length) return null;
  return (
    <section className="retained-groups" aria-label="Retained group work">
      <button
        type="button"
        className="retained-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ChevronDown size={18} aria-hidden />
        ) : (
          <ChevronRight size={18} aria-hidden />
        )}
        <span>Retained group work</span>
        <small>{entries.length}</small>
      </button>
      {open &&
        entries.map((entry) => (
          <button
            key={entry.chat.key}
            type="button"
            className="list-row retained-row"
            onClick={() => onOpen(entry.chat.peer)}
          >
            <span className="row-body">
              <strong>{entry.chat.title}</strong>
              <small>{retainedLine(entry)}</small>
            </span>
          </button>
        ))}
    </section>
  );
}
