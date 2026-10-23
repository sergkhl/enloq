import React, { useCallback, useEffect, useRef, useState } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { Explanation, Note, ScreenFrame, SmallButton } from "./screenUi";

type Pairing = components["schemas"]["PairingStatus"];

const ENDED = ["complete", "cancelled", "expired"];

// Pair a phone (ADR 47 Q1), under Settings → Privacy, as the app's
// (mobile/app/settings/pair.tsx): this browser shows a QR code that Enloq on
// a new phone scans; the phone then shows a six-digit code, this screen shows
// the same code with the phone's name, and Trust finishes it within the
// attempt's two minutes. Only Enloq can claim the code (Q10). Leaving the
// screen cancels an unfinished attempt.
export function PairPhoneScreen({ onBack }: { onBack: () => void }) {
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);
  const open = useRef<string | null>(null);

  const start = useCallback(async () => {
    setProblem("");
    setCopied(false);
    setPairing(null);
    try {
      const started = await workspaceApi<Pairing>("auth/client-pairings", {});
      open.current = started.id;
      setPairing(started);
    } catch (error) {
      setProblem((error as Error).message);
    }
  }, []);

  useEffect(() => {
    void start();
    return () => {
      const id = open.current;
      open.current = null;
      if (id)
        void workspaceApi(`auth/client-pairings/${id}`, {
          action: "cancel",
        }).catch(() => undefined);
    };
  }, [start]);

  // The claim, the approval and the phone's completion arrive by polling.
  useEffect(() => {
    if (!pairing || ENDED.includes(pairing.state)) return;
    const id = pairing.id;
    const timer = setInterval(() => {
      setNow(Date.now() / 1000);
      void workspaceApi<Pairing>(`auth/client-pairings/${id}`)
        .then((next) => {
          if (open.current !== id) return;
          if (next.state === "complete") open.current = null;
          setPairing(next);
        })
        .catch((error) => setProblem((error as Error).message));
    }, 1000);
    return () => clearInterval(timer);
  }, [pairing?.id, pairing?.state]);

  async function decide(action: "approve" | "cancel") {
    if (!pairing || busy) return;
    setBusy(true);
    setProblem("");
    try {
      const next = await workspaceApi<Pairing>(
        `auth/client-pairings/${pairing.id}`,
        { action, code: pairing.code },
      );
      if (ENDED.includes(next.state)) open.current = null;
      setPairing(next);
    } catch (error) {
      setProblem((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const left = Math.max(0, Math.ceil((pairing?.expires_at ?? 0) - now));
  const link = pairing ? `${location.origin}/?connect=${pairing.id}` : "";
  return (
    <ScreenFrame title="Pair a phone" onBack={onBack}>
      {!pairing ? (
        problem ? (
          <>
            <Note>{problem}</Note>
            <SmallButton
              label="Try again"
              primary
              onClick={() => void start()}
            />
          </>
        ) : (
          <Explanation>Starting…</Explanation>
        )
      ) : pairing.state === "waiting" ? (
        <div className="pair-phone">
          <p>
            On the new phone, open Enloq, choose Scan QR code and point it at
            this code.
          </p>
          <div className="pair-qr">
            <img
              alt="Pairing QR code"
              src={`/api/auth/client-pairings/${pairing.id}/qr.svg`}
            />
          </div>
          <Explanation>
            {left
              ? `The code expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}.`
              : "The code expired."}
          </Explanation>
          <Explanation>
            No camera on the new phone? Copy the link and paste it there.
          </Explanation>
          <div className="button-row">
            <SmallButton
              label={copied ? "Link copied" : "Copy link"}
              onClick={() =>
                void navigator.clipboard
                  .writeText(link)
                  .then(() => setCopied(true))
                  .catch(() =>
                    setProblem("Copying is unavailable in this browser."),
                  )
              }
            />
            <SmallButton label="Cancel" onClick={() => void decide("cancel")} />
          </div>
        </div>
      ) : pairing.state === "pending" ? (
        <div className="pair-phone">
          <p>{`${pairing.label} asks to pair with this server.`}</p>
          <output className="pairing-code" aria-label="Code to compare">
            {pairing.code}
          </output>
          <p>
            Trust it only if the new phone shows this same code. It will read
            and write in your connected Telegram accounts here.
          </p>
          <div className="button-row">
            <SmallButton
              label="Cancel"
              disabled={busy}
              onClick={() => void decide("cancel")}
            />
            <SmallButton
              label="Trust"
              primary
              disabled={busy}
              onClick={() => void decide("approve")}
            />
          </div>
        </div>
      ) : pairing.state === "approved" ? (
        <Explanation>Finishing on the new phone…</Explanation>
      ) : pairing.state === "complete" ? (
        <div className="pair-phone">
          <p>{`${pairing.label} is paired. It now shows in Privacy.`}</p>
          <SmallButton label="Done" primary onClick={onBack} />
        </div>
      ) : (
        <div className="pair-phone">
          <p>
            {pairing.state === "expired"
              ? "The QR code expired."
              : "Pairing was cancelled."}
          </p>
          <SmallButton
            label="Start again"
            primary
            onClick={() => void start()}
          />
        </div>
      )}
      {pairing && problem && <Note>{problem}</Note>}
    </ScreenFrame>
  );
}
