// Group Chats and Daily Briefings (ADR 34, ADR 42), shared by the web and the
// Enloq app: the application calendar's days and their bands, which originals
// each reading shows, what a day's briefing summarizes and what folds under
// its "N new messages" spoiler, the streamed topics and today's Briefing Slot.

export type GroupFilter = {
  ad: boolean;
  ignored_bot: boolean;
  allowed_bot?: boolean;
};

export type GroupOriginal = {
  message_id: number;
  date: number;
  deleted: boolean;
  sender_is_bot?: boolean | null;
  group_filter?: GroupFilter | null;
};

export type BriefingDay = {
  day: string;
  final: { id: string; covered_ids: number[] } | null;
  attempt: { id: string; state: string } | null;
  stream: { attempt: string; text: string } | null;
  analyzing: boolean;
  retry: object | null;
};

// The day a moment falls on in the application timezone, as YYYY-MM-DD.
export function dayKey(date: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  return ["year", "month", "day"]
    .map((name) => parts.find((part) => part.type === name)!.value)
    .join("-");
}

export function dayOf(message: { date: number }, timezone: string) {
  return dayKey(new Date(message.date * 1000), timezone);
}

// A date band names its day relative to today in the application timezone,
// then by weekday and date, with the year only outside the current one.
export function bandLabel(day: string, today: string) {
  const noon = (key: string, shift = 0) => {
    const [year = 1970, month = 1, value = 1] = key.split("-").map(Number);
    return new Date(Date.UTC(year, month - 1, value + shift, 12));
  };
  if (day === today) return "Today";
  if (day === noon(today, -1).toISOString().slice(0, 10)) return "Yesterday";
  return noon(day).toLocaleDateString([], {
    timeZone: "UTC",
    weekday: "long",
    month: "short",
    day: "numeric",
    year: day.slice(0, 4) === today.slice(0, 4) ? undefined : "numeric",
  });
}

// Hide Bots hides every original from a natively verified bot, with no model
// judgment, unless the owner exempted that bot in this group. Native provenance
// is required to hide any original. AI reading also never lists excluded bot
// content, whatever the preference. A deliberate citation or quote reveal
// affects its exact target only.
export function showGroupOriginal(
  message: GroupOriginal,
  hideBots: boolean,
  revealedSource?: number | null,
  aiReading = false,
) {
  if (message.message_id === revealedSource || message.sender_is_bot !== true)
    return true;
  const excluded = !!(
    message.deleted ||
    message.group_filter?.ignored_bot ||
    message.group_filter?.ad
  );
  if (aiReading && excluded) return false;
  if (!hideBots) return true;
  return message.group_filter?.allowed_bot === true && !excluded;
}

// The wording a running attempt has streamed so far, shown in place of the
// day's briefing until its final validates.
export function activePrefix(brief?: BriefingDay | null) {
  return brief?.attempt?.state === "active" &&
    brief.stream?.attempt === brief.attempt.id &&
    brief.stream.text.trim()
    ? brief.stream.text
    : null;
}

// A streamed prefix's topics: blank lines part them, and each one's first
// line is its title.
export function streamedTopics(prefix: string) {
  return prefix.split("\n\n").map((text) => {
    const [title = "", ...body] = text.split("\n");
    return { title, body: body.join("\n") };
  });
}

export type GroupDay<
  M extends GroupOriginal = GroupOriginal,
  B extends BriefingDay = BriefingDay,
> = {
  day: string;
  label: string;
  today: boolean;
  brief: B | undefined;
  prefix: string | null;
  // The day shows its originals: Originals reading, selection, or a
  // citation that revealed them in AI reading.
  showingOriginals: boolean;
  // The briefing has wording to show: a final, or a streamed prefix.
  hasWording: boolean;
  // Whether the day draws its band and section; otherwise its originals
  // are all hidden and it shows nothing.
  drawn: boolean;
  // Every original of the day, oldest first, and the ones this reading shows.
  originals: M[];
  visible: Set<number>;
  // Messages the briefing card stands for in this reading; their originals
  // stay out of the list. Empty while the day shows its originals.
  summarized: Set<number>;
  // After the card, the originals listed outside the spoiler, oldest first.
  listed: M[];
  // What the briefing does not cover yet, as AI reading lists it: the
  // spoiler's content and the slot's count.
  fresh: M[];
  // The "N new messages" spoiler is drawn, and whether it is open.
  spoiler: boolean;
  open: boolean;
  // The spoiler's messages while it is closed. Folded messages are not
  // counted as viewed (ADR 34).
  folded: number[];
};

