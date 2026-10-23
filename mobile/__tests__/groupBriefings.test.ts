import {
  activePrefix,
  bandLabel,
  briefingSlot,
  dayKey,
  groupDays,
  showGroupOriginal,
  streamedTopics,
  transientSlot,
  unreadPlace,
  viewable,
  type BriefingDay,
  type GroupOriginal,
} from "../../shared/groupBriefings";

// 2026-10-16 09:00 in the application's UTC+7 timezone.
const TZ = "Asia/Bangkok";
const DAY = "2026-10-16";
const AT = Date.UTC(2026, 9, 16, 2) / 1000;

function original(id: number, extra: Partial<GroupOriginal> = {}) {
  return {
    message_id: id,
    date: AT + id * 60,
    deleted: false,
    sender_is_bot: false,
    ...extra,
  };
}

function briefing(extra: Partial<BriefingDay> = {}): BriefingDay {
  return {
    day: DAY,
    final: { id: "f".repeat(32), covered_ids: [1, 2] },
    attempt: null,
    stream: null,
    analyzing: false,
    retry: null,
    ...extra,
  };
}

const bot = (id: number, filter = {}) =>
  original(id, {
    sender_is_bot: true,
    group_filter: { ad: false, ignored_bot: false, ...filter },
  });

describe("days and bands", () => {
  it("keys a moment by the application calendar, not the device's", () => {
    // 20:00 UTC on the 15th is already the 16th in UTC+7.
    expect(dayKey(new Date(Date.UTC(2026, 9, 15, 20)), TZ)).toBe(DAY);
    expect(dayKey(new Date(Date.UTC(2026, 9, 15, 20)), "UTC")).toBe(
      "2026-10-15",
    );
  });

  it("names today, yesterday, then weekday and date", () => {
    expect(bandLabel(DAY, DAY)).toBe("Today");
    expect(bandLabel("2026-10-15", DAY)).toBe("Yesterday");
    // Across a month boundary too.
    expect(bandLabel("2026-09-30", "2026-10-01")).toBe("Yesterday");
    const earlier = bandLabel("2026-10-12", DAY);
    expect(earlier).toMatch(/Monday/);
    expect(earlier).not.toMatch(/2026/);
    expect(bandLabel("2025-12-30", DAY)).toMatch(/2025/);
  });
});

describe("Hide Bots", () => {
  it("hides a verified bot's posts unless it is exempt", () => {
    expect(showGroupOriginal(original(1), true)).toBe(true);
    expect(showGroupOriginal(bot(1), true)).toBe(false);
    expect(showGroupOriginal(bot(1), false)).toBe(true);
    expect(showGroupOriginal(bot(1, { allowed_bot: true }), true)).toBe(true);
    // Without native provenance nothing is hidden.
    expect(
      showGroupOriginal(original(1, { sender_is_bot: null }), true, null, true),
    ).toBe(true);
  });

  it("keeps excluded bot content out of AI reading whatever the preference", () => {
    for (const filter of [{ ad: true }, { ignored_bot: true }]) {
      expect(showGroupOriginal(bot(1, filter), false, null, true)).toBe(false);
      expect(showGroupOriginal(bot(1, filter), false)).toBe(true);
      expect(
        showGroupOriginal(bot(1, { ...filter, allowed_bot: true }), true),
      ).toBe(false);
    }
  });

  it("shows a deliberately revealed target only", () => {
    expect(showGroupOriginal(bot(1), true, 1)).toBe(true);
    expect(showGroupOriginal(bot(2), true, 1)).toBe(false);
  });
});

describe("streamed briefings", () => {
  it("shows the running attempt's prefix only", () => {
    const stream = { attempt: "a", text: "Plans\nFriday" };
    expect(
      activePrefix(briefing({ attempt: { id: "a", state: "active" }, stream })),
    ).toBe("Plans\nFriday");
    expect(
      activePrefix(briefing({ attempt: { id: "b", state: "active" }, stream })),
    ).toBeNull();
    expect(
      activePrefix(briefing({ attempt: { id: "a", state: "failed" }, stream })),
    ).toBeNull();
    expect(
      activePrefix(
        briefing({
          attempt: { id: "a", state: "active" },
          stream: { attempt: "a", text: "  " },
        }),
      ),
    ).toBeNull();
  });

  it("splits topics at blank lines with the first line as the title", () => {
    expect(streamedTopics("Plans\nFriday at ten\n\nAgenda")).toEqual([
      { title: "Plans", body: "Friday at ten" },
      { title: "Agenda", body: "" },
    ]);
  });
});

