import Ionicons from "@expo/vector-icons/Ionicons";
import * as Clipboard from "expo-clipboard";
import { Redirect, router, useLocalSearchParams } from "expo-router";
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Pressable,
  StyleSheet,
  Text,
  View,
  type ViewToken,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { peerTab } from "../../../../shared/chatRoute";
import { messageTime, type HourCycle } from "../../../../shared/messageTime";
import {
  forwardedGroups,
  forwardedLine,
  forwardOf,
} from "../../../../shared/forwarding";
import {
  blockAnchor,
  blockError,
  canRetry,
  unanalysedNote,
} from "../../../../shared/aiControls";
import { reactionChoice } from "../../../../shared/reactions";
import { isReply, outsideReplies } from "../../../../shared/replyQuote";
import { editedName } from "../../../../shared/versions";
import { useAttention } from "../../../src/attention";
import { Avatar } from "../../../src/Avatar";
import { ComposerBar } from "../../../src/Composer";
import { useConnection } from "../../../src/connection";
import {
  STAGES,
  readingItems,
  unreadAnchor,
  useConversation,
  blockText,
  type Block,
  type NativeMessage,
  type ReadingItem,
  type Snapshot,
} from "../../../src/conversation";
import { useReadingView, type ReadingView } from "../../../src/data";
import { GroupConversation } from "../../../src/GroupConversation";
import {
  useComposer,
  visibleOperations,
  type Operation,
} from "../../../src/drafts";
import { BubbleMenu } from "../../../src/bubbleMenu";
import {
  useForwardDetails,
  useReactionOptions,
  useVersions,
} from "../../../src/details";
import { mediaActions, useMediaActions, voiceQueue } from "../../../src/Media";
import {
  ForwardedLines,
  ReactionChips,
  VersionControls,
} from "../../../src/MessageDetails";
import { EarlierBar } from "../../../src/EarlierBar";
import {
  DeletingMark,
  DeletionCards,
  DeletionContext,
  DeletionReview,
  Selectable,
  SelectionBar,
  useMessageDeletion,
  useMessageDeletionState,
} from "../../../src/MessageDeletion";
import { MessageMenu, type MenuTarget } from "../../../src/MessageMenu";
import { forgetRows, MediaRow, showRows } from "../../../src/motion";
import { setVoiceQueue, stopPlayback } from "../../../src/playback";
import { PendingSend } from "../../../src/PendingSend";
import { SwipeToReply } from "../../../src/SwipeToReply";
import { GalleryProvider } from "../../../src/MediaCarousel";
import { runCorners, Tail } from "../../../src/Tail";
import { AlbumGrid } from "../../../src/AlbumGrid";
import { messageRuns, type RunPlace } from "../../../../shared/runs";
import { albumRuns } from "../../../../shared/gallery";
import { MessageBody, ReadingToggle, UnreadLine } from "../../../src/Reading";
import { useReplies, type Replies } from "../../../src/replies";
import { ReplyQuote } from "../../../src/ReplyQuote";
import { ConnectionStatus } from "../../../src/Status";
import { usePalette, type Palette } from "../../../src/theme";
import { useHourCycle } from "../../../src/time";
import { useFocused, useFocusedViewing } from "../../../src/viewing";
import { SettingsLink, STAGE_TARGETS } from "../../../src/settingsLinks";
import { ChatTitle } from "../../../src/VoiceNote";
import { ReminderBar } from "../../../src/Reminders";
import { remindersPath } from "../../../../shared/reminders";

const EMOTIONS: Record<string, [string, string]> = {
  neutral: ["😐", "Neutral"],
  positive: ["🙂", "Positive or warm"],
  excited: ["🤩", "Excited"],
  sad: ["😔", "Sad"],
  worried: ["😟", "Worried"],
  frustrated: ["😤", "Frustrated"],
  angry: ["😠", "Angry"],
  confused: ["😕", "Confused"],
  unknown: ["😐", "Mixed or uncertain"],
};

// The list's rows: reading items, then the sends not yet in the messages.
type Row = ReadingItem | { kind: "pending"; key: string; operation: Operation };

export default function ConversationRoute() {
  const params = useLocalSearchParams<{
    account: string;
    peer: string;
  }>();
  const account = Number(params.account);
  const peer = String(params.peer ?? "");
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (!Number.isSafeInteger(account) || account <= 0 || !peer)
    return <Redirect href="/chats" />;
  // A chat opened by a link waits for the stored credential.
  if (connection.status !== "paired") return null;
  // One screen per chat: its readers belong to this account and peer. A
  // `chat:` or `channel:` peer is a Group Chat (ADR 42 AG3).
  return peerTab(peer) === "group" ? (
    <GroupConversation
      key={`${account}/${peer}`}
      account={account}
      peer={peer}
    />
  ) : (
    <Conversation key={`${account}/${peer}`} account={account} peer={peer} />
  );
}

