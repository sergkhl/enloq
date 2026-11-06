import { Redirect, router, useLocalSearchParams } from "expo-router";
import React, { useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import {
  AI_CONTROLS,
  groupNote,
  hasSwitch,
  serverNote,
  switchState,
  type AiControl,
} from "../../../../shared/aiMenu";
import { peerTab } from "../../../../shared/chatRoute";
import { emotionDetails } from "../../../../shared/emotion";
import { controlGuidance, guidanceDetail } from "../../../../shared/guidance";
import { EXPLAIN } from "../../../../shared/settings";
import { SUGGESTION_STYLES } from "../../../../shared/suggestions";
import type { Schemas } from "../../../src/api";
import { useChatAi, useShownChat } from "../../../src/AiMenu";
import { useConnection } from "../../../src/connection";
import { useReadingView } from "../../../src/data";
import { MemoryPanel } from "../../../src/MemoryPanel";
import { useSettings } from "../../../src/settings";
import {
  ChoiceRow,
  Divider,
  Explanation,
  LinkRow,
  RowNote,
  Section,
  SettingsPage,
  SwitchRow,
} from "../../../src/settingsUi";
import { usePalette } from "../../../src/theme";
import { TraitsPanel } from "../../../src/TraitsPanel";

// A control's page in a chat's AI Menu (ADR 62 Q16): its switch or choice
// with its explanation, its panels (Q12) and, for the owner, the Processing
// Guidance fields it uses, each for all chats (Q11, Q17). It shows the open
// chat's snapshot, which the chat screen beneath keeps reading.
export default function AiPageRoute() {
  const params = useLocalSearchParams<{
    account: string;
    peer: string;
    control?: string;
  }>();
  const account = Number(params.account);
  const peer = String(params.peer ?? "");
  const control = String(params.control ?? "") as AiControl;
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (
    !Number.isSafeInteger(account) ||
    account <= 0 ||
    !peer ||
    !(control in AI_CONTROLS)
  )
    return <Redirect href="/chats" />;
  if (connection.status !== "paired") return null;
  return (
    <AiPage
      key={`${account}/${peer}/${control}`}
      account={account}
      peer={peer}
      control={control}
    />
  );
}

const noReload = async () => undefined;

function AiPage({
  account,
  peer,
  control,
}: {
  account: number;
  peer: string;
  control: AiControl;
}) {
  const { connection, role } = useConnection();
  const shown = useShownChat(account, peer);
  const snapshot = shown?.snapshot ?? null;
  const ai = useChatAi(
    account,
    peer,
    snapshot?.ai,
    shown?.readAt ?? 0,
    shown?.reload ?? noReload,
  );
  const unreachable = connection.status === "paired" && connection.unreachable;
  // Offline, the switches wait for the server (Q16).
  const offline = unreachable || !shown?.live;
  const copy = AI_CONTROLS[control];
  const group = peerTab(peer) === "group";
  return (
    <SettingsPage title={copy.label}>
      {control === "view" ? (
        <ViewSection />
      ) : control === "group" && group ? (
        <GroupSection
          account={account}
          peer={peer}
          processing={snapshot?.group_processing}
          accountEnabled={snapshot?.ai?.account ?? true}
          readAt={shown?.readAt ?? 0}
          reload={shown?.reload ?? noReload}
          offline={offline}
        />
      ) : control === "style" ? (
        <Section>
          <View style={styles.block}>
            <Explanation>{copy.explanation}</Explanation>
          </View>
          <ChoiceRow
            label="Style"
            options={SUGGESTION_STYLES}
            value={ai.ai?.style ?? null}
            explanation={
              SUGGESTION_STYLES.find((item) => item.value === ai.ai?.style)
                ?.explanation
            }
            disabled={!ai.ai || ai.busy || offline}
            testID="suggestion-style"
            onChange={(style) => void ai.change("style", style)}
          />
        </Section>
      ) : hasSwitch(control) ? (
        <Section>
          <SwitchRow
            label={copy.label}
            explanation={copy.explanation}
            value={switchState(control, ai.ai) ?? false}
            disabled={
              switchState(control, ai.ai) === null || ai.busy || offline
            }
            testID={`ai-${control}-switch`}
            onChange={(on) => void ai.change(control, on)}
          />
          {serverNote(control, ai.ai) ? (
            <View style={styles.note}>
              <RowNote tone="warning">{serverNote(control, ai.ai)}</RowNote>
            </View>
          ) : null}
        </Section>
      ) : (
        <Section>
          <View style={styles.block}>
            <Explanation>{copy.explanation}</Explanation>
          </View>
        </Section>
      )}
      {offline && control !== "view" ? (
        <RowNote tone="warning">
          The switches can be changed once the server answers.
        </RowNote>
      ) : null}
      {ai.problem ? <RowNote>{ai.problem}</RowNote> : null}
      {control === "emotion" ? (
        <EmotionSection
          emotion={snapshot?.emotion}
          timezone={snapshot?.timezone ?? "UTC"}
        />
      ) : null}
      {control === "remember" ? (
        // What the person cares about and their traits (ADR 55 Q25).
        <>
          <Section title="Cares about">
            <View style={styles.block}>
              <MemoryPanel account={account} peer={peer} />
            </View>
          </Section>
          <Section title="Traits">
            <View style={styles.block}>
              <TraitsPanel account={account} peer={peer} subject="person" />
            </View>
          </Section>
        </>
      ) : null}
      {role !== "member" ? <GuidanceSection control={control} /> : null}
    </SettingsPage>
  );
}

// AI view, the global Reading View (ADR 14): one choice for every chat.
function ViewSection() {
  const { view, choose } = useReadingView();
  return (
    <Section>
      <SwitchRow
        label={AI_CONTROLS.view.label}
        explanation={AI_CONTROLS.view.explanation}
        value={view === "ai"}
        disabled={false}
        testID="ai-view-switch"
        onChange={(on) => void choose(on ? "ai" : "originals")}
      />
    </Section>
  );
}

// A group's Group AI processing (ADR 34) with its notes; its page holds the
// Group briefings field.
function GroupSection({
  account,
  peer,
  processing,
  accountEnabled,
  readAt,
  reload,
  offline,
}: {
  account: number;
  peer: string;
  processing: Schemas["GroupProcessing"] | null | undefined;
  accountEnabled: boolean;
  readAt: number;
  reload: () => Promise<void>;
  offline: boolean;
}) {
  const { api } = useConnection();
  const [answer, setAnswer] = useState<{
    value: Schemas["GroupProcessing"];
    at: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const shown = answer && readAt <= answer.at ? answer.value : processing;
  async function change(enabled: boolean) {
    setBusy(true);
    setProblem("");
    try {
      const value = await api<Schemas["GroupProcessing"]>(
        `accounts/${account}/chats/${encodeURIComponent(peer)}/processing`,
        { body: { enabled } },
      );
      setAnswer({ value, at: Date.now() });
      void reload();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }
  const note = groupNote(shown, accountEnabled);
  return (
    <Section>
      <SwitchRow
        label={AI_CONTROLS.group.label}
        explanation={AI_CONTROLS.group.explanation}
        value={shown?.processing_enabled ?? false}
        disabled={!shown || busy || offline}
        testID="ai-group-switch"
        onChange={(on) => void change(on)}
      />
      {note || problem ? (
        <View style={styles.note}>
          {note ? <RowNote tone="warning">{note}</RowNote> : null}
          {problem ? <RowNote>{problem}</RowNote> : null}
        </View>
      ) : null}
    </Section>
  );
}

// The emotion's estimate, why, its source and its time (ADR 62 Q12).
function EmotionSection({
  emotion,
  timezone,
}: {
  emotion: Schemas["EmotionView"] | null | undefined;
  timezone: string;
}) {
  const palette = usePalette();
  const details = emotionDetails(emotion, timezone);
  return (
    <Section title="Latest estimate">
      <View style={styles.block}>
        <Text style={[styles.label, { color: palette.text }]}>
          {details.label}
        </Text>
        <Text style={{ color: palette.text }}>{details.reason}</Text>
        <Text style={[styles.small, { color: palette.muted }]}>
          {details.source}
          {details.time ? ` · ${details.time}` : ""}
        </Text>
      </View>
    </Section>
  );
}

// The owner's Processing Guidance fields this control uses, each one text
// for all chats, opening its editor (ADR 62 Q11, Q17). Members have none.
function GuidanceSection({ control }: { control: AiControl }) {
  const { settings } = useSettings();
  const guidance = settings?.guidance;
  const fields = controlGuidance(control);
  if (!guidance || !fields.length) return null;
  return (
    <Section title="Guidance">
      {fields.map((field, index) => {
        const current = guidance.fields[field.name] ?? "";
        const edited = current !== (guidance.defaults[field.name] ?? "");
        return (
          <React.Fragment key={field.name}>
            {index ? <Divider /> : null}
            <LinkRow
              label={field.label}
              detail={guidanceDetail(edited)}
              onPress={() =>
                router.push({
                  pathname: "/guidance/[field]",
                  params: { field: field.name },
                })
              }
            />
          </React.Fragment>
        );
      })}
      <View style={styles.block}>
        <Explanation>{`Version ${guidance.version}. ${EXPLAIN.guidance}`}</Explanation>
      </View>
    </Section>
  );
}

const styles = StyleSheet.create({
  block: { paddingVertical: 10, gap: 4 },
  note: { paddingBottom: 10, gap: 4 },
  label: { fontSize: 17, fontWeight: "600" },
  small: { fontSize: 13 },
});