// Each day of a group's loaded originals and briefings, oldest first, as one
// reading shows it (ADR 34; the web's DailyBriefings).
export function groupDays<M extends GroupOriginal, B extends BriefingDay>({
  messages,
  briefings,
  view,
  timezone,
  hideBots,
  today,
  originalDays = new Set(),
  opened = new Set(),
  cited = null,
  revealed = null,
  selecting = false,
}: {
  messages: M[];
  briefings: B[];
  view: "ai" | "originals";
  timezone: string;
  hideBots: boolean;
  // Today's key in the application timezone.
  today: string;
  // Days whose briefing a citation swapped for its originals.
  originalDays?: ReadonlySet<string>;
  // Days whose spoiler the owner opened.
  opened?: ReadonlySet<string>;
  // A highlighted citation's source, shown even when Hide Bots hides it.
  cited?: number | null;
  // A message a Reply Quote brought into view.
  revealed?: number | null;
  selecting?: boolean;
}): GroupDay<M, B>[] {
  const byDay = new Map<string, M[]>();
  for (const message of messages) {
    const day = dayOf(message, timezone);
    byDay.set(day, [...(byDay.get(day) || []), message]);
  }
  const days = Array.from(
    new Set([...byDay.keys(), ...briefings.map((value) => value.day)]),
  ).sort();
  return days.map((day) => {
    const brief = briefings.find((value) => value.day === day);
    const originals = byDay.get(day) || [];
    const prefix = activePrefix(brief);
    const showingOriginals =
      selecting || view === "originals" || originalDays.has(day);
    const visible = new Set(
      originals
        .filter((message) =>
          showingOriginals
            ? showGroupOriginal(message, hideBots, cited ?? revealed)
            : showGroupOriginal(message, hideBots, revealed, true),
        )
        .map((message) => message.message_id),
    );
    const hasWording = !!(brief?.final || prefix);
    const covered = new Set(hasWording ? brief?.final?.covered_ids || [] : []);
    const summarized = showingOriginals ? new Set<number>() : covered;
    const fresh = originals.filter(
      (message) =>
        !covered.has(message.message_id) &&
        showGroupOriginal(message, hideBots, null, true),
    );
    const spoiler = !showingOriginals && hasWording && fresh.length > 0;
    const open = spoiler && opened.has(day);
    const rest = originals.filter(
      (message) => !summarized.has(message.message_id),
    );
    return {
      day,
      label: bandLabel(day, today),
      today: day === today,
      brief,
      prefix,
      showingOriginals,
      hasWording,
      drawn: !!(
        visible.size ||
        brief?.final ||
        prefix ||
        (day === today && (brief?.analyzing || brief?.retry))
      ),
      originals,
      visible,
      summarized,
      listed: spoiler
        ? rest.filter((message) => !fresh.includes(message))
        : rest,
      fresh,
      spoiler,
      open,
      folded:
        spoiler && !open ? fresh.map((message) => message.message_id) : [],
    };
  });
}

// Where a day draws the Unread messages line: above its card when the card
// stands for the first unseen message, on its closed spoiler when that
// message is folded, or above the message itself.
export function unreadPlace(day: GroupDay, unread: number | null) {
  if (unread === null) return null;
  if (day.summarized.has(unread)) return "card" as const;
  if (day.folded.includes(unread)) return "fold" as const;
  if (day.visible.has(unread)) return "message" as const;
  return null;
}

// Ready content focused viewing may acknowledge: everything but the
// originals folded under a closed spoiler (ADR 34).
export function viewable<T extends { kind: string; identity: string }>(
  content: T[],
  folded: ReadonlySet<number>,
) {
  return folded.size
    ? content.filter(
        (item) =>
          item.kind !== "original" || !folded.has(Number(item.identity)),
      )
    : content;
}

export type SlotView = {
  enabled: boolean;
  status: string;
  reason: string | null;
  attempt?: string;
  transient?: boolean;
};

// A catch-up request in flight briefly blocks admission; the slot keeps its
// last settled state through it rather than flickering to Syncing.
export function transientSlot(view: SlotView | null | undefined) {
  return view?.status === "syncing" && view.transient === true;
}

export type BriefingSlot = {
  kind: "waiting" | "update" | "retry" | "budget" | "syncing" | "up_to_date";
  // The accessible name.
  label: string;
  // The word beside the icon; the check has none.
  word: string | null;
  // The chat route a tap posts to; null for a slot that only reports.
  path: string | null;
};

// Today's band slot (ADR 34): one icon and a word at most, with no
// tooltip. Update and Retry act on a tap; the others only report. Nothing
// shows while AI is Off or today has nothing eligible.
export function briefingSlot(
  view: SlotView | null | undefined,
  { working = false, count = 0 }: { working?: boolean; count?: number } = {},
): BriefingSlot | null {
  if (!view) return null;
  if (working || view.status === "active")
    return {
      kind: "waiting",
      label: "Briefing in progress",
      word: "Waiting",
      path: null,
    };
  if (view.status === "ready")
    return {
      kind: "update",
      label: count ? `Update briefing, ${count} new` : "Update briefing",
      word: count ? `${count} new` : "Update",
      path: "/briefings/update",
    };
  if (view.status === "retry_required" && view.enabled && view.attempt)
    return {
      kind: "retry",
      label: "Retry briefing",
      word: "Retry",
      path: "/briefings/" + encodeURIComponent(view.attempt) + "/retry",
    };
  if (view.status === "budget")
    return {
      kind: "budget",
      label: "Group Briefing Budget spent",
      word: "Budget",
      path: null,
    };
  if (view.status === "syncing")
    return {
      kind: "syncing",
      label: "Telegram messages syncing",
      word: "Syncing",
      path: null,
    };
  if (view.status === "up_to_date")
    return {
      kind: "up_to_date",
      label: "Briefing up to date",
      word: null,
      path: null,
    };
  return null;
}
