import React, { useCallback, useEffect, useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";
import { MEMORY_BYTES, memoryBytes } from "../../shared/memory";
import type { Schemas } from "./api";
import { useConnection } from "./connection";
import { Explanation, RowNote } from "./settingsUi";
import { usePalette } from "./theme";
import { Button } from "./ui";

type PersonMemory = Schemas["MemoryView"];
type AccountMemory = Schemas["AccountMemoryView"];
type Memory = PersonMemory | AccountMemory;

// One note's text with its byte count: an entry holds 300 UTF-8 bytes
// (ADR 21), so letters outside English count twice. Traits' notes share it
// (ADR 55).
export function NoteField({
  value,
  label,
  testID,
  onChange,
}: {
  value: string;
  label: string;
  testID: string;
  onChange: (value: string) => void;
}) {
  const palette = usePalette();
  const used = memoryBytes(value);
  // The label and count sit above the field, where the keyboard leaves them
  // in sight while typing.
  return (
    <View style={styles.field}>
      <View style={styles.head}>
        <Text style={[styles.count, { color: palette.muted }]}>{label}</Text>
        <Text
          style={[
            styles.count,
            { color: used > MEMORY_BYTES ? palette.error : palette.muted },
          ]}
        >
          {`${used}/${MEMORY_BYTES} bytes`}
        </Text>
      </View>
      <TextInput
        testID={testID}
        accessibilityLabel={label}
        multiline
        value={value}
        onChangeText={onChange}
        style={[
          styles.input,
          { borderColor: palette.line, color: palette.text },
        ]}
      />
    </View>
  );
}

// What someone cares about (ADR 55): a chat's Person Memory in its
// Correspondent Profile (ADR 46 CU3), or the account's Account Memory in About
// you (Q21). The notes with their source, adding, editing, removing and
// clearing, Retry after a person's failed update, and a note while Settings
// has memory off. Account Memory has no Retry or updating state.
export function MemoryPanel({
  account,
  peer,
}: {
  account: number;
  // Absent: the account's own Account Memory.
  peer?: string;
}) {
  const { api } = useConnection();
  const palette = usePalette();
  const path =
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
      setMemory(await api<Memory>(path));
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    }
  }, [api, path]);
  useEffect(() => void load(), [load]);
  async function act(suffix: string, body: object, done?: () => void) {
    if (!memory) return;
    setBusy(true);
    setError("");
    try {
      setMemory(
        await api<Memory>(`${path}${suffix}`, {
          body: { revision: memory.revision, ...body },
        }),
      );
      done?.();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
      void load();
    } finally {
      setBusy(false);
    }
  }
  if (!memory)
    return error ? (
      <RowNote tone="error">{error}</RowNote>
    ) : (
      <Explanation>Loading…</Explanation>
    );
  const fits = (text: string) =>
    text.trim().length > 0 && memoryBytes(text.trim()) <= MEMORY_BYTES;
  // Only a person's memory is updated by a model call that can fail.
  const person = "updating" in memory ? memory : null;
  return (
    <View testID="memory-panel" style={styles.panel}>
      {!memory.enabled ? (
        <RowNote tone="warning">Memory is Off in Settings.</RowNote>
      ) : null}
      {person?.error ? (
        <View style={styles.note}>
          <RowNote tone="error">
            The last memory update failed; the previous memory is kept.
          </RowNote>
          <Button
            label="Retry"
            disabled={busy}
            onPress={() => void act("/retry", {})}
          />
        </View>
      ) : null}
      {person?.updating ? (
        <Explanation>
          Updating notes; the previous memory remains available.
        </Explanation>
      ) : null}
      {/* Adding comes first, where a long memory can't push it under the
          keyboard. */}
      <NoteField
        value={draft}
        label="Add a note"
        testID="takeaway-new"
        onChange={setDraft}
      />
      <Button
        label="Add"
        accessibilityLabel="Add note"
        primary
        disabled={busy || !fits(draft)}
        onPress={() =>
          void act("/entries", { text: draft.trim() }, () => setDraft(""))
        }
      />
      {memory.entries.length === 0 ? (
        <Explanation>Nothing noted yet.</Explanation>
      ) : null}
      {memory.entries.map((entry) =>
        editing?.id === entry.id ? (
          <View
            key={entry.id}
            style={[styles.entry, { borderColor: palette.line }]}
          >
            <NoteField
              value={editing.text}
              label="Edit note"
              testID="takeaway-edit"
              onChange={(text) => setEditing({ id: entry.id, text })}
            />
            <View style={styles.buttons}>
              <Button label="Cancel" onPress={() => setEditing(null)} />
              <Button
                label="Save"
                primary
                disabled={busy || !fits(editing.text)}
                onPress={() =>
                  void act(
                    `/entries/${entry.id}`,
                    { text: editing.text.trim() },
                    () => setEditing(null),
                  )
                }
              />
            </View>
          </View>
        ) : (
          <View
            key={entry.id}
            style={[styles.entry, { borderColor: palette.line }]}
          >
            <Text style={[styles.text, { color: palette.text }]}>
              {entry.text}
            </Text>
            <Text style={[styles.small, { color: palette.muted }]}>
              {entry.source === "owner"
                ? "Added or edited by you"
                : "Noted by Enloq"}
              {entry.outdated ? " · Possibly outdated" : ""}
            </Text>
            <View style={styles.buttons}>
              <Button
                label="Edit"
                accessibilityLabel={`Edit: ${entry.text}`}
                disabled={busy}
                onPress={() => setEditing({ id: entry.id, text: entry.text })}
              />
              <Button
                label="Remove"
                accessibilityLabel={`Remove: ${entry.text}`}
                disabled={busy}
                onPress={() => void act(`/entries/${entry.id}/remove`, {})}
              />
            </View>
          </View>
        ),
      )}
      {memory.entries.length > 0 && !clearing ? (
        <Button
          label="Clear…"
          disabled={busy}
          onPress={() => setClearing(true)}
        />
      ) : null}
      {clearing ? (
        <View style={styles.note}>
          <Text style={{ color: palette.text }}>
            {peer === undefined
              ? "Remove every note about you?"
              : "Remove every note about this person?"}
          </Text>
          <View style={styles.buttons}>
            <Button label="Keep" onPress={() => setClearing(false)} />
            <Button
              label="Clear memory"
              primary
              disabled={busy}
              onPress={() => void act("/clear", {}, () => setClearing(false))}
            />
          </View>
        </View>
      ) : null}
      {error ? <RowNote tone="error">{error}</RowNote> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  panel: { gap: 10 },
  field: { gap: 2 },
  input: {
    minHeight: 64,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    padding: 10,
    fontSize: 16,
    textAlignVertical: "top",
  },
  head: { flexDirection: "row", justifyContent: "space-between", gap: 8 },
  count: { fontSize: 13 },
  entry: {
    gap: 4,
    paddingBottom: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  text: { fontSize: 16 },
  small: { fontSize: 13 },
  buttons: { flexDirection: "row", gap: 8 },
  note: { gap: 6 },
});
