import Ionicons from "@expo/vector-icons/Ionicons";
import { Redirect, router, useLocalSearchParams } from "expo-router";
import React, { useEffect, useState } from "react";
import {
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { previewTime, type HourCycle } from "../../shared/messageTime";
import { AccountSheet } from "../src/AccountSheet";
import { Avatar } from "../src/Avatar";
import { useConnection } from "../src/connection";
import {
  useChatList,
  useReadingView,
  type ChatKind,
  type ConversationRow,
  type ReadingView,
} from "../src/data";
import { NotificationAsk } from "../src/NotificationAsk";
import { phonePush } from "../src/phonePush";
import { ConnectionStatus } from "../src/Status";
import { usePalette, type Palette } from "../src/theme";
import { useHourCycle } from "../src/time";
import { Body, Button } from "../src/ui";
import { RetainedGroups } from "../src/RetainedGroups";
import { RemindersList } from "../src/Reminders";
import { Conversation } from "./chat/[account]/[peer]";

export default function ChatsRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  // The list's readers start once the stored credential is loaded.
  if (connection.status !== "paired") return null;
  return <Chats />;
}

// The account's three tabs: its two lists and its own Saved Messages
// (ADR 50 Q2).
type Tab = ChatKind | "saved" | "reminders";

// The tab each account showed last, while the app runs (ADR 34's tab rule);
// the app opens on Private.
const shownLists = new Map<number, Tab>();

const EMPTY: Record<ChatKind, string> = {
  private: "No private conversations in the last seven days.",
  group: "No groups in the last seven days.",
};