export function Conversation({
  account,
  peer,
  embedded = false,
}: {
  account: number;
  peer: string;
  // Saved Messages inside its tab, under the list's header (ADR 50 Q2).
  embedded?: boolean;
}) {
  const { connection, api } = useConnection();
  const palette = usePalette();
  const { snapshot, live, readAt, captured, error, loadEarlier, reload } =
    useConversation(account, peer);
  const composer = useComposer(account, peer, snapshot, reload, readAt);
  // Opening a chat with pending reminders prepares their set when the
  // current one no longer fits (ADR 58 Q5); once per opening.
  const pending = snapshot?.reminders ?? [];
  const remindersOpened = useRef(false);
  useEffect(() => {
    if (remindersOpened.current || !live || !pending.length) return;
    remindersOpened.current = true;
    api(`${remindersPath(account, peer)}/open`, { body: {} }).then(
      () => void reload(),
      () => undefined,
    );
  }, [api, account, peer, live, pending.length]);
  const [menu, setMenu] = useState<MenuTarget | null>(null);
  const { view: chosen, choose } = useReadingView();
  // The account's own Saved Messages (ADR 50): originals only, with no AI,
  // unseen messages or reactions (Q3, Q17).
  const saved = peer === `user:${account}`;
  const view: ReadingView = saved ? "originals" : chosen;
  const hourCycle = useHourCycle();
  const focused = useFocused();
  // Only what the server just returned counts as seen, never the offline copy.
  useFocusedViewing(account, peer, !saved && focused && live, snapshot);
  useAttention(account, peer, focused);
  // In its tab the conversation starts below the list's header, while the
  // keyboard's overlap counts from the top of the window (ADR 50).
  const root = useRef<React.ElementRef<typeof SafeAreaView>>(null);
  const [top, setTop] = useState(0);
  const [copied, setCopied] = useState("");
  // A note stays long enough to read a sentence, such as why a transcript
  // couldn't be loaded.
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(""), 5000);
    return () => clearTimeout(timer);
  }, [copied]);
  const media = useMediaActions(account, peer, setCopied);
  // Forward headers a cached message still lacks (ADR 46 CU2).
  const detail = useForwardDetails(account, peer, snapshot?.messages ?? []);
  const reactions = useReactionOptions(account);
  const [away, setAway] = useState(false);
  // Blocks shown otherwise than the reading view, until it changes (CU3).
  const [blockViews, setBlockViews] = useState<Record<string, ReadingView>>({});
  useEffect(() => setBlockViews({}), [view]);
  // Leaving the chat or changing the reading view stops playback (CU1).
  useEffect(() => () => stopPlayback(), [view]);
  const chat = `${account}/${peer}`;
  useEffect(() => () => forgetRows(chat), [chat]);
  // The palette loads when a sheet with reactions opens.
  const loadReactions = reactions.load;
  useEffect(() => {
    if (menu?.react?.length) loadReactions();
  }, [menu, loadReactions]);
  // GIFs and animated stickers move only in rows on screen.
  const viewability = useRef({
    viewabilityConfig: { itemVisiblePercentThreshold: 1 },
    onViewableItemsChanged: ({
      viewableItems,
    }: {
      viewableItems: ViewToken[];
    }) =>
      showRows(
        chat,
        viewableItems.map((token) => token.key),
      ),
  }).current;
  const [earlier, setEarlier] = useState<"idle" | "loading" | "done">("idle");
  const list = useRef<FlatList<Row>>(null);
  const opened = useRef(false);
  const retries = useRef(0);
  const replies = useReplies(account, peer, snapshot?.messages ?? [], {
    title: snapshot?.chat.title ?? "",
    group: snapshot?.chat.kind === "group",
    timezone: snapshot?.timezone ?? "UTC",
  });
  // A tapped quote's message, until it is shown (ADR 41 Q5), and the row
  // briefly highlighted once it is.
  const [jump, setJump] = useState<number | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const pages = useRef(0);

  const items = useMemo(
    () => (snapshot ? readingItems(snapshot, view, blockViews) : []),
    [snapshot, view, blockViews],
  );
  // Telegram albums among the originals, as one grid (ADR 51 Q12).
  const shownItems = useMemo(() => withAlbums(items), [items]);
  const anchor = useMemo(
    () => unreadAnchor(shownItems, captured),
    [shownItems, captured],
  );
  const operations = snapshot
    ? visibleOperations(snapshot, composer.pending)
    : [];
  // Newest first for the inverted list, which opens at the latest message;
  // sends not yet among the messages come last, below them.
  const rows: Row[] = [
    ...shownItems,
    ...operations.map((operation) => ({
      kind: "pending" as const,
      key: `p${operation.id}`,
      operation,
    })),
  ];
  // Message Runs (ADR 51 Q13): closer bubbles and a tail on each run's last.
  const runs = runPlaces(rows, anchor, snapshot?.timezone ?? "UTC");
  const shown = rows.reverse();

  // ADR 23: open at the Unread messages line when there is one.
  useEffect(() => {
    if (opened.current || !anchor || !shown.length) return;
    opened.current = true;
    const index = shown.findIndex((item) => item.key === anchor);
    if (index > 0) {
      retries.current = 0;
      requestAnimationFrame(() =>
        list.current?.scrollToIndex({
          index,
          viewPosition: 1,
          animated: false,
        }),
      );
    }
  }, [anchor, shown]);

  // Scroll to the row holding the tapped quote's message, loading earlier
  // pages as "Load earlier messages" does until it is there or none is left.
  useEffect(() => {
    if (jump === null) return;
    const index = shown.findIndex((row) =>
      row.kind === "message"
        ? row.message.message_id === jump
        : row.kind === "group" &&
          row.messages.some((message) => message.message_id === jump),
    );
    if (index >= 0) {
      setJump(null);
      setHighlight(shown[index]!.key);
      retries.current = 0;
      list.current?.scrollToIndex({ index, viewPosition: 0.5, animated: true });
      return;
    }
    if (earlier === "loading") return;
    const oldest = snapshot?.messages[0]?.message_id;
    if (
      earlier === "done" ||
      oldest === undefined ||
      oldest <= jump ||
      pages.current >= 30
    ) {
      setJump(null);
      return;
    }
    pages.current += 1;
    void earlierMessages();
  });
  useEffect(() => {
    if (!highlight) return;
    const timer = setTimeout(() => setHighlight(null), 1500);
    return () => clearTimeout(timer);
  }, [highlight]);

  function jumpTo(target: number) {
    pages.current = 0;
    setJump(target);
  }

  const emotion = activeEmotion(snapshot);
  const aiEnabled =
    (connection.status === "paired" &&
      connection.accounts?.accounts.find((item) => item.account_id === account)
        ?.ai_enabled) !== false;
  const replyAllowed =
    composer.ready &&
    !composer.locked &&
    !["send", "attach", "undo"].includes(composer.action ?? "");

  // Retry a failed or interrupted block (ADR 28, ADR 46 CU3).
  async function retry(block: Block) {
    try {
      await api(
        `accounts/${account}/chats/${encodeURIComponent(peer)}/blocks/${blockAnchor(block.id)}/retry`,
        { body: { revision: block.revision } },
      );
      await reload();
    } catch (problem) {
      setCopied(
        problem instanceof Error ? problem.message : "Retry couldn't start.",
      );
    }
  }
  const owners = useMemo(() => {
    const map = new Map<number, Block>();
    for (const block of snapshot?.blocks ?? [])
      for (const source of block.sources) map.set(source, block);
    return map;
  }, [snapshot]);
  const blockControls: BlockControls = {
    aiEnabled,
    retry: (block) => void retry(block),
    show: (block, shown) =>
      setBlockViews((views) => ({ ...views, [block.id]: shown })),
    blockOf: (message) => owners.get(message.message_id),
  };

  // Message Selection and deletion (ADR 31, ADR 46 CU4).
  const deletion = useMessageDeletionState({
    account,
    peer,
    sources: snapshot?.messages ?? [],
    operations: snapshot?.deletions ?? [],
    replyHolds: snapshot?.reply_holds ?? {},
    onChange: reload,
    version: snapshot,
    originals: view === "originals",
  });
  const accountName =
    (connection.status === "paired" &&
      connection.accounts?.accounts.find((item) => item.account_id === account)
        ?.name) ||
    `Account ${account}`;

  // A private chat's reaction from the sheet (ADR 46 CU2).
  async function react(message: NativeMessage, emoji: string) {
    try {
      await api(
        `accounts/${account}/chats/${encodeURIComponent(peer)}/originals/${message.message_id}/reactions`,
        { body: reactionChoice(message.reactions, emoji) },
      );
      await reload();
    } catch (problem) {
      setCopied(
        problem instanceof Error
          ? problem.message
          : "The reaction couldn't be sent.",
      );
    }
  }

  async function copyText(text: string) {
    await Clipboard.setStringAsync(text);
    setCopied("Copied");
  }

  async function earlierMessages() {
    setEarlier("loading");
    try {
      setEarlier((await loadEarlier()) > 0 ? "idle" : "done");
    } catch {
      setEarlier("idle");
    }
  }

  const title = snapshot?.chat.title ?? "";
  // A finished voice note plays on into the chat's next one.
  const credential =
    connection.status === "paired" ? connection.credential : null;
  useEffect(() =>
    setVoiceQueue(() =>
      voiceQueue(credential, account, peer, snapshot?.messages ?? [], title),
    ),
  );
  const time = (message: { date: number }) =>
    messageTime(message.date, snapshot?.timezone ?? "UTC", hourCycle);
  return (
    <DeletionContext.Provider value={deletion}>
      <GalleryProvider
        account={account}
        peer={peer}
        messages={snapshot?.messages ?? []}
      >
        <SafeAreaView
          ref={root}
          // In its tab, the list's header holds the top of the screen.
          edges={embedded ? ["bottom"] : ["top", "left", "right", "bottom"]}
          onLayout={
            embedded
              ? () => root.current?.measureInWindow((_x, y) => setTop(y))
              : undefined
          }
          style={[styles.fill, { backgroundColor: palette.background }]}
        >
          {embedded ? null : (
            <View style={[styles.header, { borderColor: palette.line }]}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Back to conversations"
                hitSlop={8}
                onPress={() =>
                  router.canGoBack() ? router.back() : router.replace("/chats")
                }
              >
                <Ionicons
                  name="chevron-back"
                  size={26}
                  color={palette.accent}
                />
              </Pressable>
              {/* The photo, name and emotion open the Correspondent Profile
              (CU3); the emoji sits right after the name, which gives way
              first (ADR 47). Copy chat link is in the profile (Q8). */}
              <Pressable
                accessibilityRole={saved ? "header" : "button"}
                // The button names the emotion, which its emoji only shows.
                accessibilityLabel={
                  saved
                    ? title
                    : `${title}${emotion ? `, ${emotion[1]}` : ""}, open profile`
                }
                // Saved Messages has no correspondent to show.
                disabled={saved}
                onPress={() =>
                  router.push({
                    pathname: "/profile/[account]/[peer]",
                    params: { account: String(account), peer },
                  })
                }
                style={[styles.person, styles.fill]}
              >
                <Avatar
                  account={account}
                  peer={peer}
                  photoId={snapshot?.chat.photo_id}
                  name={title}
                  size={34}
                />
                <Text
                  numberOfLines={1}
                  style={[styles.title, styles.shrink, { color: palette.text }]}
                >
                  {title}
                </Text>
                {emotion ? (
                  <Text
                    accessibilityLabel={`Emotion: ${emotion[1]}`}
                    style={styles.emoji}
                  >
                    {emotion[0]}
                  </Text>
                ) : null}
              </Pressable>
              {!aiEnabled && !saved ? (
                <Text
                  style={[
                    styles.aiOff,
                    { color: palette.muted, borderColor: palette.line },
                  ]}
                >
                  AI off
                </Text>
              ) : null}
              {/* The Reading View's robot at the header's end (ADR 52 Q13);
              Saved Messages has no AI. */}
              {saved ? null : (
                <ReadingToggle view={view} choose={choose} palette={palette} />
              )}
            </View>
          )}
          {copied ? (
            <Text style={[styles.small, styles.note, { color: palette.muted }]}>
              {copied}
            </Text>
          ) : null}
          <View style={styles.status}>
            {/* The list's header shows the connection above Saved. */}
            {embedded ? null : <ConnectionStatus />}
            {error && !snapshot ? (
              <Text style={{ color: palette.error }}>{error}</Text>
            ) : null}
          </View>
          {/* Android draws edge to edge, so its window no longer resizes for
          the keyboard either: both platforms pad. */}
          <KeyboardAvoidingView
            style={styles.fill}
            behavior="padding"
            keyboardVerticalOffset={embedded ? top : 0}
          >
            {!snapshot ? (
              <ActivityIndicator
                style={styles.loading}
                color={palette.accent}
              />
            ) : (
              <ChatTitle.Provider value={title}>
                <View style={styles.fill}>
                  <FlatList
                    ref={list}
                    inverted
                    data={shown}
                    keyExtractor={(item) => item.key}
                    // Away from the latest message, a button brings it back.
                    onScroll={(event) =>
                      setAway(event.nativeEvent.contentOffset.y > 400)
                    }
                    scrollEventThrottle={100}
                    testID="messages"
                    viewabilityConfig={viewability.viewabilityConfig}
                    onViewableItemsChanged={viewability.onViewableItemsChanged}
                    onScrollToIndexFailed={({ index, averageItemLength }) => {
                      // A far row is not measured yet: move toward its estimate so it
                      // renders, then try again, a few times at most.
                      if (retries.current++ >= 5) return;
                      list.current?.scrollToOffset({
                        offset: averageItemLength * index,
                        animated: false,
                      });
                      setTimeout(
                        () =>
                          list.current?.scrollToIndex({
                            index,
                            viewPosition: 1,
                            animated: false,
                          }),
                        100,
                      );
                    }}
                    contentContainerStyle={styles.messages}
                    keyboardShouldPersistTaps="handled"
                    // Deletion cards sit under the latest message (CU4).
                    ListHeaderComponent={<DeletionCards deletion={deletion} />}
                    renderItem={({ item }) =>
                      item.kind === "pending" ? (
                        <PendingSend
                          account={account}
                          peer={peer}
                          run={runs.get(item.key)}
                          operation={item.operation}
                          timezone={snapshot.timezone}
                          hourCycle={hourCycle}
                          reply={
                            item.operation.reply_to
                              ? replies.quoteOf(
                                  item.operation.reply_to,
                                  item.operation.created,
                                )
                              : null
                          }
                          onJump={jumpTo}
                          onChange={reload}
                        />
                      ) : (
                        <MediaRow.Provider value={`${chat}/${item.key}`}>
                          {item.key === anchor ? (
                            <UnreadLine palette={palette} />
                          ) : null}
                          <Bubble
                            item={item}
                            account={account}
                            peer={peer}
                            timezone={snapshot.timezone}
                            hourCycle={hourCycle}
                            readOutbox={snapshot.chat.read_outbox}
                            view={view}
                            time={time}
                            detail={detail}
                            controls={blockControls}
                            onMenu={
                              saved
                                ? (target) => setMenu({ ...target, react: [] })
                                : setMenu
                            }
                            replies={replies}
                            onJump={jumpTo}
                            highlighted={item.key === highlight}
                            palette={palette}
                            run={runs.get(item.key) ?? SOLO}
                            swipeReply={
                              replyAllowed && !deletion.active
                                ? composer.reply
                                : null
                            }
                          />
                        </MediaRow.Provider>
                      )
                    }
                    ListFooterComponent={
                      <>
                        {saved ? null : (
                          <EarlierBar
                            account={account}
                            peer={peer}
                            older={snapshot.older}
                            progress={snapshot.progress}
                            aiEnabled={aiEnabled}
                            onChange={() => void reload()}
                          />
                        )}
                        {earlier === "done" ? null : (
                          <Pressable
                            accessibilityRole="button"
                            disabled={earlier === "loading"}
                            onPress={() => void earlierMessages()}
                            style={styles.earlier}
                          >
                            {earlier === "loading" ? (
                              <ActivityIndicator color={palette.accent} />
                            ) : (
                              <Text
                                style={{
                                  color: palette.accent,
                                  fontWeight: "600",
                                }}
                              >
                                Load earlier messages
                              </Text>
                            )}
                          </Pressable>
                        )}
                      </>
                    }
                  />
                  {away ? (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel="Scroll to latest"
                      onPress={() => {
                        list.current?.scrollToOffset({
                          offset: 0,
                          animated: true,
                        });
                        setAway(false);
                      }}
                      style={[
                        styles.latest,
                        {
                          backgroundColor: palette.background,
                          borderColor: palette.line,
                        },
                      ]}
                    >
                      <Ionicons
                        name="arrow-down"
                        size={22}
                        color={palette.accent}
                      />
                    </Pressable>
                  ) : null}
                </View>
              </ChatTitle.Provider>
            )}
            <SelectionBar deletion={deletion} />
            {snapshot ? (
              // The composer keeps its draft while the selection bar takes its
              // place.
              <View style={deletion.active ? styles.hidden : null}>
                {/* The Reminder Bar sits above the suggestions (ADR 58 Q13). */}
                {saved || !pending.length ? null : (
                  <View style={styles.reminders}>
                    <ReminderBar
                      account={account}
                      peer={peer}
                      title={title}
                      reminders={pending}
                      onChange={() => void reload()}
                    />
                  </View>
                )}
                <ComposerBar
                  composer={composer}
                  snapshot={snapshot}
                  account={account}
                  peer={peer}
                  aiEnabled={aiEnabled}
                  rewrite={!saved}
                  onSent={() => list.current?.scrollToOffset({ offset: 0 })}
                />
              </View>
            ) : null}
          </KeyboardAvoidingView>
          <MessageMenu
            target={menu}
            replyAllowed={replyAllowed}
            palette={reactions.options}
            onReply={(identity) => composer.reply(identity)}
            onCopy={(text) => void copyText(text)}
            onCopyTranscript={(message) => void media.copyTranscript(message)}
            onShare={(message) => void media.share(message)}
            onReact={(message, emoji) => void react(message, emoji)}
            onClose={() => setMenu(null)}
          />
          <DeletionReview
            deletion={deletion}
            accountName={accountName}
            chatName={title}
          />
        </SafeAreaView>
      </GalleryProvider>
    </DeletionContext.Provider>
  );
}