describe("a day's reading", () => {
  const messages = [original(1), original(2), original(3), original(4)];
  const base = {
    messages,
    briefings: [briefing()],
    view: "ai" as const,
    timezone: TZ,
    hideBots: true,
    today: DAY,
  };

  it("folds what the briefing does not cover under a closed spoiler", () => {
    const [day] = groupDays(base);
    expect(day!.label).toBe("Today");
    expect([...day!.summarized]).toEqual([1, 2]);
    expect(day!.listed).toEqual([]);
    expect(day!.fresh.map((m) => m.message_id)).toEqual([3, 4]);
    expect(day!.spoiler).toBe(true);
    expect(day!.open).toBe(false);
    expect(day!.folded).toEqual([3, 4]);
  });

  it("lists the spoiler's messages once it is opened", () => {
    const [day] = groupDays({ ...base, opened: new Set([DAY]) });
    expect(day!.open).toBe(true);
    expect(day!.folded).toEqual([]);
  });

  it("shows every original without a card or spoiler in Originals", () => {
    for (const day of [
      groupDays({ ...base, view: "originals" })[0]!,
      groupDays({ ...base, originalDays: new Set([DAY]) })[0]!,
    ]) {
      expect(day.showingOriginals).toBe(true);
      expect(day.summarized.size).toBe(0);
      expect(day.listed.map((m) => m.message_id)).toEqual([1, 2, 3, 4]);
      expect(day.spoiler).toBe(false);
      expect(day.folded).toEqual([]);
    }
  });

  it("lists a day without a briefing as its originals", () => {
    const [day] = groupDays({ ...base, briefings: [] });
    expect(day!.hasWording).toBe(false);
    expect(day!.spoiler).toBe(false);
    expect(day!.listed.map((m) => m.message_id)).toEqual([1, 2, 3, 4]);
  });

  it("leaves hidden bot posts out of the spoiler and its count", () => {
    const [day] = groupDays({
      ...base,
      messages: [...messages, bot(5), bot(6, { ad: true })],
    });
    expect(day!.fresh.map((m) => m.message_id)).toEqual([3, 4]);
    expect(day!.visible.has(5)).toBe(false);
    // A quote's reveal shows the post without counting it as new.
    const [revealed] = groupDays({
      ...base,
      messages: [...messages, bot(5)],
      revealed: 5,
    });
    expect(revealed!.visible.has(5)).toBe(true);
    expect(revealed!.fresh.map((m) => m.message_id)).toEqual([3, 4]);
  });

  it("draws a day only when it has something to show", () => {
    const yesterday = { ...bot(7), date: AT - 86_400 };
    const days = groupDays({
      ...base,
      messages: [yesterday, ...messages],
    });
    expect(days.map((day) => [day.day, day.drawn])).toEqual([
      ["2026-10-15", false],
      [DAY, true],
    ]);
    // Today's analyzing day shows before it has a message.
    const [running] = groupDays({
      ...base,
      messages: [],
      briefings: [briefing({ final: null, analyzing: true })],
    });
    expect(running!.drawn).toBe(true);
  });

  it("places the Unread line on the card, the closed spoiler or the message", () => {
    const [day] = groupDays(base);
    expect(unreadPlace(day!, 2)).toBe("card");
    expect(unreadPlace(day!, 3)).toBe("fold");
    expect(unreadPlace(day!, null)).toBeNull();
    const [open] = groupDays({ ...base, opened: new Set([DAY]) });
    expect(unreadPlace(open!, 3)).toBe("message");
  });
});

describe("focused viewing", () => {
  const ready = [
    { kind: "original", identity: "2", token: "t2" },
    { kind: "original", identity: "3", token: "t3" },
    { kind: "reaction", identity: "3", token: "r3" },
  ];

  it("leaves a closed fold's messages out of viewing until it opens", () => {
    const [closed] = groupDays({
      messages: [original(1), original(2), original(3)],
      briefings: [briefing()],
      view: "ai",
      timezone: TZ,
      hideBots: true,
      today: DAY,
    });
    expect(viewable(ready, new Set(closed!.folded))).toEqual([
      ready[0],
      ready[2],
    ]);
    expect(viewable(ready, new Set())).toEqual(ready);
  });
});

describe("today's Briefing Slot", () => {
  const view = (status: string, extra = {}) => ({
    enabled: true,
    status,
    reason: null,
    ...extra,
  });

  it("acts on Update and Retry and only reports the rest", () => {
    expect(briefingSlot(view("ready"), { count: 3 })).toEqual({
      kind: "update",
      label: "Update briefing, 3 new",
      word: "3 new",
      path: "/briefings/update",
    });
    expect(briefingSlot(view("ready"))?.word).toBe("Update");
    expect(
      briefingSlot(view("retry_required", { attempt: "a".repeat(32) }))?.path,
    ).toBe(`/briefings/${"a".repeat(32)}/retry`);
    // A Retry that may not run shows nothing to tap.
    expect(
      briefingSlot(
        view("retry_required", { enabled: false, attempt: "a".repeat(32) }),
      ),
    ).toBeNull();
    expect(briefingSlot(view("active"))?.word).toBe("Waiting");
    expect(briefingSlot(view("ready"), { working: true })?.kind).toBe(
      "waiting",
    );
    expect(briefingSlot(view("budget"))).toMatchObject({
      word: "Budget",
      path: null,
    });
    expect(briefingSlot(view("syncing"))?.word).toBe("Syncing");
    expect(briefingSlot(view("up_to_date"))).toMatchObject({
      label: "Briefing up to date",
      word: null,
    });
    expect(briefingSlot(view("off"))).toBeNull();
    expect(briefingSlot(undefined)).toBeNull();
  });

  it("treats a catch-up request's Syncing as transient", () => {
    expect(transientSlot(view("syncing", { transient: true }))).toBe(true);
    expect(transientSlot(view("syncing"))).toBe(false);
    expect(transientSlot(view("ready", { transient: true }))).toBe(false);
  });
});
