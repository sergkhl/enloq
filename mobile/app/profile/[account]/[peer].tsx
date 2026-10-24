import Ionicons from "@expo/vector-icons/Ionicons";
import * as Clipboard from "expo-clipboard";
import { Redirect, router, useLocalSearchParams } from "expo-router";
import React, { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { chatLink } from "../../../../shared/chatRoute";
import { emotionDetails } from "../../../../shared/emotion";
import { openPath } from "../../../../shared/newMessage";
import {
  SUGGESTION_STYLES,
  type SuggestionStyle,
} from "../../../../shared/suggestions";
import { Unreachable, type Schemas } from "../../../src/api";
import { Avatar } from "../../../src/Avatar";
import { chatKey, readCached } from "../../../src/cache";
import { useConnection } from "../../../src/connection";
import type { Snapshot } from "../../../src/conversation";
import { MemoryPanel } from "../../../src/MemoryPanel";
import {
  ChoiceRow,
  Explanation,
  RowNote,
  Section,
  SwitchRow,
} from "../../../src/settingsUi";
import { usePalette } from "../../../src/theme";
import { TraitsPanel } from "../../../src/TraitsPanel";
import { BackHeader, Button, Screen } from "../../../src/ui";

type Profile = Schemas["CorrespondentProfile"];
// Calm this chat and Remember this chat answer alike.
type ChatSetting = Schemas["Calming"] | Schemas["Remembering"];
type Suggesting = Schemas["SuggestionSettings"];
type GroupProfile = Schemas["GroupProfile"];
type MemberProfileValue = Schemas["MemberProfile"];

// A chat's header photo and name open its profile: a person's Correspondent
// Profile (ADR 46 Q11, CU3) or a group's Group Profile (ADR 47 Q7). Each reads
// Telegram on opening and keeps nothing, shows what the phone kept of the
// chat while offline, and holds the chat's Copy chat link (ADR 47 Q8).
export default function ProfileRoute() {
  const params = useLocalSearchParams<{
    account: string;
    peer: string;
    member?: string;
  }>();
  const account = Number(params.account);
  const peer = String(params.peer ?? "");
  const member = params.member ? String(params.member) : "";
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  if (member && /^(chat|channel):/.test(peer))
    return <MemberProfile account={account} group={peer} member={member} />;
  return /^(chat|channel):/.test(peer) ? (
    <GroupProfileScreen account={account} peer={peer} />
  ) : (
    <CorrespondentProfile account={account} peer={peer} />
  );
}

// What the phone kept of the chat, for its photo and name.
function useKeptChat(account: number, peer: string) {
  const [chat, setChat] = useState<Snapshot | null>(null);
  useEffect(() => {
    let live = true;
    void readCached<Snapshot>(chatKey(account, peer)).then(
      (kept) => live && setChat(kept),
    );
    return () => {
      live = false;
    };
  }, [account, peer]);
  return chat;
}

// One read of the profile on each opening, once the saved pairing is in hand.
function useRead<T>(path: string) {
  const { api, connection } = useConnection();
  const [value, setValue] = useState<T | null>(null);
  const [problem, setProblem] = useState("");
  const [offline, setOffline] = useState(false);
  const paired = connection.status === "paired";
  useEffect(() => {
    if (!paired) return;
    let live = true;
    setProblem("");
    setOffline(false);
    void api<T>(path).then(
      (answer) => live && setValue(answer),
      (error) => {
        if (!live) return;
        if (error instanceof Unreachable) setOffline(true);
        else setProblem(error instanceof Error ? error.message : String(error));
      },
    );
    return () => {
      live = false;
    };
  }, [api, path, paired]);
  return { value, problem, offline };
}

// The chat's link for a browser or another phone (ADR 36), kept in the
// profile since ADR 47 Q8.
function CopyChatLink({ account, peer }: { account: number; peer: string }) {
  const { connection } = useConnection();
  const palette = usePalette();
  const [copied, setCopied] = useState("");
  async function copy() {
    if (connection.status !== "paired") return;
    const link = chatLink(connection.credential.origin, "/", account, peer);
    if (!link) return;
    await Clipboard.setStringAsync(link);
    setCopied("Chat link copied");
  }
  return (
    <>
      <View style={styles.copy}>
        <Ionicons name="link-outline" size={20} color={palette.accent} />
        <View style={styles.fill}>
          <Button label="Copy chat link" onPress={() => void copy()} />
        </View>
      </View>
      {copied ? (
        <Text style={[styles.small, { color: palette.muted }]}>{copied}</Text>
      ) : null}
    </>
  );
}

// A chat's own switch: Calm this chat, on unless turned off here (ADR 53
// Q4), or Remember this chat, off unless turned on here since ADR 56 (Q9).
function ChatSwitch({
  account,
  peer,
  setting,
  label,
  explanation,
  testID,
}: {
  account: number;
  peer: string;
  setting: "calming" | "remember";
  label: string;
  explanation: string;
  testID: string;
}) {
  const { api } = useConnection();
  const path = `accounts/${account}/chats/${encodeURIComponent(peer)}/${setting}`;
  const { value, problem } = useRead<ChatSetting>(path);
  const [saved, setSaved] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const enabled = saved ?? value?.enabled ?? null;
  async function change(next: boolean) {
    setBusy(true);
    setFailure("");
    try {
      const answer = await api<ChatSetting>(path, { body: { enabled: next } });
      setSaved(answer.enabled);
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SwitchRow
        label={label}
        explanation={explanation}
        value={enabled ?? false}
        disabled={enabled === null || busy}
        testID={testID}
        onChange={(next) => void change(next)}
      />
      {failure || problem ? (
        <RowNote tone="error">{failure || problem}</RowNote>
      ) : null}
    </>
  );
}

// The chat's Suggestions choice (ADR 56 Q12 A): Off, On or, once it can be
// chosen, Idioms; On unless chosen here. A choice applies from the next
// message that qualifies; a set already written stays (Q5).
function SuggestionsChoice({
  account,
  peer,
}: {
  account: number;
  peer: string;
}) {
  const { api } = useConnection();
  const path = `accounts/${account}/chats/${encodeURIComponent(peer)}/suggestions/settings`;
  const { value, problem } = useRead<Suggesting>(path);
  const [saved, setSaved] = useState<Suggesting | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const current = saved ?? value;
  async function change(automatic: boolean, style: SuggestionStyle) {
    setBusy(true);
    setFailure("");
    try {
      setSaved(await api<Suggesting>(path, { body: { automatic, style } }));
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SwitchRow
        label="Automatic suggestions"
        value={current?.automatic ?? true}
        disabled={!current || busy}
        testID="automatic-suggestions"
        explanation="Prepare suggestions automatically; manual generation stays available."
        onChange={(on) => current && void change(on, current.style)}
      />
      <ChoiceRow
        label="Style"
        options={SUGGESTION_STYLES}
        value={current?.style ?? null}
        explanation={
          SUGGESTION_STYLES.find((s) => s.value === current?.style)?.explanation
        }
        disabled={!current || busy}
        testID="suggestion-style"
        onChange={(style) => current && void change(current.automatic, style)}
      />
      {failure || problem ? (
        <RowNote tone="error">{failure || problem}</RowNote>
      ) : null}
    </>
  );
}

// The Correspondent Profile (ADR 46 Q11, CU3): the person's photo and name,
// with their @username, a phone Telegram shares and bio; the emotion's
// details; Calm this chat, the Suggestions choice (ADR 56) and Remember this
// chat; what the person cares about and their traits (ADR 55 Q25); and Copy
// chat link.
function CorrespondentProfile({
  account,
  peer,
}: {
  account: number;
  peer: string;
}) {
  const palette = usePalette();
  const chat = useKeptChat(account, peer);
  const {
    value: profile,
    problem,
    offline,
  } = useRead<Profile>(
    `accounts/${account}/chats/${encodeURIComponent(peer)}/profile`,
  );
  const title = chat?.chat.title ?? "";
  const details = emotionDetails(chat?.emotion, chat?.timezone ?? "UTC");
  return (
    <Screen header={<BackHeader title="Profile" />}>
      <View style={styles.head}>
        <Avatar
          account={account}
          peer={peer}
          photoId={chat?.chat.photo_id}
          name={title}
          size={96}
        />
        <Text
          accessibilityRole="header"
          style={[styles.name, { color: palette.text }]}
        >
          {profile?.name || title}
        </Text>
        {profile?.username ? (
          <Text style={{ color: palette.muted }}>@{profile.username}</Text>
        ) : null}
        {profile?.phone ? (
          <Text style={{ color: palette.muted }}>+{profile.phone}</Text>
        ) : null}
      </View>
      {profile?.bio ? (
        <Text style={[styles.bio, { color: palette.text }]}>{profile.bio}</Text>
      ) : null}
      {offline ? (
        <RowNote tone="warning">
          The person's Telegram details, memory and traits need the server.
        </RowNote>
      ) : problem ? (
        <RowNote tone="error">{problem}</RowNote>
      ) : !profile ? (
        <Explanation>Reading the profile…</Explanation>
      ) : null}
      <Section title="Emotion">
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
      {offline ? null : (
        <>
          <Section title="Soften hostile messages">
            {/* It applies to messages rewritten from then on; calmed ones
                stay calmed (ADR 53 Q9). */}
            <ChatSwitch
              account={account}
              peer={peer}
              setting="calming"
              label="Soften hostile messages"
              explanation="Hostile wording is softened while keeping every fact, request, deadline and threat. Anger or frustration alone stays unchanged."
              testID="calm-this-chat"
            />
          </Section>
          <Section title="Suggestions">
            <SuggestionsChoice account={account} peer={peer} />
          </Section>
          <Section title="Memory">
            {/* Off by default (ADR 56 Q9); off, the chat teaches neither
                memory and nothing in it reads them (ADR 55 Q18). */}
            <ChatSwitch
              account={account}
              peer={peer}
              setting="remember"
              label="Remember this chat"
              explanation="Off by default. On, Enloq notes what this person cares about and their traits, and what you care about from this chat. Off, this chat teaches and uses neither; what's noted stays until you clear it."
              testID="remember-this-chat"
            />
          </Section>
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
      )}
      <CopyChatLink account={account} peer={peer} />
    </Screen>
  );
}

// A group member's profile (ADR 51 Q14), opened from their avatar or name:
// their photo and name, @username, a phone Telegram shares and bio, read on
// opening and kept nowhere; no emotion or memory.
function MemberProfile({
  account,
  group,
  member,
}: {
  account: number;
  group: string;
  member: string;
}) {
  const palette = usePalette();
  const { api } = useConnection();
  const [opening, setOpening] = useState("");
  // Message opens the private chat with them, as New Message does.
  async function message() {
    setOpening("Opening…");
    try {
      await api(openPath(account, member), { body: {} });
      setOpening("");
      router.push({
        pathname: "/chat/[account]/[peer]",
        params: { account: String(account), peer: member },
      });
    } catch (error) {
      setOpening(error instanceof Error ? error.message : String(error));
    }
  }
  const {
    value: profile,
    problem,
    offline,
  } = useRead<MemberProfileValue>(
    `accounts/${account}/chats/${encodeURIComponent(group)}/members/${encodeURIComponent(member)}/profile`,
  );
  return (
    <Screen header={<BackHeader title="Profile" />}>
      <View style={styles.head}>
        <Avatar
          account={account}
          peer={member}
          photoId={profile?.photo_id}
          name={profile?.name ?? ""}
          size={96}
        />
        <Text
          accessibilityRole="header"
          style={[styles.name, { color: palette.text }]}
        >
          {profile?.name ?? ""}
        </Text>
        {profile?.username ? (
          <Text style={{ color: palette.muted }}>@{profile.username}</Text>
        ) : null}
        {profile?.phone ? (
          <Text style={{ color: palette.muted }}>+{profile.phone}</Text>
        ) : null}
      </View>
      {profile?.bio ? (
        <Text style={[styles.bio, { color: palette.text }]}>{profile.bio}</Text>
      ) : null}
      {offline ? (
        <RowNote tone="warning">The profile needs the server.</RowNote>
      ) : problem ? (
        <RowNote tone="error">{problem}</RowNote>
      ) : !profile ? (
        <Explanation>Reading the profile…</Explanation>
      ) : null}
      <View style={styles.copy}>
        <Ionicons name="chatbubble-outline" size={20} color={palette.accent} />
        <View style={styles.fill}>
          <Button label="Message" onPress={() => void message()} />
        </View>
      </View>
      {opening ? (
        <Text style={[styles.small, { color: palette.muted }]}>{opening}</Text>
      ) : null}
    </Screen>
  );
}

// The Group Profile (ADR 47 Q7): the group's photo and title, its @username
// when public, its member count and description, with no member list, then
// Copy chat link. Group AI processing and Hide bots stay in the ⋮ sheet.
function GroupProfileScreen({
  account,
  peer,
}: {
  account: number;
  peer: string;
}) {
  const palette = usePalette();
  const chat = useKeptChat(account, peer);
  const {
    value: profile,
    problem,
    offline,
  } = useRead<GroupProfile>(
    `accounts/${account}/chats/${encodeURIComponent(peer)}/group-profile`,
  );
  const title = profile?.title || chat?.chat.title || "";
  const members = profile?.members;
  return (
    <Screen header={<BackHeader title="Group" />}>
      <View style={styles.head}>
        <Avatar
          account={account}
          peer={peer}
          photoId={chat?.chat.photo_id}
          name={title}
          size={96}
        />
        <Text
          accessibilityRole="header"
          style={[styles.name, { color: palette.text }]}
        >
          {title}
        </Text>
        {profile?.username ? (
          <Text style={{ color: palette.muted }}>@{profile.username}</Text>
        ) : null}
        {typeof members === "number" ? (
          <Text style={{ color: palette.muted }}>
            {`${members.toLocaleString("en-US")} ${members === 1 ? "member" : "members"}`}
          </Text>
        ) : null}
      </View>
      {profile?.description ? (
        <Text style={[styles.bio, { color: palette.text }]}>
          {profile.description}
        </Text>
      ) : null}
      {offline ? (
        <RowNote tone="warning">
          The group's Telegram details need the server.
        </RowNote>
      ) : problem ? (
        <RowNote tone="error">{problem}</RowNote>
      ) : !profile ? (
        <Explanation>Reading the group…</Explanation>
      ) : null}
      <CopyChatLink account={account} peer={peer} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  head: { alignItems: "center", gap: 4, paddingVertical: 8 },
  name: { fontSize: 22, fontWeight: "700", marginTop: 8, textAlign: "center" },
  bio: { fontSize: 16, lineHeight: 22 },
  block: { padding: 12, gap: 4 },
  label: { fontSize: 17, fontWeight: "600" },
  small: { fontSize: 13 },
  copy: { flexDirection: "row", alignItems: "center", gap: 10 },
  fill: { flex: 1 },
});