function activeEmotion(snapshot: Snapshot | null) {
  const emotion = snapshot?.emotion;
  if (!emotion?.code) return null;
  if (emotion.expires_at && emotion.expires_at * 1000 < Date.now()) return null;
  return EMOTIONS[emotion.code] ?? null;
}

// Telegram albums among the message rows (ADR 51 Q12): an album's first row
// carries the album, and the rest leave the list.
function withAlbums(items: ReadingItem[]): ReadingItem[] {
  const messages = items.flatMap((item) =>
    item.kind === "message" ? [item.message] : [],
  );
  const albums = new Map<number, NativeMessage[]>();
  const later = new Set<number>();
  for (const album of albumRuns(messages))
    if (album.length > 1) {
      albums.set(album[0]!.message_id, album);
      for (const message of album.slice(1)) later.add(message.message_id);
    }
  if (!albums.size) return items;
  return items.flatMap((item): ReadingItem[] => {
    if (item.kind !== "message") return [item];
    if (later.has(item.message.message_id)) return [];
    const album = albums.get(item.message.message_id);
    return album ? [{ ...item, album }] : [item];
  });
}

// Each row's place in its Message Run (ADR 51 Q13), for rows oldest first:
// the owner's messages and sends against everyone else's, a service line on
// its own, broken by the Unread Separator.
const SOLO: RunPlace = { first: true, last: true };

