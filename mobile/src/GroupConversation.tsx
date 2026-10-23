import Ionicons from "@expo/vector-icons/Ionicons";
import * as Clipboard from "expo-clipboard";
import { router } from "expo-router";
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
import { messageTime } from "../../shared/messageTime";
import {
  activePrefix,
  briefingSlot,
  dayKey,
  groupDays,
  transientSlot,
} from "../../shared/groupBriefings";
import { unreadAnchor } from "../../shared/unread";
import { Avatar } from "./Avatar";
import { ComposerBar } from "./Composer";
import { useConnection, useServerChanges } from "./connection";
import {
  useConversation,
  type BriefingUpdate,
  type DailyBriefing,
  type NativeMessage,
} from "./conversation";
import { useReadingView } from "./data";
import { useComposer, visibleOperations } from "./drafts";
import {
  groupRows,
  useHideBots,
  withGroupAlbums,
  type Day,
  type GroupRow,
} from "./groups";
import { groupSender, messageRuns } from "../../shared/runs";
import { GalleryProvider } from "./MediaCarousel";
import {
  BriefingCard,
  DayBand,
  GroupBubble,
  SlotView,
  SpoilerToggle,
} from "./GroupReading";
import { GroupSheet } from "./GroupSheet";
import {
  DeletionCards,
  DeletionContext,
  DeletionReview,
  SelectionBar,
  useMessageDeletionState,
} from "./MessageDeletion";
import { useForwardDetails, type ObservedVersion } from "./details";
import { mediaActions, useMediaActions, voiceQueue } from "./Media";
import { MessageMenu, type MenuTarget } from "./MessageMenu";
import { forgetRows, MediaRow, showRows } from "./motion";
import { setVoiceQueue, stopPlayback } from "./playback";
import { PendingSend } from "./PendingSend";
import { ReadingToggle } from "./Reading";
import { useReplies } from "./replies";
import { ConnectionStatus } from "./Status";
import { usePalette, type Palette } from "./theme";
import { useHourCycle } from "./time";
import { useFocused, useFocusedViewing } from "./viewing";
import { ChatTitle } from "./VoiceNote";

// The pinned band's height: an opening target or a jump lands below it.
const BAND = 46;
const CHANGED = "The cited source changed or is no longer available locally.";

// A row to scroll to once it is shown: centred and outlined for a quote's or
// citation's message, at the top under the pinned band for the top of a day,
// or at the bottom.
type Jump = {
  key: string;
  outline: boolean;
  place: "center" | "top" | "bottom";
};

// A citation's answer (`…/briefings/{day}/sources` and `…/originals`).
type SourcePage = {
  day: string;
  result: string;
  source: number;
  messages: NativeMessage[];
  before: boolean;
  after: boolean;
};

