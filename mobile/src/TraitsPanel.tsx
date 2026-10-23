import React, { useCallback, useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { MEMORY_BYTES, memoryBytes } from "../../shared/memory";
import { messageDate } from "../../shared/messageTime";
import { confidence, habitLines } from "../../shared/traits";
import type { Schemas } from "./api";
import { SmallButton } from "./BudgetCard";
import { useConnection } from "./connection";
import { NoteField } from "./MemoryPanel";
import { Explanation, RowNote } from "./settingsUi";
import { usePalette } from "./theme";
import { Button } from "./ui";

type Traits = Schemas["TraitsView"];
type Scale = Traits["scales"][number];
type Note = Traits["notes"][number];

// Traits hold at most 10 notes, the model's and the owner's (ADR 55 Q12 C).
const NOTES = 10;

// A scale's source (ADR 55 Q12 C, Q22): the decision model's, with its
// confidence and when it judged, or the owner's own. A scale whose evidence
// fell short at the latest judgment keeps its last level and date.
function scaleNote(scale: Scale, timezone: string) {
  if (scale.source === "owner") return "Set by you";
  const when =
    scale.judged === null
      ? ""
      : `${scale.current ? "judged" : "last judged"} ${messageDate(scale.judged, timezone)}`;
  return [confidence(scale.probability), when].filter(Boolean).join(" · ");
}

// A note's source: the owner's own, or the model's with its confidence.
function noteSource(note: Note) {
  if (note.source === "owner") return "Added or edited by you";
  return note.confidence === null
    ? "Noted by Enloq"
    : `Confidence ${confidence(note.confidence)}`;
}

// Traits (ADR 55 Q10-Q13, Q22, Q23), shown only to the owner and read by no
// model: a chat's person's in their Correspondent Profile, or the account
// holder's own in About you. Counted habits, the starter list's scales with
// their confidence, and free-form notes. The owner sets or clears any scale,
// which the model then never changes, and adds, edits or removes notes.
export function TraitsPanel({
  account,
  peer,
  subject,
}: {
  account: number;
  // Absent: the account holder's own traits.
  peer?: string;
  subject: "person" | "you";
}) {
  const { api, connection } = useConnection();
  const palette = usePalette();
  const timezone =
    (connection.status === "paired" && connection.accounts?.timezone) || "UTC";
  const path =
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
      setTraits(await api<Traits>(path));
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    }
  }, [api, path]);
  useEffect(() => void load(), [load]);
  async function act(suffix: string, body: object, done?: () => void) {
    if (!traits) return;
    setBusy(true);
    setError("");
    try {
      setTraits(
        await api<Traits>(`${path}${suffix}`, {
          body: { revision: traits.revision, ...body },
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
  if (!traits)
    return error ? (
      <RowNote tone="error">{error}</RowNote>
    ) : (
      <Explanation>Loading…</Explanation>
    );
  const fits = (text: string) =>
    text.trim().length > 0 && memoryBytes(text.trim()) <= MEMORY_BYTES;
  // A scale shows once it has a level; one the owner cleared has none.
  const shown = traits.scales.filter((scale) => scale.label);
  const habits = traits.habits ? habitLines(traits.habits, subject) : [];
  return (
    <View testID="traits-panel" style={styles.panel}>
      {!traits.enabled ? (
        <RowNote tone="warning">Memory is Off in Settings.</RowNote>
      ) : null}
      {traits.error ? <RowNote tone="error">{traits.error}</RowNote> : null}
      {habits.length > 0 ? (
        <View style={styles.lines}>
          {habits.map((line) => (
            <Text key={line} style={[styles.small, { color: palette.text }]}>
              {line}
            </Text>
          ))}
        </View>
      ) : null}
      {shown.map((scale) => (
        <View key={scale.trait} style={styles.lines}>
          <Text style={[styles.text, { color: palette.text }]}>
            {`${scale.title}: `}
            <Text style={styles.level}>{scale.label}</Text>
          </Text>
          <Text style={[styles.small, { color: palette.muted }]}>
            {scaleNote(scale, timezone)}
          </Text>
        </View>
      ))}
      {habits.length === 0 &&
      shown.length === 0 &&
      traits.notes.length === 0 ? (
        <Explanation>Nothing noted yet.</Explanation>
      ) : null}
      <Button
        label={changing ? "Done" : "Change traits"}
        onPress={() => setChanging(!changing)}
      />
      {changing
        ? traits.scales.map((scale) => (
            <View key={scale.trait} style={styles.scale}>
              <Text style={[styles.title, { color: palette.text }]}>
                {scale.title}
              </Text>
              <View style={styles.choices}>
                {scale.options.map((option) => (
                  <SmallButton
                    key={option.value}
                    label={option.label}
                    accessibilityLabel={`${scale.title}: ${option.label}`}
                    primary={scale.value === option.value}
                    selected={scale.value === option.value}
                    disabled={busy}
                    palette={palette}
                    onPress={() =>
                      void act(`/scales/${scale.trait}`, {
                        value: option.value,
                      })
                    }
                  />
                ))}
                <SmallButton
                  label="Clear"
                  accessibilityLabel={`Clear ${scale.title}`}
                  disabled={
                    busy || (scale.source === "owner" && scale.value === null)
                  }
                  palette={palette}
                  onPress={() =>
                    void act(`/scales/${scale.trait}`, { value: null })
                  }
                />
              </View>
            </View>
          ))
        : null}
      {/* Adding comes first, as in Cares about. */}
      {traits.notes.length < NOTES ? (
        <>
          <NoteField
            value={draft}
            label="Add a trait note"
            testID="trait-note-new"
            onChange={setDraft}
          />
          <Button
            label="Add"
            accessibilityLabel="Add trait note"
            primary
            disabled={busy || !fits(draft)}
            onPress={() =>
              void act("/notes", { text: draft.trim() }, () => setDraft(""))
            }
          />
        </>
      ) : (
        <Explanation>
          {`Traits hold at most ${NOTES} notes; remove one to add another.`}
        </Explanation>
      )}
      {traits.notes.map((note) =>
        editing?.id === note.id ? (
          <View
            key={note.id}
            style={[styles.entry, { borderColor: palette.line }]}
          >
            <NoteField
              value={editing.text}
              label="Edit trait note"
              testID="trait-note-edit"
              onChange={(text) => setEditing({ id: note.id, text })}
            />
            <View style={styles.buttons}>
              <Button label="Cancel" onPress={() => setEditing(null)} />
              <Button
                label="Save"
                primary
                disabled={busy || !fits(editing.text)}
                onPress={() =>
                  void act(
                    `/notes/${note.id}`,
                    { text: editing.text.trim() },
                    () => setEditing(null),
                  )
                }
              />
            </View>
          </View>
        ) : (
          <View
            key={note.id}
            style={[styles.entry, { borderColor: palette.line }]}
          >
            <Text style={[styles.text, { color: palette.text }]}>
              {note.text}
            </Text>
            <Text style={[styles.small, { color: palette.muted }]}>
              {noteSource(note)}
            </Text>
            <View style={styles.buttons}>
              <Button
                label="Edit"
                accessibilityLabel={`Edit: ${note.text}`}
                disabled={busy}
                onPress={() => setEditing({ id: note.id, text: note.text })}
              />
              <Button
                label="Remove"
                accessibilityLabel={`Remove: ${note.text}`}
                disabled={busy}
                onPress={() => void act(`/notes/${note.id}/remove`, {})}
              />
            </View>
          </View>
        ),
      )}
      {error ? <RowNote tone="error">{error}</RowNote> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  panel: { gap: 10 },
  lines: { gap: 2 },
  text: { fontSize: 16 },
  level: { fontWeight: "600" },
  small: { fontSize: 13 },
  scale: { gap: 6 },
  title: { fontSize: 15, fontWeight: "600" },
  choices: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  entry: {
    gap: 4,
    paddingBottom: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  buttons: { flexDirection: "row", gap: 8 },
});
