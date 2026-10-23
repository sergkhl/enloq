import * as Clipboard from "expo-clipboard";
import { Image } from "expo-image";
import { Redirect, router } from "expo-router";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { Refused, Unreachable, type Schemas } from "../../src/api";
import { useConnection } from "../../src/connection";
import { Explanation, RowNote, SettingsPage } from "../../src/settingsUi";
import { usePalette } from "../../src/theme";
import { Body, Button } from "../../src/ui";

type Pairing = Schemas["PairingStatus"];

const ENDED = ["complete", "cancelled", "expired"];

const message = (error: unknown) =>
  error instanceof Refused || error instanceof Unreachable
    ? error.message
    : "Pairing didn't start. Try again.";

// Pair a phone (ADR 47 Q1), under Settings → Privacy: this phone shows a QR
// code that Enloq on a new phone scans; the new phone then shows a six-digit
// code, this screen shows the same code with the phone's name, and Trust
// finishes it within the attempt's two minutes (ADR 38 Q24). Only Enloq can
// claim the code (Q10). Leaving the screen cancels an unfinished attempt.
export default function PairPhoneRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return <PairPhone />;
}

function PairPhone() {
  const { connection, api } = useConnection();
  const palette = usePalette();
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);
  const open = useRef<string | null>(null);
  const credential =
    connection.status === "paired" ? connection.credential : null;

  const start = useCallback(async () => {
    setProblem("");
    setCopied(false);
    setPairing(null);
    try {
      const started = await api<Pairing>("auth/client-pairings", { body: {} });
      open.current = started.id;
      setPairing(started);
    } catch (error) {
      setProblem(message(error));
    }
  }, [api]);

  useEffect(() => {
    void start();
    return () => {
      const id = open.current;
      open.current = null;
      if (id)
        void api(`auth/client-pairings/${id}`, {
          body: { action: "cancel" },
        }).catch(() => undefined);
    };
  }, [api, start]);

  // The claim, the approval and the new phone's completion arrive by polling.
  useEffect(() => {
    if (!pairing || ENDED.includes(pairing.state)) return;
    const id = pairing.id;
    const timer = setInterval(() => {
      setNow(Date.now() / 1000);
      void api<Pairing>(`auth/client-pairings/${id}`)
        .then((next) => {
          if (open.current !== id) return;
          if (next.state === "complete") open.current = null;
          setPairing(next);
        })
        .catch((error) => setProblem(message(error)));
    }, 1000);
    return () => clearInterval(timer);
  }, [api, pairing?.id, pairing?.state]);

  async function decide(action: "approve" | "cancel") {
    if (!pairing || busy) return;
    setBusy(true);
    setProblem("");
    try {
      const next = await api<Pairing>(`auth/client-pairings/${pairing.id}`, {
        body: { action, code: pairing.code },
      });
      if (ENDED.includes(next.state)) open.current = null;
      setPairing(next);
    } catch (error) {
      setProblem(message(error));
    } finally {
      setBusy(false);
    }
  }

  const left = Math.max(0, Math.ceil((pairing?.expires_at ?? 0) - now));
  const link =
    pairing && credential ? `${credential.origin}/?connect=${pairing.id}` : "";
  return (
    <SettingsPage title="Pair a phone">
      {!pairing ? (
        problem ? (
          <>
            <RowNote>{problem}</RowNote>
            <Button label="Try again" primary onPress={() => void start()} />
          </>
        ) : (
          <ActivityIndicator color={palette.accent} />
        )
      ) : pairing.state === "waiting" ? (
        <>
          <Body>
            On the new phone, open Enloq, choose Scan QR code and point it at
            this code.
          </Body>
          {credential ? (
            <View style={styles.qr}>
              <Image
                accessibilityLabel="Pairing QR code"
                source={{
                  uri: `${credential.origin}/api/auth/client-pairings/${pairing.id}/qr.svg`,
                  headers: { Authorization: `Bearer ${credential.token}` },
                }}
                contentFit="contain"
                style={styles.qrImage}
              />
            </View>
          ) : null}
          <Explanation>
            {left
              ? `The code expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}.`
              : "The code expired."}
          </Explanation>
          <Explanation>
            No camera on the new phone? Copy the link and paste it there.
          </Explanation>
          <Button
            label={copied ? "Link copied" : "Copy link"}
            onPress={() =>
              void Clipboard.setStringAsync(link).then(() => setCopied(true))
            }
          />
          <Button label="Cancel" onPress={() => void decide("cancel")} />
        </>
      ) : pairing.state === "pending" ? (
        <>
          <Body>{`${pairing.label} asks to pair with this server.`}</Body>
          <Text
            accessibilityLabel="Code to compare"
            style={[styles.code, { color: palette.accent }]}
          >
            {pairing.code}
          </Text>
          <Body>
            Trust it only if the new phone shows this same code. It will read
            and write in your connected Telegram accounts here.
          </Body>
          <Button
            label="Trust"
            primary
            disabled={busy}
            onPress={() => void decide("approve")}
          />
          <Button
            label="Cancel"
            disabled={busy}
            onPress={() => void decide("cancel")}
          />
        </>
      ) : pairing.state === "approved" ? (
        <View style={styles.row}>
          <ActivityIndicator color={palette.accent} />
          <Body>Finishing on the new phone…</Body>
        </View>
      ) : pairing.state === "complete" ? (
        <>
          <Body>{`${pairing.label} is paired. It now shows in Privacy.`}</Body>
          <Button label="Done" primary onPress={() => router.back()} />
        </>
      ) : (
        <>
          <Body>
            {pairing.state === "expired"
              ? "The QR code expired."
              : "Pairing was cancelled."}
          </Body>
          <Button label="Start again" primary onPress={() => void start()} />
        </>
      )}
      {pairing && problem ? <RowNote>{problem}</RowNote> : null}
    </SettingsPage>
  );
}

const styles = StyleSheet.create({
  // A QR code needs a light quiet zone in either color scheme.
  qr: {
    alignSelf: "center",
    backgroundColor: "#ffffff",
    borderRadius: 12,
    padding: 12,
  },
  qrImage: { width: 240, height: 240 },
  code: {
    fontSize: 44,
    letterSpacing: 6,
    fontVariant: ["tabular-nums"],
    fontWeight: "600",
    textAlign: "center",
    paddingVertical: 12,
  },
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
});