// A Group Chat (ADR 34) in the Enloq app (ADR 42 AG3): its days under their
// date bands, each briefed day's card in AI reading with what it does not
// cover folded under "N new messages", and originals with author lines.
export function GroupConversation({
  account,
  peer,
}: {
  account: number;
  peer: string;
}) {
  const { connection, api } = useConnection();
  const palette = usePalette();
  const {
    snapshot,
    live,
    readAt,
    captured,
    error,
    loadEarlier,
    reload,
    merge,
  } = useConversation(account, peer);
  const composer = useComposer(account, peer, snapshot, reload, readAt);
  const bots = useHideBots();
  const hideBots = bots.hideBots;
  const hourCycle = useHourCycle();
  const focused = useFocused();
  const [copied, setCopied] = useState("");
  // A note stays long enough to read a sentence, such as why a transcript
  // couldn't be loaded.
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(""), 5000);
    return () => clearTimeout(timer);
  }, [copied]);
  const [earlier, setEarlier] = useState<"idle" | "loading" | "done">("idle");
  // Days whose spoiler the owner opened, kept until the chat closes.
  const [opened, setOpened] = useState<ReadonlySet<string>>(new Set());
  // A message a Reply Quote brought into view; Hide Bots itself is unchanged.
  const [revealed, setRevealed] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const { view, choose } = useReadingView();
  // Leaving the chat or changing the reading view stops playback (CU1).
  useEffect(() => () => stopPlayback(), [view]);
  const media = useMediaActions(account, peer, setCopied);
  const chat = `${account}/${peer}`;
  useEffect(() => () => forgetRows(chat), [chat]);
  // Days a citation swapped back to their cached originals (ADR 42 Q4), the
  // citation itself, the day being read, and why a reveal was refused.
  const [originalDays, setOriginalDays] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const [citation, setCitation] = useState<{
    day: string;
    source: number;
    revision: number;
    result: string;
    highlight: boolean;
  } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  // The day holds more cached originals than the citation's window.
  const [nearby, setNearby] = useState(false);
  const request = useRef(0);
  // A reveal belongs to the reading it was made in, as on the web.
  useEffect(() => {
    ++request.current;
    setRevealed(null);
    setOriginalDays(new Set());
    setCitation(null);
    setBusy(null);
    setNotice("");
  }, [view]);
  const [menu, setMenu] = useState<MenuTarget | null>(null);
  // The group's ⋮ sheet (ADR 42 Q3), a change it is saving and its refusal.
  const [sheet, setSheet] = useState(false);
  const [saving, setSaving] = useState(false);
  const [sheetNote, setSheetNote] = useState("");
  // Today's Update or Retry, one run at a time (ADR 42 Q2).
  const [working, setWorking] = useState(false);
  const settled = useRef<BriefingUpdate | null | undefined>(undefined);
  // A row to scroll to once it is shown: a tapped quote's or citation's
  // message, outlined, or the top of a day.
  const [jump, setJump] = useState<Jump | null>(null);
  // A jump to a row drawn anew is corrected once that row is measured.
  const settling = useRef<{ jump: Jump; until: number } | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  // The day of the topmost row, whose band stays pinned above it.
  const [top, setTop] = useState<string | null>(null);
  // Days whose band is wholly on screen, so it needs no pinned copy.
  const [whole, setWhole] = useState<ReadonlySet<string>>(new Set());
  const list = useRef<FlatList<GroupRow>>(null);
  const openedAt = useRef(false);
  const retries = useRef(0);

  const timezone = snapshot?.timezone ?? "UTC";
  const title = snapshot?.chat.title ?? "";
  const messages = useMemo(() => snapshot?.messages ?? [], [snapshot]);
  // Forward headers a cached original still lacks (ADR 46 CU2).
  const detail = useForwardDetails(account, peer, messages);
  const [away, setAway] = useState(false);
  // A finished voice note plays on into the group's next one (CU1).
  const credential =
    connection.status === "paired" ? connection.credential : null;
  useEffect(() =>
    setVoiceQueue(() => voiceQueue(credential, account, peer, messages, title)),
  );
  const briefings = useMemo(() => snapshot?.briefings ?? [], [snapshot]);
  const latest = useRef<DailyBriefing[]>(briefings);
  latest.current = briefings;
  const today = dayKey(new Date(), timezone);
  const path = `accounts/${account}/chats/${encodeURIComponent(peer)}`;
  const unreachable =
    connection.status === "paired" ? connection.unreachable : true;
  const accountEnabled =
    (connection.status === "paired" &&
      connection.accounts?.accounts.find((item) => item.account_id === account)
        ?.ai_enabled) !== false;
  const processing = snapshot?.group_processing;
  const retained = !!snapshot?.retained;
  // Writing acts only while the group's native access is verified (ADR 34).
  const nativeAvailable =
    !retained &&
    processing?.supported === true &&
    processing.access === "available";
  // Message Selection and deletion of the owner's own originals (ADR 34,
  // ADR 46 CU4), while the group's access is verified.
  const deletion = useMessageDeletionState({
    account,
    peer,
    sources: snapshot?.messages ?? [],
    operations: snapshot?.deletions ?? [],
    replyHolds: snapshot?.reply_holds ?? {},
    onChange: reload,
    version: snapshot,
    allowed: nativeAvailable,
    originals: view === "originals",
    ownOnly: true,
    retained,
  });
  const accountName =
    (connection.status === "paired" &&
      connection.accounts?.accounts.find((item) => item.account_id === account)
        ?.name) ||
    `Account ${account}`;
  const replyAllowed =
    nativeAvailable &&
    composer.ready &&
    !composer.locked &&
    !["send", "attach", "undo"].includes(composer.action ?? "");
  // The slot keeps its settled state through a catch-up request's Syncing.
  const update = snapshot?.briefing_update;
  const transient = transientSlot(update);
  const slotState = transient ? (settled.current ?? update) : update;
  useEffect(() => {
    if (!transient) settled.current = update;
  });
  // An account's AI switch changes the slot and the switch notes.
  useServerChanges((change) => {
    if (change.kind === "change" && change.shared.includes("accounts"))
      void reload();
  });
  const replies = useReplies(account, peer, messages, {
    title,
    group: true,
    timezone,
  });
  const days = useMemo(
    () =>
      groupDays({
        messages,
        briefings,
        view,
        timezone,
        hideBots,
        today,
        opened,
        revealed,
        originalDays,
        cited: citation?.highlight ? citation.source : null,
      }),
    [
      messages,
      briefings,
      view,
      timezone,
      hideBots,
      today,
      opened,
      revealed,
      originalDays,
      citation,
    ],
  );
  // Folded messages are not viewed until their spoiler opens.
  const withheld = useMemo(
    () => new Set(days.flatMap((entry) => entry.folded)),
    [days],
  );
  // Only what the server just returned counts as seen, never the offline copy.
  useFocusedViewing(account, peer, focused && live, snapshot, withheld);
  const unread = unreadAnchor(
    captured,
    messages,
    view === "ai"
      ? briefings.flatMap((brief) => brief.final?.covered_ids ?? [])
      : [],
  );
  const operations = snapshot
    ? visibleOperations(snapshot, composer.pending)
    : [];
  const rows: GroupRow[] = [
    ...withGroupAlbums(groupRows(days, { view, originalDays, unread })),
    ...operations.map((operation) => ({
      kind: "pending" as const,
      key: `p${operation.id}`,
      day: today,
      operation,
    })),
  ];
  // Message Runs (ADR 51 Q13): originals and sends between bands, cards and
  // folds, broken by the Unread line.
  const places = messageRuns(
    rows.map((row) =>
      row.kind === "original"
        ? {
            sender: row.message.service
              ? `row:${row.key}`
              : groupSender(row.message, account),
            date: row.message.date,
            separated: row.unread,
          }
        : row.kind === "pending"
          ? { sender: "out", date: row.operation.created }
          : { sender: `row:${row.key}`, date: 0 },
    ),
    timezone,
  );
  const runs = new Map(rows.map((row, index) => [row.key, places[index]!]));
  // Newest first for the inverted list, which opens at the latest message.
  const shown = [...rows].reverse();

  // ADR 34: open at the earliest unseen ready message, through its card, its
  // spoiler or the message itself, under the Unread line and the pinned band.
  useEffect(() => {
    if (openedAt.current || !shown.length) return;
    const index = shown.findIndex(
      (row) =>
        (row.kind === "card" ||
          row.kind === "fold" ||
          row.kind === "original") &&
        row.unread,
    );
    if (index < 0) return;
    openedAt.current = true;
    if (index === 0) return;
    retries.current = 0;
    requestAnimationFrame(() =>
      list.current?.scrollToIndex({
        index,
        viewPosition: 1,
        viewOffset: -BAND,
        animated: false,
      }),
    );
  }, [shown]);

  // A quote's or citation's message shows once its spoiler opens, Hide Bots
  // lets it or its day's originals replace the card. A row drawn anew is not
  // measured yet, so its own layout scrolls to it again.
  useEffect(() => {
    if (jump === null) return;
    setJump(null);
    if (jump.outline) setHighlight(jump.key);
    settling.current = { jump, until: Date.now() + 1000 };
    scrollTo(jump, true);
  }, [jump, shown]);

  function scrollTo(target: Jump, animated: boolean) {
    const index = shown.findIndex((row) => row.key === target.key);
    if (index < 0) return;
    retries.current = 0;
    list.current?.scrollToIndex({
      index,
      animated,
      ...(target.place === "center"
        ? { viewPosition: 0.5 }
        : target.place === "bottom"
          ? { viewPosition: 0 }
          : { viewPosition: 1, viewOffset: -BAND }),
    });
  }

  function measured(key: string) {
    const pending = settling.current;
    if (!pending || pending.jump.key !== key) return;
    settling.current = null;
    if (Date.now() < pending.until)
      requestAnimationFrame(() => scrollTo(pending.jump, false));
  }

  // A citation whose source or briefing changed since it was revealed says
  // so; the day keeps its originals until Return to briefing.
  useEffect(() => {
    if (!citation) return;
    const current = messages.find(
      (message) => message.message_id === citation.source,
    );
    const final = briefings.find((brief) => brief.day === citation.day)?.final;
    if (
      !current ||
      current.deleted ||
      current.revision !== citation.revision ||
      final?.id !== citation.result
    ) {
      setCitation(null);
      setNotice(CHANGED);
    }
  }, [citation, messages, briefings]);

  // A citation, Show day originals or a quote to a summarized message swaps
  // the day's card for its cached originals around that message (ADR 42 Q4),
  // checked against the briefing it came from.
  async function reveal(entry: Day, source: number, highlight = true) {
    const final = entry.brief?.final;
    if (!final || entry.prefix) return;
    const sequence = ++request.current;
    const result = final.id;
    setBusy(entry.day);
    setNotice("");
    try {
      const page = await api<SourcePage>(
        `accounts/${account}/chats/${encodeURIComponent(peer)}/briefings/` +
          encodeURIComponent(entry.day) +
          (highlight
            ? `/sources?result=${result}&source=${source}`
            : `/originals?result=${result}`),
      );
      if (sequence !== request.current) return;
      const fresh = latest.current.find((brief) => brief.day === entry.day);
      const target = page.messages.find(
        (message) => message.message_id === source,
      );
      if (
        page.day !== entry.day ||
        page.result !== result ||
        page.source !== source ||
        fresh?.final?.id !== result ||
        activePrefix(fresh) ||
        !target ||
        target.deleted
      )
        throw new Error(CHANGED);
      merge(page.messages);
      setOriginalDays((days) => new Set([...days, entry.day]));
      setCitation({
        day: entry.day,
        source,
        revision: target.revision,
        result,
        highlight,
      });
      setNearby(page.before || page.after);
      setJump(
        highlight
          ? { key: `m${source}`, outline: true, place: "center" }
          : { key: `tools:${entry.day}`, outline: false, place: "top" },
      );
    } catch (problem) {
      if (sequence === request.current)
        setNotice(problem instanceof Error ? problem.message : String(problem));
    } finally {
      if (sequence === request.current) setBusy(null);
    }
  }

  function returnToBriefing(day: string) {
    ++request.current;
    setCitation(null);
    setNotice("");
    setBusy(null);
    setOriginalDays((days) => {
      const next = new Set(days);
      next.delete(day);
      return next;
    });
    setJump({ key: `card:${day}`, outline: false, place: "top" });
  }
  useEffect(() => {
    if (!highlight) return;
    const timer = setTimeout(() => setHighlight(null), 1500);
    return () => clearTimeout(timer);
  }, [highlight]);

  // ADR 41 Q12: a folded message opens its spoiler, and a hidden bot post
  // shows on its own. Groups look up no history for a quote.
  function jumpTo(identity: number) {
    const entry = days.find((value) =>
      value.originals.some((message) => message.message_id === identity),
    );
    if (!entry) return;
    // A summarized message reveals its day as a citation does.
    if (entry.summarized.has(identity)) return void reveal(entry, identity);
    if (entry.spoiler) setOpened((days) => new Set([...days, entry.day]));
    setRevealed(identity);
    setJump({ key: `m${identity}`, outline: true, place: "center" });
  }

  // The inverted list draws its last row at the top: that row's band stays
  // pinned above it unless the band itself is wholly on screen. A band with
  // only a sliver left at the top counts as gone.
  const viewability = useRef([
    {
      viewabilityConfig: { itemVisiblePercentThreshold: 1 },
      onViewableItemsChanged: ({
        viewableItems,
      }: {
        viewableItems: ViewToken<GroupRow>[];
      }) => {
        let last: ViewToken<GroupRow> | undefined;
        for (const token of viewableItems)
          if (token.index !== null && (!last || token.index > last.index!))
            last = token;
        setTop(last ? last.item.day : null);
        // GIFs and animated stickers move only in rows on screen.
        showRows(
          chat,
          viewableItems.map((token) => token.key),
        );
      },
    },
    {
      viewabilityConfig: { itemVisiblePercentThreshold: 100 },
      onViewableItemsChanged: ({
        viewableItems,
      }: {
        viewableItems: ViewToken<GroupRow>[];
      }) =>
        setWhole(
          new Set(
            viewableItems
              .filter((token) => token.item.kind === "band")
              .map((token) => token.item.day),
          ),
        ),
    },
  ]).current;

  async function earlierMessages() {
    setEarlier("loading");
    try {
      setEarlier((await loadEarlier()) > 0 ? "idle" : "done");
    } catch {
      setEarlier("idle");
    }
  }

  // A member's avatar or name opens their profile (ADR 51 Q14); a channel
  // or an anonymous admin has none.
  function openMember(message: NativeMessage) {
    if (!/^user:[1-9][0-9]*$/.test(message.author_peer ?? "")) return;
    router.push({
      pathname: "/profile/[account]/[peer]",
      params: { account: String(account), peer, member: message.author_peer! },
    });
  }

  function messageMenu(
    message: NativeMessage,
    version: ObservedVersion | null = null,
  ) {
    // While an earlier version shows, its sheet offers only Copy.
    if (version)
      return setMenu({
        reply: null,
        copy: version.message.deleted ? "" : version.message.text,
      });
    setMenu({
      reply: message.deleted ? null : message.message_id,
      // A deleted message's last words still copy (ADR 52 Q7).
      copy: message.text,
      ...mediaActions(
        message.deleted || !message.media ? [] : [message],
        (source) => messageTime(source.date, timezone, hourCycle),
      ),
      // Only a native user bot that is not exempt yet (the web's rule).
      bot:
        message.sender_is_bot === true &&
        !message.group_filter?.allowed_bot &&
        /^user:[1-9][0-9]*$/.test(message.author_peer ?? "")
          ? message.author_peer
          : null,
      select:
        !deletion.active && deletion.eligible(message)
          ? () => deletion.select(message)
          : undefined,
      hide: message.deleted ? () => deletion.hide([message]) : undefined,
    });
  }

  // Update and Retry share the slot. A refused tap only rereads the state,
  // which then shows why nothing started (Waiting, Budget, Syncing or ✓).
  async function act(route: string) {
    if (working || briefings.some((brief) => brief.attempt?.state === "active"))
      return;
    setWorking(true);
    try {
      await api(`${path}${route}`, { body: {} });
    } catch {
      // The reread below carries the current state.
    }
    try {
      await reload();
    } finally {
      setWorking(false);
    }
  }

  // Each switch saves, then shows the server's state; a refusal says why.
  async function save(change: () => Promise<unknown>) {
    if (saving) return;
    setSaving(true);
    setSheetNote("");
    try {
      await change();
      await reload();
    } catch (problem) {
      setSheetNote(
        problem instanceof Error ? problem.message : String(problem),
      );
    } finally {
      setSaving(false);
    }
  }

  async function stopIgnoring(author: string) {
    try {
      await api(`${path}/bots/${encodeURIComponent(author)}/restore`, {
        body: {},
      });
      await reload();
    } catch (problem) {
      setNotice(problem instanceof Error ? problem.message : String(problem));
    }
  }

  // Today's band carries the slot in AI reading; nothing can be tapped while
  // the server can't be reached or a draft is rewriting.
  function slotFor(entry: Day) {
    if (!entry.today || view !== "ai") return undefined;
    const slot = briefingSlot(slotState, {
      working,
      count: entry.fresh.length,
    });
    return slot ? (
      <SlotView
        slot={slot}
        disabled={
          unreachable || retained || snapshot?.draft?.state === "rewriting"
        }
        palette={palette}
        onAct={(route) => void act(route)}
      />
    ) : undefined;
  }

  function renderRow(row: GroupRow) {
    switch (row.kind) {
      case "band":
        // Full width, as the pinned copy over the list is.
        return (
          <View style={styles.wide}>
            <DayBand
              label={row.entry.label}
              slot={slotFor(row.entry)}
              palette={palette}
            />
          </View>
        );
      case "tools":
        return (
          <View style={styles.tools}>
            <Pressable
              accessibilityRole="button"
              hitSlop={6}
              onPress={() => returnToBriefing(row.day)}
              style={[styles.toolButton, { borderColor: palette.line }]}
            >
              <Text style={[styles.toolLabel, { color: palette.accent }]}>
                Return to briefing
              </Text>
            </Pressable>
            {nearby ? (
              <Text style={[styles.small, { color: palette.muted }]}>
                Showing cached originals near the cited source.
              </Text>
            ) : null}
          </View>
        );
      case "card":
        return (
          <BriefingCard
            entry={row.entry}
            unread={row.unread}
            timezone={timezone}
            hourCycle={hourCycle}
            expanded={expanded}
            busy={busy === row.day}
            palette={palette}
            onParticipants={(topic) =>
              setExpanded((shown) => {
                const next = new Set(shown);
                if (next.has(topic)) next.delete(topic);
                else next.add(topic);
                return next;
              })
            }
            onCite={(source) => void reveal(row.entry, source)}
            onOriginals={() => {
              const first = row.entry.brief?.final?.covered_ids[0];
              if (first !== undefined) void reveal(row.entry, first, false);
            }}
          />
        );
      case "fold":
        return (
          <SpoilerToggle
            count={row.entry.fresh.length}
            open={row.entry.open}
            unread={row.unread}
            palette={palette}
            onToggle={() => {
              // Opened, the fold moves to the top with its messages below
              // it; closed, to the bottom under the card it follows.
              setJump({
                key: row.key,
                outline: false,
                place: row.entry.open ? "bottom" : "top",
              });
              setOpened((days) => {
                const next = new Set(days);
                if (next.has(row.day)) next.delete(row.day);
                else next.add(row.day);
                return next;
              });
            }}
          />
        );
      case "original":
        return (
          <GroupBubble
            message={row.message}
            account={account}
            peer={peer}
            title={title}
            timezone={timezone}
            hourCycle={hourCycle}
            unread={row.unread}
            highlighted={row.key === highlight}
            replies={replies}
            palette={palette}
            view={view}
            detail={detail}
            onJump={jumpTo}
            onMenu={messageMenu}
            run={runs.get(row.key)}
            album={row.album}
            onMember={openMember}
            swipeReply={
              replyAllowed && !deletion.active ? composer.reply : null
            }
          />
        );
      case "pending":
        return (
          <PendingSend
            account={account}
            peer={peer}
            run={runs.get(row.key)}
            operation={row.operation}
            timezone={timezone}
            hourCycle={hourCycle}
            reply={
              row.operation.reply_to
                ? replies.quoteOf(row.operation.reply_to, row.operation.created)
                : null
            }
            onJump={jumpTo}
            onChange={reload}
          />
        );
    }
  }

  const pinned = top && !whole.has(top) ? top : null;
  const band = pinned
    ? days.find((entry) => entry.day === pinned && entry.drawn)
    : undefined;
  return (
    <DeletionContext.Provider value={deletion}>
      <GalleryProvider account={account} peer={peer} messages={messages}>
        <SafeAreaView
          edges={["top", "left", "right", "bottom"]}
          style={[styles.fill, { backgroundColor: palette.background }]}
        >
          <View style={[styles.header, { borderColor: palette.line }]}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Back to conversations"
              hitSlop={8}
              onPress={() =>
                router.canGoBack() ? router.back() : router.replace("/chats")
              }
            >
              <Ionicons name="chevron-back" size={26} color={palette.accent} />
            </Pressable>
            {/* The photo and name open the Group Profile (ADR 47 Q7), where
              Copy chat link is (Q8). */}
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`${title}, open profile`}
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
            </Pressable>
            {!accountEnabled || processing?.processing_enabled === false ? (
              <Text
                style={[
                  styles.offTag,
                  { color: palette.muted, borderColor: palette.line },
                ]}
              >
                AI off
              </Text>
            ) : null}
            {/* The Reading View's robot, before ⋮ (ADR 52 Q13). */}
            <ReadingToggle view={view} choose={choose} palette={palette} />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Conversation actions"
              hitSlop={8}
              onPress={() => {
                setSheetNote("");
                setSheet(true);
              }}
            >
              <Ionicons
                name="ellipsis-vertical"
                size={22}
                color={palette.muted}
              />
            </Pressable>
          </View>
          <View style={styles.status}>
            {copied ? (
              <Text style={[styles.small, { color: palette.muted }]}>
                {copied}
              </Text>
            ) : null}
            <ConnectionStatus />
            {notice ? (
              <Text accessibilityRole="alert" style={{ color: palette.error }}>
                {notice}
              </Text>
            ) : null}
            {error && !snapshot ? (
              <Text style={{ color: palette.error }}>{error}</Text>
            ) : null}
          </View>
          <KeyboardAvoidingView style={styles.fill} behavior="padding">
            {!snapshot ? (
              <ActivityIndicator
                style={styles.loading}
                color={palette.accent}
              />
            ) : (
              <View style={styles.fill}>
                <ChatTitle.Provider value={title}>
                  <FlatList
                    ref={list}
                    inverted
                    data={shown}
                    keyExtractor={(row) => row.key}
                    viewabilityConfigCallbackPairs={viewability}
                    // Away from the latest message, a button brings it back.
                    onScroll={(event) =>
                      setAway(event.nativeEvent.contentOffset.y > 400)
                    }
                    scrollEventThrottle={100}
                    testID="messages"
                    onScrollToIndexFailed={({ index, averageItemLength }) => {
                      // A far row is not measured yet: move toward its estimate so
                      // it renders, then try again, a few times at most.
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
                            viewOffset: -BAND,
                            animated: false,
                          }),
                        100,
                      );
                    }}
                    contentContainerStyle={styles.messages}
                    keyboardShouldPersistTaps="handled"
                    // Deletion cards sit under the latest message (CU4).
                    ListHeaderComponent={<DeletionCards deletion={deletion} />}
                    renderItem={({ item }) => (
                      <MediaRow.Provider value={`${chat}/${item.key}`}>
                        <View onLayout={() => measured(item.key)}>
                          {renderRow(item)}
                        </View>
                      </MediaRow.Provider>
                    )}
                    ListEmptyComponent={
                      <Text style={[styles.empty, { color: palette.muted }]}>
                        No cached messages in this group.
                      </Text>
                    }
                    ListFooterComponent={
                      earlier === "done" ? null : (
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
                      )
                    }
                  />
                </ChatTitle.Provider>
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
                {band ? (
                  <View style={styles.pinned}>
                    <DayBand
                      label={band.label}
                      slot={slotFor(band)}
                      palette={palette}
                    />
                  </View>
                ) : null}
              </View>
            )}
            {snapshot && retained ? (
              <RetainedDraft
                text={snapshot.draft?.text ?? ""}
                palette={palette}
              />
            ) : snapshot ? (
              <>
                <SelectionBar deletion={deletion} />
                {/* The composer keeps its draft while the bar takes its place. */}
                <View style={deletion.active ? styles.hidden : null}>
                  <ComposerBar
                    composer={composer}
                    snapshot={snapshot}
                    account={account}
                    peer={peer}
                    aiEnabled={accountEnabled}
                    group
                    nativeAvailable={nativeAvailable}
                    onSent={() => {
                      // A send opens today's new messages, so it shows (ADR 34).
                      setOpened((days) => new Set([...days, today]));
                      list.current?.scrollToOffset({ offset: 0 });
                    }}
                  />
                </View>
              </>
            ) : null}
          </KeyboardAvoidingView>
          <MessageMenu
            target={menu}
            replyAllowed={replyAllowed}
            onReply={(identity) => composer.reply(identity)}
            onCopy={(text) =>
              void Clipboard.setStringAsync(text).then(() =>
                setCopied("Copied"),
              )
            }
            onCopyTranscript={(message) => void media.copyTranscript(message)}
            onShare={(message) => void media.share(message)}
            onStopIgnoring={(author) => void stopIgnoring(author)}
            onClose={() => setMenu(null)}
          />
          <DeletionReview
            deletion={deletion}
            accountName={accountName}
            chatName={title}
          />
          <GroupSheet
            visible={sheet}
            processing={processing}
            accountEnabled={accountEnabled}
            hideBots={hideBots}
            busy={saving || unreachable}
            note={sheetNote}
            palette={palette}
            onProcessing={
              retained
                ? null
                : () =>
                    void save(() =>
                      api(`${path}/processing`, {
                        body: { enabled: !processing?.processing_enabled },
                      }),
                    )
            }
            onHideBots={() => void save(() => bots.choose(!hideBots))}
            onClose={() => setSheet(false)}
          />
        </SafeAreaView>
      </GalleryProvider>
    </DeletionContext.Provider>
  );
}

