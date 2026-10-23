import React, { useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useSettings } from "./SettingsScreen";
import {
  Explanation,
  LinkRow,
  Note,
  ScreenFrame,
  Section,
  SmallButton,
} from "./screenUi";
import {
  GUIDANCE_FIELDS,
  GUIDANCE_GROUPS,
  GUIDANCE_LIMIT,
  PAUSED_ROLES,
} from "../../shared/guidance";
import { EXPLAIN } from "../../shared/settings";

const message = (problem: unknown) =>
  problem instanceof Error ? problem.message : String(problem);

// Guidance, Processing Guidance under Settings → AI (ADR 45, ADR 44 MW4, ADR
// 47 Q12), as the app's screen: its version,
// a notice for each role paused after repeated format failures with Keep and
// Reset guidance, and the 11 fields, each opening its editor.
export function GuidanceScreen({
  onBack,
  onField,
}: {
  onBack: () => void;
  onField: (name: string) => void;
}) {
  const { settings, error, write } = useSettings();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const guidance = settings?.guidance;
  async function act(path: string, body: unknown) {
    setBusy(true);
    setProblem("");
    try {
      await write(path, body);
    } catch (failure) {
      setProblem(message(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <ScreenFrame title="Guidance" onBack={onBack}>
      {!guidance ? (
        <Explanation>
          {error ? "Settings show once the server answers." : "Loading…"}
        </Explanation>
      ) : (
        <>
          <Explanation>{`Version ${guidance.version}. ${EXPLAIN.guidance}`}</Explanation>
          {guidance.paused.map((role) => {
            const paused = PAUSED_ROLES[role];
            const label = paused?.label ?? role;
            return (
              <div
                key={role}
                className="paused-role"
                role="group"
                aria-label={`${label} paused`}
              >
                <p>
                  {`${label} failed the output format three times since the last guidance change, so automatic processing waits. Deliberate Rewrite still works.`}
                </p>
                <div className="button-row">
                  <SmallButton
                    label="Keep"
                    name={`Keep guidance for ${label}`}
                    disabled={busy}
                    onClick={() => void act("guidance/keep", { role })}
                  />
                  <SmallButton
                    label="Reset guidance"
                    name={`Reset guidance for ${label}`}
                    primary
                    disabled={busy}
                    onClick={() =>
                      void act("guidance", {
                        fields: Object.fromEntries(
                          (paused?.fields ?? []).map((field) => [field, null]),
                        ),
                      })
                    }
                  />
                </div>
              </div>
            );
          })}
          {problem && <Note>{problem}</Note>}
          {GUIDANCE_GROUPS.map((group) => (
            <Section key={group.title} title={group.title}>
              {group.fields.map((field) => {
                const current = guidance.fields[field.name] ?? "";
                const edited =
                  current !== (guidance.defaults[field.name] ?? "");
                return (
                  <div key={field.name}>
                    <LinkRow
                      label={field.label}
                      detail={edited ? "Edited" : "Default"}
                      onOpen={() => onField(field.name)}
                    />
                    <p className="guidance-preview">
                      {current.split("\n")[0] || "Empty"}
                    </p>
                  </div>
                );
              })}
            </Section>
          ))}
        </>
      )}
    </ScreenFrame>
  );
}

// One field's editor: the text with its count, Save, Reset to default after a
// confirmation, and the default text. Leaving with unsaved text asks first.
export function GuidanceFieldScreen({
  name,
  onBack,
}: {
  name: string;
  onBack: () => void;
}) {
  const field = GUIDANCE_FIELDS.find((item) => item.name === name);
  const { settings, error, write } = useSettings();
  const current = field ? settings?.guidance?.fields[field.name] : undefined;
  const fallback = (field && settings?.guidance?.defaults[field.name]) ?? "";
  // The unsaved text; null shows the current one, so an untouched field
  // follows changes from other clients.
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [asking, setAsking] = useState<"leave" | "reset" | null>(null);
  const [defaultShown, setDefaultShown] = useState(false);
  const value = text ?? current ?? "";
  const dirty = text !== null && current !== undefined && text !== current;
  useEffect(() => {
    if (!dirty) return;
    const closing = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", closing);
    return () => window.removeEventListener("beforeunload", closing);
  }, [dirty]);
  async function save(fields: Record<string, string | null>) {
    setBusy(true);
    setProblem("");
    try {
      await write("guidance", { fields });
      setText(null);
      setAsking(null);
    } catch (failure) {
      setProblem(message(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <ScreenFrame
      title={field?.label ?? "Guidance"}
      onBack={() => (dirty ? setAsking("leave") : onBack())}
    >
      {!field ? (
        <Note>This guidance field doesn't exist.</Note>
      ) : current === undefined ? (
        <Explanation>
          {error ? "Settings show once the server answers." : "Loading…"}
        </Explanation>
      ) : (
        <>
          <Explanation>{field.explanation}</Explanation>
          {asking === "leave" && (
            <div className="ask">
              <p>{`Discard your changes to ${field.label}?`}</p>
              <div className="button-row">
                <SmallButton
                  label="Keep editing"
                  onClick={() => setAsking(null)}
                />
                <SmallButton
                  label="Discard"
                  primary
                  onClick={() => {
                    setText(null);
                    setAsking(null);
                    onBack();
                  }}
                />
              </div>
            </div>
          )}
          <textarea
            className="guidance-text"
            aria-label={field.label}
            maxLength={GUIDANCE_LIMIT}
            disabled={busy}
            value={value}
            onChange={(event) => {
              setText(event.target.value);
              setProblem("");
            }}
          />
          <small className="guidance-count">{`${value.length}/${GUIDANCE_LIMIT}`}</small>
          {field.name === "faithfulness" && !value.trim() && (
            <Note tone="warning">
              Without faithfulness rules, rewrites may drop or change facts,
              requests, refusals or conditions.
            </Note>
          )}
          {asking === "reset" ? (
            <div className="ask">
              <p>{`Reset ${field.label} to its default? Your text is replaced by the shipped default.`}</p>
              <div className="button-row">
                <SmallButton
                  label="Cancel"
                  disabled={busy}
                  onClick={() => setAsking(null)}
                />
                <SmallButton
                  label="Reset"
                  name="Confirm reset"
                  primary
                  disabled={busy}
                  onClick={() => void save({ [field.name]: null })}
                />
              </div>
            </div>
          ) : (
            <div className="button-row">
              <SmallButton
                label="Reset to default"
                disabled={busy || current === fallback}
                onClick={() => setAsking("reset")}
              />
              <SmallButton
                label="Save"
                primary
                disabled={busy || !dirty}
                onClick={() => void save({ [field.name]: value })}
              />
            </div>
          )}
          {problem && <Note>{problem}</Note>}
          <button
            type="button"
            className="budget-history"
            aria-expanded={defaultShown}
            onClick={() => setDefaultShown((shown) => !shown)}
          >
            Default text
            {defaultShown ? (
              <ChevronUp size={16} aria-hidden />
            ) : (
              <ChevronDown size={16} aria-hidden />
            )}
          </button>
          {defaultShown && (
            <p className="guidance-default">{fallback || "Empty"}</p>
          )}
        </>
      )}
    </ScreenFrame>
  );
}
