import Ionicons from "@expo/vector-icons/Ionicons";
import DateTimePicker, {
  type DateTimePickerEvent,
} from "@react-native-community/datetimepicker";
import { getCalendars, getLocales } from "expo-localization";
import { router } from "expo-router";
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  FlatList,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  barLine,
  dateLabel,
  digestLine,
  ideaLine,
  isoDay,
  keptLine,
  listedLanguage,
  MONTHS,
  REMOVE_REMINDER,
  REPEATS,
  reminderDefaults,
  reminderPath,
  remindersPath,
  repeatLine,
  rowDate,
  summaryLine,
  timeLabel,
  WEEKDAYS,
  type Repeat,
} from "../../shared/reminders";
import type { Schemas } from "./api";
import { Avatar } from "./Avatar";
import { readCached } from "./cache";
import { useConnection, useServerChanges } from "./connection";
import { ChoiceRow, RowNote, Section } from "./settingsUi";
import { usePalette, type Palette } from "./theme";
import { BackHeader, Body, Button } from "./ui";

type Rule = Schemas["ReminderRule"];
type Reminder = Schemas["ReminderView"];
type ChatReminders = Schemas["ChatReminders"];
type Item = Schemas["ReminderItem"];
type Pending = Schemas["PendingReminder"];
type Digest = Schemas["DigestSettings"];
type Language = Schemas["Language"];
type Preview = Schemas["ReminderPreview"];
type Kept = Schemas["KeptStatus"];
const zone = () => getCalendars()[0]?.timeZone || "UTC";

// The member's digest settings, read once per screen.
function useDigest() {
  const { api } = useConnection();
  const [digest, setDigest] = useState<Digest | null>(null);
  useEffect(() => {
    let live = true;
    api<Digest>("reminders/settings").then(
      (answer) =>
        live &&
        setDigest(answer.revision ? answer : { ...answer, timezone: zone() }),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api]);
  return { digest, setDigest };
}

// The searchable timezone list and the language list (Q12).
function useLists() {
  const { api } = useConnection();
  const [zones, setZones] = useState<string[]>([]);
  const [languages, setLanguages] = useState<Language[]>([]);
  useEffect(() => {
    let live = true;
    api<{ timezones: string[] }>("reminders/timezones").then(
      (answer) => live && setZones(answer.timezones),
      () => undefined,
    );
    api<{ languages: Language[] }>("reminders/languages").then(
      (answer) => live && setLanguages(answer.languages),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api]);
  return { zones, languages };
}

// A bottom sheet over a dimmed backdrop that closes it.
function Sheet({
  visible,
  title,
  onClose,
  children,
}: {
  visible: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const palette = usePalette();
  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={styles.backdrop}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close"
          style={StyleSheet.absoluteFill}
          onPress={onClose}
        />
        <SafeAreaView
          edges={["bottom"]}
          style={[styles.sheet, { backgroundColor: palette.background }]}
        >
          <Text
            accessibilityRole="header"
            style={[styles.sheetTitle, { color: palette.text }]}
          >
            {title}
          </Text>
          {children}
        </SafeAreaView>
      </View>
    </Modal>
  );
}

// A searchable list of choices, the chosen one ticked.
function SearchList({
  label,
  options,
  value,
  onChoose,
}: {
  label: string;
  options: { value: string; label: string }[];
  value: string;
  onChoose: (value: string) => void;
}) {
  const palette = usePalette();
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const wanted = query.trim().toLowerCase().replace(/\s+/g, "_");
    return options.filter(
      (option) =>
        !wanted ||
        option.value.toLowerCase().includes(wanted) ||
        option.label.toLowerCase().replace(/\s+/g, "_").includes(wanted),
    );
  }, [options, query]);
  return (
    <View style={styles.search}>
      <TextInput
        accessibilityLabel={`Search ${label.toLowerCase()}`}
        placeholder="Search"
        placeholderTextColor={palette.muted}
        autoCorrect={false}
        autoCapitalize="none"
        value={query}
        onChangeText={setQuery}
        style={[
          styles.input,
          {
            color: palette.text,
            borderColor: palette.line,
            backgroundColor: palette.panel,
          },
        ]}
      />
      <FlatList
        data={shown}
        keyExtractor={(option) => option.value}
        keyboardShouldPersistTaps="handled"
        style={styles.choices}
        initialNumToRender={20}
        renderItem={({ item }) => (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={item.label}
            accessibilityState={{ selected: item.value === value }}
            onPress={() => onChoose(item.value)}
            style={styles.choice}
          >
            <Text
              style={[
                styles.fill,
                {
                  color: item.value === value ? palette.accent : palette.text,
                },
              ]}
            >
              {item.label}
            </Text>
            {item.value === value ? (
              <Ionicons name="checkmark" size={20} color={palette.accent} />
            ) : null}
          </Pressable>
        )}
      />
    </View>
  );
}

