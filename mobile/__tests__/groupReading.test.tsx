import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
import React from "react";
import { mockEventSources, mockParams, mockStore, reset, serve } from "./setup";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { ConnectionProvider } from "../src/connection";
import type {
  DailyBriefing,
  NativeMessage,
  Snapshot,
} from "../src/conversation";

// ADR 42 AG3: a Group Chat in the app, over authored snapshots.
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
// Today's and yesterday's UTC days, whenever the suite runs.
const START = Math.floor(Date.now() / 86_400_000) * 86_400;
const TODAY = new Date(START * 1000).toISOString().slice(0, 10);
const YESTERDAY = new Date((START - 86_400) * 1000).toISOString().slice(0, 10);

function message(
  id: number,
  text: string,
  author: string,
  extra: Partial<NativeMessage> = {},
): NativeMessage {
  return {
    account_id: 1,
    peer: "chat:42",
    message_id: id,
    revision: 1,
    date: (id < 10 ? START - 3_600 : START + 60) + id * 60,
    text,
    outgoing: false,
    service: false,
    deleted: false,
    media: null,
    reply_to: null,
    author,
    author_peer: author ? `user:${id + 100}` : null,
    author_signature: null,
    sender_is_bot: false,
    ...extra,
  } as unknown as NativeMessage;
}

const MESSAGES = [
  message(1, "Yesterday's agenda is ready.", "Alice"),
  message(2, "My answer yesterday.", "Owner", {
    author_peer: "user:1",
    outgoing: true,
  }),
  message(3, "A signed post.", "Team channel", {
    author_peer: "channel:42",
    author_signature: "Moderator",
    outgoing: true,
  }),
  message(11, "The planning session is on Friday.", "Alice"),
  message(12, "I will bring the printed agenda.", "Bob"),
  message(13, "Please include the schedule.", "Carol"),
  message(14, "Bot reminder.", "Reminder bot", {
    sender_is_bot: true,
    group_filter: { ad: false, ignored_bot: false },
  }),
  message(15, "Count me in.", "Dave"),
];

function briefing(extra: Partial<DailyBriefing> = {}): DailyBriefing {
  return {
    day: TODAY,
    start: START,
    end: START + 86_400,
    source_ids: [11, 12, 13, 15],
    covered_ids: [11, 12],
    uncovered_ids: [13, 15],
    partial: false,
    gap: false,
    cached: false,
    omissions: [],
    intake_coverage: { native_omitted: 0, range_complete: true },
    final: {
      id: "f".repeat(32),
      text: "Alice set Friday; Bob brings the agenda.",
      outcome: "briefing",
      topics: [
        {
          title: "Planning",
          text: "Alice set Friday; Bob brings the agenda.",
          citations: [11, 12],
          participants: ["Alice", "Bob", "Carol", "Dave"].map((name) => ({
            peer: `user:${name}`,
            name,
          })),
        },
      ],
      covered_ids: [11, 12],
      covered_start: START + 720,
      covered_end: START + 780,
    },
    attempt: null,
    stream: null,
    analyzing: false,
    retry: null,
    ...extra,
  };
}

function snapshot(extra: Partial<Snapshot> = {}): Snapshot {
  return {
    timezone: "UTC",
    chat: {
      account_id: 1,
      peer: "chat:42",
      title: "Planning group",
      kind: "group",
      photo_id: null,
      read_outbox: 0,
    },
    messages: MESSAGES,
    blocks: [],
    briefings: [briefing()],
    briefing_update: null,
    group_processing: {
      processing_enabled: true,
      account_processing_enabled: true,
      effective: true,
      access: "available",
      supported: true,
      reason: "ready",
    },
    unread_sources: [],
    opening_source: null,
    operations: [],
    ...extra,
  } as unknown as Snapshot;
}

let view = "ai";
let tone = "neutral";
let hideBots = true;
let served: Snapshot;
let posted: { content: { identity: string }[] }[] = [];
// Briefing source reads by path, answered with a page or a refusal (AG4).
let pages: Record<string, { status: number; body: unknown }> = {};
const sourceReads: string[] = [];