function runPlaces(rows: Row[], anchor: string | null, timezone: string) {
  const entries = rows.map((row) => {
    if (row.kind === "pending")
      return { sender: "out", date: row.operation.created };
    const message = row.kind === "message" ? row.message : row.messages.at(-1)!;
    return {
      sender: message.service
        ? `service:${message.message_id}`
        : row.kind === "message" && message.outgoing
          ? "out"
          : "in",
      date: message.date,
      separated: row.key === anchor,
    };
  });
  const places = messageRuns(entries, timezone);
  return new Map(rows.map((row, index) => [row.key, places[index]!]));
}

// The message a row's Reply answers: the message itself, or a block's newest
// current source (ADR 27).
function replyOf(item: ReadingItem) {
  const live = (
    item.kind === "message" ? [item.message] : item.messages
  ).filter((message) => !message.deleted && !message.service);
  return live.at(-1)?.message_id ?? null;
}

// What a long press offers for one row (ADR 26, 27): Reply and Copy, its
// media's Copy transcript and Share (ADR 46 CU1) and reactions (CU2).
function menuTarget(
  item: ReadingItem,
  time: (message: NativeMessage) => string,
): MenuTarget {
  const live = (
    item.kind === "message" ? [item.message] : item.messages
  ).filter((message) => !message.deleted && !message.service);
  const media = mediaActions(
    live.filter((message) => message.media),
    time,
  );
  if (item.kind === "message")
    return {
      reply: replyOf(item),
      // A deleted message's last words still copy (ADR 52 Q7).
      copy: item.message.service ? "" : item.message.text,
      ...media,
      react: live,
    };
  return {
    reply: replyOf(item),
    copy:
      item.text ??
      live
        .map((message) => message.text)
        .filter(Boolean)
        .join("\n"),
    ...media,
    react: live,
  };
}