// A date or a time chosen with the platform's own picker (Q12): Android's
// dialog, or iOS's wheels in a sheet with Done.
function PickerField({
  label,
  mode,
  value,
  text,
  disabled,
  onChange,
}: {
  label: string;
  mode: "date" | "time";
  value: Date;
  text: string;
  disabled: boolean;
  onChange: (value: Date) => void;
}) {
  const palette = usePalette();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  function changed(event: DateTimePickerEvent, chosen?: Date) {
    if (Platform.OS !== "ios") {
      setOpen(false);
      if (event.type === "set" && chosen) onChange(chosen);
    } else if (chosen) setDraft(chosen);
  }
  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${text}`}
        accessibilityState={{ disabled }}
        disabled={disabled}
        onPress={() => {
          setDraft(value);
          setOpen(true);
        }}
        style={[styles.field, { borderColor: palette.line }]}
      >
        <Text style={[styles.fieldLabel, { color: palette.muted }]}>
          {label}
        </Text>
        <Text style={[styles.fieldValue, { color: palette.accent }]}>
          {text}
        </Text>
      </Pressable>
      {open && Platform.OS !== "ios" ? (
        <DateTimePicker value={value} mode={mode} is24Hour onChange={changed} />
      ) : null}
      {Platform.OS === "ios" ? (
        <Sheet visible={open} title={label} onClose={() => setOpen(false)}>
          <DateTimePicker
            value={draft}
            mode={mode}
            is24Hour
            display="spinner"
            onChange={changed}
          />
          <Button
            label="Done"
            primary
            onPress={() => {
              setOpen(false);
              onChange(draft);
            }}
          />
        </Sheet>
      ) : null}
    </>
  );
}

function dayDate(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year!, month! - 1, date!);
}

function clockDate(time: string) {
  const [hours, minutes] = time.split(":").map(Number);
  const value = new Date();
  value.setHours(hours!, minutes!, 0, 0);
  return value;
}

function clockText(value: Date) {
  return `${String(value.getHours()).padStart(2, "0")}:${String(
    value.getMinutes(),
  ).padStart(2, "0")}`;
}

function dayText(day: string) {
  const value = dayDate(day);
  return `${WEEKDAYS[(value.getDay() + 6) % 7]!.slice(0, 3)} ${value.getDate()} ${MONTHS[value.getMonth()]!.slice(0, 3)} ${value.getFullYear()}`;
}

// Choices of a few side by side, segmented; screen readers hear radios.
function Segments<T extends string | number>({
  label,
  options,
  value,
  disabled,
  onChange,
}: {
  label: string;
  options: { value: T; label: string; name: string }[];
  value: T;
  disabled: boolean;
  onChange: (value: T) => void;
}) {
  const palette = usePalette();
  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel={label}
      style={[
        styles.segments,
        { borderColor: palette.line, opacity: disabled ? 0.5 : 1 },
      ]}
    >
      {options.map((option, index) => {
        const chosen = option.value === value;
        return (
          <Pressable
            key={String(option.value)}
            accessibilityRole="radio"
            accessibilityLabel={option.name}
            accessibilityState={{ checked: chosen, disabled }}
            disabled={disabled}
            onPress={() => !chosen && onChange(option.value)}
            style={[
              styles.segment,
              index
                ? { borderLeftWidth: 1, borderLeftColor: palette.line }
                : null,
              chosen ? { backgroundColor: palette.accent } : null,
            ]}
          >
            <Text
              numberOfLines={1}
              style={[
                styles.segmentLabel,
                { color: chosen ? palette.onAccent : palette.text },
              ]}
            >
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

// The 1–31 grid for Monthly and Yearly (Q12).
function DayGrid({
  value,
  limit,
  disabled,
  onChange,
}: {
  value: number;
  limit: number;
  disabled: boolean;
  onChange: (day: number) => void;
}) {
  const palette = usePalette();
  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel="Day"
      style={styles.days}
    >
      {Array.from({ length: limit }, (_, index) => index + 1).map((day) => {
        const chosen = day === value;
        return (
          <Pressable
            key={day}
            accessibilityRole="radio"
            accessibilityLabel={`Day ${day}`}
            accessibilityState={{ checked: chosen, disabled }}
            disabled={disabled}
            onPress={() => onChange(day)}
            style={[
              styles.day,
              {
                borderColor: chosen ? palette.accent : palette.line,
                backgroundColor: chosen ? palette.accent : palette.panel,
              },
            ]}
          >
            <Text style={{ color: chosen ? palette.onAccent : palette.text }}>
              {day}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

// The reminder editor (Q12), its own screen, opened from the profile, the
// Reminders tab and the Reminder Bar. A new schedule has no `schedule`.
export function ReminderEditor({
  account,
  peer,
  title,
  schedule,
}: {
  account: number;
  peer: string;
  title: string;
  schedule?: string;
}) {
  const { api, connection } = useConnection();
  const palette = usePalette();
  const path = remindersPath(account, peer);
  const { digest } = useDigest();
  const { languages } = useLists();
  const [saved, setSaved] = useState<Reminder | null>(null);
  const [rule, setRule] = useState<Rule | null>(null);
  const [preview, setPreview] = useState<{
    signature: string;
    next: number | null;
    kept: Kept | null;
  } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sheet, setSheet] = useState<"timezone" | "language" | null>(null);
  const dirty = useRef(false);
  const signature = JSON.stringify(rule);
  useEffect(() => {
    let live = true;
    (schedule
      ? api<Reminder>(reminderPath(account, peer, schedule)).then((value) => {
          if (!live) return;
          setSaved(value);
          if (!dirty.current) setRule(value.schedule);
        })
      : api<ChatReminders>(path).then((value) => {
          if (!live || dirty.current) return;
          // The chat's recent language, else the phone's (Q1).
          setRule(
            reminderDefaults(
              zone(),
              value.language ||
                listedLanguage(getLocales()[0]?.languageTag || "en", languages),
            ),
          );
        })
    ).catch((problem) => live && setError((problem as Error).message));
    return () => {
      live = false;
    };
  }, [api, account, peer, schedule, languages.length]);
  useEffect(() => {
    if (!rule) return;
    let alive = true;
    const timer = setTimeout(() => {
      api<Preview>(`${path}/preview`, { body: rule }).then(
        (value) => {
          if (!alive) return;
          setPreview({
            signature,
            next: value.next_at,
            kept: value.kept ?? null,
          });
          setError("");
        },
        (problem) => {
          if (!alive) return;
          setPreview(null);
          setError((problem as Error).message);
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
    setRule((old) => (old ? { ...old, ...values } : old));
  }
  async function save() {
    if (!rule || busy) return;
    setBusy(true);
    setError("");
    try {
      await api<Reminder>(
        schedule ? reminderPath(account, peer, schedule) : path,
        {
          body: schedule ? { ...rule, revision: saved?.revision ?? 0 } : rule,
        },
      );
      dirty.current = false;
      router.canGoBack() ? router.back() : router.replace("/chats");
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function remove() {
    if (!schedule || !saved || busy) return;
    Alert.alert(REMOVE_REMINDER.title, REMOVE_REMINDER.message, [
      { text: REMOVE_REMINDER.cancel, style: "cancel" },
      {
        text: REMOVE_REMINDER.confirm,
        style: "destructive",
        onPress: () => {
          setBusy(true);
          setError("");
          api(reminderPath(account, peer, schedule), {
            method: "DELETE",
            body: { revision: saved.revision },
          }).then(
            () =>
              router.canGoBack() ? router.back() : router.replace("/chats"),
            (problem) => {
              setError((problem as Error).message);
              setBusy(false);
            },
          );
        },
      },
    ]);
  }
  const offline = connection.status !== "paired" || connection.unreachable;
  const current = preview?.signature === signature;
  const disabled = busy || !rule || offline;
  const kept =
    saved && !dirty.current
      ? keptLine(saved.kept)
      : current
        ? keptLine(preview?.kept)
        : "";
  return (
    <SafeAreaView
      style={[styles.fill, { backgroundColor: palette.background }]}
    >
      <View style={styles.pageHeader}>
        <BackHeader title={schedule ? "Edit reminder" : "New reminder"} />
      </View>
      <ScrollView
        contentContainerStyle={styles.page}
        keyboardShouldPersistTaps="handled"
      >
        <Body>{title}</Body>
        {saved?.status === "paused" ? (
          <RowNote tone="warning">
            Paused: Automatic suggestions is Off.
          </RowNote>
        ) : null}
        {rule ? (
          <>
            <Section title="Repeat">
              <View style={styles.block}>
                <Segments<Repeat>
                  label="Repeat"
                  options={REPEATS.map((item) => ({
                    value: item.value,
                    label: item.short,
                    name: item.label,
                  }))}
                  value={rule.repeat}
                  disabled={disabled}
                  onChange={(repeat) => change({ repeat, interval: 1 })}
                />
                {rule.repeat === "days" || rule.repeat === "weeks" ? (
                  <View style={styles.row}>
                    <Text style={{ color: palette.text }}>Every</Text>
                    <TextInput
                      accessibilityLabel={
                        rule.repeat === "days"
                          ? "Days between reminders"
                          : "Weeks between reminders"
                      }
                      keyboardType="number-pad"
                      value={String(rule.interval)}
                      editable={!disabled}
                      onChangeText={(text) =>
                        change({
                          interval: Math.min(
                            rule.repeat === "days" ? 365 : 52,
                            Math.max(1, Number(text.replace(/\D/g, "")) || 1),
                          ),
                        })
                      }
                      style={[
                        styles.input,
                        styles.number,
                        {
                          color: palette.text,
                          borderColor: palette.line,
                          backgroundColor: palette.panel,
                        },
                      ]}
                    />
                    <Text style={{ color: palette.text }}>
                      {rule.repeat === "days" ? "days" : "weeks"}
                    </Text>
                  </View>
                ) : null}
                {rule.repeat === "weeks" ? (
                  <Segments<number>
                    label="Weekday"
                    options={WEEKDAYS.map((day, index) => ({
                      value: index,
                      label: day.slice(0, 2),
                      name: day,
                    }))}
                    value={rule.weekday}
                    disabled={disabled}
                    onChange={(weekday) => change({ weekday })}
                  />
                ) : null}
                {rule.repeat === "once" ||
                rule.repeat === "days" ||
                rule.repeat === "weeks" ? (
                  <PickerField
                    label={
                      rule.repeat === "once"
                        ? "Date"
                        : rule.repeat === "days"
                          ? "First date"
                          : "Start week"
                    }
                    mode="date"
                    value={dayDate(rule.start)}
                    text={dayText(rule.start)}
                    disabled={disabled}
                    onChange={(value) => change({ start: isoDay(value) })}
                  />
                ) : null}
                {rule.repeat === "yearly" ? (
                  <Segments<number>
                    label="Month"
                    options={MONTHS.map((name, index) => ({
                      value: index + 1,
                      label: name.slice(0, 1),
                      name,
                    }))}
                    value={rule.month}
                    disabled={disabled}
                    onChange={(month) =>
                      change({
                        month,
                        month_day: Math.min(
                          rule.month_day,
                          new Date(2024, month, 0).getDate(),
                        ),
                      })
                    }
                  />
                ) : null}
                {rule.repeat === "monthly" || rule.repeat === "yearly" ? (
                  <DayGrid
                    value={rule.month_day}
                    limit={
                      rule.repeat === "yearly"
                        ? new Date(2024, rule.month, 0).getDate()
                        : 31
                    }
                    disabled={disabled}
                    onChange={(month_day) => change({ month_day })}
                  />
                ) : null}
              </View>
            </Section>
            <Section title="Alert">
              <View style={styles.block}>
                <Segments<"digest" | "exact">
                  label="Alert"
                  options={[
                    {
                      value: "digest",
                      label: `Digest${digest ? ` · ${digest.time}` : ""}`,
                      name: "Daily digest",
                    },
                    { value: "exact", label: "Exact time", name: "Exact time" },
                  ]}
                  value={rule.delivery}
                  disabled={disabled}
                  onChange={(delivery) => change({ delivery })}
                />
                {rule.delivery === "exact" ? (
                  <PickerField
                    label="Time"
                    mode="time"
                    value={clockDate(rule.time)}
                    text={rule.time}
                    disabled={disabled}
                    onChange={(value) => change({ time: clockText(value) })}
                  />
                ) : null}
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Timezone and language: ${rule.timezone}, ${rule.language}`}
                  accessibilityState={{ disabled }}
                  disabled={disabled}
                  onPress={() => setSheet("timezone")}
                  style={[styles.field, { borderColor: palette.line }]}
                >
                  <Text
                    numberOfLines={1}
                    style={[styles.fieldValue, { color: palette.accent }]}
                  >
                    {`${rule.timezone.replace(/_/g, " ")} · ${rule.language}`}
                  </Text>
                </Pressable>
              </View>
            </Section>
            <Section title="Message Idea">
              <View style={styles.block}>
                <TextInput
                  multiline
                  accessibilityLabel="Message Idea"
                  placeholder="What you'd like to say, if anything"
                  placeholderTextColor={palette.muted}
                  maxLength={4096}
                  value={rule.idea}
                  editable={!disabled}
                  onChangeText={(idea) => change({ idea })}
                  style={[
                    styles.input,
                    styles.idea,
                    {
                      color: palette.text,
                      borderColor: palette.line,
                      backgroundColor: palette.panel,
                    },
                  ]}
                />
                {kept ? <Body muted>{kept}</Body> : null}
              </View>
            </Section>
            <Text
              accessibilityLiveRegion="polite"
              style={[styles.summary, { color: palette.muted }]}
            >
              {current
                ? preview?.next
                  ? summaryLine(rule, digest?.time ?? "09:00", preview.next)
                  : "Choose a date and time still ahead"
                : " "}
            </Text>
          </>
        ) : null}
        {error ? <RowNote>{error}</RowNote> : null}
        <Button
          label="Save reminder"
          primary
          disabled={disabled || !current || !preview?.next}
          onPress={() => void save()}
        />
        {schedule ? (
          <Button
            label="Remove reminder"
            disabled={busy || !saved || offline}
            onPress={remove}
          />
        ) : null}
      </ScrollView>
      <Sheet
        visible={!!sheet && !!rule}
        title="Timezone and language"
        onClose={() => setSheet(null)}
      >
        <ZoneLanguage
          part={sheet ?? "timezone"}
          onPart={setSheet}
          timezone={rule?.timezone ?? zone()}
          language={rule?.language ?? "English"}
          onChange={(values) => change(values)}
        />
      </Sheet>
    </SafeAreaView>
  );
}

