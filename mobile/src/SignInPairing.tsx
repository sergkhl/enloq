import * as Device from "expo-device";
import React, { useEffect, useState } from "react";
import { Keyboard, StyleSheet, TextInput } from "react-native";
import { Agreement } from "./Agreement";
import { Refused, Unreachable, type Schemas } from "./api";
import { CodeField } from "./CodeField";
import {
  signInStep,
  startSignIn,
  type SignInAttempt,
  type SignInState,
} from "./pairing";
import { serverName } from "./pairingLink";
import { usePalette } from "./theme";
import { Body, Button, Notice } from "./ui";

type Login = Schemas["LoginState"];

const problem = (error: unknown) =>
  error instanceof Refused || error instanceof Unreachable
    ? error.message
    : "Signing in didn't finish. Try again.";

// Sign in with Telegram (ADR 47 Q11, ADR 48 Q11, Q12): the phone pairs by
// signing in with its number, the code Telegram sends and the two-step
// password. The owner's account or a member's first account pairs the phone
// to them; an account the server doesn't know reads the Member Agreement and
// asks to join, and the phone then waits for the owner. The code submits
// itself on its last digit when Telegram gives its length (ADR 47 Q3).
export function SignInPairing({
  origin,
  onPaired,
  onCancel,
}: {
  origin: string;
  onPaired: (token: string) => Promise<void>;
  onCancel: () => void;
}) {
  const palette = usePalette();
  const [phone, setPhone] = useState("");
  const [attempt, setAttempt] = useState<SignInAttempt | null>(null);
  const [login, setLogin] = useState<Login | null>(null);
  const [agreement, setAgreement] = useState<Schemas["Agreement"] | null>(null);
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [now, setNow] = useState(Date.now() / 1000);

  useEffect(() => {
    if (!login?.retry_at) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [login?.retry_at]);

  function restart(message: string) {
    setAttempt(null);
    setLogin(null);
    setAgreement(null);
    setSecret("");
    setNote(message);
  }

  async function begin() {
    if (busy || !phone.trim()) return;
    Keyboard.dismiss();
    setBusy(true);
    setNote("");
    try {
      const state = await startSignIn(
        origin,
        phone.trim(),
        Device.modelName ?? "",
      );
      setAttempt({ id: state.id, proof: state.proof });
      setLogin(state.login);
    } catch (error) {
      setNote(problem(error));
    } finally {
      setBusy(false);
    }
  }

  async function submit(
    action: "code" | "password" | "resend" | "agree",
    value = secret,
  ) {
    if (busy || !attempt) return;
    setBusy(true);
    setNote("");
    setSecret("");
    try {
      const state: SignInState = await signInStep(
        origin,
        attempt,
        action,
        value,
      );
      if (state.token) {
        Keyboard.dismiss();
        await onPaired(state.token);
        return;
      }
      setLogin(state.login);
      setAgreement(state.agreement ?? null);
      if (action === "resend") setNote("Telegram sent a new code.");
    } catch (error) {
      const message = problem(error);
      // An attempt that ended starts again from the number.
      if (/ended|Start again|start a new login|expired/i.test(message))
        restart(message);
      else setNote(message);
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    if (attempt)
      void signInStep(origin, attempt, "cancel").catch(() => undefined);
    onCancel();
  }

  const wait = Math.max(0, Math.ceil((login?.retry_at ?? 0) - now));
  const input = [
    styles.input,
    {
      color: palette.text,
      borderColor: palette.line,
      backgroundColor: palette.panel,
    },
  ];
  return (
    <>
      {agreement && login?.stage === "agreement" ? (
        <Agreement
          agreement={agreement}
          action={busy ? "Asking…" : "Agree and ask to join"}
          busy={busy}
          onAgree={() => void submit("agree", agreement.version)}
        />
      ) : !login ? (
        <>
          <Body>
            {`Sign in with your Telegram account to use Enloq on ${serverName(origin)}. If you're new here, you'll read what joining means and ask the server's owner.`}
          </Body>
          <TextInput
            accessibilityLabel="Phone number"
            autoComplete="tel"
            autoFocus
            inputMode="tel"
            placeholder="+44…"
            placeholderTextColor={palette.muted}
            value={phone}
            onChangeText={setPhone}
            onSubmitEditing={() => void begin()}
            style={input}
          />
          <Button
            label={busy ? "Asking Telegram…" : "Continue"}
            primary
            disabled={busy || !phone.trim()}
            onPress={() => void begin()}
          />
        </>
      ) : login.stage === "password" ? (
        <>
          <Body>{login.message}</Body>
          <TextInput
            key="password"
            accessibilityLabel="Telegram two-step password"
            autoComplete="off"
            autoFocus
            secureTextEntry
            value={secret}
            onChangeText={setSecret}
            onSubmitEditing={() => secret && void submit("password")}
            style={input}
          />
          <Button
            label={busy ? "Checking…" : "Sign in"}
            primary
            disabled={busy || !secret}
            onPress={() => void submit("password")}
          />
        </>
      ) : (
        <>
          <Body>{login.message}</Body>
          <CodeField
            label="Login code"
            value={secret}
            length={login.code_length ?? null}
            disabled={busy}
            secure
            onChange={setSecret}
            onComplete={(value) => void submit("code", value)}
          />
          <Button
            label={busy ? "Checking…" : "Sign in"}
            primary
            disabled={busy || !secret}
            onPress={() => void submit("code")}
          />
          {login.can_resend ? (
            <Button
              label={wait ? `Resend available in ${wait}s` : "Resend code"}
              disabled={busy || wait > 0}
              onPress={() => void submit("resend", "")}
            />
          ) : null}
        </>
      )}
      {note ? <Notice tone="warning">{note}</Notice> : null}
      <Button label="Cancel" disabled={busy && !login} onPress={cancel} />
    </>
  );
}

const styles = StyleSheet.create({
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
  },
});
