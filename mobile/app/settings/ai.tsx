import { Redirect, useLocalSearchParams } from "expo-router";
import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SmallButton } from "../../src/BudgetCard";
import { useConnection } from "../../src/connection";
import { ProcessingSettings } from "../../src/ProcessingSettings";
import { message, useSettings } from "../../src/settings";
import { PAUSED_ROLES } from "../../../shared/guidance";
import { decisionModelNotice } from "../../../shared/settings";
import { RowNote, Section, SettingsPage } from "../../src/settingsUi";
import { usePalette } from "../../src/theme";

// Settings → AI (ADR 47 Q6, Q12; Processing until then): the Processing
// Character Threshold, the Concurrency Limit and the Memory and suggestions
// switches; above them, while it lasts, a decision-model outage (ADR 49 Q14),
// and each role paused after repeated format failures with Keep and Reset
// guidance (ADR 62 Q18). A block's "Paused after repeated format failures"
// opens this page with the notice outlined (ADR 45 Q8). Processing
// Guidance's fields are in the chats' AI Menus (ADR 62 Q11).
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
  const params = useLocalSearchParams<{ section?: string }>();
  const [outlined, setOutlined] = useState(params.section === "paused");
  const scroll = useRef<ScrollView>(null);
  const scrolled = useRef(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const unreachable = connection.status === "paired" && connection.unreachable;
  const timezone =
    (connection.status === "paired" && connection.accounts?.timezone) || "UTC";
  const outage = settings?.decision_model;
  const paused = settings?.guidance?.paused ?? [];

  // The outline fades once the owner has seen where they were taken.
  useEffect(() => {
    if (!outlined) return;
    const timer = setTimeout(() => setOutlined(false), 2500);
    return () => clearTimeout(timer);
  }, [outlined]);

  async function act(path: string, body: unknown) {
    setBusy(true);
    setProblem("");
    try {
      await write(path, body);
    } catch (failure) {
      setProblem(message(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsPage title="AI" scrollRef={scroll}>
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
      {paused.map((role) => {
        const label = PAUSED_ROLES[role]?.label ?? role;
        return (
          <View
            key={role}
            accessibilityLabel={`${label} paused`}
            onLayout={(event) => {
              if (params.section !== "paused" || scrolled.current) return;
              scrolled.current = true;
              scroll.current?.scrollTo({
                y: Math.max(0, event.nativeEvent.layout.y - 8),
                animated: true,
              });
            }}
            style={[
              styles.paused,
              {
                backgroundColor: palette.retry,
                borderColor: outlined ? palette.accent : palette.line,
                borderWidth: outlined ? 2 : 1,
              },
            ]}
          >
            <Text style={[styles.pausedText, { color: palette.onRetry }]}>
              {`${label} failed the output format three times since the last guidance change, so automatic processing waits. Deliberate Rewrite still works.`}
            </Text>
            <View style={styles.buttons}>
              <SmallButton
                label="Keep"
                accessibilityLabel={`Keep guidance for ${label}`}
                disabled={busy || unreachable}
                palette={palette}
                onPress={() => void act("guidance/keep", { role })}
              />
              <SmallButton
                label="Reset guidance"
                accessibilityLabel={`Reset guidance for ${label}`}
                primary
                disabled={busy || unreachable}
                palette={palette}
                onPress={() =>
                  void act("guidance", {
                    fields: Object.fromEntries(
                      (PAUSED_ROLES[role]?.fields ?? []).map((field) => [
                        field,
                        null,
                      ]),
                    ),
                  })
                }
              />
            </View>
          </View>
        );
      })}
      {problem ? <RowNote>{problem}</RowNote> : null}
      {settings ? (
        <Section>
          <ProcessingSettings
            settings={settings}
            disabled={unreachable}
            write={write}
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

const styles = StyleSheet.create({
  paused: { borderRadius: 12, padding: 12, gap: 10 },
  pausedText: { fontSize: 15, lineHeight: 21 },
  buttons: { flexDirection: "row", gap: 8, justifyContent: "flex-end" },
});