// The editor's one row for the timezone and fallback language (Q12).
function ZoneLanguage({
  part,
  onPart,
  timezone,
  language,
  onChange,
}: {
  part: "timezone" | "language";
  onPart: (part: "timezone" | "language") => void;
  timezone: string;
  language: string;
  onChange: (values: { timezone?: string; language?: string }) => void;
}) {
  const { zones, languages } = useLists();
  return (
    <View style={styles.sheetBody}>
      <ChoiceRow<"timezone" | "language">
        label="Choose"
        options={[
          { value: "timezone", label: "Timezone" },
          { value: "language", label: "Fallback language" },
        ]}
        value={part}
        disabled={false}
        onChange={onPart}
      />
      {part === "timezone" ? (
        <SearchList
          label="Timezones"
          options={zones.map((value) => ({
            value,
            label: value.replace(/_/g, " "),
          }))}
          value={timezone}
          onChoose={(value) => onChange({ timezone: value })}
        />
      ) : (
        <SearchList
          label="Languages"
          options={languages.map((item) => ({
            value: item.name,
            label:
              item.native === item.name
                ? item.name
                : `${item.name} · ${item.native}`,
          }))}
          value={language}
          onChoose={(value) => onChange({ language: value })}
        />
      )}
    </View>
  );
}

