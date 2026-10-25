import { getCalendars, getLocales } from "expo-localization";
import { router } from "expo-router";
import React, { useEffect, useRef, useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import {
  reminderDefaults,
  reminderPath,
  reminderTime,
  WEEKDAYS,
} from "../../shared/reminders";
import type { Schemas } from "./api";
import { readCached } from "./cache";
import { useConnection, useServerChanges } from "./connection";
import { ChoiceRow, RowNote } from "./settingsUi";
import { usePalette } from "./theme";
import { Body, Button } from "./ui";

type Rule = Schemas["ReminderRule"];
type Reminder = Schemas["ReminderView"];
type Digest = Schemas["DigestSettings"];
type Item = Schemas["ReminderItem"];
const zone = () => getCalendars()[0]?.timeZone || "UTC";

function Field({
  label,
  value,
  onChange,
  disabled,
  numeric = false,
}: {
  label: string;
  value: string;
  onChange: (text: string) => void;
  disabled: boolean;
  numeric?: boolean;
}) {
  const palette = usePalette();
  return (
    <View style={styles.field}>
      <Body>{label}</Body>
      <TextInput
        accessibilityLabel={label}
        value={value}
        onChangeText={onChange}
        editable={!disabled}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType={numeric ? "number-pad" : "default"}
        style={[
          styles.input,
          {
            color: palette.text,
            borderColor: palette.line,
            backgroundColor: palette.panel,
          },
        ]}
      />
    </View>
  );
}

export function ReminderEditor({
  account,
  peer,
}: {
  account: number;
  peer: string;
}) {
  const { api, connection } = useConnection();
  const palette = usePalette();
  const path = reminderPath(account, peer);
  const [saved, setSaved] = useState<Reminder | null>(null);
  const [rule, setRule] = useState<Rule>(() =>
    reminderDefaults(zone(), getLocales()[0]?.languageTag || "en"),
  );
  const [preview, setPreview] = useState<{
    signature: string;
    next: number;
  } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dirty = useRef(false);
  const signature = JSON.stringify(rule);
  const disabled =
    busy || !saved || connection.status !== "paired" || connection.unreachable;
  async function read() {
    try {
      const value = await api<Reminder>(path);
      setSaved(value);
      if (!dirty.current && value.schedule) setRule(value.schedule);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    void read();
  }, [api, path]);
  useServerChanges(() => {
    if (!dirty.current) void read();
  });
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      void api<{ next_at: number }>(`${path}/preview`, { body: rule }).then(
        (value) => {
          if (alive) {
            setPreview({ signature, next: value.next_at });
            setError("");
          }
        },
        (e) => {
          if (alive) {
            setPreview(null);
            setError(e.message);
          }
        },
      );
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [api, path, signature]);
  function change(values: Partial<Rule>) {
    dirty.current = true;
    setRule((old) => ({ ...old, ...values }));
  }
  async function save(remove = false) {
    if (!saved || disabled) return;
    setBusy(true);
    setError("");
    try {
      const value = await api<Reminder>(path, {
        body: remove
          ? { revision: saved.revision }
          : { ...rule, revision: saved.revision },
        ...(remove ? { method: "DELETE" as const } : {}),
      });
      dirty.current = false;
      setSaved(value);
      if (value.schedule) setRule(value.schedule);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <View style={styles.form}>
      {saved?.status === "paused" && (
        <RowNote>Paused: Automatic suggestions is Off.</RowNote>
      )}
      <ChoiceRow
        label="Reminder frequency"
        options={[
          { value: "weekly", label: "Weekly" },
          { value: "monthly", label: "Monthly" },
        ]}
        value={rule.frequency}
        disabled={disabled}
        onChange={(frequency) => change({ frequency })}
      />
      {rule.frequency === "weekly" ? (
        <View
          style={styles.days}
          accessibilityRole="radiogroup"
          accessibilityLabel="Reminder weekday"
        >
          {WEEKDAYS.map((day, index) => (
            <Pressable
              key={day}
              accessibilityRole="radio"
              accessibilityLabel={day}
              accessibilityState={{ checked: rule.weekday === index }}
              disabled={disabled}
              onPress={() => change({ weekday: index })}
              style={[
                styles.day,
                {
                  borderColor: palette.line,
                  backgroundColor:
                    rule.weekday === index ? palette.accent : palette.panel,
                },
              ]}
            >
              <Text
                style={{
                  color:
                    rule.weekday === index ? palette.onAccent : palette.text,
                }}
              >
                {day.slice(0, 3)}
              </Text>
            </Pressable>
          ))}
        </View>
      ) : (
        <Field
          label="Reminder calendar date"
          value={String(rule.month_day)}
          numeric
          disabled={disabled}
          onChange={(text) => change({ month_day: Number(text) })}
        />
      )}
      <Field
        label="Schedule timezone"
        value={rule.timezone}
        disabled={disabled}
        onChange={(timezone) => change({ timezone })}
      />
      <ChoiceRow
        label="Reminder alert"
        options={[
          { value: "digest", label: "Daily digest" },
          { value: "exact", label: "Exact time" },
        ]}
        value={rule.delivery}
        disabled={disabled}
        onChange={(delivery) => change({ delivery })}
      />
      {rule.delivery === "exact" && (
        <Field
          label="Reminder time"
          value={rule.time}
          disabled={disabled}
          onChange={(time) => change({ time })}
        />
      )}
      <Field
        label="Fallback language"
        value={rule.language}
        disabled={disabled}
        onChange={(language) => change({ language })}
      />
      {preview?.signature === signature && (
        <Body>
          Next: {reminderTime(preview.next, rule.timezone)} · {rule.timezone}
        </Body>
      )}
      {error ? <RowNote>{error}</RowNote> : null}
      <Button
        label="Save reminder"
        primary
        disabled={!saved || disabled || preview?.signature !== signature}
        onPress={() => void save()}
      />
      {saved?.schedule && (
        <Button
          label="Remove reminder"
          disabled={disabled}
          onPress={() => void save(true)}
        />
      )}
    </View>
  );
}

function DigestSettings() {
  const { api, connection } = useConnection();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<Digest | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void api<Digest>("reminders/settings").then(
      (answer) =>
        setValue(answer.revision ? answer : { ...answer, timezone: zone() }),
      (e) => setError(e.message),
    );
  }, [api]);
  async function save() {
    if (!value || busy) return;
    setBusy(true);
    setError("");
    try {
      setValue(await api<Digest>("reminders/settings", { body: value }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const disabled =
    busy || !value || connection.status !== "paired" || connection.unreachable;
  return (
    <View style={styles.form}>
      <Button label="Daily digest settings" onPress={() => setOpen(!open)} />
      {open && (
        <>
          <Field
            label="Digest time"
            value={value?.time || "09:00"}
            disabled={disabled}
            onChange={(time) => value && setValue({ ...value, time })}
          />
          <Field
            label="Digest timezone"
            value={value?.timezone || ""}
            disabled={disabled}
            onChange={(timezone) => value && setValue({ ...value, timezone })}
          />
          <Button
            label="Save digest settings"
            disabled={disabled}
            onPress={() => void save()}
          />
          {error ? <RowNote>{error}</RowNote> : null}
        </>
      )}
    </View>
  );
}

export function RemindersList() {
  const { api, connection, selectAccount } = useConnection();
  const palette = usePalette();
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  async function reload() {
    try {
      const value = await api<Schemas["ReminderList"]>("reminders", {
        keep: "reminders",
      });
      if (alive.current) {
        setItems(value.items);
        setError("");
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    }
  }
  useEffect(() => {
    alive.current = true;
    void readCached<Schemas["ReminderList"]>("reminders").then((value) => {
      if (alive.current && value) setItems((old) => old || value.items);
    });
    void reload();
    const timer = setInterval(() => void reload(), 60000);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, [api]);
  useServerChanges(() => void reload());
  const offline = connection.status !== "paired" || connection.unreachable;
  async function dismiss(item: Item) {
    if (!item.occurrence || busy) return;
    setBusy(true);
    try {
      await api(`${reminderPath(item.account_id, item.peer)}/dismiss`, {
        body: { occurrence: item.occurrence.id },
      });
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function review(item: Item) {
    if (!item.occurrence) return;
    selectAccount(item.account_id);
    router.push({
      pathname: "/chat/[account]/[peer]",
      params: {
        account: String(item.account_id),
        peer: item.peer,
        reminder: item.occurrence.id,
      },
    });
  }
  return (
    <ScrollView
      contentContainerStyle={styles.list}
      keyboardShouldPersistTaps="handled"
    >
      <Text
        accessibilityRole="header"
        style={[styles.title, { color: palette.text }]}
      >
        Reminders
      </Text>
      <Body muted>All accounts</Body>
      <DigestSettings />
      {error ? <RowNote>{error}</RowNote> : null}
      {items === null ? (
        <Body muted>
          {offline
            ? "Reminders show once the server answers."
            : "Loading reminders…"}
        </Body>
      ) : items.length === 0 ? (
        <Body muted>No reminders.</Body>
      ) : null}
      {[true, false].map((pending) => {
        const rows =
          items?.filter((item) => !!item.occurrence === pending) || [];
        return rows.length ? (
          <View key={String(pending)} style={styles.form}>
            <Body>{pending ? "Pending" : "Upcoming"}</Body>
            {rows.map((item, index) => (
              <View
                key={`${item.account_id}/${item.peer}/${index}`}
                style={[styles.row, { borderColor: palette.line }]}
              >
                <Body>{item.title}</Body>
                <Body muted>{item.account}</Body>
                {item.status !== "active" ? (
                  <Body muted>
                    {item.status === "paused"
                      ? "Paused: Automatic suggestions is Off."
                      : "Unavailable: sign in to this account."}
                  </Body>
                ) : (
                  <>
                    {item.occurrence && (
                      <Body>
                        Due:{" "}
                        {reminderTime(
                          item.occurrence.due_at,
                          item.schedule?.timezone || "UTC",
                        )}
                      </Body>
                    )}
                    <Body muted>
                      Next:{" "}
                      {reminderTime(
                        item.next_at,
                        item.schedule?.timezone || "UTC",
                      )}{" "}
                      · {item.schedule?.timezone}
                    </Body>
                    {!item.ai_enabled && (
                      <Body muted>AI Off: generation is paused.</Body>
                    )}
                  </>
                )}
                {item.occurrence && (
                  <>
                    <Button
                      label="Review reminder"
                      disabled={
                        busy || offline || !!error || item.status !== "active"
                      }
                      onPress={() => review(item)}
                    />
                    <Button
                      label="Dismiss"
                      disabled={
                        busy || offline || !!error || item.status !== "active"
                      }
                      onPress={() => void dismiss(item)}
                    />
                  </>
                )}
                <Button
                  label="Edit schedule"
                  disabled={offline || item.status === "unavailable"}
                  onPress={() =>
                    router.push({
                      pathname: "/profile/[account]/[peer]",
                      params: {
                        account: String(item.account_id),
                        peer: item.peer,
                      },
                    })
                  }
                />
              </View>
            ))}
          </View>
        ) : null;
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  list: { padding: 16, gap: 12 },
  title: { fontSize: 22, fontWeight: "700" },
  form: { gap: 12 },
  field: { gap: 5 },
  input: { borderWidth: 1, borderRadius: 8, padding: 10, fontSize: 16 },
  row: { gap: 8, borderBottomWidth: 1, paddingVertical: 14 },
  days: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  day: { padding: 10, borderWidth: 1, borderRadius: 8 },
});
