import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
import React from "react";
import {
  mockEventSources,
  mockParams,
  mockRouter,
  mockStore,
  reset,
  serve,
} from "./setup";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { ConnectionProvider } from "../src/connection";
import type {
  BriefingUpdate,
  DailyBriefing,
  NativeMessage,
  Snapshot,
} from "../src/conversation";

// ADR 42 AG5: today's Briefing Slot and the group's ⋮ sheet.
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
const START = Math.floor(Date.now() / 86_400_000) * 86_400;
const TODAY = new Date(START * 1000).toISOString().slice(0, 10);
const ATTEMPT = "a".repeat(32);

function message(id: number, text: string, extra = {}): NativeMessage {
  return {
    account_id: 1,
    peer: "chat:42",
    message_id: id,
    revision: 1,
    date: START + 60 + id * 60,
    text,
    outgoing: false,
    service: false,
    deleted: false,
    media: null,
    reply_to: null,
    author: `Person ${id}`,
    author_peer: `user:${id + 100}`,
    author_signature: null,
    sender_is_bot: false,
    ...extra,
  } as unknown as NativeMessage;
}

const BOT = message(4, "Bot reminder.", {
  author: "Reminder bot",
  author_peer: "user:50",
  sender_is_bot: true,
  group_filter: { ad: false, ignored_bot: false },
});
const MESSAGES = [
  message(1, "The planning session is on Friday."),
  message(2, "I will bring the agenda."),
  message(3, "Please include the schedule."),
  BOT,
];

function briefing(): DailyBriefing {
  return {
    day: TODAY,
    start: START,
    end: START + 86_400,
    source_ids: [1, 2, 3],
    covered_ids: [1, 2],
    uncovered_ids: [3],
    partial: false,
    gap: false,
    cached: false,
    omissions: [],
    intake_coverage: { native_omitted: 0, range_complete: true },
    final: {
      id: "f".repeat(32),
      text: "Friday, with an agenda.",
      outcome: "briefing",
      topics: [
        { title: "Planning", text: "Friday, with an agenda.", citations: [1] },
      ],
      covered_ids: [1, 2],
      covered_start: START + 120,
      covered_end: START + 180,
    },
    attempt: null,
    stream: null,
    analyzing: false,
    retry: null,
  };
}

const PROCESSING = {
  processing_enabled: true,
  account_processing_enabled: true,
  effective: true,
  access: "available" as "unknown" | "available" | "lost",
  supported: true,
  reason: "ready",
};

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
    briefing_update: {
      enabled: true,
      status: "ready",
      reason: null,
      day: TODAY,
    },
    group_processing: PROCESSING,
    unread_sources: [],
    opening_source: null,
    operations: [],
    ...extra,
  } as unknown as Snapshot;
}

let view = "ai";
let tone = "neutral";
let hideBots = true;
let accountAI = true;
let online = true;
let served: Snapshot;
// POSTs by path after the chat's own prefix, and the answers to give.
let posts: { path: string; body: unknown }[] = [];
type Answer = { status: number; body: unknown };
let answers: Record<string, Answer | ((body: never) => Answer)> = {};

beforeEach(() => {
  reset();
  view = "ai";
  tone = "neutral";
  hideBots = true;
  accountAI = true;
  online = true;
  served = snapshot();
  posts = [];
  answers = {};
  mockStore.set("enloq.credential", SAVED);
  mockParams.account = "1";
  mockParams.peer = "chat:42";
  serve((url, init) => {
    if (!online) return Promise.reject(new TypeError("offline"));
    if (url.endsWith("/api/accounts"))
      return {
        status: 200,
        body: {
          mode: "personal",
          timezone: "UTC",
          budget: [],
          login_available: true,
          accounts: [
            {
              account_id: 1,
              name: "Owner",
              username: "",
              phone: "",
              state: "authorized",
              connected: true,
              status: "",
              ai_enabled: accountAI,
              photo_id: null,
            },
          ],
        },
      };
    if (url.endsWith("/preferences/reading-view"))
      return { status: 200, body: { reading_view: view } };
    if (url.endsWith("/preferences/hide-bots")) {
      if (init.method === "POST") {
        posts.push({ path: "hide-bots", body: JSON.parse(init.body!) });
        const refused = answers["hide-bots"] as Answer | undefined;
        if (refused) return refused;
        hideBots = JSON.parse(init.body!).hide_bots;
      }
      return { status: 200, body: { hide_bots: hideBots } };
    }
    // The composer's remembered tone.
    if (url.endsWith("/api/preferences")) {
      if (init.method === "POST") {
        posts.push({ path: "tone", body: JSON.parse(init.body!) });
        tone = JSON.parse(init.body!).tone;
      }
      return { status: 200, body: { tone } };
    }
    if (url.endsWith("/chats/chat%3A42")) return { status: 200, body: served };
    if (url.includes("/ready-sources"))
      return { status: 200, body: { content: [], next_cursor: null } };
    if (url.endsWith("/originals/replies"))
      return {
        status: 200,
        body: { replies: [], targets: [], missing: [], pending: [] },
      };
    // Forward headers the originals lack (ADR 46 CU2): none to fill in.
    if (url.endsWith("/originals/forwarding")) return { status: 200, body: [] };
    const chat = "/api/accounts/1/chats/chat%3A42";
    const operations = "/api/accounts/1/operations";
    const prefix = url.includes(chat) ? chat : operations;
    if (init.method === "POST" && url.includes(prefix)) {
      const path = url.slice(url.indexOf(prefix) + prefix.length);
      const body = JSON.parse(init.body!);
      posts.push({ path, body });
      const answer = answers[path];
      return typeof answer === "function"
        ? answer(body as never)
        : (answer ?? { status: 200, body: {} });
    }
    throw new Error(`unexpected ${url}`);
  });
});