// The digest's time and timezone (Q2, Q7).
function DigestSheet({
  visible,
  digest,
  onSaved,
  onClose,
}: {
  visible: boolean;
  digest: Digest;
  onSaved: (value: Digest) => void;
  onClose: () => void;
}) {
  const { api } = useConnection();
  const { zones } = useLists();
  const [value, setValue] = useState(digest);
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => setValue(digest), [digest, visible]);
  async function save() {
    setBusy(true);
    setError("");
    try {
      onSaved(await api<Digest>("reminders/settings", { body: value }));
      onClose();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Sheet visible={visible} title="Daily digest" onClose={onClose}>
      <View style={styles.sheetBody}>
        <PickerField
          label="Digest time"
          mode="time"
          value={clockDate(value.time)}
          text={value.time}
          disabled={busy}
          onChange={(chosen) => setValue({ ...value, time: clockText(chosen) })}
        />
        <Button
          label={`Timezone: ${value.timezone.replace(/_/g, " ")}`}
          accessibilityLabel={`Digest timezone: ${value.timezone}`}
          onPress={() => setChoosing(!choosing)}
        />
        {choosing ? (
          <SearchList
            label="Timezones"
            options={zones.map((zoneName) => ({
              value: zoneName,
              label: zoneName.replace(/_/g, " "),
            }))}
            value={value.timezone}
            onChoose={(timezone) => {
              setValue({ ...value, timezone });
              setChoosing(false);
            }}
          />
        ) : null}
        {error ? <RowNote>{error}</RowNote> : null}
        <Button
          label="Save digest"
          primary
          disabled={busy}
          onPress={() => void save()}
        />
      </View>
    </Sheet>
  );
}

