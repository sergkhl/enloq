import { Redirect, useLocalSearchParams } from "expo-router";
import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { useConnection } from "../../src/connection";
import { MemoryPanel } from "../../src/MemoryPanel";
import { Section } from "../../src/settingsUi";
import { usePalette } from "../../src/theme";
import { TraitsPanel } from "../../src/TraitsPanel";
import { BackHeader, Screen } from "../../src/ui";

// About you (ADR 55 Q21, Q25), opened from Manage accounts: what the
// account's holder cares about, its Account Memory, and their traits, read
// from the server on opening.
export default function AboutRoute() {
  const params = useLocalSearchParams<{ account: string }>();
  const account = Number(params.account);
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return <AboutYou account={account} />;
}

function AboutYou({ account }: { account: number }) {
  const { connection } = useConnection();
  const palette = usePalette();
  // Which account it is, when the phone has several.
  const name =
    connection.status === "paired"
      ? connection.accounts?.accounts.find(
          (item) => item.account_id === account,
        )?.name
      : "";
  return (
    <Screen header={<BackHeader title="About you" />}>
      {name ? <Text style={{ color: palette.muted }}>{name}</Text> : null}
      <Section title="You care about">
        <View style={styles.block}>
          <MemoryPanel account={account} />
        </View>
      </Section>
      <Section title="Your traits">
        <View style={styles.block}>
          <TraitsPanel account={account} subject="you" />
        </View>
      </Section>
    </Screen>
  );
}

const styles = StyleSheet.create({
  block: { padding: 12, gap: 4 },
});
