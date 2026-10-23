import { CameraView, useCameraPermissions } from "expo-camera";
import * as Device from "expo-device";
import { router } from "expo-router";
import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Keyboard,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Refused, Unreachable } from "../src/api";
import { CodeField } from "../src/CodeField";
import { useConnection } from "../src/connection";
import {
  awaitApproval,
  claimPairing,
  enterCode,
  finishPairing,
  localPairing,
  ownerSignedOut,
  releaseServer,
  resendCode,
  startCode,
  type CodeAttempt,
} from "../src/pairing";
import {
  LOCAL_SERVER,
  parsePairingLink,
  serverName,
  type PairingTarget,
} from "../src/pairingLink";
import { SignInPairing } from "../src/SignInPairing";
import { usePalette } from "../src/theme";
import { Body, Button, Notice, Screen, Title } from "../src/ui";

type Step =
  | { kind: "start" }
  | { kind: "scan" }
  | { kind: "working"; text: string }
  | { kind: "compare"; code: string; approver: "server" | "browser" }
  // ADR 43 Q8: the code the server posted to the Owner Account's Saved
  // Messages, typed here.
  | { kind: "code"; origin: string; attempt: CodeAttempt; note: string }
  // ADR 47 Q11, ADR 48 Q11: signing in with Telegram pairs this phone to
  // the account's member, or asks to join for an account the server doesn't
  // know; it is also the way in while the Owner Account is signed out.
  | { kind: "signIn"; origin: string }
  | { kind: "failed"; message: string };

// Refusals that end a code attempt; others leave the code step open.
const ENDED = ["Too many wrong codes", "This pairing ended"];

const problem = (error: unknown) =>
  error instanceof Refused || error instanceof Unreachable
    ? error.message
    : "Pairing didn't finish. Try again.";

