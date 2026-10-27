import Ionicons from "@expo/vector-icons/Ionicons";
import { Redirect, router, useLocalSearchParams } from "expo-router";
import React, { useEffect, useRef, useState } from "react";
import {
  KeyboardAvoidingView,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { shownAnswer } from "../../../../shared/ask";
import { peerTab } from "../../../../shared/chatRoute";
import { dayKey } from "../../../../shared/groupBriefings";
import type { Schemas } from "../../../src/api";
import { useConnection } from "../../../src/connection";
import { SourceLinks } from "../../../src/GroupReading";
import { usePalette } from "../../../src/theme";
import { BackHeader } from "../../../src/ui";

// The longest question the service takes (ADR 59).
const LONGEST = 500;

// Ask this group (ADR 59 Q1), opened from a group's ⋮ sheet: the question
// field at the bottom and the answer above it with its source links. The
// answer lives only on this screen (Q9): a source link opens the group over
// it, Back from there returns to it, and leaving it drops the answer.
export default function AskRoute() {
  const params = useLocalSearchParams<{ account: string; peer: string }>();
  const account = Number(params.account);
  const peer = String(params.peer ?? "");
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (
    !Number.isSafeInteger(account) ||
    account <= 0 ||
    !peer ||
    peerTab(peer) !== "group"
  )
    return <Redirect href="/chats" />;
  if (connection.status !== "paired") return null;
  return <AskScreen key={`${account}/${peer}`} account={account} peer={peer} />;
}

function AskScreen({ account, peer }: { account: number; peer: string }) {
  const { connection, api } = useConnection();
  const palette = usePalette();
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<Schemas["AskView"] | null>(null);
  const [refusal, setRefusal] = useState("");
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  // The not-found line's "today" is the service's calendar day.
  const timezone =
    (connection.status === "paired" && connection.accounts?.timezone) || "UTC";
  const shown = answer && shownAnswer(answer, dayKey(new Date(), timezone));
  const ready = !!question.trim() && !asking;

  // Asking again replaces the answer. The service answers one question at a
  // time per account and says why it refuses one.
  async function ask() {
    if (!ready) return;
    setAsking(true);
    setAnswer(null);
    setRefusal("");
    try {
      const value = await api<Schemas["AskView"]>(
        `accounts/${account}/chats/${encodeURIComponent(peer)}/ask`,
        { body: { question: question.trim() } },
      );
      if (live.current) setAnswer(value);
    } catch (problem) {
      if (live.current)
        setRefusal(
          problem instanceof Error ? problem.message : String(problem),
        );
    } finally {
      if (live.current) setAsking(false);
    }
  }

  // The group at that original, highlighted, over this screen.
  function open(source: number) {
    router.push({
      pathname: "/chat/[account]/[peer]",
      params: { account: String(account), peer, source: String(source) },
    });
  }

  return (
    <SafeAreaView
      style={[styles.fill, { backgroundColor: palette.background }]}
    >
      <View style={styles.header}>
        <BackHeader title="Ask this group" />
      </View>
      <KeyboardAvoidingView style={styles.fill} behavior="padding">
        <ScrollView
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
        >
          <View accessibilityLiveRegion="polite" style={styles.answer}>
            {asking ? (
              <Text style={[styles.small, { color: palette.muted }]}>
                Answering…
              </Text>
            ) : null}
            {shown?.line ? (
              <Text style={[styles.body, { color: palette.text }]}>
                {shown.line}
              </Text>
            ) : null}
            {shown?.statements.map((statement, index) => (
              <View key={index} style={styles.statement}>
                <Text selectable style={[styles.body, { color: palette.text }]}>
                  {statement.text}
                </Text>
                <SourceLinks
                  citations={statement.citations}
                  palette={palette}
                  onCite={open}
                />
              </View>
            ))}
          </View>
          {refusal ? (
            <Text
              accessibilityRole="alert"
              style={[styles.small, { color: palette.error }]}
            >
              {refusal}
            </Text>
          ) : null}
        </ScrollView>
        <View style={[styles.bar, { borderColor: palette.line }]}>
          <TextInput
            multiline
            accessibilityLabel="Question"
            maxLength={LONGEST}
            value={question}
            onChangeText={setQuestion}
            style={[
              styles.input,
              {
                color: palette.text,
                backgroundColor: palette.panel,
                borderColor: palette.line,
              },
            ]}
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Ask"
            accessibilityState={{ disabled: !ready, busy: asking }}
            disabled={!ready}
            onPress={() => void ask()}
            style={[
              styles.send,
              { backgroundColor: palette.accent, opacity: ready ? 1 : 0.4 },
            ]}
          >
            <Ionicons name="send" size={18} color={palette.onAccent} />
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 4 },
  content: { padding: 20, gap: 14 },
  answer: { gap: 14 },
  statement: { gap: 4 },
  body: { fontSize: 16, lineHeight: 22 },
  small: { fontSize: 13 },
  // The chat composer's field and Send (Composer.tsx).
  bar: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 6,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingTop: 8,
    paddingBottom: 6,
  },
  input: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 8,
    fontSize: 16,
    maxHeight: 140,
  },
  send: {
    width: 44,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
  },
});
