import { Redirect, router } from "expo-router";
import React from "react";
import { useConnection } from "../../src/connection";
import { MenuRow, Section, SettingsPage } from "../../src/settingsUi";
import { TrustedList } from "../../src/TrustedList";

// Settings → Privacy (ADR 47 Q6): Pair a phone, which shows a QR code for
// Enloq on a new phone (Q1), then the trusted browsers and devices with
// Forget.
export default function PrivacyRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return (
    <SettingsPage title="Privacy">
      <Section>
        <MenuRow
          icon="qr-code-outline"
          label="Pair a phone"
          onPress={() => router.push("/settings/pair")}
        />
      </Section>
      <Section title="Trusted browsers and devices">
        <TrustedList />
      </Section>
    </SettingsPage>
  );
}
