import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
import React from "react";
import { mockFiles, mockParams, mockStore, reset, serve } from "./setup";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { ConnectionProvider } from "../src/connection";
import type { Block, NativeMessage, Snapshot } from "../src/conversation";
import type { Operation } from "../src/drafts";
import { lookups, withDetails, type ReplyDetails } from "../src/replies";

const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
// Noon UTC today: the messages below share one day and stay recent.
const TODAY = new Date();
const NOW =
  Date.UTC(
    TODAY.getUTCFullYear(),
    TODAY.getUTCMonth(),
    TODAY.getUTCDate(),
    12,
  ) / 1000;

function message(id: number, text: string, extra = {}): NativeMessage {
  return {
    account_id: 1,
    peer: "user:101",
    message_id: id,
    revision: 1,
    date: NOW - 3600 + id * 60,
    text,
    outgoing: false,
    service: false,
    deleted: false,
    media: null,
    reply_to: null,
    reply_read: true,
    ...extra,
  } as unknown as NativeMessage;
}

function block(id: string, sources: number[], rendered: string): Block {
  return {
    id,
    revision: "r1",
    sources,
    rendered,
    rendered_revision: "r1",
    stream: null,
    state: "ready",
    stage: null,
    error: null,
  };
}

describe("which replies need a lookup", () => {
  const none = {
    group: false,
    targets: new Map<number, NativeMessage>(),
    missing: new Set<number>(),
    answered: new Set<number>(),
  };

  test("only replies whose message is neither loaded nor known", () => {
    const messages = [
      message(1, "Loaded target"),
      message(2, "To the loaded one", { reply_to: 1 }),
      message(3, "To an older one", { reply_to: 90 }),
      message(4, "To a looked-up one", { reply_to: 91 }),
      message(5, "To a missing one", { reply_to: 92 }),
      message(6, "", { reply_to: 93, deleted: true }),
      message(7, "Elsewhere", {
        reply_to: 94,
        reply_elsewhere: { peer: "channel:9" },
      }),
      message(8, "A story", { reply_story: true }),
    ];
    expect(
      lookups(messages, {
        ...none,
        targets: new Map([[91, message(91, "Known")]]),
        missing: new Set([92]),
      }),
    ).toEqual([3]);
  });

  test("a private reply captured before its details is reread once", () => {
    const legacy = [
      message(1, "Loaded target"),
      message(2, "Old capture", { reply_to: 1, reply_read: false }),
    ];
    expect(lookups(legacy, none)).toEqual([2]);
    expect(lookups(legacy, { ...none, answered: new Set([2]) })).toEqual([]);
    expect(lookups(legacy, { ...none, group: true })).toEqual([]);
  });

  test("learned details only fill in", () => {
    const learned: ReplyDetails = {
      message_id: 2,
      reply_to: 5,
      reply_quote: null,
      reply_elsewhere: {
        peer: "channel:9",
        sender_peer: null,
        sender: "Anna",
        date: null,
      },
      reply_story: false,
      reply_read: true,
    };
    const details = new Map([[2, learned]]);
    const legacy = message(2, "Old", { reply_to: 5, reply_read: false });
    expect(withDetails(legacy, details).reply_elsewhere?.sender).toBe("Anna");
    const current = message(2, "New", { reply_to: 5 });
    expect(withDetails(current, details)).toBe(current);
  });
});

