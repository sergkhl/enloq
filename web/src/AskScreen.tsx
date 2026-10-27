import React, { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import type { components } from "./api-schema";
import { chatApi } from "./conversation-api";
import { SourceLinks } from "./DailyBriefings";
import { Note, ScreenFrame } from "./screenUi";
import { shownAnswer } from "../../shared/ask";
import { dayKey, PARTLY_SUPPORTED } from "../../shared/groupBriefings";
import "./briefings.css";
import "./ask.css";

type AskView = components["schemas"]["AskView"];

// The longest question the service takes (ADR 59).
const LONGEST = 500;

// Ask this group (ADR 59 Q1), as the app's Ask screen, over the group it was
// opened from: the question field at the bottom and the answer above it with
// its source links. The answer lives only on this screen (Q9): a source link
// hides it while the group shows that original, Back from there shows it
// again, and its own Back drops it.
export function AskScreen({
  account,
  peer,
  timezone,
  hidden,
  onBack,
  onSource,
}: {
  account: number;
  peer: string;
  // The service's, whose calendar day the not-found line's "today" is.
  timezone: string;
  hidden: boolean;
  onBack: () => void;
  onSource: (source: number) => void;
}) {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<AskView | null>(null);
  const [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const shown = answer && shownAnswer(answer, dayKey(new Date(), timezone));
  const ready = !!question.trim() && !asking;

  // Asking again replaces the answer. The service answers one question at a
  // time per account and says why it refuses one.
  async function ask() {
    if (!ready) return;
    setAsking(true);
    setAnswer(null);
    setError("");
    try {
      const value = await chatApi<AskView>(account, peer, "/ask", {
        question: question.trim(),
      });
      if (alive.current) setAnswer(value);
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setAsking(false);
    }
  }

  return (
    <div hidden={hidden}>
      <ScreenFrame
        title="Ask this group"
        onBack={onBack}
        footer={
          <footer className="ask-bar">
            <textarea
              aria-label="Question"
              maxLength={LONGEST}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
            />
            <button
              type="button"
              className="send-button"
              aria-label="Ask"
              disabled={!ready}
              onClick={() => void ask()}
            >
              <Send size={18} aria-hidden />
            </button>
          </footer>
        }
      >
        <div className="ask-answer" aria-live="polite">
          {asking && <p className="ask-status">Answering…</p>}
          {shown?.line && <p>{shown.line}</p>}
          {!!shown?.statements.length && (
            <div className="briefing-text">
              {shown.statements.map((statement, index) => (
                <p key={index}>
                  {statement.text}
                  {/* Q6: as a briefing topic's line (DailyBriefings). */}
                  {!statement.supported && (
                    <>
                      <br />
                      <small className="briefing-support">
                        {PARTLY_SUPPORTED}
                      </small>
                    </>
                  )}{" "}
                  <SourceLinks
                    citations={statement.citations}
                    onCite={onSource}
                  />
                </p>
              ))}
            </div>
          )}
        </div>
        {error && <Note>{error}</Note>}
      </ScreenFrame>
    </div>
  );
}