const mount = () =>
  render(
    <ConnectionProvider>
      <ConversationRoute />
    </ConnectionProvider>,
  );

const slotView = (status: string, extra: Partial<BriefingUpdate> = {}) => ({
  enabled: true,
  status,
  reason: null,
  day: TODAY,
  ...extra,
});

describe("today's Briefing Slot", () => {
  test.each([
    ["ready", {}, "Update briefing, 1 new", "1 new"],
    ["retry_required", { attempt: ATTEMPT }, "Retry briefing", "Retry"],
    ["active", {}, "Briefing in progress", "Waiting"],
    ["budget", {}, "Group Briefing Budget spent", "Budget"],
    ["syncing", {}, "Telegram messages syncing", "Syncing"],
    ["up_to_date", {}, "Briefing up to date", null],
  ] as const)(
    "%s reads as its icon and word",
    async (status, extra, label, word) => {
      served = snapshot({ briefing_update: slotView(status, extra) });
      await mount();
      expect((await screen.findAllByLabelText(label)).length).toBeGreaterThan(
        0,
      );
      if (word) expect(screen.getAllByText(word).length).toBeGreaterThan(0);
    },
  );

  test("a spent budget opens the Group Briefing Budget in Settings", async () => {
    served = snapshot({ briefing_update: slotView("budget") });
    await mount();
    const [budget] = await screen.findAllByRole("link", {
      name: "Group Briefing Budget spent",
    });
    await fireEvent.press(budget!);
    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: "/settings/budget",
      params: { section: "group" },
    });
    expect(posts).toEqual([]);
  });

  test("nothing shows while AI is off or in Originals", async () => {
    served = snapshot({
      briefing_update: slotView("unavailable", { enabled: false }),
    });
    const first = await mount();
    await screen.findByText("Planning");
    expect(screen.queryByLabelText(/briefing/i)).toBeTruthy();
    expect(screen.queryByLabelText(/^Update briefing/)).toBeNull();
    expect(screen.queryByLabelText("Briefing up to date")).toBeNull();
    await first.unmount();
    view = "originals";
    served = snapshot();
    await mount();
    await screen.findByText("The planning session is on Friday.");
    expect(screen.queryByLabelText(/^Update briefing/)).toBeNull();
  });

  test("Update and Retry post their route and reread, one run at a time", async () => {
    await mount();
    const update = (
      await screen.findAllByLabelText("Update briefing, 1 new")
    )[0]!;
    served = snapshot({ briefing_update: slotView("active") });
    await fireEvent.press(update);
    await waitFor(() =>
      expect(posts).toEqual([{ path: "/briefings/update", body: {} }]),
    );
    expect(
      (await screen.findAllByLabelText("Briefing in progress")).length,
    ).toBeGreaterThan(0);
    // A refused Retry only rereads: the slot shows why.
    served = snapshot({
      briefing_update: slotView("retry_required", { attempt: ATTEMPT }),
    });
    await act(async () =>
      mockEventSources[0]!.emit("change", {
        data: '{"chats":{"1":["1/chat:42"]}}',
      }),
    );
    answers[`/briefings/${ATTEMPT}/retry`] = {
      status: 400,
      body: { detail: "Briefing budget is spent" },
    };
    const retry = (await screen.findAllByLabelText("Retry briefing"))[0]!;
    served = snapshot({ briefing_update: slotView("budget") });
    await fireEvent.press(retry);
    await waitFor(() =>
      expect(posts.at(-1)).toEqual({
        path: `/briefings/${ATTEMPT}/retry`,
        body: {},
      }),
    );
    expect(
      (await screen.findAllByLabelText("Group Briefing Budget spent")).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("Briefing budget is spent")).toBeNull();
  });

  test("a catch-up's transient Syncing keeps the settled slot", async () => {
    await mount();
    await screen.findAllByLabelText("Update briefing, 1 new");
    // The reread carries an edit, so its arrival shows.
    served = snapshot({
      briefing_update: slotView("syncing", { transient: true }),
      messages: MESSAGES.map((m) =>
        m.message_id === 3 ? { ...m, revision: 2, text: "Edited." } : m,
      ),
    });
    await act(async () =>
      mockEventSources[0]!.emit("change", {
        data: '{"chats":{"1":["1/chat:42"]}}',
      }),
    );
    await fireEvent.press(screen.getByText("1 new message"));
    expect(await screen.findByText("Edited.")).toBeTruthy();
    expect(
      screen.getAllByLabelText("Update briefing, 1 new").length,
    ).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Telegram messages syncing")).toBeNull();
    // A lasting Syncing shows.
    served = snapshot({ briefing_update: slotView("syncing") });
    await act(async () =>
      mockEventSources[0]!.emit("change", {
        data: '{"chats":{"1":["1/chat:42"]}}',
      }),
    );
    expect(
      (await screen.findAllByLabelText("Telegram messages syncing")).length,
    ).toBeGreaterThan(0);
  });

  test("nothing can be tapped while the server can't be reached", async () => {
    const first = await mount();
    await screen.findAllByLabelText("Update briefing, 1 new");
    await first.unmount();
    online = false;
    await mount();
    const update = (
      await screen.findAllByLabelText("Update briefing, 1 new")
    )[0]!;
    await screen.findByText(
      "enloq.example.com can't be reached. Showing what was last loaded.",
    );
    expect(update.props.accessibilityState).toMatchObject({ disabled: true });
    await fireEvent.press(update);
    expect(posts).toEqual([]);
  });
});