// What a block's AI controls need (ADR 46 CU3).
type BlockControls = {
  aiEnabled: boolean;
  retry: (block: Block) => void;
  show: (block: Block, shown: ReadingView) => void;
  blockOf: (message: NativeMessage) => Block | undefined;
};

type BubbleProps = {
  item: ReadingItem;
  controls: BlockControls;
  account: number;
  peer: string;
  timezone: string;
  hourCycle: HourCycle | undefined;
  readOutbox: number;
  view: ReadingView;
  time: (message: NativeMessage) => string;
  // A message with the forward header the service filled in since.
  detail: (message: NativeMessage) => NativeMessage;
  onMenu: (target: MenuTarget) => void;
  replies: Replies;
  onJump: (target: number) => void;
  highlighted: boolean;
  palette: Palette;
  // Swipe to Reply's composer Reply, while Reply is available (ADR 51 Q5).
  swipeReply: ((identity: number) => void) | null;
  // Its place in its Message Run (ADR 51 Q13).
  run: RunPlace;
};

function Bubble(props: BubbleProps) {
  if (props.item.kind === "message") return <MessageRow {...props} />;
  const {
    item,
    account,
    peer,
    timezone,
    hourCycle,
    time,
    detail,
    onMenu,
    replies,
    onJump,
    highlighted,
    palette,
    swipeReply,
    run,
  } = props;
  const outline = highlighted
    ? [styles.highlighted, { borderColor: palette.accent }]
    : null;
  const sources = item.messages.map(detail);
  const live = sources.filter((message) => !message.deleted);
  // AI reading points to Originals for an edited source's versions.
  const edited = live.filter(
    (message) => message.native_edited || message.history_count,
  );
  // The web's group header: the source count and the first source's time.
  const first = item.messages[0];
  const count = item.messages.length;
  const retryable = props.controls.aiEnabled && canRetry(item.block);
  // ADR 54: text that isn't rewritten shows as its originals together, with
  // no AI label or status.
  const combined =
    item.text === null &&
    !item.block.error &&
    !item.block.stage &&
    item.block.state === "original";
  const target = () =>
    onMenu({
      ...menuTarget({ ...item, messages: sources }, time),
      retry: retryable ? () => props.controls.retry(item.block) : undefined,
      toggleView:
        item.text !== null && !item.streaming
          ? {
              label: "Show original",
              run: () => props.controls.show(item.block, "originals"),
            }
          : undefined,
    });
  // "Not analysed: …" for what the AI skipped in a finished block.
  const note =
    item.text !== null && ["ready", "nothing"].includes(item.block.state)
      ? unanalysedNote(item.block.unanalysed, sources)
      : null;
  // ADR 41 Q11: one quote per message outside the group that it answers.
  const quoted = outsideReplies(
    item.messages
      .filter((message) => !message.deleted)
      .map((message) => replies.message(message)),
  );
  const reply = replyOf({ ...item, messages: sources });
  return (
    <SwipeToReply
      enabled={!!swipeReply && reply !== null}
      onReply={() => reply !== null && swipeReply?.(reply)}
      color={palette.muted}
    >
      <Pressable
        accessibilityHint="Long press for message actions"
        delayLongPress={350}
        onLongPress={target}
        style={[
          styles.bubble,
          styles.incoming,
          { backgroundColor: palette.panel },
          run.first ? null : styles.follow,
          runCorners(run, "left"),
          outline,
        ]}
      >
        {run.last ? <Tail side="left" color={palette.panel} /> : null}
        <View style={styles.head}>
          <Text style={[styles.small, { color: palette.muted }]}>
            {combined ? "" : "AI · "}
            {count} {count === 1 ? "message" : "messages"}
            {first ? ` · ${messageTime(first.date, timezone, hourCycle)}` : ""}
            {/* ADR 53 Q5: the shown rewrite was calmed. */}
            {item.block.calmed && item.text !== null && !item.streaming
              ? " · Softened"
              : ""}
          </Text>
          {edited.length ? (
            <Ionicons
              accessible
              accessibilityLabel={editedName(
                Math.max(
                  ...edited.map((message) => message.history_count ?? 0),
                ),
              )}
              name="pencil"
              size={12}
              color={palette.muted}
            />
          ) : null}
        </View>
        <ForwardedLines
          lines={forwardedGroups(live, timezone)}
          palette={palette}
        />
        {quoted.map((message) => (
          <ReplyQuote
            key={message.message_id}
            quote={replies.quote(message)}
            palette={palette}
            onJump={onJump}
          />
        ))}
        <BubbleMenu.Provider value={target}>
          {item.text !== null ? (
            <>
              <Text style={[styles.text, { color: palette.text }]}>
                {item.text}
              </Text>
              {/* ADR 24: every source attachment stays in AI reading too. */}
              {item.messages
                .filter((message) => message.media && message.media !== "link")
                .map((message) => (
                  <MessageBody
                    key={message.message_id}
                    message={message}
                    account={account}
                    peer={peer}
                    palette={palette}
                    inverse={false}
                    hideText
                  />
                ))}
            </>
          ) : (
            item.messages.map((message) => (
              <MessageBody
                key={message.message_id}
                message={message}
                account={account}
                peer={peer}
                palette={palette}
                inverse={false}
              />
            ))
          )}
        </BubbleMenu.Provider>
        {note ? (
          <Text style={[styles.small, { color: palette.muted }]}>{note}</Text>
        ) : null}
        <ReactionChips sources={live} palette={palette} />
        {item.streaming ? (
          <Text style={[styles.small, { color: palette.muted }]}>
            Streaming · incomplete
          </Text>
        ) : item.text === null && !combined ? (
          // Waiting for budget and the format pause open Settings (ADR 45 Q8).
          !item.block.error && STAGE_TARGETS[item.block.stage ?? ""] ? (
            <SettingsLink
              target={STAGE_TARGETS[item.block.stage ?? ""]!}
              style={styles.small}
            >
              {STAGES[item.block.stage ?? ""]!}
            </SettingsLink>
          ) : (
            <View style={styles.blockStatus}>
              <Text
                style={[styles.small, styles.fill, { color: palette.muted }]}
              >
                {blockError(item.block.error) ??
                  STAGES[item.block.stage ?? ""] ??
                  (item.block.state === "failed"
                    ? "Rewrite failed"
                    : "Original text")}
              </Text>
              {retryable ? (
                <Pressable
                  accessibilityRole="button"
                  hitSlop={8}
                  onPress={() => props.controls.retry(item.block)}
                >
                  <Text style={[styles.retry, { color: palette.accent }]}>
                    Retry
                  </Text>
                </Pressable>
              ) : null}
            </View>
          )
        ) : null}
      </Pressable>
    </SwipeToReply>
  );
}

