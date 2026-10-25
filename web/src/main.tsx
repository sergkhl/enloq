import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowDown, ArrowLeft, BellOff, LoaderCircle } from "lucide-react";
import {
  LoginDialog,
  ManageAccounts,
  workspaceApi,
  type Overview,
  type Account,
} from "./accounts";
import { BrowserGate } from "./BrowserAuth";
import "./style.css";
import "./rewriting.css";
import "./reading.css";
import "./sending.css";
import { AiBubble, OriginalBubble, type MenuTarget } from "./Bubble";
import { ConversationHeader } from "./ConversationHeader";
import { GroupSheet } from "./GroupSheet";
import { MessageSheet } from "./MessageSheet";
import { EarlierBar } from "./EarlierBar";
import {
  ComposerSlot,
  DeletionCards,
  MessageDeletionProvider,
  SelectionBar,
} from "./MessageDeletion";
import { ProfileScreen } from "./ProfileScreen";
import { download, isVoice, transcriptText } from "./MediaFiles";
import { useForwardDetails, useReactionOptions } from "./detailData";
import { reactionChoice } from "../../shared/reactions";
import { blockAnchor, canRetry, unanalysedNote } from "../../shared/aiControls";
import { setVoiceQueue, stopPlayback } from "./playback";
import { ChatTitle, voiceSource } from "./VoiceNote";
import { UnreadSeparator } from "./UnreadSeparator";

import {
  chatApi,
  type Chat,
  type ConversationData,
  type NativeMessage,
  type Operation,
  type RetainedGroup,
} from "./conversation-api";
import { unreadAnchor } from "../../shared/unread";
import { Composer } from "./Composer";
import { SwipeReply } from "./SwipeToReply";
import { NewMessageScreen } from "./NewMessageScreen";
import { GalleryProvider } from "./MediaCarousel";
import { messageRuns, type RunEntry, type RunPlace } from "../../shared/runs";
import { albumRuns } from "../../shared/gallery";
import { openPath } from "../../shared/newMessage";
import { PendingSend } from "./PendingSends";
import { useEmotion, type Emotion } from "./Emotion";
import { useSuggestions } from "./Suggestions";
import { useAccountProcessing } from "./ProcessingControl";
import { useReadingView } from "./ReadingView";
import { useConversationScroll } from "./useConversationScroll";
import {
  ReplyQuotes,
  highlightJump,
  jumpTarget,
  useReplyResolution,
} from "./ReplyQuote";
import { useGroupedSources } from "./useGroupedSources";
import { Avatar } from "./Avatar";
import { useChanges, touchesChat } from "./events";
import {
  AiScreen,
  BudgetScreen,
  NotificationsScreen,
  SettingsScreen,
} from "./SettingsScreen";
import { GuidanceFieldScreen, GuidanceScreen } from "./GuidanceScreen";
import { PrivacyScreen } from "./DevicesScreen";
import { PairPhoneScreen } from "./PairPhoneScreen";
import { PeopleScreen } from "./PeopleScreen";
import { STAGE_TARGETS, type SettingsTarget } from "../../shared/settings";
import {
  openedByNotification,
  useNotificationNavigation,
} from "./NotificationNavigation";
import { useNotificationPresence } from "./Notifications";
import { LocalViewing } from "./LocalViewing";
import { previewTime } from "../../shared/messageTime";
import { rememberAccount, startupAccount } from "./accountSelection";
import {
  parseRoute,
  peerFragment,
  peerTab,
  type Destination,
  type RouteIntent,
} from "../../shared/chatRoute";
import { accept, publish, refuse, watchHistory } from "./routeHistory";
import { rememberedTab, rememberTab, type ChatTab } from "./chatSession";
import { useGroupProcessing } from "./GroupProcessingControl";
import { DailyBriefings } from "./DailyBriefings";
import { useHideBots } from "./HideBots";
import { ListScreen } from "./ListScreen";
import { RemindersList } from "./Reminders";

// Processing Stage labels, as the app words them (mobile/src/conversation.ts);
// done shows the rewrite, failed shows its error.
const STAGES: Record<string, string> = {
  quiet: "Waiting for a quiet moment…",
  queued: "Queued",
  preparing: "Preparing…",
  transcribing: "Transcribing…",
  reading_images: "Reading images…",
  rewriting: "Analyzing…",
  budget: "Waiting for budget",
  retrying: "Provider busy, retrying shortly",
  format_paused: "Paused after repeated format failures",
  paused: "Paused: AI processing is Off for this account",
  nothing: "Nothing to rewrite",
};