describe("the group's header", () => {
  test("its photo and name open the Group Profile, and it copies no link", async () => {
    await mount();
    await fireEvent.press(
      await screen.findByLabelText("Planning group, open profile"),
    );
    const { mockRouter } = require("./setup");
    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: "/profile/[account]/[peer]",
      params: { account: "1", peer: "chat:42" },
    });
    // Copy chat link lives in the Group Profile (ADR 47 Q8).
    expect(screen.queryByLabelText("Copy chat link")).toBeNull();
    expect(screen.getByLabelText("Conversation actions")).toBeTruthy();
  });
});

describe("the group's actions sheet", () => {
  test("Group AI processing switches with its notes and the AI off tag", async () => {
    await mount();
    await screen.findByText("Planning");
    expect(screen.queryByText("AI off")).toBeNull();
    await fireEvent.press(screen.getByLabelText("Conversation actions"));
    const row = screen.getByRole("switch", { name: "Group AI processing" });
    expect(row.props.accessibilityState).toMatchObject({ checked: true });
    served = snapshot({
      group_processing: { ...PROCESSING, processing_enabled: false },
    });
    await fireEvent.press(row);
    await waitFor(() =>
      expect(posts).toEqual([
        { path: "/processing", body: { enabled: false } },
      ]),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("switch", { name: "Group AI processing" }).props
          .accessibilityState,
      ).toMatchObject({ checked: false }),
    );
    expect(screen.getByText("AI off")).toBeTruthy();
  });

  test.each([
    [{ supported: false }, "This group is unavailable for AI processing."],
    [
      { access: "lost" as const },
      "Group access was lost. Cached originals remain available.",
    ],
    [{ access: "unknown" as const }, "Group access has not been verified."],
  ])("notes why it can't brief: %o", async (change, note) => {
    served = snapshot({ group_processing: { ...PROCESSING, ...change } });
    await mount();
    await screen.findByText("Planning group");
    await fireEvent.press(screen.getByLabelText("Conversation actions"));
    expect(screen.getByText(note)).toBeTruthy();
  });

  test("account AI Off shows its note and the AI off tag", async () => {
    accountAI = false;
    await mount();
    expect(await screen.findByText("AI off")).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Conversation actions"));
    expect(
      screen.getByText("AI processing is Off for this account."),
    ).toBeTruthy();
  });

  test("a refused switch says why and keeps its state", async () => {
    answers["/processing"] = {
      status: 400,
      body: { detail: "Group access changed; try again" },
    };
    answers["hide-bots"] = {
      status: 400,
      body: { detail: "Hide bots must be On or Off" },
    };
    await mount();
    await screen.findByText("Planning");
    await fireEvent.press(screen.getByLabelText("Conversation actions"));
    await fireEvent.press(
      screen.getByRole("switch", { name: "Group AI processing" }),
    );
    expect(
      await screen.findByText("Group access changed; try again"),
    ).toBeTruthy();
    await fireEvent.press(screen.getByRole("switch", { name: "Hide bots" }));
    expect(await screen.findByText("Hide bots must be On or Off")).toBeTruthy();
    expect(
      screen.getByRole("switch", { name: "Hide bots" }).props
        .accessibilityState,
    ).toMatchObject({ checked: true });
  });

  test("Hide bots Off shows a bot's posts, which can be exempted", async () => {
    view = "originals";
    await mount();
    await screen.findByText("The planning session is on Friday.");
    expect(screen.queryByText("Bot reminder.")).toBeNull();
    await fireEvent.press(screen.getByLabelText("Conversation actions"));
    await fireEvent.press(screen.getByRole("switch", { name: "Hide bots" }));
    expect(posts).toContainEqual({
      path: "hide-bots",
      body: { hide_bots: false },
    });
    expect(await screen.findByText("Bot reminder.")).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Close"));
    await fireEvent(screen.getByText("Bot reminder."), "longPress");
    await fireEvent.press(await screen.findByText("Stop ignoring this bot"));
    await waitFor(() =>
      expect(posts.at(-1)).toEqual({
        path: "/bots/user%3A50/restore",
        body: {},
      }),
    );
  });

  test("an exempt bot or a person offers no exemption", async () => {
    view = "originals";
    hideBots = false;
    served = snapshot({
      messages: [
        MESSAGES[0]!,
        {
          ...BOT,
          group_filter: { ad: false, ignored_bot: false, allowed_bot: true },
        },
      ],
    });
    await mount();
    await fireEvent(await screen.findByText("Bot reminder."), "longPress");
    expect(await screen.findByText("Copy")).toBeTruthy();
    expect(screen.queryByText("Stop ignoring this bot")).toBeNull();
    await fireEvent.press(screen.getByLabelText("Close"));
    await fireEvent(
      screen.getByText("The planning session is on Friday."),
      "longPress",
    );
    expect(await screen.findByText("Copy")).toBeTruthy();
    expect(screen.queryByText("Stop ignoring this bot")).toBeNull();
  });
});

