import Ionicons from "@expo/vector-icons/Ionicons";
import React from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  useColorScheme,
  View,
} from "react-native";
import {
  PARTLY_SUPPORTED,
  streamedTopics,
  type BriefingSlot,
} from "../../shared/groupBriefings";
import { messageTime, type HourCycle } from "../../shared/messageTime";
import { participantLabel } from "../../shared/participantLabel";
import { isReply } from "../../shared/replyQuote";
import type { NativeMessage } from "./conversation";
import type { Day } from "./groups";
import { forwardedLine, forwardOf } from "../../shared/forwarding";
import { BubbleMenu } from "./bubbleMenu";
import type { ReadingView } from "./data";
import { useVersions, type ObservedVersion } from "./details";
import {
  ForwardedLines,
  ReactionChips,
  VersionControls,
} from "./MessageDetails";
import { MessageBody, UnreadLine } from "./Reading";
import type { Replies } from "./replies";
import { DeletingMark, Selectable } from "./MessageDeletion";
import { ReplyQuote } from "./ReplyQuote";
import { SwipeToReply } from "./SwipeToReply";
import { runCorners, Tail } from "./Tail";
import { Avatar } from "./Avatar";
import { AlbumGrid } from "./AlbumGrid";
import { nameColor, type RunPlace } from "../../shared/runs";
import type { Palette } from "./theme";
import { openSettings } from "./settingsLinks";

// A group's reading (ADR 34, ADR 42 AG3), as the web's DailyBriefings draws
// it: date bands, briefing cards, the "N new messages" spoiler and originals
// with their author lines.

// A day's band (ADR 34): green-tinted and full width with a 2 px
// accent rule, today's with the Briefing Slot at its right end.
export function DayBand({
  label,
  slot,
  palette,
}: {
  label: string;
  slot?: React.ReactNode;
  palette: Palette;
}) {
  return (
    <View
      style={[
        styles.band,
        { backgroundColor: palette.band, borderColor: palette.accent },
      ]}
    >
      <View style={styles.bandSide} />
      <Text
        accessibilityRole="header"
        numberOfLines={1}
        style={[styles.bandLabel, { color: palette.text }]}
      >
        {label}
      </Text>
      <View style={[styles.bandSide, styles.bandEnd]}>{slot}</View>
    </View>
  );
}

const SLOT_ICONS: Record<
  Exclude<BriefingSlot["kind"], "waiting">,
  React.ComponentProps<typeof Ionicons>["name"]
> = {
  update: "refresh",
  retry: "warning-outline",
  budget: "wallet-outline",
  syncing: "swap-vertical",
  up_to_date: "checkmark",
};

// Today's Briefing Slot (ADR 34; ADR 42 Q2): an icon and a word
// at most, with no tooltip. Update (green) and Retry (amber) act on a tap,
// and Budget opens its budget in Settings (ADR 45 Q8); the others only
// report.
export function SlotView({
  slot,
  disabled,
  palette,
  onAct,
}: {
  slot: BriefingSlot;
  disabled: boolean;
  palette: Palette;
  onAct: (path: string) => void;
}) {
  const acts = !!slot.path;
  const color = !acts
    ? palette.muted
    : slot.kind === "retry"
      ? palette.onRetry
      : palette.onAccent;
  const content = (
    <>
      {slot.kind === "waiting" ? (
        <ActivityIndicator size="small" color={color} />
      ) : (
        <Ionicons name={SLOT_ICONS[slot.kind]} size={15} color={color} />
      )}
      {slot.word ? (
        <Text style={[styles.slotWord, { color }]}>{slot.word}</Text>
      ) : null}
    </>
  );
  // A spent Group Briefing Budget opens it in Settings (ADR 45 Q8).
  if (slot.kind === "budget")
    return (
      <Pressable
        accessibilityRole="link"
        accessibilityLabel={slot.label}
        accessibilityHint="Opens Settings"
        hitSlop={6}
        onPress={() => openSettings("group")}
        style={styles.slot}
      >
        {content}
      </Pressable>
    );
  if (!slot.path)
    return (
      <View
        accessible
        accessibilityRole="text"
        accessibilityLabel={slot.label}
        style={styles.slot}
      >
        {content}
      </View>
    );
  const path = slot.path;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={slot.label}
      accessibilityState={{ disabled }}
      disabled={disabled}
      hitSlop={6}
      onPress={() => onAct(path)}
      style={[
        styles.slot,
        {
          backgroundColor:
            slot.kind === "retry" ? palette.retry : palette.accent,
          opacity: disabled ? 0.5 : 1,
        },
      ]}
    >
      {content}
    </Pressable>
  );
}

