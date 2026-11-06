import React, { useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useSettings } from "./SettingsScreen";
import { Explanation, Note, ScreenFrame, SmallButton } from "./screenUi";
import { GUIDANCE_FIELDS, GUIDANCE_LIMIT } from "../../shared/guidance";

const message = (problem: unknown) =>
  problem instanceof Error ? problem.message : String(problem);

// One Processing Guidance field's editor, opened from the AI Menu page of the
// control it shapes (ADR 62 Q11, Q17): the text with its count, Save, Reset to
// default after a confirmation, and the default text. It is one text for all
// chats, with the guidance's version. Leaving with unsaved text asks first.
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
          <Explanation>
            {`For all chats · Version ${settings?.guidance?.version ?? 0}`}
          </Explanation>
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
