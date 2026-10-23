import { Redirect, router, useLocalSearchParams } from "expo-router";
import React, { useEffect, useRef, useState } from "react";
import { Image, StyleSheet, Text, TextInput, View } from "react-native";
import type { Schemas } from "../src/api";
import { CodeField } from "../src/CodeField";
import { useConnection } from "../src/connection";
import { usePalette } from "../src/theme";
import { BackHeader, Body, Button, Notice, Screen } from "../src/ui";

type Login = Schemas["LoginState"];

const ENDED = ["error", "expired", "cancelled"];

// Add account and Sign in again in the app (ADR 43 Q3, Q10), over the
// service's login flow: a QR code for Telegram on another device to scan,
// or phone number, code and two-step password, with Resend and Cancel. The
// login belongs to this phone; codes and passwords live only in this screen's
// state and are never kept on the phone or logged.
export default function LoginRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return <LoginScreen />;
}

function LoginScreen() {
  const { connection, api, refresh, selectAccount } = useConnection();
  const palette = usePalette();
  const params = useLocalSearchParams<{ account?: string }>();
  const expectedId = Number(params.account) || null;
  const accounts =
    connection.status === "paired" ? (connection.accounts?.accounts ?? []) : [];
  const expected = accounts.find((item) => item.account_id === expectedId);
  const [method, setMethod] = useState<"qr" | "phone">("qr");
  const [flow, setFlow] = useState<Login | null>(null);
  const current = useRef<Login | null>(null);
  const [phone, setPhone] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);
  const alive = useRef(true);
  const done = useRef(false);

  function update(value: Login) {
    current.current = value;
    if (!alive.current) return;
    setFlow(value);
    if (value.stage === "complete" && value.account_id && !done.current) {
      done.current = true;
      const account = value.account_id;
      void refresh().then(() => {
        selectAccount(account);
        router.replace("/chats");
      });
    }
  }

  async function start(choice: "qr" | "phone") {
    setBusy(true);
    setError("");
    setSecret("");
    try {
      const value = await api<Login>("logins", {
        body: {
          method: choice,
          phone: choice === "phone" ? phone : "",
          expected_id: expectedId,
        },
      });
      current.current = value;
      if (!alive.current) {
        await api(`logins/${value.id}/cancel`, { body: {} });
        return;
      }
      update(value);
    } catch (problem) {
      if (alive.current) setError(message(problem));
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  async function cancelFlow() {
    const pending = current.current;
    current.current = null;
    if (pending && pending.stage !== "complete")
      await api(`logins/${pending.id}/cancel`, { body: {} }).catch(
        () => undefined,
      );
  }

  useEffect(() => {
    alive.current = true;
    void start("qr");
    return () => {
      alive.current = false;
      // Leaving the screen ends a login that did not finish.
      void cancelFlow();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!flow || flow.stage === "complete" || ENDED.includes(flow.stage))
      return;
    const timer = setInterval(() => {
      setNow(Date.now() / 1000);
      if (busy) return;
      const id = flow.id;
      void api<Login>(`logins/${id}`)
        .then((value) => {
          if (current.current?.id === value.id) update(value);
        })
        .catch((problem) => alive.current && setError(message(problem)));
    }, 1000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow?.id, flow?.stage, busy]);

  async function change(choice: "qr" | "phone") {
    setBusy(true);
    setError("");
    await cancelFlow();
    setFlow(null);
    setSecret("");
    setMethod(choice);
    setBusy(false);
    if (choice === "qr") await start("qr");
  }

  // Telegram's code submits itself on its last digit when its length is
  // known (ADR 47 Q3); the two-step password only from Sign in.
  async function submit(
    action: "code" | "password" | "resend",
    value = secret,
  ) {
    if (!flow || busy) return;
    setBusy(true);
    setError("");
    setSecret("");
    try {
      update(
        await api<Login>(`logins/${flow.id}/${action}`, {
          body: action === "resend" ? {} : { value },
        }),
      );
    } catch (problem) {
      setError(message(problem));
    } finally {
      setBusy(false);
    }
  }

  const ended = !!flow && ENDED.includes(flow.stage);
  const wait = Math.max(0, Math.ceil((flow?.retry_at ?? 0) - now));
  const credential =
    connection.status === "paired" ? connection.credential : null;
  return (
    <Screen>
      <BackHeader
        title={
          expected ? `Sign in again · ${expected.name}` : "Add Telegram account"
        }
      />
      {connection.status === "paired" &&
      connection.accounts?.mode === "offline" ? (
        <Notice tone="warning">
          Synthetic login for this offline demo. Do not enter real credentials.
        </Notice>
      ) : null}
      <Text
        accessibilityLiveRegion="polite"
        style={[styles.status, { color: palette.text }]}
      >
        {flow?.message ||
          (method === "qr"
            ? "Preparing a private QR code…"
            : "Enter the phone number of an existing Telegram account.")}
      </Text>
      {flow?.stage === "qr" && credential ? (
        <View style={styles.qr}>
          <Image
            accessibilityLabel="Scan this QR code with Telegram to log in"
            // An array: React Native's Android Image sends headers only from one.
            source={[
              {
                uri: `${credential.origin}/api/logins/${flow.id}/qr?v=${flow.qr_revision}`,
                headers: { Authorization: `Bearer ${credential.token}` },
              },
            ]}
            style={styles.qrImage}
          />
        </View>
      ) : null}
      {method === "phone" && (!flow || ended) ? (
        <>
          <TextInput
            accessibilityLabel="Phone number"
            autoComplete="off"
            inputMode="tel"
            placeholder="+44…"
            placeholderTextColor={palette.muted}
            value={phone}
            onChangeText={setPhone}
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
            label="Continue"
            primary
            disabled={busy || !phone.trim()}
            onPress={() => void start("phone")}
          />
        </>
      ) : null}
      {flow?.stage === "code" || flow?.stage === "password" ? (
        <>
          {flow.stage === "code" ? (
            <CodeField
              key="code"
              label="Login code"
              value={secret}
              length={flow.code_length ?? null}
              disabled={busy}
              secure
              onChange={setSecret}
              onComplete={(value) => void submit("code", value)}
            />
          ) : (
            <TextInput
              key="password"
              accessibilityLabel="Telegram two-step password"
              autoComplete="off"
              secureTextEntry
              value={secret}
              onChangeText={setSecret}
              style={[
                styles.input,
                {
                  color: palette.text,
                  borderColor: palette.line,
                  backgroundColor: palette.panel,
                },
              ]}
            />
          )}
          {flow.delivery && flow.stage === "code" ? (
            <Body muted>
              Telegram selected: {flow.delivery.replace(/_/g, " ")}
            </Body>
          ) : null}
          <Button
            label={busy ? "Checking…" : "Sign in"}
            primary
            disabled={busy || !secret.trim()}
            onPress={() => void submit(flow.stage as "code" | "password")}
          />
          {flow.can_resend ? (
            <Button
              label={wait ? `Resend available in ${wait}s` : "Resend code"}
              disabled={busy || wait > 0}
              onPress={() => void submit("resend", "")}
            />
          ) : null}
        </>
      ) : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
      {method === "qr" && (ended || (error && !flow)) ? (
        <Button
          label="Try QR login again"
          primary
          disabled={busy}
          onPress={() => void start("qr")}
        />
      ) : null}
      <Button
        label={
          method === "qr" ? "Log in by phone number" : "Log in with QR code"
        }
        disabled={busy}
        onPress={() => void change(method === "qr" ? "phone" : "qr")}
      />
      <Body muted>
        Your other accounts stay connected. Each account has its own
        conversations and drafts.
      </Body>
    </Screen>
  );
}

const message = (problem: unknown) =>
  problem instanceof Error ? problem.message : String(problem);

const styles = StyleSheet.create({
  status: { fontSize: 16, lineHeight: 22 },
  qr: { alignItems: "center" },
  qrImage: { width: 260, height: 260, backgroundColor: "#fff" },
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
  },
});