// One message as its own bubble, with its forward line, its reactions and,
// in Originals, Telegram's earlier versions to step through (ADR 46 CU2).
function MessageRow({
  item,
  controls,
  account,
  peer,
  timezone,
  hourCycle,
  readOutbox,
  view,
  time,
  detail,
  onMenu,
  replies,
  onJump,
  highlighted,
  palette,
  swipeReply,
  run,
}: BubbleProps) {
  const message = detail(
    replies.message(item.kind === "message" ? item.message : item.messages[0]!),
  );
  const album = item.kind === "message" ? item.album : undefined;
  const versions = useVersions(account, peer, message.service ? null : message);
  if (message.service)
    return (
      <Text style={[styles.service, { color: palette.muted }]}>
        {message.text}
      </Text>
    );
  const shown = versions.shown;
  const current = shown?.message ?? message;
  const outgoing = !!message.outgoing;
  const muted = outgoing ? palette.onAccent : palette.muted;
  const forward = forwardedLine(forwardOf(current, message), timezone);
  const deletion = useMessageDeletion();
  // Its block's AI text, against the reading view (ADR 46 CU3).
  const block = controls.blockOf(message);
  const rewritten = block ? blockText(block).text !== null : false;
  // While an earlier version shows, its sheet offers only Copy.
  const target = () =>
    onMenu(
      shown
        ? { reply: null, copy: current.deleted ? "" : current.text }
        : {
            ...menuTarget(
              { kind: "message", key: item.key, message } as ReadingItem,
              time,
            ),
            toggleView:
              block && rewritten
                ? {
                    label: "Show AI text",
                    run: () => controls.show(block, "ai"),
                  }
                : undefined,
            select:
              deletion && !deletion.active && deletion.eligible(message)
                ? () => deletion.select(message)
                : undefined,
            hide:
              deletion && message.deleted
                ? () => deletion.hide([message])
                : undefined,
          },
    );
  // Telegram's own marks: Sent once accepted, Read once it reports so.
  const read = message.message_id <= readOutbox;
  // While an earlier version shows, or while selecting, it has no Reply.
  const reply =
    shown || deletion?.active
      ? null
      : replyOf({ kind: "message", key: item.key, message } as ReadingItem);
  return (
    <Selectable message={message} version={!!shown} album={album}>
      <SwipeToReply
        enabled={!!swipeReply && reply !== null}
        onReply={() => reply !== null && swipeReply?.(reply)}
        color={palette.muted}
      >
        <Pressable
          accessibilityHint="Long press for message actions"
          delayLongPress={350}
          onLongPress={target}
          style={[
            styles.bubble,
            outgoing
              ? [styles.outgoing, { backgroundColor: palette.accent }]
              : [styles.incoming, { backgroundColor: palette.panel }],
            run.first ? null : styles.follow,
            runCorners(run, outgoing ? "right" : "left"),
            highlighted
              ? [
                  styles.highlighted,
                  { borderColor: outgoing ? palette.onAccent : palette.accent },
                ]
              : null,
          ]}
        >
          {run.last ? (
            <Tail
              side={outgoing ? "right" : "left"}
              color={outgoing ? palette.accent : palette.panel}
            />
          ) : null}
          <ForwardedLines
            lines={forward ? [forward] : []}
            palette={palette}
            inverse={outgoing}
          />
          {/* ADR 41: every reply quotes its message; a deleted one shows
          what it last said, its quote included (ADR 52 Q7). */}
          {isReply(message) ? (
            <ReplyQuote
              quote={replies.quote(message)}
              palette={palette}
              inverse={outgoing}
              onJump={onJump}
            />
          ) : null}
          <BubbleMenu.Provider value={target}>
            {album ? (
              <AlbumGrid
                album={album}
                account={account}
                peer={peer}
                palette={palette}
                inverse={outgoing}
              />
            ) : (
              <MessageBody
                message={message}
                account={account}
                peer={peer}
                palette={palette}
                inverse={outgoing}
                version={shown}
              />
            )}
          </BubbleMenu.Provider>
          {!shown ? (
            <ReactionChips
              sources={[message]}
              palette={palette}
              inverse={outgoing}
            />
          ) : null}
          {versions.error ? (
            <Text style={[styles.small, { color: muted }]}>
              {versions.error}
            </Text>
          ) : null}
          <View style={styles.meta}>
            <VersionControls
              message={message}
              versions={versions}
              view={view}
              color={muted}
            />
            <Text style={[styles.time, { color: muted }]}>
              {messageTime(current.date, timezone, hourCycle)}
            </Text>
            {outgoing && !message.deleted ? (
              <Ionicons
                accessibilityLabel={read ? "Read" : "Sent"}
                name={read ? "checkmark-done" : "checkmark"}
                size={14}
                color={palette.onAccent}
              />
            ) : null}
          </View>
          <DeletingMark message={message} color={muted} />
        </Pressable>
      </SwipeToReply>
    </Selectable>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  shrink: { flexShrink: 1 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  title: { fontSize: 17, fontWeight: "600" },
  emoji: { fontSize: 20 },
  bar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 12,
    paddingTop: 8,
  },
  status: { paddingHorizontal: 12, gap: 6, paddingTop: 6 },
  note: { paddingHorizontal: 12, paddingTop: 4 },
  loading: { marginTop: 40 },
  messages: { padding: 12, gap: 8 },
  small: { fontSize: 13 },
  service: { textAlign: "center", fontSize: 13, marginVertical: 4 },
  bubble: { maxWidth: "88%", borderRadius: 14, padding: 10, gap: 4 },
  // The border takes the padding's place, so nothing moves.
  highlighted: { borderWidth: 2, padding: 8 },
  incoming: { alignSelf: "flex-start" },
  outgoing: { alignSelf: "flex-end" },
  // A run's later bubbles sit 2 apart instead of 8 (ADR 51 Q13).
  follow: { marginTop: -6 },
  text: { fontSize: 16, lineHeight: 22 },
  time: { fontSize: 12 },
  meta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    alignSelf: "flex-end",
  },
  earlier: { alignItems: "center", padding: 12 },
  head: { flexDirection: "row", alignItems: "center", gap: 6 },
  person: { flexDirection: "row", alignItems: "center", gap: 10 },
  hidden: { display: "none" },
  reminders: { paddingHorizontal: 10, paddingTop: 6 },
  aiOff: {
    fontSize: 12,
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  blockStatus: { flexDirection: "row", alignItems: "center", gap: 10 },
  retry: { fontSize: 14, fontWeight: "600" },
  latest: {
    position: "absolute",
    right: 14,
    bottom: 12,
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: "center",
    justifyContent: "center",
    elevation: 3,
  },
});
