import React, { useEffect, useRef, useState } from "react";
import {
  reminderDefaults,
  reminderPath,
  reminderTime,
  WEEKDAYS,
} from "../../shared/reminders";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { useChanges } from "./events";
import "./reminders.css";

type Rule = components["schemas"]["ReminderRule"];
type Reminder = components["schemas"]["ReminderView"];
export type ReminderItem = components["schemas"]["ReminderItem"];
type Digest = components["schemas"]["DigestSettings"];
const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

export function ReminderEditor({
  account,
  peer,
}: {
  account: number;
  peer: string;
}) {
  const path = reminderPath(account, peer);
  const [saved, setSaved] = useState<Reminder | null>(null);
  const [rule, setRule] = useState<Rule>(() =>
    reminderDefaults(zone(), navigator.language || "en"),
  );
  const [preview, setPreview] = useState<{
    signature: string;
    next: number;
  } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dirty = useRef(false);
  const signature = JSON.stringify(rule);
  async function read() {
    try {
      const value = await workspaceApi<Reminder>(path);
      setSaved(value);
      if (!dirty.current && value.schedule) setRule(value.schedule);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    void read();
  }, [path]);
  useChanges(() => {
    if (!dirty.current) void read();
  });
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      void workspaceApi<{ next_at: number }>(`${path}/preview`, rule).then(
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
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [path, signature]);
  function change(values: Partial<Rule>) {
    dirty.current = true;
    setRule((old) => ({ ...old, ...values }));
  }
  async function save(remove = false) {
    if (!saved || busy) return;
    setBusy(true);
    setError("");
    try {
      const value = await workspaceApi<Reminder>(
        path,
        remove
          ? { revision: saved.revision }
          : { ...rule, revision: saved.revision },
        undefined,
        remove ? { method: "DELETE" } : undefined,
      );
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
    <div className="screen-block reminder-form">
      {saved?.status === "paused" && (
        <p role="status">Paused: Automatic suggestions is Off.</p>
      )}
      <label>
        Repeat
        <select
          aria-label="Reminder frequency"
          value={rule.frequency}
          disabled={!saved || busy}
          onChange={(e) =>
            change({ frequency: e.target.value as Rule["frequency"] })
          }
        >
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
        </select>
      </label>
      {rule.frequency === "weekly" ? (
        <label>
          Weekday
          <select
            aria-label="Reminder weekday"
            value={rule.weekday}
            disabled={!saved || busy}
            onChange={(e) => change({ weekday: Number(e.target.value) })}
          >
            {WEEKDAYS.map((day, index) => (
              <option key={day} value={index}>
                {day}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <label>
          Calendar date
          <input
            aria-label="Reminder calendar date"
            type="number"
            min={1}
            max={31}
            value={rule.month_day}
            disabled={!saved || busy}
            onChange={(e) => change({ month_day: Number(e.target.value) })}
          />
        </label>
      )}
      <label>
        Schedule timezone
        <input
          aria-label="Schedule timezone"
          value={rule.timezone}
          disabled={!saved || busy}
          onChange={(e) => change({ timezone: e.target.value })}
        />
      </label>
      <label>
        Alert
        <select
          aria-label="Reminder alert"
          value={rule.delivery}
          disabled={!saved || busy}
          onChange={(e) =>
            change({ delivery: e.target.value as Rule["delivery"] })
          }
        >
          <option value="digest">Daily digest</option>
          <option value="exact">Exact time</option>
        </select>
      </label>
      {rule.delivery === "exact" && (
        <label>
          Local time
          <input
            aria-label="Reminder time"
            type="time"
            value={rule.time}
            disabled={!saved || busy}
            onChange={(e) => change({ time: e.target.value })}
          />
        </label>
      )}
      <label>
        Fallback language
        <input
          aria-label="Fallback language"
          value={rule.language}
          disabled={!saved || busy}
          onChange={(e) => change({ language: e.target.value })}
        />
      </label>
      {preview?.signature === signature && (
        <p role="status">
          Next: {reminderTime(preview.next, rule.timezone)} · {rule.timezone}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="reminder-actions">
        <button
          className="primary"
          type="button"
          disabled={!saved || busy || preview?.signature !== signature}
          onClick={() => void save()}
        >
          Save reminder
        </button>
        {saved?.schedule && (
          <button
            type="button"
            disabled={!saved || busy}
            onClick={() => void save(true)}
          >
            Remove reminder
          </button>
        )}
      </div>
    </div>
  );
}

function DigestSettings() {
  const [value, setValue] = useState<Digest | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void workspaceApi<Digest>("reminders/settings").then(
      (answer) =>
        setValue(answer.revision ? answer : { ...answer, timezone: zone() }),
      (e) => setError(e.message),
    );
  }, []);
  async function save() {
    if (!value || busy) return;
    setBusy(true);
    setError("");
    try {
      setValue(await workspaceApi<Digest>("reminders/settings", value));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="reminder-digest">
      <summary>Daily digest settings</summary>
      <div className="reminder-form">
        <label>
          Digest time
          <input
            aria-label="Digest time"
            type="time"
            value={value?.time || "09:00"}
            disabled={!value || busy}
            onChange={(e) =>
              value && setValue({ ...value, time: e.target.value })
            }
          />
        </label>
        <label>
          Digest timezone
          <input
            aria-label="Digest timezone"
            value={value?.timezone || ""}
            disabled={!value || busy}
            onChange={(e) =>
              value && setValue({ ...value, timezone: e.target.value })
            }
          />
        </label>
        <button
          type="button"
          disabled={!value || busy}
          onClick={() => void save()}
        >
          Save digest settings
        </button>
        {error && <p role="alert">{error}</p>}
      </div>
    </details>
  );
}

export function RemindersList({
  onReview,
  onEdit,
}: {
  onReview: (item: ReminderItem) => void;
  onEdit: (item: ReminderItem) => void;
}) {
  const [items, setItems] = useState<ReminderItem[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const alive = useRef(true);
  async function reload() {
    try {
      const value = await workspaceApi<{ items: ReminderItem[] }>("reminders");
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
    void reload();
    return () => {
      alive.current = false;
    };
  }, []);
  useChanges(() => void reload(), 60000);
  async function dismiss(item: ReminderItem) {
    if (!item.occurrence || busy) return;
    setBusy(item.occurrence.id);
    try {
      await workspaceApi(
        `${reminderPath(item.account_id, item.peer)}/dismiss`,
        { occurrence: item.occurrence.id },
      );
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="reminders-list" aria-label="Reminders">
      <h2>Reminders</h2>
      <p className="reminder-scope">All accounts</p>
      <DigestSettings />
      {error && <p role="alert">{error}</p>}
      {items === null && <p>Loading reminders…</p>}
      {items?.length === 0 && <p>No reminders.</p>}
      {[true, false].map((pending) => {
        const rows =
          items?.filter((item) => !!item.occurrence === pending) || [];
        return rows.length ? (
          <section
            key={String(pending)}
            aria-label={pending ? "Pending reminders" : "Upcoming reminders"}
          >
            <h3>{pending ? "Pending" : "Upcoming"}</h3>
            {rows.map((item, index) => (
              <article
                className="reminder-row"
                key={`${item.account_id}/${item.peer}/${index}`}
              >
                <strong>{item.title}</strong>
                <span>{item.account}</span>
                {item.status !== "active" ? (
                  <p>
                    {item.status === "paused"
                      ? "Paused: Automatic suggestions is Off."
                      : "Unavailable: sign in to this account."}
                  </p>
                ) : (
                  <>
                    {item.occurrence && (
                      <p>
                        Due:{" "}
                        {reminderTime(
                          item.occurrence.due_at,
                          item.schedule?.timezone || "UTC",
                        )}
                      </p>
                    )}
                    <p>
                      Next:{" "}
                      {reminderTime(
                        item.next_at,
                        item.schedule?.timezone || "UTC",
                      )}{" "}
                      · {item.schedule?.timezone}
                    </p>
                    {!item.ai_enabled && <p>AI Off: generation is paused.</p>}
                  </>
                )}
                <div className="reminder-actions">
                  {item.occurrence && (
                    <>
                      <button
                        type="button"
                        disabled={!!busy || !!error || item.status !== "active"}
                        onClick={() => onReview(item)}
                      >
                        Review reminder
                      </button>
                      <button
                        type="button"
                        disabled={!!busy || !!error || item.status !== "active"}
                        onClick={() => void dismiss(item)}
                      >
                        Dismiss
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    disabled={!!error || item.status === "unavailable"}
                    onClick={() => onEdit(item)}
                  >
                    Edit schedule
                  </button>
                </div>
              </article>
            ))}
          </section>
        ) : null;
      })}
    </div>
  );
}