beforeEach(() => {
  reset();
  view = "ai";
  tone = "neutral";
  hideBots = true;
  served = snapshot();
  posted = [];
  pages = {};
  sourceReads.length = 0;
  mockStore.set("enloq.credential", SAVED);
  mockParams.account = "1";
  mockParams.peer = "chat:42";
  serve((url, init) => {
    if (url.endsWith("/api/accounts"))
      return {
        status: 200,
        body: {
          mode: "personal",
          timezone: "UTC",
          budget: [],
          login_available: true,
          accounts: [],
        },
      };
    if (url.endsWith("/preferences/reading-view")) {
      if (init.method === "POST") view = JSON.parse(init.body!).reading_view;
      return { status: 200, body: { reading_view: view } };
    }
    if (url.endsWith("/preferences/hide-bots"))
      return { status: 200, body: { hide_bots: hideBots } };
    // The composer's remembered tone.
    if (url.endsWith("/api/preferences"))
      return { status: 200, body: { tone } };
    if (url.endsWith("/chats/chat%3A42")) return { status: 200, body: served };
    if (url.includes("/ready-sources"))
      return {
        status: 200,
        body: {
          content: [11, 12, 13, 15].map((id) => ({
            kind: "original",
            identity: String(id),
            token: String(id).repeat(32).slice(0, 64),
          })),
          next_cursor: null,
        },
      };
    if (url.endsWith("/viewed")) {
      posted.push(JSON.parse(init.body!));
      return { status: 200, body: { viewed: 1 } };
    }
    const read = /\/briefings\/[^/]+\/(sources|originals)\?.*$/.exec(url);
    if (read) {
      sourceReads.push(read[0]);
      return pages[read[0]] ?? { status: 400, body: { detail: "No page" } };
    }
    if (url.endsWith("/originals/replies"))
      return {
        status: 200,
        body: { replies: [], targets: [], missing: [], pending: [] },
      };
    throw new Error(`unexpected ${url}`);
  });
});

const mount = () =>
  render(
    <ConnectionProvider>
      <ConversationRoute />
    </ConnectionProvider>,
  );

test("days read under their bands, with the card and the folded rest", async () => {
  await mount();
  expect(await screen.findByText("Planning")).toBeTruthy();
  expect(screen.getAllByText("Today").length).toBeGreaterThan(0);
  expect(screen.getByText("Yesterday")).toBeTruthy();
  // Yesterday has no briefing: its originals, each with its author line.
  expect(screen.getByText("Yesterday's agenda is ready.")).toBeTruthy();
  // Alice's author line, and her name among the card's participants.
  expect(screen.getAllByText("Alice")).toHaveLength(2);
  // The owner's own messages carry no name, as in Telegram (ADR 51 Q13).
  expect(screen.queryByText("You")).toBeNull();
  expect(screen.getByText("Team channel · Moderator")).toBeTruthy();
  // Today's card stands for what it covers, with three participants.
  expect(
    screen.getByText("Alice set Friday; Bob brings the agenda."),
  ).toBeTruthy();
  expect(screen.getByLabelText("Source 11")).toBeTruthy();
  expect(screen.getByText("+1 more")).toBeTruthy();
  expect(screen.queryByText("Dave")).toBeNull();
  expect(screen.getByText(/^Covered /)).toBeTruthy();
  expect(screen.queryByText("The planning session is on Friday.")).toBeNull();
  // What it does not cover folds; the bot's post stays hidden.
  expect(screen.getByText("2 new messages")).toBeTruthy();
  expect(screen.queryByText("Please include the schedule.")).toBeNull();
  await fireEvent.press(screen.getByText("2 new messages"));
  expect(await screen.findByText("Please include the schedule.")).toBeTruthy();
  expect(screen.getByText("Count me in.")).toBeTruthy();
  expect(screen.queryByText("Bot reminder.")).toBeNull();
  await fireEvent.press(screen.getByText("+1 more"));
  // Dave's author line in the opened spoiler, and now among the participants.
  expect(screen.getAllByText("Dave")).toHaveLength(2);
});

test("Originals shows every original without a card or spoiler", async () => {
  view = "originals";
  await mount();
  expect(
    await screen.findByText("The planning session is on Friday."),
  ).toBeTruthy();
  expect(screen.getByText("Please include the schedule.")).toBeTruthy();
  expect(screen.queryByText("Planning")).toBeNull();
  expect(screen.queryByText(/new messages?$/)).toBeNull();
  expect(screen.queryByText("Bot reminder.")).toBeNull();
});

test("with Hide Bots Off a bot's post shows in Originals", async () => {
  view = "originals";
  hideBots = false;
  await mount();
  expect(await screen.findByText("Bot reminder.")).toBeTruthy();
  expect(screen.getByText("Reminder bot")).toBeTruthy();
});

