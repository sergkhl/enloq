import React, { useEffect, useId, useRef, useState } from "react";
import { Bot, ChevronRight } from "lucide-react";
import { chatApi, type GroupProcessing } from "./conversation-api";
import type { Emotion } from "./Emotion";
import type { useGroupProcessing } from "./GroupProcessingControl";
import type { useHideBots } from "./HideBots";
import { MemoryPanel } from "./MemoryPanel";
import { useMe } from "./me";
import type { useReadingView } from "./ReadingView";
import { useSettings } from "./SettingsScreen";
import {
  ChoiceRow,
  Explanation,
  LinkRow,
  Note,
  ScreenFrame,
  Section,
  SwitchRow,
} from "./screenUi";
import { TraitsPanel } from "./TraitsPanel";
import {
  ACCOUNT_OFF,
  AI_CONTROLS,
  ASK_THIS_GROUP,
  HIDE_BOTS,
  PRIVATE_CONTROLS,
  answered,
  chatWrite,
  groupNote,
  hasSwitch,
  robotLabel,
  serverNote,
  switchState,
  type AiControl,
  type ChatAi,
} from "../../shared/aiMenu";
import { emotionDetails } from "../../shared/emotion";
import { controlGuidance, guidanceDetail } from "../../shared/guidance";
import { EXPLAIN } from "../../shared/settings";
import {
  SUGGESTION_STYLES,
  type SuggestionStyle,
} from "../../shared/suggestions";
import "./aiMenu.css";

// A chat's AI Menu in the web, as in the app (ADR 62, ADR 44): the robot at
// the header's end opens it as a bottom sheet. Its first row is AI view, the
// global Reading View; a private chat's controls follow, and a group's Group
// AI processing, Ask this group and Hide bots. A tap on a switch flips it; a
// tap on its row opens the control's page (Q16).

type Reading = ReturnType<typeof useReadingView>;

// The robot: filled while AI work can run in the chat, an outline otherwise,
// and its name says which (Q14).
export function RobotButton({
  ai,
  onOpen,
}: {
  ai: ChatAi | null | undefined;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="robot-button"
      data-on={ai?.running ? "true" : "false"}
      aria-label={robotLabel(ai)}
      aria-haspopup="dialog"
      onClick={onOpen}
    >
      <Bot size={20} aria-hidden />
    </button>
  );
}

// A private chat's switches and Style as the menu shows them: the snapshot's,
// or a change's answer until the conversation is read again after it.
export function useChatAi(
  account: number,
  peer: string,
  ai: ChatAi | null | undefined,
  refresh: () => Promise<void>,
) {
  const [answer, setAnswer] = useState<ChatAi | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const shown = answer ?? ai ?? null;
  async function change(control: AiControl, value: boolean | SuggestionStyle) {
    if (!shown || busy) return;
    const write = chatWrite(control, shown, value);
    if (!write) return;
    setBusy(true);
    setProblem("");
    try {
      const reply = await chatApi<{
        automatic?: boolean;
        style?: SuggestionStyle;
        enabled?: boolean;
      }>(account, peer, write.suffix, write.body);
      setAnswer(answered(shown, control, reply));
      // The read after the change carries it from here.
      await refresh();
    } catch (failure) {
      setProblem((failure as Error).message);
    } finally {
      setAnswer(null);
      setBusy(false);
    }
  }
  return { ai: shown, busy, problem, change };
}