// Pairing (ADR 38 Q3, ADR 43 Q8, Q9, ADR 48 Q11): sign in with Telegram, or
// as the owner Connect to the release server with a code from Saved
// Messages, scan the QR code a trusted phone or browser shows (ADR 47 Q1) or
// paste its link, or in Enloq Dev use the local test server.
export default function Pair() {
  const { connection, paired } = useConnection();
  const palette = usePalette();
  const [step, setStep] = useState<Step>({ kind: "start" });
  const [link, setLink] = useState("");
  const [code, setCode] = useState("");
  const [permission, requestPermission] = useCameraPermissions();
  const attempt = useRef<AbortController | null>(null);
  const server = releaseServer();

  useEffect(() => () => attempt.current?.abort(), []);

  async function finish(origin: string, token: string) {
    const status = await paired({ origin, token });
    // The list, or the waiting screen for a new member (ADR 48 Q19).
    router.replace(status === "waiting" ? "/waiting" : "/chats");
  }

  // A link's pairing: the code is compared where it was started.
  async function connect(target: PairingTarget) {
    if (attempt.current) return;
    Keyboard.dismiss();
    const controller = new AbortController();
    attempt.current = controller;
    try {
      setStep({
        kind: "working",
        text: `Contacting ${serverName(target.origin)}…`,
      });
      const claim = await claimPairing(target, Device.modelName ?? "");
      if (claim.state !== "approved")
        setStep({
          kind: "compare",
          code: claim.code,
          approver: claim.approver,
        });
      await awaitApproval(target, claim.proof, { signal: controller.signal });
      setStep({ kind: "working", text: "Finishing…" });
      await finish(target.origin, await finishPairing(target, claim.proof));
    } catch (error) {
      if (!controller.signal.aborted)
        setStep({ kind: "failed", message: problem(error) });
    } finally {
      if (attempt.current === controller) attempt.current = null;
    }
  }

  function pasted(text: string) {
    const target = parsePairingLink(text);
    if (target) void connect(target);
    else
      setStep({
        kind: "failed",
        message: "That isn't a pairing link from your server.",
      });
  }

  async function local() {
    try {
      setStep({ kind: "working", text: "Contacting the local test server…" });
      await connect(await localPairing(LOCAL_SERVER));
    } catch (error) {
      setStep({ kind: "failed", message: problem(error) });
    }
  }

  async function startCodePairing() {
    Keyboard.dismiss();
    setCode("");
    try {
      setStep({ kind: "working", text: `Asking ${serverName(server)}…` });
      const started = await startCode(server, Device.modelName ?? "");
      setStep({ kind: "code", origin: server, attempt: started, note: "" });
    } catch (error) {
      if (ownerSignedOut(error)) setStep({ kind: "signIn", origin: server });
      else setStep({ kind: "failed", message: problem(error) });
    }
  }

  // The code's last digit submits it (ADR 47 Q3); Pair stays for a retry.
  async function submitCode(
    current: Extract<Step, { kind: "code" }>,
    value = code,
  ) {
    Keyboard.dismiss();
    try {
      setStep({ kind: "working", text: "Checking the code…" });
      await finish(
        current.origin,
        await enterCode(current.origin, current.attempt, value.trim()),
      );
    } catch (error) {
      const message = problem(error);
      setCode("");
      if (ENDED.some((reason) => message.startsWith(reason)))
        setStep({ kind: "failed", message });
      else setStep({ ...current, note: message });
    }
  }

  async function resend(current: Extract<Step, { kind: "code" }>) {
    setCode("");
    try {
      const attempt = await resendCode(current.origin, current.attempt);
      setStep({
        ...current,
        attempt,
        note: "A new code is in your Saved Messages.",
      });
    } catch (error) {
      const message = problem(error);
      if (ENDED.some((reason) => message.startsWith(reason)))
        setStep({ kind: "failed", message });
      else setStep({ ...current, note: message });
    }
  }

  function cancel() {
    attempt.current?.abort();
    attempt.current = null;
    setStep({ kind: "start" });
  }

  async function scan() {
    const allowed = permission?.granted || (await requestPermission()).granted;
    if (allowed) setStep({ kind: "scan" });
    else
      setStep({
        kind: "failed",
        message: "Camera access is off. Paste the pairing link instead.",
      });
  }

  return (
    <Screen>
      <Title>Pair this phone</Title>
      {connection.status === "unpaired" && connection.notice ? (
        <Notice tone="warning">{connection.notice}</Notice>
      ) : null}
      {step.kind === "scan" ? (
        <>
          <View style={styles.camera}>
            <CameraView
              style={StyleSheet.absoluteFill}
              facing="back"
              barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
              onBarcodeScanned={({ data }) => pasted(data)}
            />
          </View>
          <Button label="Cancel" onPress={cancel} />
        </>
      ) : step.kind === "working" ? (
        <View style={styles.row}>
          <ActivityIndicator color={palette.accent} />
          <Body>{step.text}</Body>
        </View>
      ) : step.kind === "compare" ? (
        <>
          <Body>
            Check that the phone or browser showing the QR code shows this code,
            then choose Trust there.
          </Body>
          <Text
            accessibilityLabel="Pairing matching code"
            style={[styles.code, { color: palette.accent }]}
          >
            {step.code}
          </Text>
          <Body muted>The code expires after two minutes.</Body>
          <Button label="Cancel" onPress={cancel} />
        </>
      ) : step.kind === "signIn" ? (
        <SignInPairing
          origin={step.origin}
          onPaired={(token) => finish(step.origin, token)}
          onCancel={() => setStep({ kind: "start" })}
        />
      ) : step.kind === "code" ? (
        <>
          <Body>
            Enter the code sent to your Saved Messages in Telegram. It expires
            after two minutes.
          </Body>
          <CodeField
            label="Pairing code"
            value={code}
            length={6}
            large
            onChange={setCode}
            onComplete={(value) => void submitCode(step, value)}
          />
          {step.note ? <Notice tone="warning">{step.note}</Notice> : null}
          <Button
            label="Pair"
            primary
            disabled={code.length !== 6}
            onPress={() => void submitCode(step)}
          />
          <Button label="Send a new code" onPress={() => void resend(step)} />
          <Button label="Cancel" onPress={cancel} />
        </>
      ) : (
        <>
          {step.kind === "failed" ? (
            <Notice tone="error">{step.message}</Notice>
          ) : null}
          <Body>
            {`Sign in with your Telegram account to use Enloq on ${serverName(server)}.`}
          </Body>
          <Button
            label="Sign in with Telegram"
            primary
            onPress={() => setStep({ kind: "signIn", origin: server })}
          />
          <Body muted>
            {`If you own ${serverName(server)}, it can post a pairing code to your Saved Messages in Telegram instead.`}
          </Body>
          <Button
            label={`Connect to ${serverName(server)}`}
            onPress={() => void startCodePairing()}
          />
          <Body muted>
            Or scan the QR code a trusted phone or browser shows in Settings →
            Privacy, or paste its link.
          </Body>
          <Button label="Scan QR code" onPress={() => void scan()} />
          <TextInput
            accessibilityLabel="Pairing link"
            autoCapitalize="none"
            autoCorrect={false}
            inputMode="url"
            placeholder="https://…/?connect=…"
            placeholderTextColor={palette.muted}
            value={link}
            onChangeText={setLink}
            onSubmitEditing={() => pasted(link)}
            style={[
              styles.input,
              {
                color: palette.text,
                borderColor: palette.line,
                backgroundColor: palette.panel,
              },
            ]}
          />
          <Button
            label="Connect"
            disabled={!link.trim()}
            onPress={() => pasted(link)}
          />
          {__DEV__ ? (
            // ADR 43 Q9: Enloq Dev only, for the authored fixture.
            <Button
              label="Use the local test server"
              onPress={() => void local()}
            />
          ) : null}
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  camera: {
    aspectRatio: 1,
    width: "100%",
    borderRadius: 12,
    overflow: "hidden",
    backgroundColor: "#000",
  },
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  code: {
    fontSize: 44,
    letterSpacing: 6,
    fontVariant: ["tabular-nums"],
    fontWeight: "600",
    paddingVertical: 12,
  },
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
  },
});