test("a running attempt's streamed topics replace the card", async () => {
  served = snapshot({
    briefings: [
      briefing({
        attempt: { id: "a".repeat(32), state: "active", error: null },
        stream: { attempt: "a".repeat(32), text: "Plans\nFriday at ten" },
        analyzing: true,
      }),
    ],
  });
  await mount();
  expect(await screen.findByText("Plans")).toBeTruthy();
  expect(screen.getByText("Friday at ten")).toBeTruthy();
  expect(screen.queryByText("Planning")).toBeNull();
  expect(screen.queryByText(/^Covered /)).toBeNull();
});

test("local outcomes and incomplete coverage say so", async () => {
  const value = briefing({
    partial: true,
    omissions: [{ message_id: 15, reason: "capped" }],
    intake_coverage: { native_omitted: 0, range_complete: false },
  });
  served = snapshot({
    briefings: [
      { ...value, final: { ...value.final!, local_outcome: "ads_skipped" } },
    ],
  });
  await mount();
  expect(await screen.findByText("Ads skipped")).toBeTruthy();
  expect(screen.getByText(/· Partial coverage/)).toBeTruthy();
  expect(
    screen.getByText(
      "1 cached sources not covered. The complete day's originals have not been verified.",
    ),
  ).toBeTruthy();
});

test("the chat opens on the closed spoiler under the Unread line", async () => {
  served = snapshot({ unread_sources: [13, 15], opening_source: 13 });
  await mount();
  expect(await screen.findByText("Unread messages")).toBeTruthy();
  expect(screen.getByText("2 new messages")).toBeTruthy();
  await fireEvent.press(screen.getByText("2 new messages"));
  // Opened, the line stands above the message itself.
  expect(await screen.findByText("Please include the schedule.")).toBeTruthy();
  expect(screen.getAllByText("Unread messages")).toHaveLength(1);
});

test("focused viewing leaves the closed spoiler's messages out until it opens", async () => {
  await mount();
  await screen.findByText("2 new messages");
  await waitFor(() => expect(posted).toHaveLength(1), { timeout: 3000 });
  expect(posted[0]!.content.map((item) => item.identity)).toEqual(["11", "12"]);
  await fireEvent.press(screen.getByText("2 new messages"));
  await waitFor(() => expect(posted).toHaveLength(2), { timeout: 3000 });
  expect(posted[1]!.content.map((item) => item.identity)).toEqual([
    "11",
    "12",
    "13",
    "15",
  ]);
});

test("a quote opens its message's spoiler or shows a hidden bot's post", async () => {
  served = snapshot({
    messages: [
      ...MESSAGES.filter((m) => m.message_id < 10),
      message(16, "Agreed with the schedule.", "Erin", { reply_to: 2 }),
      message(17, "Thanks, bot.", "Frank", { reply_to: 14 }),
      { ...MESSAGES.find((m) => m.message_id === 14)!, date: START + 900 },
    ],
    briefings: [
      briefing({
        day: YESTERDAY,
        start: START - 86_400,
        end: START,
        covered_ids: [1],
        final: { ...briefing().final!, covered_ids: [1] },
      }),
    ],
  });
  await mount();
  expect(await screen.findByText("Agreed with the schedule.")).toBeTruthy();
  // Yesterday's own answer is folded with the signed post under its spoiler;
  // only the quote shows its wording.
  expect(screen.getByText("2 new messages")).toBeTruthy();
  expect(screen.queryByText("A signed post.")).toBeNull();
  await fireEvent.press(screen.getByLabelText(/^Reply to You.*My answer/));
  expect(await screen.findByText("A signed post.")).toBeTruthy();
  expect(screen.getAllByText("My answer yesterday.")).toHaveLength(2);
  // The bot's post shows only in the quote until the quote is tapped.
  expect(screen.getAllByText("Bot reminder.")).toHaveLength(1);
  await fireEvent.press(screen.getByLabelText(/^Reply to Reminder bot/));
  await waitFor(() =>
    expect(screen.getAllByText("Bot reminder.")).toHaveLength(2),
  );
});

test("the offline copy of an opened group stays readable", async () => {
  const first = await mount();
  expect(await screen.findByText("Planning")).toBeTruthy();
  await first.unmount();
  serve(() => Promise.reject(new TypeError("offline")));
  await mount();
  expect(await screen.findByText("Planning")).toBeTruthy();
  expect(
    await screen.findByText(
      "enloq.example.com can't be reached. Showing what was last loaded.",
    ),
  ).toBeTruthy();
  await act(() => new Promise((resolve) => setTimeout(resolve, 1500)));
});