// The sheet over the conversation, closed by Escape or a tap outside it.
function Sheet({
  onClose,
  children,
}: {
  onClose: () => void;
  children: React.ReactNode;
}) {
  const sheet = useRef<HTMLDivElement>(null);
  useEffect(() => {
    sheet.current?.querySelector<HTMLElement>("button")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div
        ref={sheet}
        className="sheet-bottom ai-menu"
        role="dialog"
        aria-modal="true"
        aria-label="AI Menu"
        onClick={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

// One control: its name and line open its page, and its switch, when it has
// one, flips it in place.
function MenuRow({
  label,
  detail,
  line,
  notes = [],
  value,
  disabled,
  onToggle,
  onOpen,
}: {
  label: string;
  // What it holds, such as Style's choice.
  detail?: string;
  line?: string;
  // Why it can't run, under it.
  notes?: (string | null | undefined)[];
  // Null for a row without a switch.
  value: boolean | null;
  disabled: boolean;
  onToggle?: () => void;
  onOpen?: () => void;
}) {
  const described = useId();
  const shownNotes = notes.filter(Boolean) as string[];
  const text = (
    <>
      <span className="ai-row-label">
        {label}
        {detail && <span className="ai-row-detail">{` · ${detail}`}</span>}
      </span>
      <span id={described} className="ai-row-lines">
        {line && <span className="ai-row-line">{line}</span>}
        {shownNotes.map((note) => (
          <span key={note} className="ai-row-note">
            {note}
          </span>
        ))}
      </span>
    </>
  );
  return (
    <div className="ai-row">
      {onOpen ? (
        <button
          type="button"
          className="ai-row-open"
          aria-label={detail ? `${label}, ${detail}` : label}
          aria-describedby={described}
          onClick={onOpen}
        >
          {text}
        </button>
      ) : (
        <div className="ai-row-open">{text}</div>
      )}
      {value !== null && (
        <button
          type="button"
          role="switch"
          className="switch-line ai-switch"
          aria-label={label}
          aria-checked={value}
          disabled={disabled}
          onClick={onToggle}
        >
          <span className="switch-knob" aria-hidden />
        </button>
      )}
      {onOpen ? (
        <button
          type="button"
          className="ai-row-more"
          tabIndex={-1}
          aria-hidden
          onClick={onOpen}
        >
          <ChevronRight size={18} />
        </button>
      ) : (
        // A row without a page keeps its switch in line with the others'.
        <span className="ai-row-more" aria-hidden />
      )}
    </div>
  );
}

function ViewRow({
  reading,
  onOpen,
}: {
  reading: Reading;
  onOpen: () => void;
}) {
  return (
    <MenuRow
      label={AI_CONTROLS.view.label}
      line={AI_CONTROLS.view.line}
      value={reading.view === "ai"}
      disabled={reading.busy}
      onToggle={() =>
        void reading.choose(reading.view === "ai" ? "originals" : "ai")
      }
      onOpen={onOpen}
    />
  );
}

// A private chat's AI Menu (Q15): AI view, then Automatic suggestions,
// Style, Emotion, Soften hostile messages, Voice and images and Remember this
// chat, with the notes saying why nothing runs.
export function PrivateAiMenu({
  ai,
  reading,
  onOpen,
  onClose,
}: {
  ai: ReturnType<typeof useChatAi>;
  reading: Reading;
  onOpen: (control: AiControl) => void;
  onClose: () => void;
}) {
  const style = SUGGESTION_STYLES.find((item) => item.value === ai.ai?.style);
  return (
    <Sheet onClose={onClose}>
      <ViewRow reading={reading} onOpen={() => onOpen("view")} />
      {PRIVATE_CONTROLS.map((control) => {
        const value = switchState(control, ai.ai);
        return (
          <MenuRow
            key={control}
            label={AI_CONTROLS[control].label}
            detail={control === "style" ? style?.label : undefined}
            line={
              control === "style"
                ? style?.explanation
                : AI_CONTROLS[control].line
            }
            notes={[serverNote(control, ai.ai)]}
            value={hasSwitch(control) ? (value ?? false) : null}
            disabled={value === null || ai.busy}
            onToggle={() => void ai.change(control, !value)}
            onOpen={() => onOpen(control)}
          />
        );
      })}
      {ai.ai && !ai.ai.account && <p className="sheet-note">{ACCOUNT_OFF}</p>}
      {(ai.problem || reading.error) && (
        <p className="sheet-note error" role="alert">
          {ai.problem || reading.error}
        </p>
      )}
    </Sheet>
  );
}

// A group's AI Menu (Q13): AI view, Group AI processing with its notes, Ask
// this group while it is On, and Hide bots; a retained group's holds AI view
// and Hide bots.
export function GroupAiMenu({
  processing,
  accountEnabled,
  hideBots,
  reading,
  onOpen,
  onAsk,
  onClose,
}: {
  // Null for a retained group, which has no Group AI processing (ADR 34).
  processing: ReturnType<typeof useGroupProcessing> | null;
  accountEnabled: boolean | null;
  hideBots: ReturnType<typeof useHideBots>;
  reading: Reading;
  onOpen: (control: AiControl) => void;
  onAsk: () => void;
  onClose: () => void;
}) {
  const refusal = processing?.error || hideBots.error || reading.error;
  return (
    <Sheet onClose={onClose}>
      <ViewRow reading={reading} onOpen={() => onOpen("view")} />
      {processing && (
        <MenuRow
          label={AI_CONTROLS.group.label}
          line={AI_CONTROLS.group.line}
          notes={[groupNote(processing.value, accountEnabled)]}
          value={processing.value?.processing_enabled ?? false}
          disabled={processing.value === null || processing.busy}
          onToggle={() => void processing.toggle()}
          onOpen={() => onOpen("group")}
        />
      )}
      {processing?.value?.processing_enabled && (
        <MenuRow
          label={ASK_THIS_GROUP}
          value={null}
          disabled={false}
          onOpen={onAsk}
        />
      )}
      <MenuRow
        label={HIDE_BOTS.label}
        line={HIDE_BOTS.line}
        value={hideBots.hideBots}
        disabled={hideBots.busy}
        onToggle={() => void hideBots.choose(!hideBots.hideBots)}
      />
      {refusal && (
        <p className="sheet-note error" role="alert">
          {refusal}
        </p>
      )}
    </Sheet>
  );
}

// A control's page (Q16): its switch or choice with its explanation, its
// panels (Q12) and, for the owner, the Processing Guidance fields it uses,
// each for all chats (Q11, Q17). It covers the conversation, which keeps
// reading beneath it.
export function AiPage({
  control,
  account,
  peer,
  ai,
  reading,
  emotion,
  timezone,
  processing,
  accountEnabled,
  onField,
  onBack,
}: {
  control: AiControl;
  account: number;
  peer: string;
  ai: ReturnType<typeof useChatAi>;
  reading: Reading;
  emotion: Emotion | null;
  timezone: string;
  processing: ReturnType<typeof useGroupProcessing> | null;
  accountEnabled: boolean | null;
  // A Guidance field's editor.
  onField: (name: string) => void;
  onBack: () => void;
}) {
  const me = useMe();
  const copy = AI_CONTROLS[control];
  const value = switchState(control, ai.ai);
  return (
    <ScreenFrame title={copy.label} onBack={onBack}>
      {control === "view" ? (
        <Section>
          <SwitchRow
            label={copy.label}
            explanation={copy.explanation}
            checked={reading.view === "ai"}
            disabled={reading.busy}
            onChange={(on) => void reading.choose(on ? "ai" : "originals")}
          />
          {reading.error && <Note>{reading.error}</Note>}
        </Section>
      ) : control === "group" ? (
        <GroupSection processing={processing} accountEnabled={accountEnabled} />
      ) : control === "style" ? (
        <Section>
          <div className="screen-block">
            <Explanation>{copy.explanation}</Explanation>
          </div>
          <ChoiceRow
            label="Style"
            options={SUGGESTION_STYLES}
            value={ai.ai?.style ?? null}
            explanation={
              SUGGESTION_STYLES.find((item) => item.value === ai.ai?.style)
                ?.explanation
            }
            disabled={!ai.ai || ai.busy}
            onChange={(style) => void ai.change("style", style)}
          />
        </Section>
      ) : hasSwitch(control) ? (
        <Section>
          <SwitchRow
            label={copy.label}
            explanation={copy.explanation}
            checked={value ?? false}
            disabled={value === null || ai.busy}
            onChange={(on) => void ai.change(control, on)}
          />
          {serverNote(control, ai.ai) && (
            <Note tone="warning">{serverNote(control, ai.ai)}</Note>
          )}
        </Section>
      ) : (
        <Section>
          <div className="screen-block">
            <Explanation>{copy.explanation}</Explanation>
          </div>
        </Section>
      )}
      {ai.problem && <Note>{ai.problem}</Note>}
      {control === "emotion" && (
        <EmotionSection emotion={emotion} timezone={timezone} />
      )}
      {control === "remember" && (
        // What the person cares about and their traits (ADR 55 Q25).
        <>
          <Section title="Cares about">
            <MemoryPanel account={account} peer={peer} />
          </Section>
          <Section title="Traits">
            <TraitsPanel account={account} peer={peer} />
          </Section>
        </>
      )}
      {me?.role === "owner" && (
        <GuidanceSection control={control} onField={onField} />
      )}
    </ScreenFrame>
  );
}

// A group's Group AI processing (ADR 34) with its notes.
function GroupSection({
  processing,
  accountEnabled,
}: {
  processing: ReturnType<typeof useGroupProcessing> | null;
  accountEnabled: boolean | null;
}) {
  const value: GroupProcessing | null = processing?.value ?? null;
  const note = groupNote(value, accountEnabled);
  return (
    <Section>
      <SwitchRow
        label={AI_CONTROLS.group.label}
        explanation={AI_CONTROLS.group.explanation}
        checked={value?.processing_enabled ?? false}
        disabled={!processing || value === null || processing.busy}
        onChange={() => void processing?.toggle()}
      />
      {note && <Note tone="warning">{note}</Note>}
      {processing?.error && <Note>{processing.error}</Note>}
    </Section>
  );
}

// The emotion's estimate, why, its source and its time (ADR 62 Q12).
function EmotionSection({
  emotion,
  timezone,
}: {
  emotion: Emotion | null;
  timezone: string;
}) {
  const details = emotionDetails(emotion, timezone);
  return (
    <Section title="Latest estimate">
      <div className="screen-block profile-emotion">
        <strong>{details.label}</strong>
        <p>{details.reason}</p>
        <small>
          {details.source}
          {details.time ? ` · ${details.time}` : ""}
        </small>
      </div>
    </Section>
  );
}

// The owner's Processing Guidance fields this control uses, each one text
// for all chats, opening its editor (ADR 62 Q11, Q17). Members have none.
function GuidanceSection({
  control,
  onField,
}: {
  control: AiControl;
  onField: (name: string) => void;
}) {
  const { settings } = useSettings();
  const guidance = settings?.guidance;
  const fields = controlGuidance(control);
  if (!guidance || !fields.length) return null;
  return (
    <Section title="Guidance">
      {fields.map((field) => {
        const current = guidance.fields[field.name] ?? "";
        const edited = current !== (guidance.defaults[field.name] ?? "");
        return (
          <LinkRow
            key={field.name}
            label={field.label}
            detail={guidanceDetail(edited)}
            onOpen={() => onField(field.name)}
          />
        );
      })}
      <div className="screen-block">
        <Explanation>{`Version ${guidance.version}. ${EXPLAIN.guidance}`}</Explanation>
      </div>
    </Section>
  );
}