describe("quotes in the conversation", () => {
  let served: Snapshot;
  let view = "originals";
  let answer: unknown;
  let history: NativeMessage[];
  let calls: ReturnType<typeof serve>;
  const asked = () =>
    calls
      .filter((call) => call.url.endsWith("/originals/replies"))
      .map((call) => JSON.parse(call.body!).identities);

  function snapshot(extra: Partial<Snapshot> = {}): Snapshot {
    return {
      timezone: "UTC",
      chat: {
        account_id: 1,
        peer: "user:101",
        title: "Anna",
        photo_id: null,
        kind: "private",
      },
      messages: [],
      blocks: [],
      unread_sources: [],
      ...extra,
    } as Snapshot;
  }

  beforeEach(() => {
    reset();
    answer = { replies: [], targets: [], missing: [], pending: [] };
    history = [];
    view = "originals";
    served = snapshot();
    mockStore.set("enloq.credential", SAVED);
    mockParams.account = "1";
    mockParams.peer = "user:101";
    calls = serve((url, init) => {
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
      if (url.endsWith("/preferences/reading-view"))
        return { status: 200, body: { reading_view: view } };
      if (url.endsWith("/chats/user%3A101"))
        return { status: 200, body: served };
      if (url.endsWith("/originals/replies") && init.method === "POST")
        return { status: 200, body: answer };
      if (url.includes("/history?before="))
        return { status: 200, body: history.splice(0) };
      if (url.endsWith("/notifications/attention"))
        return { status: 200, body: { events: [], sound: false } };
      if (url.includes("/ready-sources"))
        return { status: 200, body: { content: [], next_cursor: null } };
      if (url.endsWith("/viewed")) return { status: 200, body: { viewed: 0 } };
      throw new Error(`unexpected ${url}`);
    });
  });

  afterEach(() => jest.useRealTimers());

  function mount() {
    return render(
      <ConnectionProvider>
        <ConversationRoute />
      </ConnectionProvider>,
    );
  }

  test("every reply quotes its message, in both directions", async () => {
    served = snapshot({
      messages: [
        message(1, "Are you coming tonight?"),
        message(2, "Yes, at eight", { outgoing: true, reply_to: 1 }),
        message(3, "Great", { reply_to: 2 }),
        message(4, "", { reply_to: 1, deleted: true }),
      ],
    });
    await mount();
    expect(
      await screen.findAllByLabelText("Reply to Anna: Are you coming tonight?"),
    ).toBeTruthy();
    expect(screen.getByLabelText("Reply to You: Yes, at eight")).toBeTruthy();
    // A deleted reply keeps what it last showed, its quote too (ADR 52 Q7);
    // nothing needed a lookup.
    expect(
      screen.getAllByLabelText("Reply to Anna: Are you coming tonight?"),
    ).toHaveLength(2);
    expect(asked()).toEqual([]);
  });

  test("a merged AI bubble quotes only messages outside it", async () => {
    served = snapshot({
      messages: [
        message(1, "Dinner on Friday?", { outgoing: true }),
        message(2, "Hello"),
        message(3, "Friday works", { reply_to: 1 }),
        message(4, "And hello again", { reply_to: 2 }),
      ],
      blocks: [block("a", [2, 3, 4], "Anna says Friday works.")],
    });
    view = "ai";
    await mount();
    expect(await screen.findByText("Anna says Friday works.")).toBeTruthy();
    expect(
      screen.getByLabelText("Reply to You: Dinner on Friday?"),
    ).toBeTruthy();
    expect(screen.queryByLabelText(/Reply to Anna: Hello/)).toBeNull();
    expect(asked()).toEqual([]);
  });

  test("an older message is looked up, kept offline and jumped to", async () => {
    const older = message(1, "The plan from Monday");
    served = snapshot({
      messages: [message(5, "Agreed", { reply_to: 1 })],
    });
    answer = { replies: [], targets: [older], missing: [], pending: [] };
    await mount();
    const quote = await screen.findByLabelText(
      "Reply to Anna: The plan from Monday",
    );
    expect(asked()).toEqual([[5]]);
    const kept = [...mockFiles.keys()].find((name) =>
      name.includes(encodeURIComponent("replies:1/user:101")),
    );
    expect(JSON.parse(mockFiles.get(kept!)!).value.targets).toEqual([older]);
    // The jump loads earlier messages until the quoted one is shown, then
    // outlines it briefly.
    history = [older];
    fireEvent.press(quote);
    await waitFor(() =>
      expect(screen.getAllByText("The plan from Monday")).toHaveLength(2),
    );
    expect(calls.some((call) => call.url.includes("/history?before=5"))).toBe(
      true,
    );
    const shown = screen
      .getAllByHintText("Long press for message actions")
      .find(
        (bubble) =>
          within(bubble).queryByText("The plan from Monday") &&
          !within(bubble).queryByLabelText(/^Reply to/),
      );
    expect(shown).toHaveStyle({ borderColor: "#237052" });
    await waitFor(
      () => expect(shown).not.toHaveStyle({ borderColor: "#237052" }),
      { timeout: 3000 },
    );
  });

  test("resolved quotes read offline, without what is too old", async () => {
    served = snapshot({
      messages: [
        message(5, "Agreed", { reply_to: 1 }),
        message(6, "Still true", { reply_to: 2 }),
      ],
    });
    answer = {
      replies: [],
      targets: [
        message(1, "The plan from Monday"),
        message(2, "A plan from last year", { date: NOW - 40 * 86400 }),
      ],
      missing: [],
      pending: [],
    };
    const first = await mount();
    expect(
      await screen.findByLabelText(/^Reply to Anna.*: A plan from last year$/),
    ).toBeTruthy();
    await first.unmount();
    serve(() => Promise.reject(new TypeError("offline")));
    await mount();
    expect(
      await screen.findByLabelText("Reply to Anna: The plan from Monday"),
    ).toBeTruthy();
    expect(screen.getByLabelText("Reply to an earlier message")).toBeTruthy();
  });

  test("an unknown message, another chat and a story never jump", async () => {
    served = snapshot({
      messages: [
        message(5, "To an unknown one", { reply_to: 2 }),
        message(6, "To another chat", {
          reply_to: 9,
          reply_elsewhere: {
            peer: "channel:9",
            sender_peer: null,
            sender: "Sam",
            date: null,
          },
          reply_quote: "The fragment",
        }),
        message(7, "To a story", { reply_story: true }),
      ],
    });
    answer = { replies: [], targets: [], missing: [2], pending: [] };
    await mount();
    expect(
      await screen.findByLabelText("Reply to an earlier message"),
    ).toBeTruthy();
    expect(
      screen.getByLabelText("Reply to Sam · in another chat: The fragment"),
    ).toBeTruthy();
    expect(screen.getByLabelText("Reply to a story")).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: /^Reply to/ })).toEqual([]);
  });

  test("a pending lookup is retried after a minute", async () => {
    jest.useFakeTimers({ doNotFake: ["nextTick"] });
    served = snapshot({ messages: [message(5, "Agreed", { reply_to: 1 })] });
    answer = { replies: [], targets: [], missing: [], pending: [1] };
    await mount();
    await waitFor(() => expect(asked()).toEqual([[5]]));
    answer = {
      replies: [],
      targets: [message(1, "Found later")],
      missing: [],
      pending: [],
    };
    jest.advanceTimersByTime(61_000);
    expect(
      await screen.findByLabelText("Reply to Anna: Found later"),
    ).toBeTruthy();
    expect(asked()).toEqual([[5], [5]]);
  });

  test("a pending send quotes the message it replies to", async () => {
    const operation: Operation = {
      id: "op1",
      state: "queued",
      text: "On my way",
      images: [],
      reply_to: 1,
      created: NOW,
      native_ids: [],
      submitted: true,
      retryable: false,
      cancellable: true,
      checkable: false,
    };
    served = snapshot({
      messages: [message(1, "Where are you?")],
      operations: [operation],
    });
    await mount();
    expect(await screen.findByText("On my way")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Reply to Anna: Where are you?" }),
    ).toBeTruthy();
    expect(screen.queryByText("Reply to #1")).toBeNull();
  });
});