// A Correspondent Profile's Reminder section (Q12): the person's schedules,
// each opening the editor, and Add reminder.
export function ProfileReminders({
  account,
  peer,
  title,
}: {
  account: number;
  peer: string;
  title: string;
}) {
  const { api } = useConnection();
  const palette = usePalette();
  const { digest } = useDigest();
  const [value, setValue] = useState<ChatReminders | null>(null);
  const [error, setError] = useState("");
  const path = remindersPath(account, peer);
  async function read() {
    try {
      setValue(await api<ChatReminders>(path));
      setError("");
    } catch (problem) {
      setError((problem as Error).message);
    }
  }
  useEffect(() => {
    void read();
  }, [api, path]);
  useServerChanges(() => void read());
  return (
    <View style={styles.block}>
      {value?.items.map((item) => (
        <Pressable
          key={item.id}
          accessibilityRole="button"
          accessibilityLabel={`${repeatLine(item.schedule, digest?.time ?? "09:00")}, ${ideaLine(item.schedule.idea) || "Check in"}`}
          onPress={() => openEditor(account, peer, title, item.id)}
          style={styles.profileRow}
        >
          <Text style={[styles.rowTitle, { color: palette.text }]}>
            {repeatLine(item.schedule, digest?.time ?? "09:00")}
          </Text>
          <Text numberOfLines={1} style={{ color: palette.muted }}>
            {ideaLine(item.schedule.idea) || "Check in"}
          </Text>
          <Text style={[styles.small, { color: palette.muted }]}>
            {rowDate(item.occurrence, item.next_at, item.schedule.timezone)}
            {item.status === "paused" ? " · Paused" : ""}
          </Text>
        </Pressable>
      ))}
      {error ? <RowNote>{error}</RowNote> : null}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Add reminder"
        onPress={() => openEditor(account, peer, title)}
        style={styles.profileRow}
      >
        <Text style={[styles.link, { color: palette.accent }]}>
          Add reminder
        </Text>
      </Pressable>
    </View>
  );
}

