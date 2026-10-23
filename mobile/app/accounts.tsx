import { Redirect, router } from "expo-router";
import React, { useState } from "react";
import { StyleSheet, Switch, Text, View } from "react-native";
import type { Schemas } from "../src/api";
import { Avatar } from "../src/Avatar";
import { clearAccountCache } from "../src/cache";
import { useConnection } from "../src/connection";
import { serverName } from "../src/pairingLink";
import { usePalette } from "../src/theme";
import { BackHeader, Body, Button, Notice, Screen } from "../src/ui";

type Account = Schemas["AccountSummary"];
type Confirm = { account: Account; action: "logout" | "remove-local-data" };

// Manage accounts in the app (ADR 43 Q3, Q10): Add account, Sign in again
// and Reconnect for an account that needs them, and Log out and Remove local
// data with the web's confirmations; the service keeps its guards, such as
// unresolved sends blocking removal, and spending records stay. A signed-in
// account opens About you (ADR 55 Q21).
export default function ManageAccounts() {
  const { connection, api, refresh } = useConnection();
  const palette = usePalette();
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  const overview = connection.accounts;
  const accounts = overview?.accounts ?? [];
  const server = serverName(connection.credential.origin);

  async function act(path: string, body: unknown, after?: () => void) {
    setBusy(true);
    setError("");
    try {
      await api(path, { body });
      after?.();
      setConfirm(null);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      await refresh().catch(() => undefined);
      setBusy(false);
    }
  }

  if (confirm) {
    const logout = confirm.action === "logout";
    const name =
      confirm.account.name || `Account ${confirm.account.account_id}`;
    return (
      <Screen>
        <BackHeader title="Manage accounts" />
        <Text style={[styles.heading, { color: palette.text }]}>
          {logout ? `Log out of ${name}?` : `Remove local data for ${name}?`}
        </Text>
        <Body>
          {logout
            ? `Only this app signs out. Your drafts and conversation cache stay on ${server} under the normal retention policy, and return when you sign in again.`
            : `This deletes this account's local conversation cache, unsent drafts and images from ${server}. It does not delete Telegram messages. Budget records remain.`}
        </Body>
        {error ? <Notice tone="error">{error}</Notice> : null}
        <Button
          label="Keep account"
          disabled={busy}
          onPress={() => {
            setConfirm(null);
            setError("");
          }}
        />
        <Button
          label={logout ? "Confirm logout" : "Remove local data"}
          primary
          disabled={busy}
          onPress={() =>
            void act(
              `accounts/${confirm.account.account_id}/${confirm.action}`,
              { confirm: true },
              logout
                ? undefined
                : () => clearAccountCache(confirm.account.account_id),
            )
          }
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <BackHeader title="Manage accounts" />
      <Body muted>
        Each account has its own conversations and drafts. AI usage limits are
        shared.
      </Body>
      {accounts.map((account) => {
        const name = account.name || `Account ${account.account_id}`;
        return (
          <View
            key={account.account_id}
            accessibilityLabel={name}
            style={[styles.account, { borderColor: palette.line }]}
          >
            <View style={styles.row}>
              <Avatar
                account={account.account_id}
                peer={`user:${account.account_id}`}
                photoId={
                  account.state === "authorized" ? account.photo_id : null
                }
                name={account.name}
                size={40}
              />
              <View style={styles.fill}>
                <Text style={[styles.name, { color: palette.text }]}>
                  {name}
                </Text>
                <Text style={{ color: palette.muted }}>
                  {account.username
                    ? `@${account.username}`
                    : account.phone || `Account ${account.account_id}`}
                </Text>
                <Text style={[styles.small, { color: palette.muted }]}>
                  {account.status}
                </Text>
              </View>
            </View>
            {/* Each signed-in account's AI processing (ADR 46 CU3). */}
            {account.state === "authorized" ? (
              <View style={styles.row}>
                <Text style={[styles.fill, { color: palette.text }]}>
                  AI processing
                </Text>
                <Switch
                  accessibilityLabel={`AI processing for ${name}`}
                  value={account.ai_enabled}
                  disabled={busy}
                  onValueChange={(value) =>
                    void act(`accounts/${account.account_id}/processing`, {
                      ai_enabled: value,
                    })
                  }
                  trackColor={{ true: palette.accent }}
                />
              </View>
            ) : null}
            <View style={styles.actions}>
              {/* Its own memory and traits (ADR 55 Q21). */}
              {account.state === "authorized" ? (
                <Button
                  label="About you"
                  accessibilityLabel={`About you: ${name}`}
                  disabled={busy}
                  onPress={() =>
                    router.push({
                      pathname: "/about/[account]",
                      params: { account: String(account.account_id) },
                    })
                  }
                />
              ) : null}
              {account.state === "authorized" && !account.connected ? (
                <Button
                  label="Reconnect"
                  disabled={busy}
                  onPress={() =>
                    void act(`accounts/${account.account_id}/reconnect`, {})
                  }
                />
              ) : null}
              {account.state === "authorized" ||
              account.state === "logout_pending" ? (
                <Button
                  label={
                    account.state === "logout_pending"
                      ? "Retry logout"
                      : "Log out"
                  }
                  disabled={busy}
                  onPress={() => setConfirm({ account, action: "logout" })}
                />
              ) : (
                <Button
                  label="Sign in again"
                  disabled={busy || !overview?.login_available}
                  onPress={() =>
                    router.push({
                      pathname: "/login",
                      params: { account: String(account.account_id) },
                    })
                  }
                />
              )}
              {account.state === "requires_login" ? (
                <Button
                  label="Log out"
                  disabled={busy}
                  onPress={() => setConfirm({ account, action: "logout" })}
                />
              ) : null}
              {account.state === "logged_out" ? (
                <Button
                  label="Remove local data"
                  disabled={busy}
                  onPress={() =>
                    setConfirm({ account, action: "remove-local-data" })
                  }
                />
              ) : null}
            </View>
          </View>
        );
      })}
      {error ? <Notice tone="error">{error}</Notice> : null}
      <Button
        label="Add account"
        primary
        disabled={busy || !overview?.login_available}
        onPress={() => router.push("/login")}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  heading: { fontSize: 20, fontWeight: "700" },
  account: { borderWidth: 1, borderRadius: 12, padding: 12, gap: 10 },
  row: { flexDirection: "row", alignItems: "center", gap: 12 },
  name: { fontSize: 17, fontWeight: "600" },
  small: { fontSize: 13 },
  actions: { gap: 8 },
});
