import React, { useEffect, useMemo, useRef, useState } from "react";
import { Check, MoreHorizontal, Pencil, Search, X } from "lucide-react";
import {
  barLine,
  dateLabel,
  digestLine,
  ideaLine,
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
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { Avatar } from "./Avatar";
import { useChanges } from "./events";
import { Note, ScreenFrame, Section } from "./screenUi";
import "./reminders.css";

type Rule = components["schemas"]["ReminderRule"];
type Reminder = components["schemas"]["ReminderView"];
type ChatReminders = components["schemas"]["ChatReminders"];
export type ReminderItem = components["schemas"]["ReminderItem"];
export type PendingReminder = components["schemas"]["PendingReminder"];
type Digest = components["schemas"]["DigestSettings"];
type Language = components["schemas"]["Language"];
type Preview = components["schemas"]["ReminderPreview"];
type Kept = components["schemas"]["KeptStatus"];
const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

// The member's digest settings, read once per screen.
function useDigest() {
  const [digest, setDigest] = useState<Digest | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    workspaceApi<Digest>("reminders/settings").then(
      (answer) =>
        live &&
        setDigest(answer.revision ? answer : { ...answer, timezone: zone() }),
      (problem) => live && setError((problem as Error).message),
    );
    return () => {
      live = false;
    };
  }, []);
  return { digest, setDigest, error };
}

