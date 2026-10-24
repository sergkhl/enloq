import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Bell,
  ChevronDown,
  ChevronUp,
  Cpu,
  Lock,
  Minus,
  Pencil,
  Plus,
  Users,
  Wallet,
} from "lucide-react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { useChanges } from "./events";
import { useMe } from "./me";
import { NotificationSettings } from "./Notifications";
import { usePeople } from "./PeopleScreen";
import {
  Explanation,
  LinkRow,
  MenuRow,
  Note,
  ScreenFrame,
  Section,
  SmallButton,
  SwitchRow,
} from "./screenUi";
import { messageDate, messageTime } from "../../shared/messageTime";
import {
  BUDGETS,
  EXPLAIN,
  budgetAmount,
  characterCount,
  decisionModelNotice,
  dollars,
  resetLabel,
  type BudgetFamily,
} from "../../shared/settings";
import { PEOPLE, waitingLabel } from "../../shared/people";

export type Settings = components["schemas"]["Settings"];
type BudgetState = components["schemas"]["BudgetState"];
type Write = (path: string, body: unknown) => Promise<void>;

const message = (problem: unknown) =>
  problem instanceof Error ? problem.message : String(problem);

// The service-wide settings, as the app reads them (mobile/src/settings.ts):
// every write is reconciled through a fresh read, and a shared change from
// any client rereads them.
export function useSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const load = useCallback(async () => {
    const mine = ++generation.current;
    try {
      const value = await workspaceApi<Settings>("settings");
      if (mine !== generation.current) return;
      setSettings(value);
      setError("");
    } catch (problem) {
      if (mine === generation.current) setError(message(problem));
    }
  }, []);
  useEffect(() => {
    void load();
    return () => {
      ++generation.current;
    };
  }, [load]);
  useChanges((change) => {
    if (change.all || change.shared.length) void load();
  });
  const write = useCallback<Write>(
    async (path, body) => {
      await workspaceApi<Settings>(`settings/${path}`, body);
      await load();
    },
    [load],
  );
  return { settings, error, write };
}

type NotificationState = components["schemas"]["NotificationStatus"];

// The People row's waiting count, read only by the owner.
function PeopleRow({ onOpen }: { onOpen: () => void }) {
  const { people } = usePeople();
  const waiting = people?.requests.length ?? 0;
  return (
    <MenuRow
      icon={Users}
      label={PEOPLE.title}
      badge={waitingLabel(waiting) || undefined}
      detail={people ? String(people.members.length) : undefined}
      onOpen={onOpen}
    />
  );
}

// Settings behind the list header's gear, in the app's design (ADR 45, ADR 44
// MW4), as a menu since ADR 47 Q6: Notifications, Budget, AI and Privacy, each
// with its icon and what it holds, then Disconnect for this browser. Since
// ADR 48 the owner also has People, and a member has no AI row but Leave.
export function SettingsScreen({
  onBack,
  onOpen,
}: {
  onBack: () => void;
  onOpen: (
    page: "notifications" | "budget" | "ai" | "privacy" | "people",
  ) => void;
}) {
  const { settings } = useSettings();
  const me = useMe();
  const owner = me?.role === "owner";
  const [trusted, setTrusted] = useState<number | null>(null);
  const [notifications, setNotifications] = useState<boolean | null>(null);
  useEffect(() => {
    void workspaceApi<unknown[]>("auth/browsers")
      .then((rows) => setTrusted(rows.length))
      .catch(() => undefined);
    void workspaceApi<NotificationState>("notifications")
      .then((status) =>
        setNotifications(
          !!status.push &&
            "Notification" in window &&
            Notification.permission === "granted",
        ),
      )
      .catch(() => undefined);
  }, []);
  return (
    <ScreenFrame title="Settings" onBack={onBack}>
      <Section>
        <MenuRow
          icon={Bell}
          label="Notifications"
          detail={
            notifications === null ? undefined : notifications ? "On" : "Off"
          }
          onOpen={() => onOpen("notifications")}
        />
        <MenuRow
          icon={Wallet}
          label="Budget"
          detail={
            settings
              ? `${dollars(settings.budget.spent)} of ${dollars(settings.budget.limit)}`
              : undefined
          }
          onOpen={() => onOpen("budget")}
        />
        {owner && (
          <MenuRow
            icon={Cpu}
            label="AI"
            badge={settings?.guidance?.paused.length ? "Paused" : undefined}
            onOpen={() => onOpen("ai")}
          />
        )}
        <MenuRow
          icon={Lock}
          label="Privacy"
          detail={trusted === null ? undefined : String(trusted)}
          onOpen={() => onOpen("privacy")}
        />
        {owner && <PeopleRow onOpen={() => onOpen("people")} />}
      </Section>
      {me?.role === "member" && <LeaveEnloq />}
      <Disconnect />
    </ScreenFrame>
  );
}

