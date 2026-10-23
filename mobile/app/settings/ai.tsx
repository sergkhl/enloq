import { Redirect, router } from "expo-router";
import React from "react";
import { ActivityIndicator } from "react-native";
import { useConnection } from "../../src/connection";
import { ProcessingSettings } from "../../src/ProcessingSettings";
import { useSettings } from "../../src/settings";
import { decisionModelNotice } from "../../../shared/settings";
import {
  Divider,
  LinkRow,
  RowNote,
  Section,
  SettingsPage,
} from "../../src/settingsUi";
import { usePalette } from "../../src/theme";

// Settings → AI (ADR 47 Q6, Q12; Processing until then): the Processing
// Character Threshold, the Concurrency Limit, the Memory and suggestions
// switches, and Guidance, Processing Guidance's screens (ADR 45 Q6); above
// them, while it lasts, a decision-model outage (ADR 49 Q14).
export default function AiRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return <AiPage />;
}

function AiPage() {
  const { connection } = useConnection();
  const palette = usePalette();
  const { settings, error, write } = useSettings();
  const unreachable = connection.status === "paired" && connection.unreachable;
  const timezone =
    (connection.status === "paired" && connection.accounts?.timezone) || "UTC";
  const outage = settings?.decision_model;
  return (
    <SettingsPage title="AI">
      {unreachable ? (
        <RowNote tone="warning">
          Settings can be changed once the server answers.
        </RowNote>
      ) : null}
      {outage && !outage.available && outage.since ? (
        <RowNote tone="warning">
          {decisionModelNotice(outage.since, timezone)}
        </RowNote>
      ) : null}
      {settings ? (
        <Section>
          <ProcessingSettings
            settings={settings}
            disabled={unreachable}
            write={write}
          />
          <Divider />
          <LinkRow
            label="Guidance"
            detail={`Version ${settings.guidance?.version ?? 0}`}
            badge={settings.guidance?.paused.length ? "Paused" : undefined}
            onPress={() => router.push("/settings/guidance")}
          />
        </Section>
      ) : error ? (
        <RowNote tone="warning">Settings show once the server answers.</RowNote>
      ) : (
        <ActivityIndicator color={palette.accent} />
      )}
    </SettingsPage>
  );
}