// The searchable timezone list and the language list (Q12), read on demand.
function useLists() {
  const [zones, setZones] = useState<string[]>([]);
  const [languages, setLanguages] = useState<Language[]>([]);
  useEffect(() => {
    let live = true;
    void workspaceApi<{ timezones: string[] }>("reminders/timezones").then(
      (answer) => live && setZones(answer.timezones),
      () => undefined,
    );
    void workspaceApi<{ languages: Language[] }>("reminders/languages").then(
      (answer) => live && setLanguages(answer.languages),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, []);
  return { zones, languages };
}

// A sheet over the column, closed by its backdrop or Escape.
function Sheet({
  label,
  onClose,
  children,
}: {
  label: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    panel.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div
        ref={panel}
        className="sheet reminder-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="reminder-sheet-head">
          <h2>{label}</h2>
          <button
            type="button"
            className="header-icon"
            aria-label="Close"
            onClick={onClose}
          >
            <X size={20} aria-hidden />
          </button>
        </header>
        {children}
      </div>
    </div>
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
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const wanted = query.trim().toLowerCase().replace(/\s+/g, "_");
    return options
      .filter(
        (option) =>
          !wanted ||
          option.value.toLowerCase().includes(wanted) ||
          option.label.toLowerCase().replace(/\s+/g, "_").includes(wanted),
      )
      .slice(0, 80);
  }, [options, query]);
  return (
    <div className="reminder-search">
      <label className="reminder-search-field">
        <Search size={16} aria-hidden />
        <input
          type="search"
          aria-label={`Search ${label.toLowerCase()}`}
          placeholder="Search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <div className="reminder-choices" role="listbox" aria-label={label}>
        {shown.map((option) => (
          <button
            key={option.value}
            type="button"
            role="option"
            aria-selected={option.value === value}
            onClick={() => onChoose(option.value)}
          >
            <span>{option.label}</span>
            {option.value === value && <Check size={18} aria-hidden />}
          </button>
        ))}
      </div>
    </div>
  );
}

// The editor's one row for the timezone and fallback language (Q12).
function ZoneLanguageSheet({
  timezone,
  language,
  onChange,
  onClose,
}: {
  timezone: string;
  language: string;
  onChange: (values: { timezone?: string; language?: string }) => void;
  onClose: () => void;
}) {
  const { zones, languages } = useLists();
  const [part, setPart] = useState<"timezone" | "language">("timezone");
  return (
    <Sheet label="Timezone and language" onClose={onClose}>
      <div className="choice-segments" role="tablist" aria-label="Choose">
        {(
          [
            ["timezone", "Timezone", "Timezone"],
            ["language", "Language", "Fallback language"],
          ] as const
        ).map(([value, label, name]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-label={name}
            aria-selected={part === value}
            onClick={() => setPart(value)}
          >
            {label}
          </button>
        ))}
      </div>
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
    </Sheet>
  );
}

// The digest's time and timezone (Q2, Q7).
function DigestSheet({
  digest,
  onSaved,
  onClose,
}: {
  digest: Digest;
  onSaved: (value: Digest) => void;
  onClose: () => void;
}) {
  const { zones } = useLists();
  const [value, setValue] = useState(digest);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [choosing, setChoosing] = useState(false);
  async function save() {
    setBusy(true);
    setError("");
    try {
      onSaved(await workspaceApi<Digest>("reminders/settings", value));
      onClose();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Sheet label="Daily digest" onClose={onClose}>
      <label className="reminder-field">
        <span>Time</span>
        <input
          type="time"
          aria-label="Digest time"
          value={value.time}
          disabled={busy}
          onChange={(event) =>
            event.target.value &&
            setValue({ ...value, time: event.target.value })
          }
        />
      </label>
      <button
        type="button"
        className="reminder-zone-row"
        aria-label={`Digest timezone: ${value.timezone}`}
        onClick={() => setChoosing(!choosing)}
      >
        <span>Timezone</span>
        <strong>{value.timezone.replace(/_/g, " ")}</strong>
      </button>
      {choosing && (
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
      )}
      {error && <p role="alert">{error}</p>}
      <button
        type="button"
        className="primary"
        disabled={busy}
        onClick={() => void save()}
      >
        Save digest
      </button>
    </Sheet>
  );
}

// The 1–31 grid for Monthly and Yearly (Q12).
function DayGrid({
  value,
  limit = 31,
  disabled,
  onChange,
}: {
  value: number;
  limit?: number;
  disabled: boolean;
  onChange: (day: number) => void;
}) {
  return (
    <div className="reminder-days" role="radiogroup" aria-label="Day">
      {Array.from({ length: limit }, (_, index) => index + 1).map((day) => (
        <button
          key={day}
          type="button"
          role="radio"
          aria-label={`Day ${day}`}
          aria-checked={day === value}
          disabled={disabled}
          onClick={() => onChange(day)}
        >
          {day}
        </button>
      ))}
    </div>
  );
}

function Segments<T extends string | number>({
  label,
  options,
  value,
  disabled,
  onChange,
}: {
  label: string;
  options: { value: T; label: string; name?: string }[];
  value: T;
  disabled: boolean;
  onChange: (value: T) => void;
}) {
  return (
    <div className="choice-segments" role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          role="radio"
          aria-label={option.name}
          aria-checked={option.value === value}
          disabled={disabled}
          onClick={() => option.value !== value && onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

// The reminder editor (Q12), its own screen: opened from the profile, the
// Reminders tab and the Reminder Bar. A new schedule has no `schedule`.
export function ReminderEditorScreen({
  account,
  peer,
  title,
  schedule,
  onBack,
}: {
  account: number;
  peer: string;
  title: string;
  schedule?: string;
  onBack: () => void;
}) {
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
  const [sheet, setSheet] = useState(false);
  const dirty = useRef(false);
  const signature = JSON.stringify(rule);
  useEffect(() => {
    let live = true;
    (schedule
      ? workspaceApi<Reminder>(reminderPath(account, peer, schedule)).then(
          (value) => {
            if (!live) return;
            setSaved(value);
            if (!dirty.current) setRule(value.schedule);
          },
        )
      : workspaceApi<ChatReminders>(path).then((value) => {
          if (!live || dirty.current) return;
          // The chat's recent language, else the browser's (Q1).
          setRule(
            reminderDefaults(
              zone(),
              value.language ||
                listedLanguage(navigator.language || "en", languages),
            ),
          );
        })
    ).catch((problem) => live && setError((problem as Error).message));
    return () => {
      live = false;
    };
  }, [account, peer, schedule, languages.length]);
  useEffect(() => {
    if (!rule) return;
    let alive = true;
    const timer = setTimeout(() => {
      void workspaceApi<Preview>(`${path}/preview`, rule).then(
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
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [path, signature]);
  function change(values: Partial<Rule>) {
    dirty.current = true;
    setRule((old) => (old ? { ...old, ...values } : old));
  }
  async function save() {
    if (!rule || busy) return;
    setBusy(true);
    setError("");
    try {
      await workspaceApi<Reminder>(
        schedule ? reminderPath(account, peer, schedule) : path,
        schedule ? { ...rule, revision: saved?.revision ?? 0 } : rule,
      );
      dirty.current = false;
      onBack();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!schedule || !saved || busy) return;
    if (!window.confirm(`${REMOVE_REMINDER.title} ${REMOVE_REMINDER.message}`))
      return;
    setBusy(true);
    setError("");
    try {
      await workspaceApi(
        reminderPath(account, peer, schedule),
        { revision: saved.revision },
        undefined,
        { method: "DELETE" },
      );
      onBack();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const current = preview?.signature === signature;
  const disabled = busy || !rule;
  const kept =
    saved && !dirty.current
      ? keptLine(saved.kept)
      : current
        ? keptLine(preview?.kept)
        : "";
  return (
    <ScreenFrame
      title={schedule ? "Edit reminder" : "New reminder"}
      onBack={onBack}
    >
      <p className="reminder-person">{title}</p>
      {saved?.status === "paused" && (
        <Note tone="info">Paused: Automatic suggestions is Off.</Note>
      )}
      {rule && (
        <>
          <Section title="Repeat">
            <div className="screen-block reminder-form">
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
              {(rule.repeat === "days" || rule.repeat === "weeks") && (
                <label className="reminder-field">
                  <span>Every</span>
                  <input
                    type="number"
                    aria-label={
                      rule.repeat === "days"
                        ? "Days between reminders"
                        : "Weeks between reminders"
                    }
                    min={1}
                    max={rule.repeat === "days" ? 365 : 52}
                    value={rule.interval}
                    disabled={disabled}
                    onChange={(event) =>
                      change({
                        interval: Math.max(1, Number(event.target.value) || 1),
                      })
                    }
                  />
                  <span>{rule.repeat === "days" ? "days" : "weeks"}</span>
                </label>
              )}
              {rule.repeat === "weeks" && (
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
              )}
              {(rule.repeat === "once" ||
                rule.repeat === "days" ||
                rule.repeat === "weeks") && (
                <label className="reminder-field">
                  <span>
                    {rule.repeat === "once"
                      ? "Date"
                      : rule.repeat === "days"
                        ? "First on"
                        : "From the week of"}
                  </span>
                  <input
                    type="date"
                    aria-label={
                      rule.repeat === "once"
                        ? "Date"
                        : rule.repeat === "days"
                          ? "First date"
                          : "Start week"
                    }
                    value={rule.start}
                    disabled={disabled}
                    onChange={(event) =>
                      event.target.value &&
                      change({ start: event.target.value })
                    }
                  />
                </label>
              )}
              {rule.repeat === "yearly" && (
                <label className="reminder-field">
                  <span>Month</span>
                  <select
                    aria-label="Month"
                    value={rule.month}
                    disabled={disabled}
                    onChange={(event) => {
                      const month = Number(event.target.value);
                      const days = new Date(2024, month, 0).getDate();
                      change({
                        month,
                        month_day: Math.min(rule.month_day, days),
                      });
                    }}
                  >
                    {MONTHS.map((name, index) => (
                      <option key={name} value={index + 1}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {(rule.repeat === "monthly" || rule.repeat === "yearly") && (
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
              )}
            </div>
          </Section>
          <Section title="Alert">
            <div className="screen-block reminder-form">
              <Segments<"digest" | "exact">
                label="Alert"
                options={[
                  {
                    value: "digest",
                    label: `Digest${digest ? ` · ${digest.time}` : ""}`,
                    name: "Daily digest",
                  },
                  { value: "exact", label: "Exact time" },
                ]}
                value={rule.delivery}
                disabled={disabled}
                onChange={(delivery) => change({ delivery })}
              />
              {rule.delivery === "exact" && (
                <label className="reminder-field">
                  <span>Time</span>
                  <input
                    type="time"
                    aria-label="Time"
                    value={rule.time}
                    disabled={disabled}
                    onChange={(event) =>
                      event.target.value && change({ time: event.target.value })
                    }
                  />
                </label>
              )}
              <button
                type="button"
                className="reminder-zone-row"
                aria-label={`Timezone and language: ${rule.timezone}, ${rule.language}`}
                disabled={disabled}
                onClick={() => setSheet(true)}
              >
                <span>
                  {rule.timezone.replace(/_/g, " ")} · {rule.language}
                </span>
              </button>
            </div>
          </Section>
          <Section title="Message Idea">
            <div className="screen-block">
              <textarea
                className="reminder-idea"
                aria-label="Message Idea"
                placeholder="What you'd like to say, if anything"
                value={rule.idea}
                maxLength={4096}
                disabled={disabled}
                onChange={(event) => change({ idea: event.target.value })}
              />
              {kept && <p className="reminder-kept">{kept}</p>}
            </div>
          </Section>
          <p className="reminder-summary" role="status">
            {current
              ? preview?.next
                ? summaryLine(rule, digest?.time ?? "09:00", preview.next)
                : "Choose a date and time still ahead"
              : " "}
          </p>
        </>
      )}
      {error && <Note>{error}</Note>}
      <div className="reminder-actions">
        <button
          type="button"
          className="primary"
          disabled={disabled || !current || !preview?.next}
          onClick={() => void save()}
        >
          Save reminder
        </button>
        {schedule && (
          <button
            type="button"
            className="danger"
            disabled={busy || !saved}
            onClick={() => void remove()}
          >
            Remove reminder
          </button>
        )}
      </div>
      {sheet && rule && (
        <ZoneLanguageSheet
          timezone={rule.timezone}
          language={rule.language}
          onChange={(values) => change(values)}
          onClose={() => setSheet(false)}
        />
      )}
    </ScreenFrame>
  );
}

// A Correspondent Profile's Reminder section (Q12): the person's schedules,
// each opening the editor, and Add reminder.
export function ProfileReminders({
  account,
  peer,
  onEdit,
  onAdd,
}: {
  account: number;
  peer: string;
  onEdit: (schedule: string) => void;
  onAdd: () => void;
}) {
  const [value, setValue] = useState<ChatReminders | null>(null);
  const [error, setError] = useState("");
  const { digest } = useDigest();
  const path = remindersPath(account, peer);
  async function read() {
    try {
      setValue(await workspaceApi<ChatReminders>(path));
      setError("");
    } catch (problem) {
      setError((problem as Error).message);
    }
  }
  useEffect(() => {
    void read();
  }, [path]);
  useChanges(() => void read());
  return (
    <div className="screen-block profile-reminders">
      {value?.items.map((item) => (
        <button
          key={item.id}
          type="button"
          className="profile-reminder"
          onClick={() => onEdit(item.id)}
        >
          <strong>{repeatLine(item.schedule, digest?.time ?? "09:00")}</strong>
          <span>{ideaLine(item.schedule.idea) || "Check in"}</span>
          <small>
            {rowDate(item.occurrence, item.next_at, item.schedule.timezone)}
            {item.status === "paused" ? " · Paused" : ""}
          </small>
        </button>
      ))}
      {error && <p role="alert">{error}</p>}
      <button type="button" className="reminder-add-link" onClick={onAdd}>
        Add reminder
      </button>
    </div>
  );
}

// The Reminder Bar (Q13): above the chat's suggestions while it has pending
// occurrences, each with its line, when it fell due, Dismiss and its editor.
export function ReminderBar({
  account,
  peer,
  reminders,
  onEdit,
  onChange,
}: {
  account: number;
  peer: string;
  reminders: PendingReminder[];
  onEdit: (schedule: string) => void;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const timezone = zone();
  if (!reminders.length) return null;
  async function dismiss(item: PendingReminder) {
    setBusy(item.occurrence);
    setError("");
    try {
      await workspaceApi(
        `${reminderPath(account, peer, item.schedule)}/dismiss`,
        {
          occurrence: item.occurrence,
        },
      );
      onChange();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="reminder-bar" aria-label="Reminders">
      {reminders.map((item) => {
        const line = barLine(item.idea);
        const when = `${dateLabel(item.due_at, timezone)}, ${timeLabel(item.due_at, timezone)}`;
        return (
          <div className="reminder-bar-row" key={item.occurrence}>
            <button
              type="button"
              className="reminder-bar-line"
              aria-label={`Edit reminder: ${line}, due ${when}`}
              onClick={() => onEdit(item.schedule)}
            >
              <Pencil size={14} aria-hidden />
              <span>{line}</span>
              <small>{when}</small>
            </button>
            <button
              type="button"
              className="reminder-dismiss"
              aria-label={`Dismiss reminder: ${line}`}
              disabled={!!busy}
              onClick={() => void dismiss(item)}
            >
              Dismiss
            </button>
          </div>
        );
      })}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

// The Reminders tab (Q7): the digest line, then the member's reminders as
// chat-like rows across their accounts, and Add reminder.
export function RemindersList({
  several,
  onOpen,
  onEdit,
  onAdd,
}: {
  // The member has several accounts: rows name their account.
  several: boolean;
  onOpen: (item: ReminderItem) => void;
  onEdit: (item: ReminderItem) => void;
  onAdd: () => void;
}) {
  const [items, setItems] = useState<ReminderItem[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [menu, setMenu] = useState<string | null>(null);
  const [digestSheet, setDigestSheet] = useState(false);
  const { digest, setDigest } = useDigest();
  const alive = useRef(true);
  async function reload() {
    try {
      const value = await workspaceApi<{ items: ReminderItem[] }>("reminders");
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
    void reload();
    return () => {
      alive.current = false;
    };
  }, []);
  useChanges(() => void reload(), 60000);
  async function dismiss(item: ReminderItem) {
    if (!item.occurrence || busy) return;
    setBusy(item.id);
    try {
      await workspaceApi(
        `${reminderPath(item.account_id, item.peer, item.id)}/dismiss`,
        { occurrence: item.occurrence.id },
      );
      await reload();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="reminders-list" aria-label="Reminders">
      <div className="reminders-top">
        <button
          type="button"
          className="reminder-digest-line"
          disabled={!digest}
          onClick={() => setDigestSheet(true)}
        >
          {digestLine(digest?.time ?? "09:00")}
        </button>
        <button type="button" className="reminder-add" onClick={onAdd}>
          Add reminder
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {items === null && <p className="list-note">Loading reminders…</p>}
      {items?.length === 0 && <p className="list-note">No reminders.</p>}
      <ul className="reminder-rows">
        {items?.map((item) => {
          const unavailable = item.status === "unavailable";
          const pending = !!item.occurrence;
          const line = ideaLine(item.schedule.idea) || "Check in";
          const date = rowDate(
            item.occurrence,
            item.next_at,
            item.schedule.timezone,
          );
          return (
            <li
              key={`${item.account_id}/${item.id}`}
              className={`reminder-row${pending ? " pending" : ""}`}
            >
              <button
                type="button"
                className="reminder-row-main"
                aria-label={`${item.title}${several ? `, ${item.account}` : ""}, ${line}, ${date}`}
                disabled={unavailable}
                onClick={() => (pending ? onOpen(item) : onEdit(item))}
              >
                <Avatar
                  account={item.account_id}
                  peer={item.peer || `user:${item.account_id}`}
                  name={item.title}
                  photoId={item.photo_id}
                  className="row-avatar"
                />
                <span className="row-body">
                  <span className="row-line">
                    <strong>{item.title}</strong>
                    <time>{date}</time>
                  </span>
                  {/* The account label has its own line, as in the app. */}
                  {several && (
                    <span className="row-line reminder-repeat">
                      {item.account}
                    </span>
                  )}
                  <span className="row-line reminder-repeat">
                    {repeatLine(item.schedule, digest?.time ?? "09:00")}
                  </span>
                  <span className="row-line">
                    <span className="row-preview">
                      {unavailable
                        ? "Unavailable: sign in to this account."
                        : item.status === "paused"
                          ? "Paused: Automatic suggestions is Off."
                          : line}
                    </span>
                  </span>
                </span>
              </button>
              {!unavailable && (
                <div className="reminder-row-actions">
                  <div className="reminder-more">
                    <button
                      type="button"
                      className="header-icon"
                      aria-label={`More for ${item.title}`}
                      aria-haspopup="menu"
                      aria-expanded={menu === item.id}
                      onClick={() => setMenu(menu === item.id ? null : item.id)}
                    >
                      <MoreHorizontal size={20} aria-hidden />
                    </button>
                    {menu === item.id && (
                      <div
                        className="reminder-menu"
                        role="menu"
                        aria-label={`${item.title} reminder`}
                      >
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setMenu(null);
                            onEdit(item);
                          }}
                        >
                          Edit
                        </button>
                      </div>
                    )}
                  </div>
                  {pending && (
                    <button
                      type="button"
                      className="reminder-dismiss"
                      aria-label={`Dismiss reminder for ${item.title}`}
                      disabled={!!busy}
                      onClick={() => void dismiss(item)}
                    >
                      Dismiss
                    </button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {digestSheet && digest && (
        <DigestSheet
          digest={digest}
          onSaved={setDigest}
          onClose={() => setDigestSheet(false)}
        />
      )}
    </section>
  );
}

// Add reminder chooses the account when the member has several (Q7).
export function ReminderAccountSheet({
  accounts,
  onChoose,
  onClose,
}: {
  accounts: { account_id: number; name: string; photo_id?: string | null }[];
  onChoose: (account: number) => void;
  onClose: () => void;
}) {
  return (
    <Sheet label="Add reminder for" onClose={onClose}>
      {accounts.map((account) => (
        <button
          key={account.account_id}
          type="button"
          className="sheet-row"
          aria-label={account.name || `Account ${account.account_id}`}
          onClick={() => onChoose(account.account_id)}
        >
          <Avatar
            account={account.account_id}
            peer={`user:${account.account_id}`}
            name={account.name}
            photoId={account.photo_id}
            className="sheet-avatar"
          />
          <span className="sheet-row-body">
            <strong>{account.name || `Account ${account.account_id}`}</strong>
          </span>
        </button>
      ))}
    </Sheet>
  );
}
