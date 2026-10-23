import React, { useCallback, useEffect, useState } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { Explanation, Note, SmallButton } from "./screenUi";
import { MEMORY_BYTES, memoryBytes } from "../../shared/memory";

type PersonMemory = components["schemas"]["MemoryView"];
type AccountMemory = components["schemas"]["AccountMemoryView"];
type Memory = PersonMemory | AccountMemory;

// One note's text with its byte count: an entry holds 300 UTF-8 bytes
// (ADR 21), so letters outside English count twice.
export function NoteField({
  value,
  label,
  onChange,
}: {
  value: string;
  label: string;
  onChange: (value: string) => void;
}) {
  const used = memoryBytes(value);
  // The label and count sit above the field, where a phone's keyboard
  // leaves them in sight while typing.
  return (
    <label className="memory-field">
      <span className="memory-field-head">
        <span>{label}</span>
        <small className={used > MEMORY_BYTES ? "over" : undefined}>
          {`${used}/${MEMORY_BYTES} bytes`}
        </small>
      </span>
      <textarea
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

// What someone cares about (ADR 55): a chat's Person Memory in its
// Correspondent Profile (ADR 46 CU3), or the account's Account Memory in About
// you. The notes with their source, adding, editing, removing and clearing,
// Retry after a person's failed update, and a note while Settings has memory
// off.
export function MemoryPanel({
  account,
  peer,
}: {
  account: number;
  // Absent: the account's own Account Memory.
  peer?: string;
}) {
  const base =
    peer === undefined
      ? `accounts/${account}/memory`
      : `accounts/${account}/chats/${encodeURIComponent(peer)}/memory`;
  const [memory, setMemory] = useState<Memory | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(
    null,
  );
  const [clearing, setClearing] = useState(false);
  const load = useCallback(async () => {
    try {
      setMemory(await workspaceApi<Memory>(base));
    } catch (problem) {
      setError((problem as Error).message);
    }
  }, [base]);
  useEffect(() => void load(), [load]);
  async function act(path: string, body: object, done?: () => void) {
    if (!memory) return;
    setBusy(true);
    setError("");
    try {
      setMemory(
        await workspaceApi<Memory>(`${base}${path}`, {
          revision: memory.revision,
          ...body,
        }),
      );
      done?.();
    } catch (problem) {
      setError((problem as Error).message);
      void load();
    } finally {
      setBusy(false);
    }
  }
  if (!memory)
    return error ? <Note>{error}</Note> : <Explanation>Loading…</Explanation>;
  const fits = (text: string) =>
    text.trim().length > 0 && memoryBytes(text.trim()) <= MEMORY_BYTES;
  const person = "updating" in memory ? memory : null;
  return (
    <div className="memory-panel">
      {!memory.enabled && (
        <Note tone="warning">Memory is Off in Settings.</Note>
      )}
      {person?.error && (
        <div className="memory-note">
          <Note>
            The last memory update failed; the previous memory is kept.
          </Note>
          <SmallButton
            label="Retry"
            disabled={busy}
            onClick={() => void act("/retry", {})}
          />
        </div>
      )}
      {person?.updating && (
        <Explanation>
          Updating notes; the previous memory remains available.
        </Explanation>
      )}
      {/* Adding comes first, where a long memory can't push it under a
          phone's keyboard. */}
      <NoteField value={draft} label="Add a note" onChange={setDraft} />
      <div className="button-row">
        <SmallButton
          label="Add"
          name="Add note"
          primary
          disabled={busy || !fits(draft)}
          onClick={() =>
            void act("/entries", { text: draft.trim() }, () => setDraft(""))
          }
        />
      </div>
      {memory.entries.length === 0 && (
        <Explanation>Nothing noted yet.</Explanation>
      )}
      {memory.entries.map((entry) =>
        editing?.id === entry.id ? (
          <div key={entry.id} className="memory-entry">
            <NoteField
              value={editing.text}
              label="Edit note"
              onChange={(text) => setEditing({ id: entry.id, text })}
            />
            <div className="button-row">
              <SmallButton label="Cancel" onClick={() => setEditing(null)} />
              <SmallButton
                label="Save"
                primary
                disabled={busy || !fits(editing.text)}
                onClick={() =>
                  void act(
                    `/entries/${entry.id}`,
                    { text: editing.text.trim() },
                    () => setEditing(null),
                  )
                }
              />
            </div>
          </div>
        ) : (
          <div key={entry.id} className="memory-entry">
            <p>{entry.text}</p>
            <small>
              {entry.source === "owner"
                ? "Added or edited by you"
                : "Noted by Enloq"}
              {entry.outdated ? " · Possibly outdated" : ""}
            </small>
            <div className="button-row">
              <SmallButton
                label="Edit"
                name={`Edit: ${entry.text}`}
                disabled={busy}
                onClick={() => setEditing({ id: entry.id, text: entry.text })}
              />
              <SmallButton
                label="Remove"
                name={`Remove: ${entry.text}`}
                disabled={busy}
                onClick={() => void act(`/entries/${entry.id}/remove`, {})}
              />
            </div>
          </div>
        ),
      )}
      {memory.entries.length > 0 && !clearing && (
        <div className="button-row">
          <SmallButton
            label="Clear…"
            disabled={busy}
            onClick={() => setClearing(true)}
          />
        </div>
      )}
      {clearing && (
        <div className="memory-note">
          <p>
            {peer === undefined
              ? "Remove every note about you?"
              : "Remove every note about this person?"}
          </p>
          <div className="button-row">
            <SmallButton label="Keep" onClick={() => setClearing(false)} />
            <SmallButton
              label="Clear memory"
              primary
              disabled={busy}
              onClick={() => void act("/clear", {}, () => setClearing(false))}
            />
          </div>
        </div>
      )}
      {error && <Note>{error}</Note>}
    </div>
  );
}