describe("writing in a group (AG6)", () => {
  const DRAFT = {
    chat_key: "1/chat:42",
    revision: 1,
    text: "",
    attachments: [],
    selected_media: null,
    reply_to: null,
    undo: null,
    state: "idle",
  };
  const operation = (extra = {}) => ({
    id: "o".repeat(32),
    state: "queued",
    text: "Hello group",
    images: [],
    reply_to: null,
    created: START + 600,
    native_ids: [],
    submitted: true,
    retryable: false,
    cancellable: false,
    checkable: false,
    ...extra,
  });

  beforeEach(() => {
    served = snapshot({ draft: DRAFT } as Partial<Snapshot>);
    answers["/draft"] = (body: { revision: number; text: string }) => ({
      status: 200,
      body: { ...DRAFT, ...body, revision: body.revision + 1 },
    });
  });

  test("a group has Send and no suggestion generation or rewrite controls", async () => {
    await mount();
    expect(await screen.findByLabelText("Send")).toBeTruthy();
    expect(screen.queryByLabelText("Suggestions")).toBeNull();
    expect(screen.queryByLabelText("Rewrite")).toBeNull();
    expect(screen.queryByLabelText("Rewrite tone")).toBeNull();
    expect(posts.some((post) => post.path === "tone")).toBe(false);
  });

  test("without verified group access nothing is sent or rewritten", async () => {
    view = "originals";
    served = snapshot({
      draft: { ...DRAFT, text: "Hello group" },
      group_processing: { ...PROCESSING, access: "lost" },
    } as Partial<Snapshot>);
    await mount();
    expect(
      await screen.findByText("Native group access is unavailable"),
    ).toBeTruthy();
    for (const name of ["Send", "Attach"])
      expect(
        screen.getByLabelText(name).props.accessibilityState,
      ).toMatchObject({ disabled: true });
    await fireEvent(
      screen.getByText("Please include the schedule."),
      "longPress",
    );
    expect(
      screen.getByRole("menuitem", { name: "Reply" }).props.accessibilityState,
    ).toMatchObject({ disabled: true });
  });

  test("a reply in a group names the participant", async () => {
    view = "originals";
    await mount();
    await fireEvent(
      await screen.findByText("Please include the schedule."),
      "longPress",
    );
    await fireEvent.press(screen.getByRole("menuitem", { name: "Reply" }));
    expect(await screen.findByText("Reply to Person 3")).toBeTruthy();
    await waitFor(() =>
      expect(posts).toContainEqual({
        path: "/draft",
        body: { revision: 1, text: "", reply_to: 3 },
      }),
    );
  });

  test("a send opens today's new messages", async () => {
    answers["/send"] = {
      status: 200,
      body: {
        operation: operation(),
        draft: { ...DRAFT, revision: 3 },
      },
    };
    await mount();
    expect(await screen.findByText("1 new message")).toBeTruthy();
    expect(screen.queryByText("Please include the schedule.")).toBeNull();
    await fireEvent.changeText(screen.getByLabelText("Message"), "Hello group");
    await waitFor(() =>
      expect(posts.some((post) => post.path === "/draft")).toBe(true),
    );
    served = snapshot({
      draft: { ...DRAFT, revision: 3 },
      messages: [
        ...MESSAGES,
        message(5, "Hello group", {
          outgoing: true,
          author: "Owner",
          author_peer: "user:1",
        }),
      ],
    } as Partial<Snapshot>);
    await fireEvent.press(screen.getByLabelText("Send"));
    await waitFor(() =>
      expect(posts.some((post) => post.path === "/send")).toBe(true),
    );
    expect(
      await screen.findByText("Please include the schedule."),
    ).toBeTruthy();
    expect(screen.getByText("2 new messages")).toBeTruthy();
    // The sent message shows, without a name (ADR 51 Q13).
    expect(screen.getByText("Hello group")).toBeTruthy();
  });

  test("a send paused by lost access resumes after its review", async () => {
    const paused = operation({
      state: "refused",
      cause: "group_access_loss",
      reason: "Group access was lost before this send.",
      resumable: true,
    });
    served = snapshot({
      draft: DRAFT,
      operations: [paused],
    } as Partial<Snapshot>);
    const id = "o".repeat(32);
    answers[`/${id}/review-resume`] = {
      status: 200,
      body: {
        token: "f".repeat(64),
        expires: Date.now() / 1000 + 120,
        account_id: 1,
        peer: "chat:42",
        title: "Planning group",
        operation: paused,
      },
    };
    answers[`/${id}/resume`] = {
      status: 200,
      body: { ...paused, state: "queued", resumable: false },
    };
    await mount();
    const card = await screen.findByLabelText("Not sent message");
    expect(within(card).getByText("Not sent")).toBeTruthy();
    expect(within(card).queryByLabelText("Retry")).toBeNull();
    // Its actions are in its sheet, opened from its "!" (ADR 51 Q4).
    const review = async () => {
      await fireEvent.press(
        within(card).getByLabelText("Not sent: message actions"),
      );
      await fireEvent.press(
        screen.getByRole("menuitem", { name: "Review and resume" }),
      );
    };
    expect(screen.queryByRole("menuitem", { name: "Retry" })).toBeNull();
    await review();
    expect(await screen.findByText("Resume paused send")).toBeTruthy();
    expect(screen.getByText("Owner · Planning group")).toBeTruthy();
    expect(screen.getByText(/^Resume within \d:\d\d$/)).toBeTruthy();
    await fireEvent.press(screen.getByText("Cancel review"));
    expect(screen.queryByText("Resume paused send")).toBeNull();
    expect(posts.filter((post) => post.path.endsWith("/resume"))).toEqual([]);
    await review();
    await fireEvent.press(await screen.findByText("Resume send"));
    await waitFor(() =>
      expect(posts.filter((post) => post.path.endsWith("/resume"))).toEqual([
        { path: `/${id}/resume`, body: { token: "f".repeat(64) } },
      ]),
    );
  });

  test("a retained group shows its cached history and saved draft read-only", async () => {
    served = snapshot({
      retained: true,
      draft: { ...DRAFT, text: "A kept draft" },
      group_processing: { ...PROCESSING, access: "lost" },
    } as Partial<Snapshot>);
    await mount();
    expect(
      await screen.findByText("Saved draft · Retained group work"),
    ).toBeTruthy();
    expect(screen.getByText("A kept draft")).toBeTruthy();
    expect(screen.queryByLabelText("Message")).toBeNull();
    expect(screen.queryByLabelText("Send")).toBeNull();
    await fireEvent.press(screen.getByLabelText("Conversation actions"));
    expect(
      screen.queryByRole("switch", { name: "Group AI processing" }),
    ).toBeNull();
    expect(screen.getByRole("switch", { name: "Hide bots" })).toBeTruthy();
  });
});
