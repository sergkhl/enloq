import React, { useCallback, useEffect, useState } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { NoteField } from "./MemoryPanel";
import { Explanation, Note, SmallButton } from "./screenUi";
import { MEMORY_BYTES, memoryBytes } from "../../shared/memory";
import { confidence, habitLines } from "../../shared/traits";

type Traits = components["schemas"]["TraitsView"];
type Scale = Traits["scales"][number];

const day = (seconds: number) =>
  new Date(seconds * 1000).toLocaleDateString([], {
    day: "numeric",
    month: "short",
  });

// A scale's source: the decision model's, with its confidence and date, or
// the owner's own (ADR 55 Q12 C, Q22).
function scaleNote(scale: Scale) {
  if (scale.source === "owner") return "Set by you";
  const when = scale.judged === null ? "" : day(scale.judged);
  return scale.current
    ? `${confidence(scale.probability)} · judged ${when}`
    : `${confidence(scale.probability)} · last judged ${when}`;
}

// Traits (ADR 55 Q10-Q13, Q22, Q23), only ever shown to the owner: a chat's
// person's in their profile, or the account's own in About you. Counted
// habits, the starter list's scales with their confidence, and free-form
// notes; the owner can set or clear any scale and add, edit or remove notes.
export function TraitsPanel({
  account,
  peer,
}: {
  account: number;
  // Absent: the account holder's own traits.
  peer?: string;
}) {
  const base =
    peer === undefined
      ? `accounts/${account}/traits`
      : `accounts/${account}/chats/${encodeURIComponent(peer)}/traits`;
  const [traits, setTraits] = useState<Traits | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [changing, setChanging] = useState(false);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(
    null,
  );
  const load = useCallback(async () => {
    try {
      setTraits(await workspaceApi<Traits>(base));
    } catch (problem) {
      setError((problem as Error).message);
    }
  }, [base]);
  useEffect(() => void load(), [load]);
  async function act(path: string, body: object, done?: () => void) {
    if (!traits) return;
    setBusy(true);
    setError("");
    try {
      setTraits(
        await workspaceApi<Traits>(`${base}${path}`, {
          revision: traits.revision,
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
  if (!traits)
    return error ? <Note>{error}</Note> : <Explanation>Loading…</Explanation>;
  const fits = (text: string) =>
    text.trim().length > 0 && memoryBytes(text.trim()) <= MEMORY_BYTES;
  const shown = traits.scales.filter((scale) => scale.label);
  const habits = traits.habits
    ? habitLines(traits.habits, peer === undefined ? "you" : "person")
    : [];
  return (
    <div className="memory-panel traits-panel">
      {!traits.enabled && (
        <Note tone="warning">Memory is Off in Settings.</Note>
      )}
      {traits.error && <Note>{traits.error}</Note>}
      {habits.length > 0 && (
        <ul className="trait-habits" aria-label="Habits">
          {habits.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {shown.map((scale) => (
        <div key={scale.trait} className="memory-entry">
          <p>
            {scale.title}: <strong>{scale.label}</strong>
          </p>
          <small>{scaleNote(scale)}</small>
        </div>
      ))}
      {!shown.length && !habits.length && !traits.notes.length && (
        <Explanation>Nothing noted yet.</Explanation>
      )}
      <div className="button-row">
        <SmallButton
          label={changing ? "Done" : "Change traits"}
          disabled={busy}
          onClick={() => setChanging(!changing)}
        />
      </div>
      {changing &&
        traits.scales.map((scale) => (
          <fieldset key={scale.trait} className="trait-options">
            <legend>{scale.title}</legend>
            <div className="button-row">
              {scale.options.map((option) => (
                <SmallButton
                  key={option.value}
                  label={option.label}
                  name={`${scale.title}: ${option.label}`}
                  primary={scale.value === option.value}
                  disabled={busy}
                  onClick={() =>
                    void act(`/scales/${scale.trait}`, { value: option.value })
                  }
                />
              ))}
              <SmallButton
                label="Clear"
                name={`Clear ${scale.title}`}
                disabled={busy || (scale.source === "owner" && !scale.value)}
                onClick={() =>
                  void act(`/scales/${scale.trait}`, { value: null })
                }
              />
            </div>
          </fieldset>
        ))}
      <NoteField value={draft} label="Add a trait note" onChange={setDraft} />
      <div className="button-row">
        <SmallButton
          label="Add"
          name="Add trait note"
          primary
          disabled={busy || !fits(draft)}
          onClick={() =>
            void act("/notes", { text: draft.trim() }, () => setDraft(""))
          }
        />
      </div>
      {traits.notes.map((note) =>
        editing?.id === note.id ? (
          <div key={note.id} className="memory-entry">
            <NoteField
              value={editing.text}
              label="Edit trait note"
              onChange={(text) => setEditing({ id: note.id, text })}
            />
            <div className="button-row">
              <SmallButton label="Cancel" onClick={() => setEditing(null)} />
              <SmallButton
                label="Save"
                primary
                disabled={busy || !fits(editing.text)}
                onClick={() =>
                  void act(
                    `/notes/${note.id}`,
                    { text: editing.text.trim() },
                    () => setEditing(null),
                  )
                }
              />
            </div>
          </div>
        ) : (
          <div key={note.id} className="memory-entry">
            <p>{note.text}</p>
            <small>
              {note.source === "owner"
                ? "Added or edited by you"
                : `Confidence ${confidence(note.confidence)}`}
            </small>
            <div className="button-row">
              <SmallButton
                label="Edit"
                name={`Edit: ${note.text}`}
                disabled={busy}
                onClick={() => setEditing({ id: note.id, text: note.text })}
              />
              <SmallButton
                label="Remove"
                name={`Remove: ${note.text}`}
                disabled={busy}
                onClick={() => void act(`/notes/${note.id}/remove`, {})}
              />
            </div>
          </div>
        ),
      )}
      {error && <Note>{error}</Note>}
    </div>
  );
}