export function openEditor(
  account: number,
  peer: string,
  title: string,
  schedule?: string,
) {
  router.push({
    pathname: "/reminder/[account]/[peer]",
    params: {
      account: String(account),
      peer,
      title,
      ...(schedule ? { schedule } : {}),
    },
  });
}

// The Reminder Bar (Q13): above the chat's suggestions while it has pending
// occurrences, each with its line, when it fell due, Dismiss and its editor.
export function ReminderBar({
  account,
  peer,
  title,
  reminders,
  onChange,
}: {
  account: number;
  peer: string;
  title: string;
  reminders: Pending[];
  onChange: () => void;
}) {
  const { api } = useConnection();
  const palette = usePalette();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  if (!reminders.length) return null;
  const timezone = zone();
  async function dismiss(item: Pending) {
    setBusy(item.occurrence);
    setError("");
    try {
      await api(`${reminderPath(account, peer, item.schedule)}/dismiss`, {
        body: { occurrence: item.occurrence },
      });
      onChange();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <View accessibilityLabel="Reminders" style={styles.bar}>
      {reminders.map((item) => {
        const line = barLine(item.idea);
        const when = `${dateLabel(item.due_at, timezone)}, ${timeLabel(item.due_at, timezone)}`;
        return (
          <View
            key={item.occurrence}
            style={[
              styles.barRow,
              { backgroundColor: palette.panel, borderColor: palette.line },
            ]}
          >
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Edit reminder: ${line}, due ${when}`}
              onPress={() => openEditor(account, peer, title, item.schedule)}
              style={styles.barLine}
            >
              <Ionicons name="time-outline" size={16} color={palette.accent} />
              <Text
                numberOfLines={1}
                style={[styles.fill, { color: palette.text }]}
              >
                {line}
              </Text>
              <Text style={[styles.small, { color: palette.muted }]}>
                {when}
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Dismiss reminder: ${line}`}
              disabled={!!busy}
              onPress={() => void dismiss(item)}
              style={[styles.dismiss, { borderColor: palette.line }]}
            >
              <Text style={[styles.small, { color: palette.accent }]}>
                Dismiss
              </Text>
            </Pressable>
          </View>
        );
      })}
      {error ? <RowNote>{error}</RowNote> : null}
    </View>
  );
}