// Settings → Notifications: this browser's switch, sound and test.
export function NotificationsScreen({ onBack }: { onBack: () => void }) {
  return (
    <ScreenFrame title="Notifications" onBack={onBack}>
      <Section title="This browser">
        <NotificationSettings />
      </Section>
    </ScreenFrame>
  );
}

// Settings → Budget: both budgets with today's spending. A budget message's
// link opens it with that budget scrolled into view and outlined (ADR 45 Q8).
export function BudgetScreen({
  section,
  timezone,
  onBack,
}: {
  section: "daily" | "group" | null;
  timezone: string;
  onBack: () => void;
}) {
  const { settings, error, write } = useSettings();
  // A member sees their budgets; only the owner sets them (ADR 48 Q2).
  const me = useMe();
  const readOnly = me?.role !== "owner";
  const [outlined, setOutlined] = useState(section);
  const daily = useRef<HTMLDivElement>(null);
  const group = useRef<HTMLDivElement>(null);
  const scrolled = useRef(false);
  useEffect(() => {
    if (!section || scrolled.current || !settings) return;
    const target = (section === "group" ? group : daily).current;
    if (!target) return;
    scrolled.current = true;
    target.scrollIntoView({ block: "start" });
  }, [section, settings]);
  useEffect(() => {
    if (!outlined) return;
    const timer = setTimeout(() => setOutlined(null), 2500);
    return () => clearTimeout(timer);
  }, [outlined]);
  return (
    <ScreenFrame title="Budget" onBack={onBack}>
      {settings ? (
        <>
          {me?.role === "member" && (
            <Explanation>{PEOPLE.memberBudget}</Explanation>
          )}
          <Section sectionRef={daily}>
            <BudgetCard
              family="daily"
              state={settings.budget}
              outlined={outlined === "daily"}
              timezone={timezone}
              write={write}
              readOnly={readOnly}
            />
          </Section>
          <Section sectionRef={group}>
            <BudgetCard
              family="group_briefing"
              state={settings.group_budget}
              outlined={outlined === "group"}
              timezone={timezone}
              write={write}
              readOnly={readOnly}
            />
          </Section>
        </>
      ) : error ? (
        <Note tone="warning">Settings show once the server answers.</Note>
      ) : (
        <Explanation>Loading…</Explanation>
      )}
    </ScreenFrame>
  );
}

// Settings → AI (ADR 47 Q12): the Processing Character Threshold, the
// Concurrency Limit, the Chat Memory and suggestions switches, and Guidance;
// above them, while it lasts, a decision-model outage (ADR 49 Q14).
export function AiScreen({
  timezone,
  onBack,
  onGuidance,
}: {
  timezone: string;
  onBack: () => void;
  onGuidance: () => void;
}) {
  const { settings, error, write } = useSettings();
  const outage = settings?.decision_model;
  return (
    <ScreenFrame title="AI" onBack={onBack}>
      {outage && !outage.available && outage.since ? (
        <Note tone="warning">
          {decisionModelNotice(outage.since, timezone)}
        </Note>
      ) : null}
      {settings ? (
        <Section>
          <MinimumCharacters
            value={settings.processing_threshold}
            write={write}
          />
          <ModelCalls value={settings.concurrency} write={write} />
          <SettingSwitch
            label="Memory"
            explanation={EXPLAIN.memory}
            route="memory"
            value={settings.memory}
            write={write}
          />
          <SettingSwitch
            label="Automatic suggestions"
            explanation={EXPLAIN.suggestions}
            route="suggestions"
            value={settings.suggestions}
            write={write}
          />
          <LinkRow
            label="Guidance"
            detail={`Version ${settings.guidance?.version ?? 0}`}
            badge={settings.guidance?.paused.length ? "Paused" : undefined}
            onOpen={onGuidance}
          />
        </Section>
      ) : error ? (
        <Note tone="warning">Settings show once the server answers.</Note>
      ) : (
        <Explanation>Loading…</Explanation>
      )}
    </ScreenFrame>
  );
}