// The chosen account's seven-day Private and Groups lists (ADR 38 Q6, ADR 23,
// ADR 42 AG2), and its Saved Messages in the third tab (ADR 50).
function Chats() {
  const params = useLocalSearchParams<{ tab?: string }>();
  const [reminders, setReminders] = useState(params.tab === "reminders");
  useEffect(() => setReminders(params.tab === "reminders"), [params.tab]);
  const { connection, account, selectAccount, refresh } = useConnection();
  const palette = usePalette();
  const [lists, setLists] = useState(() => new Map(shownLists));
  const tab = reminders
    ? "reminders"
    : (account !== null && lists.get(account)) || "private";
  const kind: ChatKind =
    tab === "saved" || tab === "reminders" ? "private" : tab;
  const { rows, reload } = useChatList(
    account,
    tab === "saved" || tab === "reminders" ? null : kind,
  );
  const { view } = useReadingView();
  const [choosing, setChoosing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const hourCycle = useHourCycle();
  if (connection.status !== "paired") return <Redirect href="/pair" />;
  const accounts = (connection.accounts?.accounts ?? []).filter(
    (item) => item.state === "authorized",
  );
  const shown = accounts.find((item) => item.account_id === account);
  const timezone = connection.accounts?.timezone ?? "UTC";

  function showList(next: Tab) {
    setReminders(next === "reminders");
    router.setParams({ tab: next === "reminders" ? "reminders" : undefined });
    if (next === "reminders") return;
    if (account === null) return;
    shownLists.set(account, next);
    setLists(new Map(shownLists));
  }

  async function pull() {
    setRefreshing(true);
    try {
      await Promise.all([refresh(), reload()]);
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <SafeAreaView
      edges={["top", "left", "right"]}
      style={[styles.fill, { backgroundColor: palette.background }]}
    >
      {/* Once per pairing, Android only (ADR 47 Q4). */}
      {phonePush && connection.accounts ? <NotificationAsk /> : null}
      <View style={styles.header}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={
            shown ? `Account: ${shown.name}. Switch account` : "Accounts"
          }
          onPress={() => setChoosing(true)}
          style={styles.switcher}
        >
          {shown ? (
            <Avatar
              account={shown.account_id}
              peer={`user:${shown.account_id}`}
              photoId={shown.photo_id}
              name={shown.name}
              size={34}
            />
          ) : null}
          <Text
            numberOfLines={1}
            style={[styles.accountName, { color: palette.text }]}
          >
            {tab === "reminders" ? "All accounts" : shown?.name || "Enloq"}
          </Text>
          {/* The sheet holds Settings too, so it always has more to show
              (ADR 51 Q21). */}
          <Ionicons name="chevron-down" size={18} color={palette.muted} />
        </Pressable>
        {/* The tabs share the header row, as icons (ADR 50 Q1). */}
        {connection.accounts && accounts.length === 0 ? null : (
          <ListSwitch tab={tab} onChoose={showList} palette={palette} />
        )}
        {/* New Message takes the gear's place on Private; Groups and Saved
            keep the place empty, so the icons never move (ADR 51 Q15, Q19,
            Q20). Settings is in the account sheet. */}
        <View style={styles.compose}>
          {tab === "private" && account !== null ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="New message"
              hitSlop={8}
              onPress={() =>
                router.push({
                  pathname: "/new-message",
                  params: { account: String(account) },
                })
              }
            >
              <Ionicons
                name="create-outline"
                size={24}
                color={palette.accent}
              />
            </Pressable>
          ) : null}
        </View>
      </View>
      <View style={styles.status}>
        <ConnectionStatus />
      </View>
      {tab === "reminders" ? (
        <RemindersList />
      ) : connection.accounts && accounts.length === 0 ? (
        <View style={[styles.empty, styles.gap]}>
          <Body muted>No Telegram account is signed in on this server.</Body>
          <Button
            label="Add account"
            primary
            disabled={!connection.accounts.login_available}
            onPress={() => router.push("/login")}
          />
        </View>
      ) : tab === "saved" && account !== null ? (
        <Conversation
          key={`${account}/saved`}
          account={account}
          peer={`user:${account}`}
          embedded
        />
      ) : (
        <>
          <FlatList
            data={rows ?? []}
            keyExtractor={(row) => row.peer}
            refreshControl={
              <RefreshControl
                refreshing={refreshing}
                onRefresh={() => void pull()}
                tintColor={palette.accent}
              />
            }
            // Retained group work at the foot of Groups (ADR 46 CU6).
            ListFooterComponent={
              kind === "group" && account ? (
                <RetainedGroups account={account} version={rows} />
              ) : null
            }
            ListEmptyComponent={
              <View style={styles.empty}>
                <Body muted>
                  {rows === null
                    ? connection.unreachable
                      ? "Conversations show once the server answers."
                      : "Loading conversations…"
                    : EMPTY[kind]}
                </Body>
              </View>
            }
            renderItem={({ item }) => (
              <ChatRow
                row={item}
                account={account!}
                view={view}
                timezone={timezone}
                hourCycle={hourCycle}
                palette={palette}
              />
            )}
          />
        </>
      )}
      <AccountSheet
        visible={choosing}
        accounts={accounts}
        current={account}
        onSelect={(id) => {
          setReminders(false);
          router.setParams({ tab: undefined });
          selectAccount(id);
        }}
        onClose={() => setChoosing(false)}
      />
    </SafeAreaView>
  );
}

// Private | Groups | Saved as icons in the header (ADR 42 AG2, ADR 50 Q1,
// Q9, Q10): outlined, the chosen one filled on the accent.
const TABS = [
  { tab: "private", label: "Private", icon: "person" },
  { tab: "group", label: "Groups", icon: "people" },
  { tab: "saved", label: "Saved", icon: "bookmark" },
  { tab: "reminders", label: "Reminders", icon: "time" },
] as const;

function ListSwitch({
  tab,
  onChoose,
  palette,
}: {
  tab: Tab;
  onChoose: (tab: Tab) => void;
  palette: Palette;
}) {
  return (
    <View
      accessibilityRole="tablist"
      style={[styles.switch, { borderColor: palette.line }]}
    >
      {TABS.map((option) => {
        const selected = option.tab === tab;
        return (
          <Pressable
            key={option.tab}
            accessibilityRole="tab"
            accessibilityLabel={option.label}
            accessibilityState={{ selected }}
            onPress={() => onChoose(option.tab)}
            style={[
              styles.switchOption,
              selected ? { backgroundColor: palette.accent } : null,
            ]}
          >
            <Ionicons
              name={selected ? option.icon : `${option.icon}-outline`}
              size={18}
              color={selected ? palette.onAccent : palette.accent}
            />
          </Pressable>
        );
      })}
    </View>
  );
}

function ChatRow({
  row,
  account,
  view,
  timezone,
  hourCycle,
  palette,
}: {
  row: ConversationRow;
  account: number;
  view: ReadingView;
  timezone: string;
  hourCycle: HourCycle | undefined;
  palette: Palette;
}) {
  const preview =
    (view === "ai" ? row.preview?.ai : row.preview?.original) ?? "";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${row.title}${row.unseen ? ", new messages" : ""}${row.muted ? ", muted" : ""}`}
      onPress={() =>
        router.push({
          pathname: "/chat/[account]/[peer]",
          params: { account: String(account), peer: row.peer },
        })
      }
      style={({ pressed }) => [
        styles.chatRow,
        pressed ? { backgroundColor: palette.panel } : null,
      ]}
    >
      <Avatar
        account={account}
        peer={row.peer}
        photoId={row.photo_id}
        name={row.title}
        size={48}
      />
      <View style={styles.fill}>
        <View style={styles.line}>
          <Text
            numberOfLines={1}
            style={[styles.rowTitle, styles.fill, { color: palette.text }]}
          >
            {row.title}
          </Text>
          {row.muted ? (
            <Ionicons
              name="notifications-off"
              size={14}
              color={palette.muted}
            />
          ) : null}
          <Text style={[styles.time, { color: palette.muted }]}>
            {previewTime(row.preview?.date, timezone, hourCycle)}
          </Text>
        </View>
        <View style={styles.line}>
          {/* A deleted newest message, by what it last said (ADR 52 Q10). */}
          {row.preview?.deleted ? (
            <Ionicons
              accessibilityLabel="Deleted message"
              name="trash-outline"
              size={14}
              color={palette.muted}
            />
          ) : null}
          <Text
            numberOfLines={1}
            style={[styles.preview, styles.fill, { color: palette.muted }]}
          >
            {preview}
          </Text>
          {row.unseen ? (
            <View style={[styles.dot, { backgroundColor: palette.accent }]} />
          ) : null}
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  compose: { width: 24, height: 24 },
  fill: { flex: 1 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 4,
  },
  switcher: { flexDirection: "row", alignItems: "center", gap: 10, flex: 1 },
  accountName: { fontSize: 18, fontWeight: "700", flexShrink: 1 },
  status: { paddingHorizontal: 16, gap: 8, paddingBottom: 4 },
  switch: {
    flexDirection: "row",
    borderWidth: 1,
    borderRadius: 8,
    overflow: "hidden",
  },
  switchOption: { paddingHorizontal: 8, paddingVertical: 4 },
  empty: { padding: 24 },
  gap: { gap: 14 },
  chatRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  line: { flexDirection: "row", alignItems: "center", gap: 6 },
  rowTitle: { fontSize: 16, fontWeight: "600" },
  time: { fontSize: 13 },
  preview: { fontSize: 15, marginTop: 2 },
  dot: { width: 10, height: 10, borderRadius: 5 },
});