function Conversation({
  account,
  accountName,
  processing,
  reading,
  chat,
  retained = false,
  embedded = false,
  reminder,
  onBack,
  onTarget,
  onProfile,
  setNavigationGuard,
  viewingActive,
  visible,
}: {
  account: number;
  accountName: string;
  // The account's AI processing; null until it is known.
  processing: boolean | null;
  reading: ReturnType<typeof useReadingView>;
  chat: Chat;
  retained?: boolean;
  // Saved Messages inside its tab, under the list's header (ADR 50 Q2).
  embedded?: boolean;
  reminder?: string;
  onBack: () => void;
  // A budget or pause message opens its Settings section (ADR 45 Q8).
  onTarget: (target: SettingsTarget) => void;
  // A chat's header opens its profile: the Correspondent Profile (ADR 46
  // CU3) or the Group Profile (ADR 47 Q7).
  onProfile: (profile: {
    title: string;
    photoId: string | null;
    emotion: Emotion | null;
    timezone: string;
    // A group member's profile, from their avatar or name (ADR 51 Q14).
    member?: string;
  }) => void;
  setNavigationGuard: (guard: (() => Promise<boolean>) | null) => void;
  viewingActive: boolean;
  visible: boolean;
}) {
  const group = chat.kind === "group";
  // The account's own Saved Messages (ADR 50): originals only, no AI (Q3).
  const saved = chat.kind === "saved";
  const view = saved ? "originals" : reading.view;
  const [data, setData] = useState<ConversationData | null>(null);
  const retainedWork = retained || data?.retained === true;
  const emotion = useEmotion(group ? undefined : (data?.emotion ?? undefined));
  const suggestions = useSuggestions(
    group ? undefined : (data?.suggestions ?? undefined),
  );
  const groupProcessing = useGroupProcessing(account, chat.peer, group);
  const hideBots = useHideBots();
  // Each composer send; briefing reading opens today's new messages for it.
  const [sent, setSent] = useState(0);
  const [older, setOlder] = useState<NativeMessage[]>([]);
  const retainedOriginals = useRef(older);
  retainedOriginals.current = older;
  const [error, setError] = useState("");
  // A message's sheet, the group's actions sheet, and a short note such as
  // "Chat link copied" beside the reading bar.
  const [sheet, setSheet] = useState<MenuTarget | null>(null);
  const [actions, setActions] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (!note) return;
    const timer = setTimeout(() => setNote(""), 2000);
    return () => clearTimeout(timer);
  }, [note]);
  const [reply, setReply] = useState<{
    identity: number | null;
    nonce: number;
  } | null>(null);
  // The latest submission until the conversation snapshot carries it.
  const [pending, setPending] = useState<Operation | null>(null);
  const alive = useRef(true);
  const responseVersion = useRef(0);
  const refreshing = useRef<Promise<void> | null>(null);
  const refreshAgain = useRef(false);
  const opening = useRef<
    { source: number | null; separator: boolean } | undefined
  >(undefined);
  const localWindow = useRef<number | null>(null);
  // Ready unseen messages captured on opening or on return to focused viewing.
  const [unreadSources, setUnreadSources] = useState<number[]>([]);
  function refresh(): Promise<void> {
    refreshAgain.current = true;
    if (refreshing.current) return refreshing.current;
    const version = responseVersion.current;
    refreshing.current = (async () => {
      do {
        refreshAgain.current = false;
        try {
          const value = await chatApi<ConversationData>(account, chat.peer);
          if (!alive.current || version !== responseVersion.current) return;
          if (
            !opening.current &&
            value.opening_source &&
            !value.messages.some((m) => m.message_id === value.opening_source)
          )
            localWindow.current = value.opening_source;
          if (localWindow.current !== null) {
            const window = await chatApi<NativeMessage[]>(
              account,
              chat.peer,
              `/window?around=${localWindow.current}`,
            );
            if (!alive.current || version !== responseVersion.current) return;
            setOlder((previous) => [
              ...previous.filter(
                (m) => !window.some((n) => n.message_id === m.message_id),
              ),
              ...window,
            ]);
          }
          if (group && retainedOriginals.current.length) {
            const identities = retainedOriginals.current
              .map((source) => source.message_id)
              .filter(
                (identity) =>
                  !value.messages.some((m) => m.message_id === identity),
              );
            const current: NativeMessage[] = [];
            const missing = new Set<number>();
            // Citation windows can be outside the latest conversation page.
            // Revalidate their cached originals before publishing a new snapshot.
            for (let offset = 0; offset < identities.length; offset += 100) {
              const page = await chatApi<{
                messages: NativeMessage[];
                missing: number[];
              }>(account, chat.peer, "/originals/current", {
                identities: identities.slice(offset, offset + 100),
              });
              if (!alive.current || version !== responseVersion.current) return;
              current.push(...page.messages);
              for (const identity of page.missing) missing.add(identity);
            }
            setOlder((previous) => {
              const retained = new Map(
                previous
                  .filter((source) => !missing.has(source.message_id))
                  .map((source) => [source.message_id, source]),
              );
              for (const source of current) {
                const prior = retained.get(source.message_id);
                if (
                  !prior ||
                  (source.revision >= prior.revision &&
                    (!prior.deleted || source.deleted))
                )
                  retained.set(source.message_id, source);
              }
              return Array.from(retained.values());
            });
          }
          if (!opening.current) {
            // The Unread Separator is captured with the opening target, before
            // focused viewing can acknowledge its sources (ADR 28).
            const captured = value.unread_sources || [];
            setUnreadSources(captured);
            opening.current = {
              source: value.opening_source ?? null,
              separator: captured[0] === value.opening_source,
            };
          }
          // Present each received snapshot. A newer event queues another read
          // instead of starving streamed text by discarding this response.
          setData(value);
          setError("");
        } catch (e) {
          if (!alive.current || version !== responseVersion.current) return;
          setError((e as Error).message);
        }
      } while (refreshAgain.current);
    })().finally(() => {
      refreshing.current = null;
    });
    return refreshing.current;
  }
  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => {
      alive.current = false;
      ++responseVersion.current;
    };
  }, [account, chat.peer]);
  // A chat-level action, then the conversation it changed.
  async function act(action: () => Promise<unknown>) {
    try {
      await action();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  // Change notifications replace polling; stages advance by time only while
  // waiting for quiet, so a slow timer covers that transition.
  useChanges((change) => {
    if (
      touchesChat(change, account, chat.key) ||
      change.shared.includes("suggestions_enabled")
    )
      void refresh();
  }, 15000);
  const nativeById = new Map<number, NativeMessage>();
  for (const source of [...older, ...(data?.messages || [])]) {
    const prior = nativeById.get(source.message_id);
    if (
      !prior ||
      (source.revision >= prior.revision && (!prior.deleted || source.deleted))
    )
      nativeById.set(source.message_id, source);
  }
  const nativeMessages = Array.from(nativeById.values()).sort(
    (a, b) => a.message_id - b.message_id,
  );
  const grouped = useGroupedSources(
    account,
    chat.peer,
    group ? [] : data?.blocks || [],
    nativeMessages,
  );
  // Forwarded lines return with CU2; their details aren't read meanwhile.
  const replies = useReplyResolution(
    account,
    chat.peer,
    group,
    grouped.messages,
  );
  // Forward headers a cached message still lacks (ADR 46 CU2).
  const detail = useForwardDetails(account, chat.peer, replies.messages);
  const messages = useMemo(
    () => replies.messages.map(detail),
    [replies.messages, detail],
  );
  const reactionOptions = useReactionOptions(account);
  const unread = unreadAnchor(
    unreadSources,
    messages,
    group && view === "ai"
      ? (data?.briefings || []).flatMap(
          (brief) => brief.final?.covered_ids || [],
        )
      : [],
  );
  // A submission shows until its own native messages are loaded, so it is
  // never displayed twice or missing during confirmation.
  const loaded = new Set(messages.map((m) => m.message_id));
  const operations = data?.operations || [];
  const submissions = [
    ...operations.filter(
      (o) =>
        o.state !== "confirmed" ||
        !o.native_ids.length ||
        o.native_ids.some((identity) => !loaded.has(identity)),
    ),
    ...(pending && !operations.some((o) => o.id === pending.id)
      ? [pending]
      : []),
  ].sort((a, b) => a.created - b.created);
  const scroll = useConversationScroll(
    JSON.stringify([
      view,
      unread,
      messages,
      data?.blocks,
      data?.briefings,
      submissions.map((o) => [o.id, o.state]),
    ]),
    opening.current,
  );
  // A tapped Reply Quote's message (ADR 41 Q5, Q12): highlighted where it is
  // shown, after earlier pages load in a private chat or after a group reveals
  // it as a briefing citation does.
  const groupReveal = useRef<
    ((source: number) => "positioned" | "revealing" | false) | null
  >(null);
  const [jumping, setJumping] = useState<{
    identity: number;
    until: number;
  } | null>(null);
  const jumpRequest = useRef(0);
  useEffect(() => {
    if (!jumping) return;
    const timer = setInterval(() => {
      const element = jumpTarget(scroll.list.current, jumping.identity);
      if (element) {
        setJumping(null);
        highlightJump(scroll.stream.current, element);
      } else if (Date.now() > jumping.until) setJumping(null);
    }, 100);
    return () => clearInterval(timer);
  }, [jumping]);
  useEffect(() => {
    ++jumpRequest.current;
    setJumping(null);
  }, [account, chat.peer]);
  async function jump(identity: number) {
    const request = ++jumpRequest.current;
    scroll.pause();
    const element = jumpTarget(scroll.list.current, identity);
    if (element) {
      setJumping(null);
      highlightJump(scroll.stream.current, element);
      return;
    }
    if (group) {
      const revealed = groupReveal.current?.(identity) || false;
      setJumping(
        revealed === "revealing"
          ? { identity, until: Date.now() + 5000 }
          : null,
      );
      return;
    }
    // Earlier pages load the way Load originals does, until the message is
    // loaded or the history ends before it.
    setJumping({ identity, until: Date.now() + 60_000 });
    let before = messages[0]?.message_id || 0;
    try {
      for (let pages = 0; pages < 40 && before > identity; pages += 1) {
        const page = await chatApi<NativeMessage[]>(
          account,
          chat.peer,
          `/history?before=${before}`,
        );
        if (!alive.current || request !== jumpRequest.current) return;
        if (!page.length) break;
        setOlder((previous) => [...page, ...previous]);
        if (page.some((m) => m.message_id === identity)) return;
        before = Math.min(...page.map((m) => m.message_id));
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    }
    if (alive.current && request === jumpRequest.current) setJumping(null);
  }
  const replyAllowed = !retainedWork && data?.draft.state !== "rewriting";
  // Blocks shown otherwise than the reading view, until it changes (CU3).
  const [blockViews, setBlockViews] = useState<
    Record<string, "ai" | "originals">
  >({});
  useEffect(() => setBlockViews({}), [view]);
  function chooseBlockView(id: string, view: "ai" | "originals") {
    setBlockViews((views) => ({ ...views, [id]: view }));
  }
  const aiText = (block: ConversationData["blocks"][number]) =>
    !!block.rendered && block.rendered_revision === block.revision;
  // Retry a failed or interrupted block (ADR 28, ADR 46 CU3).
  async function retry(block: ConversationData["blocks"][number]) {
    try {
      await chatApi(
        account,
        chat.peer,
        `/blocks/${blockAnchor(block.id)}/retry`,
        {
          revision: block.revision,
        },
      );
      void refresh();
    } catch (problem) {
      setNote((problem as Error).message);
    }
  }
  // Leaving the chat or changing the reading view stops playback (CU1).
  useEffect(() => () => stopPlayback(), [view]);
  // A finished voice note plays on into the chat's next one.
  useEffect(() =>
    setVoiceQueue(() =>
      messages
        .filter((message) => isVoice(message) && !message.deleted)
        .map((message) => voiceSource(account, chat.peer, message, chat.title)),
    ),
  );
  // The palette loads when a sheet with reactions opens.
  const loadReactions = reactionOptions.load;
  useEffect(() => {
    if (sheet?.react?.length) loadReactions();
  }, [sheet, loadReactions]);
  // A private chat's reaction from the sheet (ADR 46 CU2).
  async function react(message: NativeMessage, emoji: string) {
    try {
      await chatApi(
        account,
        chat.peer,
        `/originals/${message.message_id}/reactions`,
        reactionChoice(message.reactions, emoji),
      );
      void refresh();
    } catch (problem) {
      setNote((problem as Error).message);
    }
  }
  // Fresh reads of loaded sources replace older copies, never newer ones.
  function mergeSources(values: NativeMessage[], missing: number[] = []) {
    setOlder((previous) => {
      if (
        !values.length &&
        !previous.some((source) => missing.includes(source.message_id))
      )
        return previous;
      // What the service no longer keeps, or the owner hid or deleted
      // through Enloq (ADR 52), leaves.
      const retained = new Map(
        previous
          .filter((source) => !missing.includes(source.message_id))
          .map((source) => [source.message_id, source]),
      );
      for (const source of values) {
        const prior = retained.get(source.message_id);
        if (
          !prior ||
          (source.revision >= prior.revision &&
            (!prior.deleted || source.deleted))
        )
          retained.set(source.message_id, source);
      }
      return Array.from(retained.values());
    });
  }
  async function copy(text: string, said: string) {
    try {
      await navigator.clipboard.writeText(text);
      setNote(said);
    } catch {
      setNote("Copy is blocked in this browser.");
    }
  }
  // A private chat in AI reading: each incoming block once, at its first
  // loaded source; Originals shows every message (the app's readingItems).
  const rows: React.ReactNode[] = [];
  // Message Runs (ADR 51 Q13): each row's sender and date first, then the
  // rows drawn with their places.
  const runRows: {
    key: string;
    entry: RunEntry;
    node: (run: RunPlace) => React.ReactNode;
  }[] = [];
  if (!group && data) {
    const shown = new Set<string>();
    // A block's own choice overrides the reading view (ADR 46 CU3).
    const blockOf = (m: NativeMessage) => {
      const found = data.blocks.find((b) => b.sources.includes(m.message_id));
      return found && (blockViews[found.id] ?? view) === "ai"
        ? found
        : undefined;
    };
    // Telegram albums among the originals, drawn as one grid (ADR 51 Q12).
    const albums = new Map<number, NativeMessage[]>();
    const inAlbum = new Set<number>();
    for (const album of albumRuns(messages.filter((m) => !blockOf(m))))
      if (album.length > 1) {
        albums.set(album[0]!.message_id, album);
        for (const m of album.slice(1)) inAlbum.add(m.message_id);
      }
    for (const m of messages) {
      const found = data.blocks.find((b) => b.sources.includes(m.message_id));
      const block = blockOf(m);
      if (!block && inAlbum.has(m.message_id)) continue;
      if (!block) {
        runRows.push({
          key: `m${m.message_id}`,
          entry: {
            sender: m.service
              ? `service:${m.message_id}`
              : m.outgoing
                ? "out"
                : "in",
            date: m.date,
            separated: unread === m.message_id,
          },
          node: (run) => (
            <OriginalBubble
              key={`m${m.message_id}`}
              run={run}
              album={albums.get(m.message_id)}
              message={m}
              account={account}
              peer={chat.peer}
              name={chat.title}
              group={false}
              timezone={data.timezone}
              readOutbox={data.chat.read_outbox || 0}
              originals={view === "originals"}
              toggleView={
                found && aiText(found)
                  ? {
                      label: "Show AI text",
                      run: () => chooseBlockView(found.id, "ai"),
                    }
                  : undefined
              }
              onMenu={setSheet}
            />
          ),
        });
        continue;
      }
      if (shown.has(block.id)) continue;
      shown.add(block.id);
      const originals = messages.filter((n) =>
        block.sources.includes(n.message_id),
      );
      const sourceStatus = grouped.status(block);
      const text = sourceStatus
        ? null
        : block.stream?.text ||
          (block.rendered_revision === block.revision
            ? block.rendered
            : null) ||
          null;
      // ADR 54: text that isn't rewritten shows as its combined originals,
      // with no AI label or status.
      const combined =
        text === null &&
        !sourceStatus &&
        !block.error &&
        !block.stage &&
        block.state === "original";
      runRows.push({
        key: `b${block.id}`,
        entry: {
          sender: "in",
          date: originals.at(-1)?.date ?? 0,
          separated:
            unread !== null && originals.some((n) => n.message_id === unread),
        },
        node: (run) => (
          <AiBubble
            key={`b${block.id}`}
            run={run}
            originals={originals}
            sourceCount={block.sources.length}
            text={text}
            calmed={!!block.calmed && !block.stream}
            originalsOnly={combined}
            streaming={!!block.stream}
            status={
              combined
                ? null
                : sourceStatus ||
                  (block.error ? (
                    block.error.includes("(omitted_target_text)") ? (
                      "The AI returned no rewrite text. Showing originals."
                    ) : (
                      block.error
                    )
                  ) : block.stage && STAGE_TARGETS[block.stage] ? (
                    // Waiting for budget and the format pause open Settings
                    // (ADR 45 Q8).
                    <button
                      type="button"
                      className="status-link"
                      onClick={() => onTarget(STAGE_TARGETS[block.stage!]!)}
                    >
                      {STAGES[block.stage]}
                    </button>
                  ) : (
                    (block.stage && STAGES[block.stage]) ||
                    (block.state === "failed"
                      ? "Rewrite failed"
                      : "Original text")
                  ))
            }
            viewedContent={block.viewed_content}
            account={account}
            peer={chat.peer}
            timezone={data.timezone}
            note={
              text !== null && ["ready", "nothing"].includes(block.state)
                ? unanalysedNote(block.unanalysed, originals)
                : null
            }
            retry={
              canRetry(block) && processing !== false && !sourceStatus
                ? () => void retry(block)
                : undefined
            }
            toggleView={
              text !== null
                ? {
                    label: "Show original",
                    run: () => chooseBlockView(block.id, "originals"),
                  }
                : undefined
            }
            onMenu={setSheet}
          />
        ),
      });
    }
  }
  // Sends not yet among the messages continue the owner's last run.
  const sendPlaces = new Map<string, RunPlace>();
  if (data) {
    const places = messageRuns(
      [
        ...runRows.map((row) => row.entry),
        ...submissions.map((operation) => ({
          sender: "out",
          date: operation.created,
        })),
      ],
      data.timezone,
    );
    runRows.forEach((row, index) => {
      if (row.entry.separated)
        rows.push(<UnreadSeparator key={`u${row.key}`} />);
      rows.push(row.node(places[index]!));
    });
    submissions.forEach((operation, index) =>
      sendPlaces.set(operation.id, places[runRows.length + index]!),
    );
  }
  return (
    <ReplyQuotes
      value={{
        account,
        peer: chat.peer,
        name: chat.title,
        group,
        timezone: data?.timezone || "UTC",
        target: replies.target,
        pending: replies.pending,
        jump: (identity) => void jump(identity),
      }}
    >
      <ChatTitle.Provider value={chat.title}>
        {/* Swipe to Reply takes the sheet's Reply while it is allowed. */}
        <SwipeReply.Provider
          value={
            replyAllowed
              ? (identity) => setReply({ identity, nonce: Date.now() })
              : null
          }
        >
          <GalleryProvider
            account={account}
            peer={chat.peer}
            messages={messages}
          >
            <section className="conversation-pane" aria-label="Conversation">
              {/* Message Selection and deletion (ADR 31, ADR 34, ADR 46 CU4). */}
              <MessageDeletionProvider
                account={account}
                peer={chat.peer}
                accountName={accountName}
                chatName={chat.title}
                sources={messages}
                operations={data?.deletions || []}
                replyHolds={data?.reply_holds || {}}
                onChange={refresh}
                onSources={mergeSources}
                version={data}
                allowed={
                  !group ||
                  (!retainedWork &&
                    groupProcessing.value?.supported === true &&
                    groupProcessing.value.access === "available")
                }
                originals={view === "originals"}
                ownOnly={group}
                retained={retainedWork}
              >
                {!embedded && (
                  <ConversationHeader
                    account={account}
                    peer={chat.peer}
                    title={chat.title}
                    photoId={chat.photo_id}
                    emotion={group ? undefined : emotion}
                    aiOff={
                      processing === false ||
                      (group &&
                        groupProcessing.value?.processing_enabled === false)
                    }
                    onBack={onBack}
                    onActions={group ? () => setActions(true) : undefined}
                    view={saved ? undefined : reading.view}
                    onView={reading.choose}
                    onProfile={() =>
                      onProfile({
                        title: chat.title,
                        photoId: chat.photo_id,
                        emotion: group ? null : emotion,
                        timezone: data?.timezone || "UTC",
                      })
                    }
                  />
                )}
                {note && (
                  <p
                    className={saved ? "saved-note" : "view-note"}
                    role="status"
                  >
                    {note}
                  </p>
                )}
                <div className="stream-area">
                  <div
                    className="message-stream"
                    ref={scroll.stream}
                    onScroll={scroll.onScroll}
                  >
                    <div className="message-list" ref={scroll.list}>
                      <LocalViewing
                        account={account}
                        peer={chat.peer}
                        active={viewingActive && !!data && !!opening.current}
                        readyOnly={group}
                        version={data}
                        stream={scroll.stream}
                        onReturn={setUnreadSources}
                      >
                        <div className="history-top">
                          {!group && !saved && data && (
                            <EarlierBar
                              account={account}
                              peer={chat.peer}
                              older={data.older}
                              progress={data.progress}
                              processing={processing}
                              onChange={() => void refresh()}
                            />
                          )}
                          <button
                            className="history-button"
                            onClick={async () => {
                              scroll.pause();
                              try {
                                const before = messages[0]?.message_id || 0;
                                const page = await chatApi<NativeMessage[]>(
                                  account,
                                  chat.peer,
                                  `/history?before=${before}`,
                                );
                                if (!alive.current) return;
                                setOlder((previous) => [...page, ...previous]);
                                await refresh();
                              } catch (e) {
                                setError((e as Error).message);
                              }
                            }}
                          >
                            Load earlier messages
                          </button>
                        </div>
                        {error && <p role="alert">{error}</p>}
                        {group && data && (
                          <DailyBriefings
                            account={account}
                            peer={chat.peer}
                            title={chat.title}
                            briefings={data.briefings || []}
                            updateView={data.briefing_update || undefined}
                            hideBots={hideBots}
                            sent={sent}
                            messages={messages}
                            view={view}
                            timezone={data.timezone}
                            locked={
                              retainedWork || data.draft.state === "rewriting"
                            }
                            unread={unread}
                            scroll={scroll.stream}
                            revealRef={groupReveal}
                            onPause={() => scroll.pause()}
                            onSources={mergeSources}
                            onMenu={setSheet}
                            onMember={(message) => {
                              if (
                                !/^user:[1-9][0-9]*$/.test(
                                  message.author_peer ?? "",
                                )
                              )
                                return;
                              onProfile({
                                title: message.author || "",
                                photoId: message.author_photo_id ?? null,
                                emotion: null,
                                timezone: data?.timezone || "UTC",
                                member: message.author_peer!,
                              });
                            }}
                            onBudget={() => onTarget("group")}
                            onChange={refresh}
                          />
                        )}
                        {rows}
                        <DeletionCards />
                        {submissions.map((operation) => (
                          <PendingSend
                            key={operation.id}
                            account={account}
                            peer={chat.peer}
                            run={sendPlaces.get(operation.id)}
                            operation={operation}
                            accountName={accountName}
                            timezone={data?.timezone || "UTC"}
                            onChange={refresh}
                          />
                        ))}
                        {data &&
                          !data.messages.length &&
                          !submissions.length && (
                            <p className="list-note">
                              No cached messages. Load earlier messages to read
                              this conversation.
                            </p>
                          )}
                      </LocalViewing>
                    </div>
                  </div>
                  {/* Away from the latest message, a button brings it back. */}
                  {scroll.away && (
                    <button
                      type="button"
                      className="scroll-latest"
                      aria-label="Scroll to latest"
                      onClick={() => scroll.latest()}
                    >
                      <ArrowDown size={22} aria-hidden />
                    </button>
                  )}
                </div>
                <SelectionBar />
                {data && (
                  <ComposerSlot>
                    <Composer
                      reminder={
                        reminder === data.reminder_occurrence
                          ? reminder
                          : undefined
                      }
                      account={account}
                      visible={visible}
                      processing={processing}
                      retained={retainedWork}
                      nativeAvailable={
                        !group ||
                        (!retainedWork &&
                          groupProcessing.value?.supported === true &&
                          groupProcessing.value.access === "available")
                      }
                      replyHolds={data.reply_holds || {}}
                      deletedTargets={messages
                        .filter((m) => m.deleted)
                        .map((m) => m.message_id)}
                      chat={chat}
                      incoming={data.draft}
                      suggestions={suggestions}
                      reply={reply}
                      onPending={(operation, clear) =>
                        setPending((current) =>
                          operation
                            ? operation
                            : clear && current?.id !== clear
                              ? current
                              : null,
                        )
                      }
                      refresh={refresh}
                      onSend={() => {
                        setSent((count) => count + 1);
                        scroll.latest();
                      }}
                      onBudget={() => onTarget("daily")}
                      setNavigationGuard={setNavigationGuard}
                    />
                  </ComposerSlot>
                )}
                {sheet && (
                  <MessageSheet
                    target={sheet}
                    replyAllowed={replyAllowed}
                    onReply={(identity) =>
                      setReply({ identity, nonce: Date.now() })
                    }
                    palette={reactionOptions.options}
                    onReact={
                      group || saved
                        ? undefined
                        : (message, emoji) => void react(message, emoji)
                    }
                    onCopy={(text) => void copy(text, "Copied")}
                    onCopyTranscript={(message) =>
                      void transcriptText(account, chat.peer, message).then(
                        (text) =>
                          text
                            ? copy(text, "Transcript copied")
                            : setNote("No transcript yet."),
                        () => setNote("The transcript couldn't be loaded."),
                      )
                    }
                    onDownload={(message) =>
                      download(account, chat.peer, message)
                    }
                    onStopIgnoring={
                      group
                        ? (author) =>
                            void act(() =>
                              chatApi(
                                account,
                                chat.peer,
                                `/bots/${encodeURIComponent(author)}/restore`,
                                {},
                              ),
                            )
                        : undefined
                    }
                    onClose={() => setSheet(null)}
                  />
                )}
                {actions && group && (
                  <GroupSheet
                    processing={retainedWork ? null : groupProcessing}
                    accountEnabled={processing}
                    hideBots={hideBots}
                    onClose={() => setActions(false)}
                  />
                )}
              </MessageDeletionProvider>
            </section>
          </GalleryProvider>
        </SwipeReply.Provider>
      </ChatTitle.Provider>
    </ReplyQuotes>
  );
}

// The application's one destination (ADR 36). Account, tab, chat and list
// visibility commit together, with the history entry they publish.
type Route = {
  reminder?: string;
  account: number;
  tab: ChatTab;
  // The open conversation; leaving it closes it, as in the app (ADR 44 MW1).
  peer: string;
  list: boolean;
  // Ordinary startup: the overview may still choose an authorized account
  // for the list it opens.
  restore: { anyAccount: boolean } | null;
  // A link that names no destination, a chat that still needs its account, or
  // a document opened by a notification whose conversation is not known yet.
  problem:
    | null
    | "invalid"
    | "notification"
    | "notification-unavailable"
    | { choose: string };
  entry: "push" | "replace";
};
type Entry = Route["entry"];

const openChat = (
  account: number,
  tab: ChatTab,
  peer: string,
  entry: Entry,
): Route => ({
  account,
  tab,
  peer,
  list: false,
  restore: null,
  problem: null,
  entry,
});
const openList = (
  account: number,
  tab: ChatTab,
  entry: Entry,
  peer = "",
): Route => ({ ...openChat(account, tab, peer, entry), list: true });
// Opening without a chat link shows the remembered account's list; the
// remembered chat per tab is gone (ADR 44, derived design).
const restoreList = (
  account: number,
  tab: ChatTab,
  anyAccount: boolean,
  entry: Entry,
): Route => ({ ...openList(account, tab, entry), restore: { anyAccount } });

// An explicit URL wins over saved choices; only the plain and account-only
// forms restore from them.
function intended(
  intent: RouteIntent,
  from: Route | null,
  entry: Entry,
): Route {
  switch (intent.kind) {
    case "reminders":
      return restoreList(
        from?.account || startupAccount(),
        "reminders",
        true,
        entry,
      );
    case "restore": {
      const account = startupAccount();
      return restoreList(account, rememberedTab(account), true, entry);
    }
    case "account":
      return restoreList(
        intent.account,
        rememberedTab(intent.account),
        false,
        entry,
      );
    case "list":
      return openList(intent.account, intent.tab, entry);
    case "chat":
      // The account's own chat is Saved Messages, its third tab (ADR 50).
      if (intent.peer === `user:${intent.account}`)
        return openList(intent.account, "saved", entry);
      return {
        ...openChat(intent.account, peerTab(intent.peer), intent.peer, entry),
        reminder: intent.occurrence,
      };
    case "choose":
      return {
        ...openChat(0, peerTab(intent.peer), "", entry),
        problem: { choose: intent.peer },
      };
    default:
      return { ...openChat(0, "private", "", entry), problem: "invalid" };
  }
}

const routeKey = (route: Route) =>
  JSON.stringify([
    route.account,
    route.tab,
    route.peer,
    route.list,
    route.restore,
    route.problem,
    route.reminder,
  ]);

function App() {
  const reading = useReadingView();
  const [navigating, setNavigating] = useState(false);
  const navigationVersion = useRef(0);
  const navigationGuard = useRef<(() => Promise<boolean>) | null>(null);
  function setNavigationGuard(guard: (() => Promise<boolean>) | null) {
    navigationGuard.current = guard;
  }
  async function navigationAllowed(current: () => boolean) {
    while (current()) {
      const guard = navigationGuard.current;
      if (guard && !(await guard())) return false;
      if (!current()) return false;
      // Sending or attaching can start while a draft save is awaited. Recheck
      // the current composer guard if its action state changed during the save.
      if (guard === navigationGuard.current) return true;
    }
    return false;
  }
  async function navigate(
    action: (
      current: () => boolean,
    ) => void | boolean | Promise<void | boolean>,
  ) {
    const version = ++navigationVersion.current;
    const current = () =>
      alive.current && version === navigationVersion.current;
    setNavigating(true);
    try {
      if (!(await navigationAllowed(current))) return false;
      return (await action(current)) !== false && current();
    } catch (error) {
      if (!current()) return false;
      throw error;
    } finally {
      if (current()) setNavigating(false);
    }
  }

  const [overview, setOverview] = useState<Overview | null>(null);
  const [route, setRoute] = useState<Route>(() =>
    // A notification's own window waits for its conversation before any
    // ordinary route state (ADR 36).
    openedByNotification()
      ? { ...openChat(0, "private", "", "replace"), problem: "notification" }
      : intended(parseRoute(location.search, location.hash), null, "replace"),
  );
  // The committed route for awaited work, ahead of the next render.
  const destination = useRef(route);
  const listVersion = useRef(0);
  const preparedList = useRef<{ account: number; kind: ChatTab } | null>(null);
  function commit(next: Route) {
    const old = destination.current;
    if (next.account !== old.account || next.tab !== old.tab) {
      // An older list read cannot resolve or replace the new destination.
      ++listVersion.current;
      preparedList.current = null;
    }
    destination.current = next;
    setRoute(next);
  }
  const accountId = route.account;
  const kind = route.tab;
  const [loadedChats, setLoadedChats] = useState<{
    account: number;
    kind: ChatTab;
    values: Chat[];
  } | null>(null);
  const [loadedRetained, setLoadedRetained] = useState<{
    account: number;
    entries: RetainedGroup[];
  } | null>(null);
  // Settings and the account screens open over the list or conversation, as
  // a stack Back walks down (ADR 44 MW4). Settings is a menu of four pages
  // since ADR 47 Q6.
  type Screen =
    | { kind: "settings" }
    | { kind: "notifications" }
    | { kind: "budget"; section: "daily" | "group" | null }
    | { kind: "ai" }
    | { kind: "privacy" }
    | { kind: "people" }
    | { kind: "pair" }
    | { kind: "guidance" }
    | { kind: "field"; name: string }
    | { kind: "accounts" }
    | { kind: "login"; expected?: Account }
    | { kind: "new-message"; account: number }
    | {
        kind: "profile";
        account: number;
        peer: string;
        title: string;
        photoId: string | null;
        emotion: Emotion | null;
        timezone: string;
        member?: string;
      };
  const [screens, setScreens] = useState<Screen[]>([]);
  const top = screens.at(-1);
  const open = (screen: Screen) => setScreens((stack) => [...stack, screen]);
  const back = () => setScreens((stack) => stack.slice(0, -1));
  // A budget message opens Settings → Budget, a pause Guidance (ADR 45 Q8).
  const openTarget = (target: SettingsTarget) =>
    open(
      target === "guidance"
        ? { kind: "guidance" }
        : { kind: "budget", section: target },
    );
  async function reviewReminder(
    target: { account_id: number; peer: string; occurrence?: string },
    notificationCurrent = () => true,
  ) {
    return navigate(async (current) => {
      const [opened, reminder] = await Promise.all([
        workspaceApi<Chat>(openPath(target.account_id, target.peer), {}),
        workspaceApi<{ occurrence?: { id: string } | null }>(
          `accounts/${target.account_id}/chats/${encodeURIComponent(target.peer)}/reminder`,
        ),
      ]);
      if (!current() || !notificationCurrent()) return false;
      if (!target.occurrence || reminder.occurrence?.id !== target.occurrence)
        throw new Error("This reminder is no longer pending.");
      if (!(await navigationAllowed(() => current() && notificationCurrent())))
        return false;
      setOpenedChats((entries) => [
        ...entries.filter(
          (entry) =>
            !(
              entry.account === target.account_id &&
              entry.chat.peer === target.peer
            ),
        ),
        { account: target.account_id, chat: opened },
      ]);
      commit({
        ...openChat(
          target.account_id,
          "private",
          target.peer,
          destination.current.problem === "notification" ? "replace" : "push",
        ),
        reminder: target.occurrence,
      });
    });
  }
  const [accountSheet, setAccountSheet] = useState(false);
  const [error, setError] = useState("");
  const overviewVersion = useRef(0),
    alive = useRef(true);
  const overviewSnapshot = useRef<{ version: number; value: Overview } | null>(
    null,
  );
  const account = overview?.accounts.find((a) => a.account_id === accountId);
  const authorized = account?.state === "authorized";
  const listed =
    kind === "reminders"
      ? !!overview
      : authorized &&
        loadedChats?.account === accountId &&
        loadedChats.kind === kind;
  const chats = listed && kind !== "reminders" ? loadedChats!.values : [];
  const retainedGroups =
    authorized && kind === "group" && loadedRetained?.account === accountId
      ? loadedRetained.entries
      : [];
  const peer = route.problem ? "" : route.peer;
  function selectList(id: number, nextKind: ChatTab) {
    const now = destination.current;
    // Choosing what is already showing is not a navigation.
    if (
      now.list &&
      now.account === id &&
      now.tab === nextKind &&
      !now.restore &&
      !now.problem
    )
      return;
    // The account switcher and the tabs live on the list, which they keep.
    commit(openList(id, nextKind, "push"));
  }
  async function accounts() {
    const version = ++overviewVersion.current;
    const value = await workspaceApi<Overview>("accounts");
    if (!alive.current || version !== overviewVersion.current) return;
    overviewSnapshot.current = { version, value };
    setOverview(value);
    const now = destination.current;
    // Only ordinary startup may choose another authorized account. An exact
    // account stays, visibly unavailable, when it is signed out (ADR 36).
    if (
      now.restore?.anyAccount &&
      !value.accounts.some(
        (a) => a.account_id === now.account && a.state === "authorized",
      )
    ) {
      const fallback =
        value.accounts.find((a) => a.state === "authorized")?.account_id || 0;
      if (fallback !== now.account)
        commit({
          ...now,
          account: fallback,
          tab: now.tab === "reminders" ? "reminders" : rememberedTab(fallback),
        });
    }
  }
  useEffect(() => {
    alive.current = true;
    const refresh = () =>
      void accounts().catch((e) => alive.current && setError(e.message));
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => {
      alive.current = false;
      ++overviewVersion.current;
      clearInterval(timer);
    };
  }, []);
  const chatList = useRef<() => void>(() => {});
  useChanges((change) => {
    if (accountId && touchesChat(change, accountId)) chatList.current();
  }, 60000);
  useEffect(() => {
    let alive = true;
    chatList.current = () => {};
    if (accountId && authorized && kind !== "reminders") {
      const prepared =
        preparedList.current?.account === accountId &&
        preparedList.current.kind === kind;
      if (prepared) preparedList.current = null;
      let refreshing: Promise<void> | null = null;
      let again = false;
      const refresh = (): Promise<void> => {
        again = true;
        if (refreshing) return refreshing;
        refreshing = (async () => {
          do {
            again = false;
            const version = listVersion.current;
            const current = () =>
              alive &&
              version === listVersion.current &&
              destination.current.account === accountId &&
              destination.current.tab === kind;
            try {
              const [received, retained] = await Promise.all([
                workspaceApi<Chat[]>(
                  `accounts/${accountId}/chats?kind=${kind}`,
                ),
                kind === "group"
                  ? workspaceApi<RetainedGroup[]>(
                      `accounts/${accountId}/retained-groups`,
                    )
                  : Promise.resolve([]),
              ]);
              if (current()) {
                setLoadedChats({
                  account: accountId,
                  kind,
                  values: received.filter(
                    (c) => (c.kind ?? "private") === kind,
                  ),
                });
                setLoadedRetained({ account: accountId, entries: retained });
              }
            } catch (error) {
              if (current()) setError((error as Error).message);
            }
          } while (alive && again);
        })().finally(() => {
          refreshing = null;
        });
        return refreshing;
      };
      if (!prepared) void refresh();
      chatList.current = () => void refresh();
      return () => {
        alive = false;
      };
    }
    return () => {
      alive = false;
    };
  }, [accountId, kind, authorized]);
  const retainedEntry = retainedGroups.find(
    (entry) => entry.chat.peer === peer,
  );
  // Private chats opened outside the seven-day list, by New Message or a
  // chat link (ADR 51 Q17).
  const [openedChats, setOpenedChats] = useState<
    { account: number; chat: Chat }[]
  >([]);
  const [linkFailed, setLinkFailed] = useState<string | null>(null);
  const chat =
    chats.find((c) => c.peer === peer) ||
    openedChats.find(
      (entry) => entry.account === accountId && entry.chat.peer === peer,
    )?.chat ||
    retainedEntry?.chat;
  const remember = (opened: Chat) =>
    setOpenedChats((entries) => [
      ...entries.filter(
        (entry) =>
          !(entry.account === accountId && entry.chat.peer === opened.peer),
      ),
      { account: accountId, chat: opened },
    ]);
  // A link to a person's private chat outside the list opens it as New
  // Message does; a group's link still needs the list (ADR 36 Q5).
  const linked =
    listed &&
    !!peer &&
    !route.list &&
    !route.restore &&
    !chat &&
    /^user:[1-9][0-9]*$/.test(peer);
  useEffect(() => {
    if (!linked || !peer) return;
    let alive = true;
    workspaceApi<Chat>(openPath(accountId, peer), {}).then(
      (opened) => alive && remember(opened),
      () => alive && setLinkFailed(`${accountId}/${peer}`),
    );
    return () => {
      alive = false;
    };
  }, [linked, accountId, peer]);
  useEffect(() => {
    // Startup's list is settled once the overview has chosen its account.
    const now = destination.current;
    if (now.restore && !now.problem && overview)
      commit({ ...now, restore: null });
  }, [route, overview]);
  useEffect(() => {
    // Only successful navigation becomes a saved choice.
    if (
      kind === "reminders" ||
      !listed ||
      route.problem ||
      route.restore ||
      (!route.list && !chat)
    )
      return;
    rememberAccount(accountId);
    rememberTab(accountId, kind);
  }, [
    listed,
    accountId,
    kind,
    route.list,
    route.restore,
    route.problem,
    chat?.peer,
  ]);
  useEffect(() => {
    // An exact chat missing from the loaded list may be newer than that read.
    if (listed && peer && !route.list && !route.restore && !chat)
      chatList.current();
  }, [accountId, kind, peer]);
  const address: Destination | null =
    kind === "reminders" && !route.problem
      ? { kind: "reminders" }
      : route.problem || route.restore || !accountId
        ? null
        : route.list || !peer
          ? { kind: "list", account: accountId, tab: kind }
          : peerFragment(peer)
            ? {
                kind: "chat",
                account: accountId,
                peer,
                occurrence: route.reminder,
              }
            : null;
  const addressKey = address ? JSON.stringify(address) : "";
  useEffect(() => {
    // An exact destination keeps its address while it is pending or
    // unavailable; restoration publishes only what it resolved.
    if (address) publish(address, route.entry);
  }, [addressKey]);
  useEffect(
    () =>
      watchHistory((arrival) => {
        const now = destination.current;
        const target = intended(
          parseRoute(location.search, location.hash),
          now,
          "replace",
        );
        if (routeKey(target) === routeKey(now)) return void accept(arrival);
        // Back, Forward and hash edits pass the same draft and action guard.
        // The browser already shows their entry, so a refusal returns it to
        // the committed one; nothing is replayed later (ADR 36).
        navigate(() => {
          if (!accept(arrival)) return false;
          commit(target);
        }).then(
          (moved) => {
            if (!moved) refuse(arrival);
          },
          (error) => {
            refuse(arrival);
            if (alive.current) setError((error as Error).message);
          },
        );
      }),
    [],
  );
  const processing = useAccountProcessing(authorized ? accountId : 0);
  useNotificationPresence(
    authorized ? accountId : 0,
    route.list ? null : chat?.peer || null,
  );
  useNotificationNavigation(
    !!overview,
    async (target, notificationCurrent) => {
      return navigate(async (current) => {
        const version = ++overviewVersion.current;
        const [receivedOverview, destinations] = await Promise.all([
          workspaceApi<Overview>("accounts"),
          workspaceApi<Chat[]>(
            `accounts/${target.account_id}/chats?kind=private`,
          ),
        ]);
        if (!current() || !notificationCurrent()) return false;
        // The owner can keep editing or start a send during destination reads.
        // Save/recheck that current draft before publishing the exact target.
        if (
          !(await navigationAllowed(() => current() && notificationCurrent()))
        )
          return false;
        const value =
          overviewSnapshot.current && overviewSnapshot.current.version > version
            ? overviewSnapshot.current.value
            : receivedOverview;
        if (
          !value.accounts.some(
            (a) =>
              a.account_id === target.account_id && a.state === "authorized",
          ) ||
          !destinations.some(
            (c) =>
              c.peer === target.peer && (c.kind ?? "private") === "private",
          )
        )
          throw new Error("This notification's conversation is unavailable.");
        if (
          !overviewSnapshot.current ||
          overviewSnapshot.current.version <= version
        )
          overviewSnapshot.current = { version, value };
        const now = destination.current;
        const prepared =
          now.account === target.account_id && now.tab === "private"
            ? null
            : { account: target.account_id, kind: "private" as const };
        setOverview(value);
        setLoadedChats({
          account: target.account_id,
          kind: "private",
          values: destinations.filter(
            (c) => (c.kind ?? "private") === "private",
          ),
        });
        // Invalidate older list reads before publishing this validated
        // destination. Their result must not replace the exact clicked chat.
        ++listVersion.current;
        // An existing window adds an entry; the notification's own window
        // replaces its transient locator address.
        commit(
          openChat(
            target.account_id,
            "private",
            target.peer,
            now.problem === "notification" ? "replace" : "push",
          ),
        );
        preparedList.current = prepared;
      });
    },
    (message) => {
      // An existing window keeps its destination. The notification's own
      // window shows unavailable, never another conversation.
      const now = destination.current;
      if (now.problem === "notification")
        commit({ ...now, problem: "notification-unavailable" });
      else setError(message);
    },
    () => {
      // A valid Test names no conversation: ordinary startup continues.
      if (destination.current.problem === "notification")
        commit(
          intended(parseRoute(location.search, location.hash), null, "replace"),
        );
    },
    () => {
      const version = navigationVersion.current;
      return () => alive.current && version === navigationVersion.current;
    },
    () => {
      // A Join Request's push opens Settings → People (ADR 48 Q19).
      if (destination.current.problem === "notification")
        commit(
          intended(parseRoute(location.search, location.hash), null, "replace"),
        );
      setScreens([{ kind: "settings" }, { kind: "people" }]);
    },
    () => {
      // A decision-model outage's push opens Settings → AI (ADR 49 Q14).
      if (destination.current.problem === "notification")
        commit(
          intended(parseRoute(location.search, location.hash), null, "replace"),
        );
      setScreens([{ kind: "settings" }, { kind: "ai" }]);
    },
    async (target, current) => {
      if (target.kind === "reminders")
        return navigate(() =>
          commit(
            openList(
              destination.current.account,
              "reminders",
              destination.current.problem === "notification"
                ? "replace"
                : "push",
            ),
          ),
        );
      if (target.account_id && target.peer && target.occurrence)
        await reviewReminder(
          {
            account_id: target.account_id,
            peer: target.peer,
            occurrence: target.occurrence,
          },
          current,
        );
    },
  );
  // The list shows for a signed-in account, while loading and when no account
  // is signed in at all; a signed-out exact account names why instead.
  const listShown =
    route.list &&
    !route.problem &&
    (kind === "reminders" || authorized || !overview || !accountId);
  const startup = () => intended({ kind: "restore" }, null, "push");
  const conversations = (target: () => Route) => (
    <button
      className="secondary"
      onClick={() => void navigate(() => commit(target()))}
    >
      Open conversations
    </button>
  );
  const choice = typeof route.problem === "object" ? route.problem : null;
  const exactAccount =
    !route.problem && !!accountId && !route.restore?.anyAccount;
  // What the pane says while no conversation is open. An exact link keeps its
  // address and names why it is not showing.
  const unopened: {
    title: string;
    detail: string;
    actions?: React.ReactNode;
  } =
    route.problem === "invalid"
      ? {
          title: "This link is not valid",
          detail: "Check the address, or open your conversations.",
          actions: conversations(startup),
        }
      : route.problem === "notification"
        ? {
            title: "Opening notification…",
            detail: "Finding this notification's conversation.",
            actions: conversations(startup),
          }
        : route.problem === "notification-unavailable"
          ? {
              title: "This conversation is unavailable",
              detail: "This notification's conversation is unavailable.",
              actions: conversations(startup),
            }
          : choice
            ? {
                title: "Choose an account",
                detail:
                  "This link opens a conversation in one of your accounts.",
                actions: (
                  <div
                    className="route-accounts"
                    role="group"
                    aria-label="Choose an account"
                  >
                    {(overview?.accounts || [])
                      .filter((a) => a.state === "authorized")
                      .map((a) => (
                        <button
                          key={a.account_id}
                          className="secondary"
                          onClick={() =>
                            void navigate(() =>
                              commit(
                                openChat(
                                  a.account_id,
                                  peerTab(choice.choose),
                                  choice.choose,
                                  "push",
                                ),
                              ),
                            )
                          }
                        >
                          <Avatar
                            account={a.account_id}
                            peer={`user:${a.account_id}`}
                            name={a.name}
                            photoId={a.photo_id}
                            className="small-avatar"
                          />
                          {a.name}
                        </button>
                      ))}
                  </div>
                ),
              }
            : overview && exactAccount && !account
              ? {
                  title: "This account is not available",
                  detail: "Sign in to that Telegram account to open this link.",
                  actions: (
                    <>
                      <button
                        className="secondary"
                        disabled={!overview.login_available}
                        onClick={() => open({ kind: "login" })}
                      >
                        Add account
                      </button>
                      {conversations(startup)}
                    </>
                  ),
                }
              : overview && exactAccount && account && !authorized
                ? {
                    title: `${account.name} is signed out`,
                    detail: "Sign in to this account to open the link.",
                    actions: (
                      <>
                        <button
                          className="secondary"
                          disabled={!overview.login_available}
                          onClick={() =>
                            open({ kind: "login", expected: account })
                          }
                        >
                          Sign in
                        </button>
                        {conversations(startup)}
                      </>
                    ),
                  }
                : listed &&
                    peer &&
                    !route.list &&
                    !route.restore &&
                    !chat &&
                    (!linked || linkFailed === `${accountId}/${peer}`)
                  ? {
                      title: "This conversation is unavailable",
                      detail:
                        "It is not among this account's conversations from the last seven days.",
                      actions: conversations(() =>
                        openList(accountId, kind, "push"),
                      ),
                    }
                  : {
                      title: "Opening conversation…",
                      detail: "",
                      actions: conversations(() =>
                        openList(accountId, kind, "push"),
                      ),
                    };
  // The column shows one screen at a time, as the Enloq app does (ADR 44 Q1):
  // the list, an open conversation, or why a link can't open.
  const conversationShown = !!chat && !route.list && !route.problem;
  return (
    <main className="rewriting-app">
      {conversationShown ? (
        <Conversation
          reminder={route.reminder}
          key={`${accountId}/${peer}`}
          account={accountId}
          accountName={account?.name || `Account ${accountId}`}
          processing={processing.enabled}
          reading={reading}
          chat={chat}
          retained={!!retainedEntry}
          onBack={() =>
            // Leaving a conversation closes it, as in the app (MW1).
            void navigate(() => {
              commit(
                openList(
                  accountId,
                  route.reminder ? "reminders" : kind,
                  "push",
                ),
              );
            })
          }
          onTarget={openTarget}
          onProfile={(profile) =>
            open({ kind: "profile", account: accountId, peer, ...profile })
          }
          setNavigationGuard={setNavigationGuard}
          viewingActive={!top && !navigating}
          visible
        />
      ) : listShown ? (
        <ListScreen
          overview={overview}
          account={account}
          tab={kind}
          chats={chats}
          loaded={listed}
          view={reading.view}
          error={error}
          sheet={accountSheet}
          onSheet={setAccountSheet}
          onSelectAccount={(id) =>
            void navigate(() => selectList(id, rememberedTab(id)))
          }
          onTab={(tab) => void navigate(() => selectList(accountId, tab))}
          onOpen={(selected) =>
            void navigate(() =>
              commit(openChat(accountId, kind, selected, "push")),
            )
          }
          onManage={() => open({ kind: "accounts" })}
          onAdd={() => open({ kind: "login" })}
          onSettings={() => open({ kind: "settings" })}
          onNewMessage={() => open({ kind: "new-message", account: accountId })}
          reminders={
            <RemindersList
              onReview={(item) =>
                void reviewReminder({
                  account_id: item.account_id,
                  peer: item.peer,
                  occurrence: item.occurrence?.id,
                }).catch((e) => setError(e.message))
              }
              onEdit={(item) =>
                open({
                  kind: "profile",
                  account: item.account_id,
                  peer: item.peer,
                  title: item.title,
                  photoId: null,
                  emotion: null,
                  timezone: item.schedule?.timezone || "UTC",
                })
              }
            />
          }
          saved={
            kind === "saved" && chats[0] ? (
              <Conversation
                key={`${accountId}/${chats[0].peer}`}
                account={accountId}
                accountName={account?.name || `Account ${accountId}`}
                processing={processing.enabled}
                reading={reading}
                chat={chats[0]}
                embedded
                onBack={() => {}}
                onTarget={openTarget}
                onProfile={() => {}}
                setNavigationGuard={setNavigationGuard}
                viewingActive={!top && !navigating}
                visible
              />
            ) : undefined
          }
        />
      ) : (
        <section className="problem-screen">
          <h1>{unopened.title}</h1>
          <p>{unopened.detail}</p>
          {unopened.actions}
          {error && <p role="alert">{error}</p>}
        </section>
      )}
      {top?.kind === "settings" && (
        <SettingsScreen
          onBack={back}
          onOpen={(kind) =>
            open(kind === "budget" ? { kind, section: null } : { kind })
          }
        />
      )}
      {top?.kind === "notifications" && <NotificationsScreen onBack={back} />}
      {top?.kind === "budget" && (
        <BudgetScreen
          key={JSON.stringify(top)}
          section={top.section}
          timezone={overview?.timezone || "UTC"}
          onBack={back}
        />
      )}
      {top?.kind === "ai" && (
        <AiScreen
          timezone={overview?.timezone || "UTC"}
          onBack={back}
          onGuidance={() => open({ kind: "guidance" })}
        />
      )}
      {top?.kind === "privacy" && (
        <PrivacyScreen
          timezone={overview?.timezone || "UTC"}
          onBack={back}
          onPair={() => open({ kind: "pair" })}
        />
      )}
      {top?.kind === "people" && (
        <PeopleScreen timezone={overview?.timezone || "UTC"} onBack={back} />
      )}
      {top?.kind === "pair" && <PairPhoneScreen onBack={back} />}
      {top?.kind === "guidance" && (
        <GuidanceScreen
          onBack={back}
          onField={(name) => open({ kind: "field", name })}
        />
      )}
      {top?.kind === "field" && (
        <GuidanceFieldScreen key={top.name} name={top.name} onBack={back} />
      )}
      {top?.kind === "profile" && (
        <ProfileScreen
          key={`${top.account}/${top.peer}/${top.member ?? ""}`}
          account={top.account}
          peer={top.peer}
          member={top.member}
          onMessage={(opened) => {
            remember(opened);
            back();
            void navigate(() =>
              commit(openChat(top.account, "private", opened.peer, "push")),
            );
          }}
          title={top.title}
          photoId={top.photoId}
          emotion={top.emotion}
          timezone={top.timezone}
          onBack={back}
        />
      )}
      {top?.kind === "new-message" && (
        <NewMessageScreen
          account={top.account}
          onBack={back}
          onOpened={(opened) => {
            remember(opened);
            back();
            void navigate(() =>
              commit(openChat(top.account, "private", opened.peer, "push")),
            );
          }}
        />
      )}
      {top?.kind === "accounts" && overview && (
        <ManageAccounts
          overview={overview}
          onClose={back}
          onRefresh={async () => {
            await accounts();
            // Logging out of or removing the account on screen shows the
            // first account still signed in, as the app does.
            const value = overviewSnapshot.current?.value;
            const now = destination.current;
            if (!value || now.problem) return;
            const signedIn = value.accounts.filter(
              (a) => a.state === "authorized",
            );
            if (signedIn.some((a) => a.account_id === now.account)) return;
            const next = signedIn[0]?.account_id || 0;
            commit(openList(next, rememberedTab(next), "push"));
          }}
          onLogin={(a) => open({ kind: "login", expected: a })}
        />
      )}
      {top?.kind === "login" && (
        <LoginDialog
          expected={top.expected}
          offline={overview?.mode === "offline"}
          onClose={back}
          onAdded={(id) => {
            // Signing in closes the screens it came through.
            setScreens([]);
            void navigate(async (current) => {
              await accounts();
              if (!(await navigationAllowed(current))) return false;
              const now = destination.current;
              const signedIn = (wanted: number) =>
                !!overviewSnapshot.current?.value.accounts.some(
                  (a) => a.account_id === wanted && a.state === "authorized",
                );
              // An exact destination waits for its own account: signing in
              // to that account opens it, and another account never
              // retargets it (ADR 36).
              if (
                typeof now.problem === "object" && now.problem
                  ? true
                  : !now.problem &&
                    !now.restore?.anyAccount &&
                    !!now.account &&
                    (now.account === id || !signedIn(now.account))
              )
                return;
              // Ordinary startup already follows the first account signed in.
              if (now.restore?.anyAccount && now.account === id) return;
              selectList(id, rememberedTab(id));
            }).catch((error) => alive.current && setError(error.message));
          }}
        />
      )}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(
  <BrowserGate>
    <App />
  </BrowserGate>,
);
