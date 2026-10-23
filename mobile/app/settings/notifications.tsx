import { Redirect } from "expo-router";
import React from "react";
import { useConnection } from "../../src/connection";
import { PhoneNotifications } from "../../src/PhoneNotifications";
import { phonePush } from "../../src/phonePush";
import { Explanation, Section, SettingsPage } from "../../src/settingsUi";

// Settings → Notifications (ADR 47 Q6): this phone's switch, sound and test
// on Android (ADR 38). iOS has no push yet, so the
// menu shows no row for it.
export default function NotificationsRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return (
    <SettingsPage title="Notifications">
      {phonePush ? (
        <Section title="This phone">
          <PhoneNotifications />
        </Section>
      ) : (
        <Explanation>This phone has no notifications yet.</Explanation>
      )}
    </SettingsPage>
  );
}