// A briefed day's card: its topics with bold titles, citations and the first
// three participants, the coverage line and the notes on what it misses. A
// running attempt's streamed topics show in its place.
export function BriefingCard({
  entry,
  unread,
  timezone,
  hourCycle,
  expanded,
  busy,
  palette,
  onParticipants,
  onCite,
  onOriginals,
}: {
  entry: Day;
  unread: boolean;
  timezone: string;
  hourCycle: HourCycle | undefined;
  // Topics whose participants show in full.
  expanded: ReadonlySet<string>;
  // A citation of this day is being read.
  busy: boolean;
  palette: Palette;
  onParticipants: (topic: string) => void;
  onCite: (source: number) => void;
  onOriginals: () => void;
}) {
  const { brief, prefix } = entry;
  const wording = !entry.showingOriginals && entry.hasWording ? brief : null;
  const final = wording?.final ?? null;
  const missing = !!brief && !prefix && (brief.partial || brief.gap);
  const text = { color: palette.text };
  const muted = { color: palette.muted };
  return (
    <View
      accessibilityLabel={`Daily briefing for ${entry.label}`}
      style={[styles.card, { backgroundColor: palette.panel }]}
    >
      {unread ? <UnreadLine palette={palette} /> : null}
      {wording && prefix
        ? streamedTopics(prefix).map((topic, index) => (
            <View key={index} style={styles.topic}>
              <Text style={[styles.title, text]}>{topic.title}</Text>
              {topic.body ? (
                <Text style={[styles.body, text]}>{topic.body}</Text>
              ) : null}
            </View>
          ))
        : null}
      {final && !prefix ? (
        final.local_outcome ? (
          <Text style={[styles.body, text]}>
            {final.local_outcome === "ads_skipped"
              ? "Ads skipped"
              : "Bot messages skipped"}
          </Text>
        ) : final.outcome === "no_substance" ? (
          <Text style={[styles.body, text]}>
            No substantive updates in the covered messages.
          </Text>
        ) : (
          final.topics.map((topic, index) => {
            const key = `${final.id}/${index}`;
            const participants = topic.participants ?? [];
            const all = expanded.has(key);
            const names = all ? participants : participants.slice(0, 3);
            return (
              <View key={key} style={styles.topic}>
                {topic.title ? (
                  <Text style={[styles.title, text]}>{topic.title}</Text>
                ) : null}
                <Text selectable style={[styles.body, text]}>
                  {topic.text}
                </Text>
                {topic.fully_supported === false ? (
                  <Text style={[styles.small, muted]}>{PARTLY_SUPPORTED}</Text>
                ) : null}
                <SourceLinks
                  citations={topic.citations}
                  busy={busy}
                  palette={palette}
                  onCite={onCite}
                />
                {participants.length ? (
                  <View style={styles.row}>
                    {names.map((participant) => (
                      <Text
                        key={participant.peer}
                        style={[styles.small, muted]}
                      >
                        {participant.name}
                      </Text>
                    ))}
                    {participants.length > 3 ? (
                      <Pressable
                        accessibilityRole="button"
                        accessibilityState={{ expanded: all }}
                        hitSlop={6}
                        onPress={() => onParticipants(key)}
                      >
                        <Text style={[styles.small, { color: palette.accent }]}>
                          {all
                            ? "Show fewer"
                            : `+${participants.length - 3} more`}
                        </Text>
                      </Pressable>
                    ) : null}
                  </View>
                ) : null}
              </View>
            );
          })
        )
      ) : null}
      {final && !prefix ? (
        <Text style={[styles.small, muted]}>
          {`Covered ${messageTime(final.covered_start, timezone, hourCycle)}–${messageTime(final.covered_end, timezone, hourCycle)}`}
          {wording?.partial ? " · Partial coverage" : ""}
          {wording?.cached ? " · Cached briefing" : ""}
        </Text>
      ) : null}
      {wording && !prefix ? (
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ disabled: busy }}
          disabled={busy}
          hitSlop={6}
          onPress={onOriginals}
          style={styles.link}
        >
          <Text style={[styles.small, { color: palette.accent }]}>
            Show day originals
          </Text>
        </Pressable>
      ) : null}
      {missing && brief ? (
        <Text style={[styles.small, muted]}>
          {brief.omissions.length
            ? `${brief.omissions.length} cached sources not covered. `
            : ""}
          {brief.intake_coverage.native_omitted
            ? `${brief.intake_coverage.native_omitted} additional originals were outside the native intake limit. `
            : ""}
          {!brief.intake_coverage.range_complete || brief.gap
            ? "The complete day's originals have not been verified."
            : ""}
        </Text>
      ) : null}
    </View>
  );
}

