import { Redirect, router } from "expo-router";
import React, { useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { SmallButton } from "../../../src/BudgetCard";
import { useConnection } from "../../../src/connection";
import { GUIDANCE_GROUPS, PAUSED_ROLES } from "../../../../shared/guidance";
import { EXPLAIN } from "../../../../shared/settings";
import { message, useSettings } from "../../../src/settings";
import {
  Divider,
  Explanation,
  LinkRow,
  RowNote,
  Section,
} from "../../../src/settingsUi";
import { usePalette } from "../../../src/theme";
import { BackHeader, Screen } from "../../../src/ui";

// Guidance, Processing Guidance under Settings → AI (ADR 45 Q5, Q6; ADR 47
// Q12): its version, a notice for each role
// paused after repeated format failures with Reset guidance and Keep, and the
// fields, each opening its own editor.
export default function GuidanceRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return <Guidance />;
}

function Guidance() {
  const { connection } = useConnection();
  const palette = usePalette();
  const { settings, error, write } = useSettings();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const unreachable = connection.status === "paired" && connection.unreachable;
  const guidance = settings?.guidance;

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
    <Screen header={<BackHeader title="Guidance" />}>
      {unreachable ? (
        <RowNote tone="warning">
          Settings can be changed once the server answers.
        </RowNote>
      ) : null}
      {!guidance ? (
        <Explanation>
          {error ? "Settings show once the server answers." : "Loading…"}
        </Explanation>
      ) : (
        <>
          <Explanation>
            {`Version ${guidance.version}. ${EXPLAIN.guidance}`}
          </Explanation>
          {guidance.paused.map((role) => {
            const paused = PAUSED_ROLES[role];
            return (
              <View
                key={role}
                accessibilityLabel={`${paused?.label ?? role} paused`}
                style={[
                  styles.paused,
                  { backgroundColor: palette.retry, borderColor: palette.line },
                ]}
              >
                <Text style={[styles.pausedText, { color: palette.onRetry }]}>
                  {`${paused?.label ?? role} failed the output format three times since the last guidance change, so automatic processing waits. Deliberate Rewrite still works.`}
                </Text>
                <View style={styles.buttons}>
                  <SmallButton
                    label="Keep"
                    accessibilityLabel={`Keep guidance for ${paused?.label ?? role}`}
                    disabled={busy || unreachable}
                    palette={palette}
                    onPress={() => void act("guidance/keep", { role })}
                  />
                  <SmallButton
                    label="Reset guidance"
                    accessibilityLabel={`Reset guidance for ${paused?.label ?? role}`}
                    primary
                    disabled={busy || unreachable}
                    palette={palette}
                    onPress={() =>
                      void act("guidance", {
                        fields: Object.fromEntries(
                          (paused?.fields ?? []).map((field) => [field, null]),
                        ),
                      })
                    }
                  />
                </View>
              </View>
            );
          })}
          {problem ? <RowNote>{problem}</RowNote> : null}
          {GUIDANCE_GROUPS.map((group) => (
            <Section key={group.title} title={group.title}>
              {group.fields.map((field, index) => {
                const current = guidance.fields[field.name] ?? "";
                const edited =
                  current !== (guidance.defaults[field.name] ?? "");
                return (
                  <React.Fragment key={field.name}>
                    {index ? <Divider /> : null}
                    <LinkRow
                      label={field.label}
                      detail={edited ? "Edited" : "Default"}
                      onPress={() =>
                        router.push({
                          pathname: "/settings/guidance/[field]",
                          params: { field: field.name },
                        })
                      }
                    />
                    <Text
                      numberOfLines={1}
                      style={[styles.preview, { color: palette.muted }]}
                    >
                      {current.split("\n")[0] || "Empty"}
                    </Text>
                  </React.Fragment>
                );
              })}
            </Section>
          ))}
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  paused: { borderWidth: 1, borderRadius: 12, padding: 12, gap: 10 },
  pausedText: { fontSize: 15, lineHeight: 21 },
  buttons: { flexDirection: "row", gap: 8, justifyContent: "flex-end" },
  preview: { fontSize: 13, paddingBottom: 8, marginTop: -4 },
});