function BudgetCard({
  family,
  state,
  outlined,
  timezone,
  write,
  readOnly = false,
}: {
  family: BudgetFamily;
  state: BudgetState;
  outlined: boolean;
  timezone: string;
  write: Write;
  readOnly?: boolean;
}) {
  const copy = BUDGETS[family];
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState(false);
  const reset = resetLabel(state.resets_at, timezone);
  const share =
    state.limit > 0
      ? Math.min(1, state.spent / state.limit)
      : state.reached
        ? 1
        : 0;
  const typed = budgetAmount(text);
  async function save() {
    if (typed === null) {
      setProblem("$0 to $10, in whole cents.");
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      await write(copy.route, { limit_usd: typed });
      setEditing(false);
    } catch (failure) {
      setProblem(message(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      className={`budget-card${outlined ? " outlined" : ""}`}
      aria-label={copy.title}
      role="group"
    >
      <strong>{copy.title}</strong>
      <Explanation>{copy.explanation}</Explanation>
      <div
        className="budget-meter"
        role="progressbar"
        aria-label={`${copy.title} spent today`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(share * 100)}
      >
        <span
          className={state.reached || share >= 0.8 ? "high" : ""}
          style={{ width: `${Math.round(share * 100)}%` }}
        />
      </div>
      <p className="budget-spent">
        <strong>{dollars(state.spent, 4)}</strong> of {dollars(state.limit)}{" "}
        today · {state.calls} {state.calls === 1 ? "call" : "calls"}
      </p>
      {state.reached ? (
        <Note tone="warning">{copy.reached(reset)}</Note>
      ) : (
        <Explanation>Resets {reset}</Explanation>
      )}
      {readOnly ? null : editing ? (
        <>
          <div className="budget-editor">
            <span>$</span>
            <input
              aria-label={`${copy.title} per day in dollars`}
              autoFocus
              inputMode="decimal"
              value={text}
              disabled={busy}
              onChange={(event) => {
                setText(event.target.value);
                setProblem("");
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") void save();
              }}
            />
            <SmallButton
              label="Cancel"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setProblem("");
              }}
            />
            <SmallButton
              label="Save"
              primary
              disabled={busy}
              onClick={() => void save()}
            />
          </div>
          {typed === "0.00" && <Note tone="warning">{copy.zero}</Note>}
        </>
      ) : (
        <div>
          <SmallButton
            label="Change limit"
            name={`Change ${copy.title} limit`}
            onClick={() => {
              setText((state.limit / 1e9).toFixed(2));
              setProblem("");
              setEditing(true);
            }}
          />
        </div>
      )}
      {problem && <Note>{problem}</Note>}
      {state.changes.length > 0 && (
        <>
          <button
            type="button"
            className="budget-history"
            aria-label={`Recent changes to the ${copy.title}`}
            aria-expanded={history}
            onClick={() => setHistory((value) => !value)}
          >
            Recent changes
            {history ? (
              <ChevronUp size={16} aria-hidden />
            ) : (
              <ChevronDown size={16} aria-hidden />
            )}
          </button>
          {history &&
            state.changes.map((change, index) => {
              const at = Number(change.at);
              return (
                <small key={index} className="budget-change">
                  {`${messageDate(at, timezone)} ${messageTime(at, timezone)} · ${dollars(Number(change.old))} → ${dollars(Number(change.new))}`}
                </small>
              );
            })}
        </>
      )}
    </div>
  );
}

function MinimumCharacters({ value, write }: { value: number; write: Write }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  async function save() {
    const limit = characterCount(text);
    if (limit === null) {
      setProblem("A whole number from 0 to 10,000.");
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      await write("processing-threshold", { limit });
      setEditing(false);
    } catch (failure) {
      setProblem(message(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="screen-block">
      {editing ? (
        <>
          <div className="budget-editor">
            <span className="screen-row-label">Minimum characters</span>
            <input
              aria-label="Minimum characters"
              autoFocus
              inputMode="numeric"
              value={text}
              disabled={busy}
              onChange={(event) => {
                setText(event.target.value);
                setProblem("");
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") void save();
              }}
            />
          </div>
          <div className="button-row">
            <SmallButton
              label="Cancel"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setProblem("");
              }}
            />
            <SmallButton
              label="Save"
              primary
              disabled={busy}
              onClick={() => void save()}
            />
          </div>
        </>
      ) : (
        <button
          type="button"
          className="screen-row"
          aria-label={`Minimum characters, ${value}. Change`}
          onClick={() => {
            setText(String(value));
            setProblem("");
            setEditing(true);
          }}
        >
          <span className="screen-row-label">Minimum characters</span>
          <span className="screen-row-detail">
            {value.toLocaleString("en-US")}
          </span>
          <Pencil size={16} aria-hidden />
        </button>
      )}
      <Explanation>{EXPLAIN.minimum}</Explanation>
      {problem && <Note>{problem}</Note>}
    </div>
  );
}

function ModelCalls({ value, write }: { value: number; write: Write }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  async function change(limit: number) {
    setBusy(true);
    setProblem("");
    try {
      await write("concurrency", { limit });
    } catch (failure) {
      setProblem(message(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="screen-block">
      <div className="stepper-row">
        <span className="screen-row-label">Model calls at once</span>
        <button
          type="button"
          className="stepper"
          aria-label="Fewer model calls at once"
          disabled={busy || value <= 1}
          onClick={() => void change(value - 1)}
        >
          <Minus size={18} aria-hidden />
        </button>
        <span
          className="stepper-value"
          aria-label={`${value} model calls at once`}
        >
          {value}
        </span>
        <button
          type="button"
          className="stepper"
          aria-label="More model calls at once"
          disabled={busy || value >= 8}
          onClick={() => void change(value + 1)}
        >
          <Plus size={18} aria-hidden />
        </button>
      </div>
      <Explanation>{EXPLAIN.calls}</Explanation>
      {problem && <Note>{problem}</Note>}
    </div>
  );
}

function SettingSwitch({
  label,
  explanation,
  route,
  value,
  write,
}: {
  label: string;
  explanation: string;
  route: string;
  value: boolean;
  write: Write;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  return (
    <div>
      <SwitchRow
        label={label}
        explanation={explanation}
        checked={value}
        disabled={busy}
        onChange={async (enabled) => {
          setBusy(true);
          setProblem("");
          try {
            await write(route, { enabled });
          } catch (failure) {
            setProblem(message(failure));
          } finally {
            setBusy(false);
          }
        }}
      />
      {problem && <Note>{problem}</Note>}
    </div>
  );
}

// Disconnect for this browser (ADR 43 Q2, ADR 45 Q4): its trust ends on the
// server, and the page shows the connection screen.
// A member leaves (ADR 48 Q9): Enloq signs out of their Telegram accounts
// here and deletes everything it keeps for them, this browser's trust too.
function LeaveEnloq() {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  async function leave() {
    if (!window.confirm(PEOPLE.leaveConfirm)) return;
    setBusy(true);
    setProblem("");
    try {
      const left = await workspaceApi<{ confirmed: boolean }>(
        "members/me/leave",
        { confirm: true },
      );
      if (!left.confirmed) window.alert(PEOPLE.unconfirmed);
      window.dispatchEvent(new Event("browser-auth-lost"));
    } catch (failure) {
      setProblem(message(failure));
      setBusy(false);
    }
  }
  return (
    <div className="disconnect">
      <button
        type="button"
        className="disconnect-row"
        disabled={busy}
        onClick={() => void leave()}
      >
        {PEOPLE.leave}
      </button>
      {problem && <Note>{problem}</Note>}
    </div>
  );
}

function Disconnect() {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const host = window.location.host;
  async function leave() {
    setBusy(true);
    setProblem("");
    try {
      await workspaceApi("auth/disconnect", {});
      window.dispatchEvent(new Event("browser-auth-lost"));
    } catch (failure) {
      setProblem(message(failure));
      setBusy(false);
    }
  }
  return (
    <div className="disconnect">
      {confirming ? (
        <>
          <p>
            Disconnect from {host}? This browser forgets its trust, and pairs
            again to come back.
          </p>
          <div className="button-row">
            <SmallButton
              label="Cancel"
              disabled={busy}
              onClick={() => setConfirming(false)}
            />
            <button
              type="button"
              className="small-button danger"
              disabled={busy}
              onClick={() => void leave()}
            >
              Disconnect
            </button>
          </div>
        </>
      ) : (
        <button
          type="button"
          className="disconnect-row"
          onClick={() => setConfirming(true)}
        >
          Disconnect from {host}
        </button>
      )}
      {problem && <Note>{problem}</Note>}
    </div>
  );
}