// A statement's `#ID` links to its sources, each opening its original: a
// briefing topic's (ADR 42 Q4) and an Ask this group answer's (ADR 59 Q1).
export function SourceLinks({
  citations,
  busy = false,
  palette,
  onCite,
}: {
  citations: number[];
  // A citation is being read.
  busy?: boolean;
  palette: Palette;
  onCite: (source: number) => void;
}) {
  return (
    <View style={styles.row}>
      {citations.map((identity) => (
        <Pressable
          key={identity}
          accessibilityRole="button"
          accessibilityLabel={`Source ${identity}`}
          accessibilityState={{ disabled: busy }}
          disabled={busy}
          hitSlop={6}
          onPress={() => onCite(identity)}
        >
          <Text style={[styles.small, { color: palette.accent }]}>
            #{identity}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

// The messages a briefing does not cover yet fold under one spoiler, closed
// by default and kept open until the chat closes (ADR 34).
export function SpoilerToggle({
  count,
  open,
  unread,
  palette,
  onToggle,
}: {
  count: number;
  open: boolean;
  unread: boolean;
  palette: Palette;
  onToggle: () => void;
}) {
  return (
    <View>
      {unread ? <UnreadLine palette={palette} /> : null}
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={onToggle}
        style={[
          styles.fold,
          { backgroundColor: palette.panel, borderColor: palette.line },
        ]}
      >
        <Ionicons
          name={open ? "chevron-down" : "chevron-forward"}
          size={14}
          color={palette.text}
        />
        <Text style={[styles.foldLabel, { color: palette.text }]}>
          {count} new {count === 1 ? "message" : "messages"}
        </Text>
      </Pressable>
    </View>
  );
}

// A group original with its author line: the name or You, a signature, and
// `(ad)` for a validated ad. Groups show Sent, never Read (ADR 34). Its
// forward line, display-only reactions and, in Originals, earlier versions
// follow the private chat's (ADR 46 CU2).
export function GroupBubble({
  message,
  account,
  peer,
  title,
  timezone,
  hourCycle,
  unread,
  highlighted,
  replies,
  palette,
  view,
  detail = (value) => value,
  onJump,
  onMenu,
  swipeReply = null,
  run = { first: true, last: true },
  onMember,
  album,
}: {
  message: NativeMessage;
  account: number;
  peer: string;
  title: string;
  timezone: string;
  hourCycle: HourCycle | undefined;
  unread: boolean;
  highlighted: boolean;
  replies: Replies;
  palette: Palette;
  view: ReadingView;
  // The message with the forward header the service filled in since.
  detail?: (message: NativeMessage) => NativeMessage;
  onJump: (target: number) => void;
  // A shown earlier version's sheet offers only Copy.
  onMenu: (message: NativeMessage, version: ObservedVersion | null) => void;
  // Swipe to Reply's composer Reply, while Reply is available (ADR 51 Q5).
  swipeReply?: ((identity: number) => void) | null;
  // Its place in its Message Run (ADR 51 Q13).
  run?: RunPlace;
  // A member's avatar or name opens their profile (Q14).
  onMember?: (message: NativeMessage) => void;
  // A Telegram album this original begins, as one grid (Q12).
  album?: NativeMessage[];
}) {
  const shown = detail(replies.message(message));
  const versions = useVersions(account, peer, shown.service ? null : shown);
  if (shown.service)
    return (
      <Text style={[styles.service, { color: palette.muted }]}>
        {shown.text}
      </Text>
    );
  const outgoing = !!shown.outgoing;
  const scheme = useColorScheme() === "dark" ? "dark" : "light";
  // Telegram's phone look (ADR 51 Q13): another person's run has their name
  // in their colour on its first bubble and their avatar beside its last;
  // the owner's own messages carry neither. An ad keeps its mark.
  const label = `${participantLabel(shown, title, account, true)}${
    shown.group_filter?.ad ? " · (ad)" : ""
  }`;
  const own = shown.author_peer === `user:${account}`;
  const named = !own && (run.first || !!shown.group_filter?.ad);
  // Only a person has a profile: not a channel or an anonymous admin.
  const member =
    onMember && /^user:[1-9][0-9]*$/.test(shown.author_peer ?? "")
      ? () => onMember(shown)
      : undefined;
  const muted = outgoing ? palette.onAccent : palette.muted;
  const version = versions.shown;
  const current = version?.message ?? shown;
  const forward = forwardedLine(forwardOf(current, shown), timezone);
  const menu = () => onMenu(shown, version);
  // The sheet's Reply: none for a deleted message or a shown version.
  const reply = shown.deleted || version ? null : shown.message_id;
  return (
    <>
      {unread ? <UnreadLine palette={palette} /> : null}
      <Selectable message={shown} version={!!version} album={album}>
        <SwipeToReply
          enabled={!!swipeReply && reply !== null}
          onReply={() => reply !== null && swipeReply?.(reply)}
          color={palette.muted}
        >
          <View style={outgoing ? null : styles.memberRow}>
            {outgoing ? null : (
              <View style={styles.avatarSlot}>
                {run.last ? (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`Profile: ${label}`}
                    disabled={!member}
                    onPress={member}
                  >
                    <Avatar
                      account={account}
                      peer={shown.author_peer ?? peer}
                      photoId={shown.author_photo_id}
                      name={shown.author || label}
                      size={34}
                      tint={nameColor(shown.author_peer, scheme)}
                    />
                  </Pressable>
                ) : null}
              </View>
            )}
            <Pressable
              accessibilityHint="Long press for message actions"
              delayLongPress={350}
              onLongPress={menu}
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
                      {
                        borderColor: outgoing
                          ? palette.onAccent
                          : palette.accent,
                      },
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
              {named ? (
                <Text
                  numberOfLines={1}
                  accessibilityRole={member ? "button" : undefined}
                  onPress={member}
                  style={[
                    styles.author,
                    {
                      color: outgoing
                        ? palette.onAccent
                        : nameColor(shown.author_peer, scheme),
                    },
                  ]}
                >
                  {label}
                </Text>
              ) : null}
              <ForwardedLines
                lines={forward ? [forward] : []}
                palette={palette}
                inverse={outgoing}
              />
              {/* A deleted message keeps its quote (ADR 52 Q7). */}
              {isReply(shown) ? (
                <ReplyQuote
                  quote={replies.quote(shown)}
                  palette={palette}
                  inverse={outgoing}
                  onJump={onJump}
                />
              ) : null}
              <BubbleMenu.Provider value={menu}>
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
                    message={shown}
                    account={account}
                    peer={peer}
                    palette={palette}
                    inverse={outgoing}
                    version={version}
                  />
                )}
              </BubbleMenu.Provider>
              {!version ? (
                <ReactionChips
                  sources={[shown]}
                  palette={palette}
                  inverse={outgoing}
                />
              ) : null}
              {versions.error ? (
                <Text style={[styles.time, { color: muted }]}>
                  {versions.error}
                </Text>
              ) : null}
              <View style={styles.meta}>
                <VersionControls
                  message={shown}
                  versions={versions}
                  view={view}
                  color={muted}
                />
                <Text style={[styles.time, { color: muted }]}>
                  {messageTime(current.date, timezone, hourCycle)}
                </Text>
                {outgoing && !shown.deleted ? (
                  <Ionicons
                    accessibilityLabel="Sent"
                    name="checkmark"
                    size={14}
                    color={palette.onAccent}
                  />
                ) : null}
              </View>
              <DeletingMark message={shown} color={muted} />
            </Pressable>
          </View>
        </SwipeToReply>
      </Selectable>
    </>
  );
}

const styles = StyleSheet.create({
  band: {
    flexDirection: "row",
    alignItems: "center",
    minHeight: 44,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderBottomWidth: 2,
  },
  bandSide: { flex: 1, flexDirection: "row" },
  bandEnd: { justifyContent: "flex-end" },
  bandLabel: { fontSize: 16, fontWeight: "700", textAlign: "center" },
  slot: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    minHeight: 32,
    paddingHorizontal: 10,
    borderRadius: 16,
  },
  slotWord: { fontSize: 13, fontWeight: "600" },
  card: { borderRadius: 14, padding: 12, gap: 8 },
  topic: { gap: 4 },
  title: { fontSize: 16, fontWeight: "700" },
  body: { fontSize: 16, lineHeight: 22 },
  small: { fontSize: 13 },
  row: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  link: { alignSelf: "flex-start" },
  fold: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 6,
    borderWidth: 1,
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  foldLabel: { fontSize: 14, fontWeight: "600" },
  service: { textAlign: "center", fontSize: 13, marginVertical: 4 },
  bubble: { maxWidth: "88%", borderRadius: 14, padding: 10, gap: 4 },
  // The border takes the padding's place, so nothing moves.
  highlighted: { borderWidth: 2, padding: 8 },
  incoming: { alignSelf: "flex-start" },
  outgoing: { alignSelf: "flex-end" },
  // A run's later bubbles sit 2 apart instead of 8 (ADR 51 Q13).
  follow: { marginTop: -6 },
  // Another person's bubble beside its avatar column.
  memberRow: { flexDirection: "row", alignItems: "flex-end", gap: 6 },
  avatarSlot: { width: 34 },
  author: { fontSize: 13, fontWeight: "600" },
  time: { fontSize: 12 },
  meta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    alignSelf: "flex-end",
  },
});