test("an opening outside the loaded page reads its window, rechecked on each reread", async () => {
  const far = message(5, "An earlier unseen update.", "Gina");
  let current: { messages: NativeMessage[]; missing: number[] } = {
    messages: [far],
    missing: [],
  };
  const asked: number[][] = [];
  served = snapshot({
    messages: MESSAGES.filter((m) => m.message_id > 10),
    unread_sources: [5],
    opening_source: 5,
  });
  const base = global.fetch;
  global.fetch = jest.fn(async (url: string, init: RequestInit) => {
    if (url.includes("/window?around=5"))
      return { status: 200, ok: true, json: async () => [far] };
    if (url.endsWith("/originals/current")) {
      asked.push(JSON.parse(String(init.body)).identities);
      return { status: 200, ok: true, json: async () => current };
    }
    return base(url, init);
  }) as unknown as typeof fetch;
  await mount();
  expect(await screen.findByText("An earlier unseen update.")).toBeTruthy();
  expect(screen.getByText("Unread messages")).toBeTruthy();
  // The cache no longer holds it: the next reread drops it.
  current = { messages: [], missing: [5] };
  await act(async () =>
    mockEventSources[0]!.emit("change", {
      data: '{"chats":{"1":["1/chat:42"]}}',
    }),
  );
  await waitFor(() =>
    expect(screen.queryByText("An earlier unseen update.")).toBeNull(),
  );
  expect(asked.at(-1)).toEqual([5]);
});