// A retained group (ADR 34) is read-only here, opened from Retained group
// work at the foot of Groups (ADR 46 CU6): its saved draft shows with Copy.
function RetainedDraft({ text, palette }: { text: string; palette: Palette }) {
  const [copied, setCopied] = useState(false);
  return (
    <View style={[styles.retained, { borderColor: palette.line }]}>
      <Text style={[styles.small, { color: palette.muted }]}>
        Saved draft · Retained group work
      </Text>
      {text ? (
        <>
          <Text selectable style={{ color: palette.text }}>
            {text}
          </Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Copy saved draft"
            hitSlop={6}
            onPress={() =>
              void Clipboard.setStringAsync(text).then(() => setCopied(true))
            }
          >
            <Text style={[styles.small, { color: palette.accent }]}>
              {copied ? "Copied" : "Copy draft"}
            </Text>
          </Pressable>
        </>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  hidden: { display: "none" },
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
  fill: { flex: 1 },
  shrink: { flexShrink: 1 },
  person: { flexDirection: "row", alignItems: "center", gap: 10 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  title: { fontSize: 17, fontWeight: "600" },
  bar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 12,
    paddingTop: 8,
  },
  status: { paddingHorizontal: 12, gap: 6, paddingTop: 6 },
  loading: { marginTop: 40 },
  messages: { padding: 12, gap: 8 },
  small: { fontSize: 13 },
  offTag: {
    fontSize: 12,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  empty: { textAlign: "center", padding: 24 },
  earlier: { alignItems: "center", padding: 12 },
  wide: { marginHorizontal: -12 },
  tools: { gap: 4, alignItems: "flex-start" },
  toolButton: {
    borderWidth: 1,
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  toolLabel: { fontSize: 14, fontWeight: "600" },
  pinned: { position: "absolute", top: 0, left: 0, right: 0 },
  retained: {
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 4,
  },
});