// The Reminders tab (Q7): the digest line, then the member's reminders as
// chat-like rows across their accounts, and Add reminder.
export function RemindersList() {
  const { api, connection, selectAccount } = useConnection();
  const palette = usePalette();
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [menu, setMenu] = useState<Item | null>(null);
  const [digestSheet, setDigestSheet] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const { digest, setDigest } = useDigest();
  const alive = useRef(true);
  const accounts = (
    connection.status === "paired" ? (connection.accounts?.accounts ?? []) : []
  ).filter((item) => item.state === "authorized");
  const several =
    connection.status === "paired" &&
    (connection.accounts?.accounts.length ?? 0) > 1;
  async function reload() {
    try {
      const value = await api<Schemas["ReminderList"]>("reminders", {
        keep: "reminders",
      });
      if (alive.current) {
        setItems(value.items);
        setError("");
      }
    } catch (problem) {
      if (alive.current) setError((problem as Error).message);
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
    setBusy(item.id);
    try {
      await api(
        `${reminderPath(item.account_id, item.peer, item.id)}/dismiss`,
        {
          body: { occurrence: item.occurrence.id },
        },
      );
      await reload();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy("");
    }
  }
  function open(item: Item) {
    if (item.status === "unavailable") return;
    if (item.occurrence) {
      selectAccount(item.account_id);
      router.push({
        pathname: "/chat/[account]/[peer]",
        params: { account: String(item.account_id), peer: item.peer },
      });
    } else openEditor(item.account_id, item.peer, item.title, item.id);
  }
  function add(account: number) {
    setChoosing(false);
    router.push({
      pathname: "/new-message",
      params: { account: String(account), purpose: "reminder" },
    });
  }
  return (
    <>
      <FlatList
        data={items ?? []}
        keyExtractor={(item) => `${item.account_id}/${item.id}`}
        ListHeaderComponent={
          <View style={styles.top}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={digestLine(digest?.time ?? "09:00")}
              disabled={!digest}
              onPress={() => setDigestSheet(true)}
              style={styles.fill}
            >
              <Text style={[styles.link, { color: palette.accent }]}>
                {digestLine(digest?.time ?? "09:00")}
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Add reminder"
              disabled={offline || !accounts.length}
              onPress={() =>
                accounts.length > 1
                  ? setChoosing(true)
                  : accounts[0] && add(accounts[0].account_id)
              }
            >
              <Text style={[styles.link, { color: palette.accent }]}>
                Add reminder
              </Text>
            </Pressable>
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            {error ? <RowNote>{error}</RowNote> : null}
            <Body muted>
              {items === null
                ? offline
                  ? "Reminders show once the server answers."
                  : "Loading reminders…"
                : "No reminders."}
            </Body>
          </View>
        }
        ListFooterComponent={
          error && items?.length ? (
            <View style={styles.empty}>
              <RowNote>{error}</RowNote>
            </View>
          ) : null
        }
        renderItem={({ item }) => (
          <ReminderRow
            item={item}
            several={several}
            digestTime={digest?.time ?? "09:00"}
            busy={!!busy || offline}
            onOpen={() => open(item)}
            onDismiss={() => void dismiss(item)}
            onMore={() => setMenu(item)}
            palette={palette}
          />
        )}
      />
      {digest ? (
        <DigestSheet
          visible={digestSheet}
          digest={digest}
          onSaved={setDigest}
          onClose={() => setDigestSheet(false)}
        />
      ) : null}
      <Sheet
        visible={!!menu}
        title={menu ? `${menu.title} reminder` : ""}
        onClose={() => setMenu(null)}
      >
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Edit"
          onPress={() => {
            const item = menu;
            setMenu(null);
            if (item)
              openEditor(item.account_id, item.peer, item.title, item.id);
          }}
          style={styles.choice}
        >
          <Ionicons name="create-outline" size={20} color={palette.accent} />
          <Text style={[styles.fill, { color: palette.text }]}>Edit</Text>
        </Pressable>
      </Sheet>
      <Sheet
        visible={choosing}
        title="Add reminder for"
        onClose={() => setChoosing(false)}
      >
        {accounts.map((account) => (
          <Pressable
            key={account.account_id}
            accessibilityRole="button"
            accessibilityLabel={account.name || `Account ${account.account_id}`}
            onPress={() => add(account.account_id)}
            style={styles.choice}
          >
            <Avatar
              account={account.account_id}
              peer={`user:${account.account_id}`}
              photoId={account.photo_id}
              name={account.name}
              size={36}
            />
            <Text style={[styles.fill, { color: palette.text }]}>
              {account.name || `Account ${account.account_id}`}
            </Text>
          </Pressable>
        ))}
      </Sheet>
    </>
  );
}

function ReminderRow({
  item,
  several,
  digestTime,
  busy,
  onOpen,
  onDismiss,
  onMore,
  palette,
}: {
  item: Item;
  several: boolean;
  digestTime: string;
  busy: boolean;
  onOpen: () => void;
  onDismiss: () => void;
  onMore: () => void;
  palette: Palette;
}) {
  const unavailable = item.status === "unavailable";
  const pending = !!item.occurrence;
  const line = ideaLine(item.schedule.idea) || "Check in";
  const date = rowDate(item.occurrence, item.next_at, item.schedule.timezone);
  return (
    <View style={styles.reminderRow}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${item.title}${several ? `, ${item.account}` : ""}, ${line}, ${date}`}
        disabled={unavailable}
        onPress={onOpen}
        style={({ pressed }) => [
          styles.rowMain,
          pressed ? { backgroundColor: palette.panel } : null,
        ]}
      >
        <Avatar
          account={item.account_id}
          peer={item.peer || `user:${item.account_id}`}
          photoId={item.photo_id}
          name={item.title}
          size={48}
        />
        <View style={styles.fill}>
          <View style={styles.line}>
            <Text
              numberOfLines={1}
              style={[styles.rowTitle, styles.fill, { color: palette.text }]}
            >
              {item.title}
            </Text>
            <Text
              style={[
                styles.small,
                {
                  color: pending ? palette.accent : palette.muted,
                  fontWeight: pending ? "600" : "400",
                },
              ]}
            >
              {date}
            </Text>
          </View>
          {several ? (
            <Text style={[styles.small, { color: palette.muted }]}>
              {item.account}
            </Text>
          ) : null}
          <Text
            numberOfLines={1}
            style={[styles.small, { color: palette.muted }]}
          >
            {repeatLine(item.schedule, digestTime)}
          </Text>
          <Text numberOfLines={1} style={{ color: palette.muted }}>
            {unavailable
              ? "Unavailable: sign in to this account."
              : item.status === "paused"
                ? "Paused: Automatic suggestions is Off."
                : line}
          </Text>
        </View>
      </Pressable>
      {!unavailable ? (
        <View style={styles.actions}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`More for ${item.title}`}
            hitSlop={8}
            onPress={onMore}
            style={styles.more}
          >
            <Ionicons
              name="ellipsis-horizontal"
              size={20}
              color={palette.accent}
            />
          </Pressable>
          {pending ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Dismiss reminder for ${item.title}`}
              disabled={busy}
              onPress={onDismiss}
              style={[styles.dismiss, { borderColor: palette.line }]}
            >
              <Text style={[styles.small, { color: palette.accent }]}>
                Dismiss
              </Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  pageHeader: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 },
  page: { padding: 16, gap: 16, paddingBottom: 32 },
  block: { paddingVertical: 10, gap: 10 },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  input: { borderWidth: 1, borderRadius: 8, padding: 10, fontSize: 16 },
  number: { width: 72, textAlign: "center" },
  idea: { minHeight: 96, textAlignVertical: "top" },
  field: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    minHeight: 44,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: 8,
  },
  fieldLabel: { fontSize: 15 },
  fieldValue: { fontSize: 16, fontWeight: "600", flexShrink: 1 },
  summary: { fontSize: 14 },
  segments: {
    flexDirection: "row",
    borderWidth: 1,
    borderRadius: 8,
    overflow: "hidden",
  },
  segment: {
    flex: 1,
    minHeight: 40,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 4,
  },
  segmentLabel: { fontSize: 14, fontWeight: "600" },
  days: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  day: {
    width: 38,
    height: 38,
    borderWidth: 1,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
  },
  backdrop: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  sheet: {
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 8,
    maxHeight: "85%",
  },
  sheetTitle: { fontSize: 15, fontWeight: "600", paddingVertical: 10 },
  sheetBody: { gap: 10, paddingBottom: 8 },
  search: { gap: 8 },
  choices: { maxHeight: 360 },
  choice: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
  },
  top: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  link: { fontSize: 16, fontWeight: "600" },
  empty: { padding: 24, gap: 8 },
  reminderRow: { flexDirection: "row", alignItems: "center", paddingRight: 8 },
  rowMain: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingLeft: 16,
    paddingRight: 6,
    paddingVertical: 10,
  },
  line: { flexDirection: "row", alignItems: "center", gap: 6 },
  rowTitle: { fontSize: 16, fontWeight: "600" },
  small: { fontSize: 13 },
  dismiss: {
    borderWidth: 1,
    borderRadius: 16,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  more: { padding: 6 },
  actions: { alignItems: "flex-end", gap: 2 },
  profileRow: { paddingVertical: 8, gap: 2 },
  bar: { gap: 4 },
  barRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  barLine: { flex: 1, flexDirection: "row", alignItems: "center", gap: 6 },
});