describe("citations in place (AG4)", () => {
  const RESULT = "f".repeat(32);
  const page = (source: number, extra = {}) => ({
    status: 200,
    body: {
      day: TODAY,
      result: RESULT,
      source,
      messages: MESSAGES.filter((m) => m.message_id > 10),
      before: false,
      after: true,
      ...extra,
    },
  });
  // The message's own bubble, not one whose quote shows its wording.
  const bubble = (text: string) =>
    screen
      .getAllByHintText("Long press for message actions")
      .find(
        (value) =>
          within(value).queryByText(text) &&
          !within(value).queryByLabelText(/^Reply to/),
      )!;

  test("a citation shows its day's originals outlined, and Return brings the card back", async () => {
    pages[`/briefings/${TODAY}/sources?result=${RESULT}&source=12`] = page(12);
    await mount();
    await fireEvent.press(await screen.findByLabelText("Source 12"));
    expect(
      await screen.findByText("I will bring the printed agenda."),
    ).toBeTruthy();
    expect(screen.queryByText("Planning")).toBeNull();
    expect(screen.queryByText(/new messages?$/)).toBeNull();
    expect(
      screen.getByText("Showing cached originals near the cited source."),
    ).toBeTruthy();
    expect(bubble("I will bring the printed agenda.")).toHaveStyle({
      borderWidth: 2,
    });
    await fireEvent.press(screen.getByText("Return to briefing"));
    expect(await screen.findByText("Planning")).toBeTruthy();
    expect(screen.queryByText("I will bring the printed agenda.")).toBeNull();
    expect(screen.getByText("2 new messages")).toBeTruthy();
  });

  test("Show day originals reveals the day without an outline", async () => {
    pages[`/briefings/${TODAY}/originals?result=${RESULT}`] = page(11, {
      after: false,
    });
    await mount();
    await fireEvent.press(await screen.findByText("Show day originals"));
    expect(
      await screen.findByText("The planning session is on Friday."),
    ).toBeTruthy();
    expect(bubble("The planning session is on Friday.")).not.toHaveStyle({
      borderWidth: 2,
    });
    expect(
      screen.queryByText("Showing cached originals near the cited source."),
    ).toBeNull();
    expect(screen.getByText("Return to briefing")).toBeTruthy();
  });

  test("a refused or changed citation says so and keeps the briefing", async () => {
    pages[`/briefings/${TODAY}/sources?result=${RESULT}&source=11`] = {
      status: 400,
      body: {
        detail: "Requested original changed; return to the current briefing",
      },
    };
    pages[`/briefings/${TODAY}/sources?result=${RESULT}&source=12`] = page(12, {
      result: "e".repeat(32),
    });
    await mount();
    await fireEvent.press(await screen.findByLabelText("Source 11"));
    expect(
      await screen.findByText(
        "Requested original changed; return to the current briefing",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Planning")).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Source 12"));
    expect(await screen.findByText(CHANGED)).toBeTruthy();
    expect(screen.getByText("Planning")).toBeTruthy();
  });

  test("a revealed source that changes says so", async () => {
    pages[`/briefings/${TODAY}/sources?result=${RESULT}&source=12`] = page(12);
    await mount();
    await fireEvent.press(await screen.findByLabelText("Source 12"));
    await screen.findByText("Return to briefing");
    served = snapshot({
      messages: MESSAGES.map((m) =>
        m.message_id === 12 ? { ...m, revision: 2, text: "Edited." } : m,
      ),
    });
    await act(async () =>
      mockEventSources[0]!.emit("change", {
        data: '{"chats":{"1":["1/chat:42"]}}',
      }),
    );
    expect(await screen.findByText(CHANGED)).toBeTruthy();
    expect(screen.getByText("Edited.")).toBeTruthy();
  });

  test("a quote to a summarized message reveals its day", async () => {
    pages[`/briefings/${TODAY}/sources?result=${RESULT}&source=11`] = page(11);
    served = snapshot({
      messages: [
        ...MESSAGES,
        message(16, "On Friday then.", "Erin", { reply_to: 11 }),
      ],
      briefings: [
        briefing({
          final: { ...briefing().final!, covered_ids: [11, 12] },
          uncovered_ids: [13, 15, 16],
        }),
      ],
    });
    await mount();
    await fireEvent.press(await screen.findByText("3 new messages"));
    await fireEvent.press(
      await screen.findByLabelText(/^Reply to Alice.*planning session/),
    );
    await waitFor(() =>
      expect(
        screen.getAllByText("The planning session is on Friday."),
      ).toHaveLength(2),
    );
    expect(sourceReads).toEqual([
      `/briefings/${TODAY}/sources?result=${RESULT}&source=11`,
    ]);
    expect(bubble("The planning session is on Friday.")).toHaveStyle({
      borderWidth: 2,
    });
  });

  // ADR 59 Q1: an answer's source link opens the group over the Ask screen.
  describe("from an Ask this group answer", () => {
    // The cache around a source outside the loaded page, and the reads of it.
    let windows: Record<number, NativeMessage[]> = {};
    const around: number[] = [];
    beforeEach(() => {
      windows = {};
      around.length = 0;
      const base = global.fetch;
      global.fetch = jest.fn(async (url: string, init: RequestInit) => {
        const read = /\/window\?around=(\d+)$/.exec(url);
        if (!read) return base(url, init);
        around.push(Number(read[1]));
        return {
          status: 200,
          ok: true,
          json: async () => windows[Number(read[1])] ?? [],
        };
      }) as unknown as typeof fetch;
    });

    test("a source outside the loaded page is read with its window and opens outlined", async () => {
      windows[5] = [message(5, "An earlier decision.", "Gina")];
      // The source replaces the opening at the earliest unseen message.
      served = snapshot({
        messages: MESSAGES.filter((m) => m.message_id > 10),
        unread_sources: [3, 13],
        opening_source: 3,
      });
      mockParams.source = "5";
      await mount();
      expect(await screen.findByText("An earlier decision.")).toBeTruthy();
      expect(around).toEqual([5]);
      await waitFor(() =>
        expect(bubble("An earlier decision.")).toHaveStyle({ borderWidth: 2 }),
      );
      expect(screen.getByLabelText("Back to answer")).toBeTruthy();
      expect(screen.queryByLabelText("Back to conversations")).toBeNull();
    });

    test("a summarized source reveals its day as a citation does", async () => {
      pages[`/briefings/${TODAY}/sources?result=${RESULT}&source=12`] =
        page(12);
      mockParams.source = "12";
      await mount();
      expect(
        await screen.findByText("I will bring the printed agenda."),
      ).toBeTruthy();
      expect(screen.getByText("Return to briefing")).toBeTruthy();
      expect(sourceReads).toEqual([
        `/briefings/${TODAY}/sources?result=${RESULT}&source=12`,
      ]);
      expect(bubble("I will bring the printed agenda.")).toHaveStyle({
        borderWidth: 2,
      });
    });

    test("a source the cache no longer holds says so", async () => {
      mockParams.source = "7";
      await mount();
      expect(await screen.findByText(CHANGED)).toBeTruthy();
      expect(screen.getByText("Planning")).toBeTruthy();
    });
  });
});

const CHANGED = "The cited source changed or is no longer available locally.";
