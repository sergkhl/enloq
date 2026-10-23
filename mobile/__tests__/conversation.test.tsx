import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { Linking } from "react-native";
import {
  mockClipboard,
  mockEventSources,
  mockParams,
  mockRouter,
  mockStore,
  reset,
  serve,
} from "./setup";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { ConnectionProvider } from "../src/connection";
import {
  mergeMessages,
  readingItems,
  unreadAnchor,
  type Block,
  type NativeMessage,
  type Snapshot,
} from "../src/conversation";

function message(id: number, text: string, extra = {}): NativeMessage {
  return {
    account_id: 1,
    peer: "user:101",
    message_id: id,
    revision: 1,
    date: 1_700_000_000 + id * 60,
    text,
    outgoing: false,
    service: false,
    deleted: false,
    media: null,
    ...extra,
  } as unknown as NativeMessage;
}

function block(id: string, sources: number[], extra = {}): Block {
  return {
    id,
    revision: "r1",
    sources,
    rendered: null,
    rendered_revision: null,
    stream: null,
    state: "ready",
    stage: null,
    error: null,
    ...extra,
  };
}

function snapshot(extra: Partial<Snapshot> = {}): Snapshot {
  return {
    timezone: "UTC",
    chat: { account_id: 1, peer: "user:101", title: "Anna", photo_id: null },
    messages: [
      message(1, "Hello from Anna"),
      message(2, "Are you coming tonight?"),
      message(3, "Yes, at eight", { outgoing: true }),
      message(4, "Great, see you"),
    ],
    blocks: [
      block("a", [1, 2], {
        rendered: "Anna greets you and asks about tonight.",
        rendered_revision: "r1",
      }),
      block("b", [4], {
        stream: { attempt: "x", text: "Anna is glad", revision: "r1" },
      }),
    ],
    unread_sources: [4],
    ...extra,
  } as Snapshot;
}

describe("reading items", () => {
  test("AI reading shows each group once at its first loaded source", () => {
    const items = readingItems(snapshot(), "ai");
    expect(items.map((item) => item.key)).toEqual(["ba", "m3", "bb"]);
    const [first, , last] = items;
    expect(first).toMatchObject({
      text: "Anna greets you and asks about tonight.",
      streaming: false,
    });
    expect(last).toMatchObject({ text: "Anna is glad", streaming: true });
  });

  test("a group without a current rewrite shows no AI text", () => {
    const items = readingItems(
      snapshot({
        blocks: [
          block("a", [1, 2], { rendered: "old", rendered_revision: "r0" }),
          block("b", [4], {
            stream: { attempt: "x", text: "t", revision: "r0" },
          }),
        ],
      }),
      "ai",
    );
    expect(items[0]).toMatchObject({ kind: "group", text: null });
    expect(items[2]).toMatchObject({ kind: "group", text: null });
  });

  test("Originals reading shows every message", () => {
    expect(readingItems(snapshot(), "originals").map((i) => i.key)).toEqual([
      "m1",
      "m2",
      "m3",
      "m4",
    ]);
  });

  test("the unread line sits above the first loaded unseen source", () => {
    const ai = readingItems(snapshot(), "ai");
    expect(unreadAnchor(ai, [2, 4])).toBe("ba");
    const originals = readingItems(snapshot(), "originals");
    expect(unreadAnchor(originals, [2, 4])).toBe("m2");
    expect(unreadAnchor(originals, [99])).toBeNull();
    const deleted = snapshot({
      messages: [message(2, "", { deleted: true }), message(4, "x")],
    });
    expect(unreadAnchor(readingItems(deleted, "originals"), [2, 4])).toBe("m4");
  });

  test("merging keeps the higher revision in order", () => {
    const merged = mergeMessages(
      [message(5, "five"), message(2, "two", { revision: 2 })],
      [message(2, "stale"), message(1, "one")],
    );
    expect(merged.map((m) => [m.message_id, m.text])).toEqual([
      [1, "one"],
      [2, "two"],
      [5, "five"],
    ]);
  });
});

const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});

describe("the conversation screen", () => {
  let view = "ai";
  // A reading-view read held until the test releases it.
  let held: ((view: string) => void) | null = null;
  let holding = false;
  let posted: unknown[] = [];
  let attention: unknown[] = [];
  let served: Snapshot;
  let calls: ReturnType<typeof serve>;

  beforeEach(() => {
    reset();
    view = "ai";
    posted = [];
    attention = [];
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
      if (url.endsWith("/preferences/reading-view")) {
        if (init.method === "POST") view = JSON.parse(init.body!).reading_view;
        else if (holding) {
          holding = false;
          return new Promise((resolve) => {
            held = (stale) =>
              resolve({ status: 200, body: { reading_view: stale } });
          }) as never;
        }
        return { status: 200, body: { reading_view: view } };
      }
      if (url.endsWith("/chats/user%3A101"))
        return { status: 200, body: served };
      if (url.endsWith("/originals/2/transcript/status"))
        return {
          status: 200,
          body: {
            state: "done",
            transcript: { text: "Authored voice transcript", language: "en" },
            reason: null,
          },
        };
      if (url.endsWith("/originals/2/transcript"))
        return {
          status: 200,
          body: { text: "Authored voice transcript", language: "en" },
        };
      if (url.endsWith("/notifications/attention")) {
        attention.push(JSON.parse(init.body!));
        return { status: 200, body: { events: [], sound: false } };
      }
      if (url.includes("/ready-sources"))
        return {
          status: 200,
          body: {
            content: [
              { kind: "original", identity: "4", token: "a".repeat(64) },
              { kind: "rewrite", identity: "b", token: "b".repeat(64) },
            ],
            next_cursor: null,
          },
        };
      if (url.endsWith("/viewed")) {
        posted.push(JSON.parse(init.body!));
        return { status: 200, body: { viewed: 1 } };
      }
      if (url.includes("/history?before=1"))
        return { status: 200, body: [message(0, "An earlier hello")] };
      throw new Error(`unexpected ${url}`);
    });
  });

  function mount() {
    return render(
      <ConnectionProvider>
        <ConversationRoute />
      </ConnectionProvider>,
    );
  }

  test("reads AI groups, marks unread and acknowledges after a focused second", async () => {
    await mount();
    expect(
      await screen.findByText("Anna greets you and asks about tonight."),
    ).toBeTruthy();
    expect(screen.getByText("Streaming · incomplete")).toBeTruthy();
    expect(screen.getByText("Unread messages")).toBeTruthy();
    expect(screen.getByText("Yes, at eight")).toBeTruthy();
    await waitFor(() => expect(posted).toHaveLength(1), { timeout: 3000 });
    expect(posted[0]).toEqual({
      content: [{ kind: "original", identity: "4", token: "a".repeat(64) }],
    });
  });

  test("Originals reading is the global choice", async () => {
    await mount();
    await screen.findByText("Anna greets you and asks about tonight.");
    // The header's robot, pressed for AI (ADR 52 Q13).
    const toggle = screen.getByRole("togglebutton", { name: "AI view" });
    expect(toggle.props.accessibilityState).toEqual({ checked: true });
    await fireEvent.press(toggle);
    expect(await screen.findByText("Hello from Anna")).toBeTruthy();
    expect(view).toBe("originals");
    expect(
      calls.some(
        (call) =>
          call.method === "POST" &&
          call.url.endsWith("/preferences/reading-view"),
      ),
    ).toBe(true);
  });

  test("a reading view chosen while the saved one loads is kept", async () => {
    holding = true;
    await mount();
    await screen.findByText("Anna greets you and asks about tonight.");
    await fireEvent.press(
      screen.getByRole("togglebutton", { name: "AI view" }),
    );
    expect(await screen.findByText("Hello from Anna")).toBeTruthy();
    // The read that started first answers with the old choice afterwards.
    await act(async () => held?.("ai"));
    expect(
      screen.getByRole("togglebutton", { name: "AI view" }).props
        .accessibilityState,
    ).toEqual({ checked: false });
    expect(view).toBe("originals");
  });

  test("the header keeps the link for the profile, and earlier messages load", async () => {
    await mount();
    await screen.findByText("Anna greets you and asks about tonight.");
    // Copy chat link lives in the profile (ADR 47 Q8).
    expect(screen.queryByLabelText("Copy chat link")).toBeNull();
    expect(mockClipboard).toEqual([]);
    await fireEvent.press(screen.getByText("Load earlier messages"));
    expect(await screen.findByText("An earlier hello")).toBeTruthy();
  });

  test("AI reading keeps each source's photo and voice note", async () => {
    served = snapshot({
      messages: [
        message(1, "Please check the green sample.", { media: "image" }),
        message(2, "", { media: "audio" }),
      ],
      blocks: [
        block("a", [1, 2], {
          rendered: "Anna asks you to check the sample.",
          rendered_revision: "r1",
        }),
      ],
      unread_sources: [],
    });
    await mount();
    expect(
      await screen.findByText("Anna asks you to check the sample."),
    ).toBeTruthy();
    expect(screen.getByLabelText("Photo, opens full screen")).toBeTruthy();
    expect(screen.getByLabelText(/^Play voice message/)).toBeTruthy();
    // The rewrite replaces the source text; only the attachments repeat.
    expect(screen.queryByText("Please check the green sample.")).toBeNull();
    await fireEvent.press(screen.getByText("Show transcript"));
    expect(await screen.findByText("Authored voice transcript")).toBeTruthy();
  });

  test("a link in a message opens in the browser", async () => {
    const open = jest
      .spyOn(Linking, "openURL")
      .mockResolvedValue(undefined as never);
    served = snapshot({
      messages: [message(1, "The plan is at https://example.com/plan.")],
      blocks: [],
    });
    await mount();
    await fireEvent.press(
      await screen.findByRole("link", { name: "https://example.com/plan" }),
    );
    expect(open).toHaveBeenCalledWith("https://example.com/plan");
    open.mockRestore();
  });

  test("waiting for budget and the format pause open Settings", async () => {
    served = snapshot({
      blocks: [
        block("a", [1, 2], { state: "queued", stage: "budget" }),
        block("b", [4], { state: "queued", stage: "format_paused" }),
      ],
    });
    await mount();
    await fireEvent.press(await screen.findByText("Waiting for budget"));
    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: "/settings/budget",
      params: { section: "daily" },
    });
    await fireEvent.press(
      screen.getByText("Paused after repeated format failures"),
    );
    expect(mockRouter.push).toHaveBeenCalledWith("/settings/guidance");
    expect(
      screen.getByRole("link", { name: "Waiting for budget" }),
    ).toBeTruthy();
  });

  test("a chat opened before reads offline, without acknowledging", async () => {
    const first = await mount();
    expect(
      await screen.findByText("Anna greets you and asks about tonight."),
    ).toBeTruthy();
    await waitFor(() => expect(posted).toHaveLength(1), { timeout: 3000 });
    await first.unmount();
    serve(() => Promise.reject(new TypeError("offline")));
    await mount();
    expect(
      await screen.findByText("Anna greets you and asks about tonight."),
    ).toBeTruthy();
    expect(
      await screen.findByText(
        "enloq.example.com can't be reached. Showing what was last loaded.",
      ),
    ).toBeTruthy();
    await act(() => new Promise((resolve) => setTimeout(resolve, 1500)));
    expect(posted).toHaveLength(1);
  });

  test("an open chat reports attention, so pushes for it stay quiet", async () => {
    const shown = await mount();
    await waitFor(() => expect(attention.length).toBeGreaterThan(0));
    expect(attention[0]).toEqual({
      tab: expect.stringMatching(/^[a-f0-9]{32}$/),
      account_id: 1,
      peer: "user:101",
      focused: true,
      audio: false,
    });
    await shown.unmount();
    await waitFor(() =>
      expect(attention.at(-1)).toMatchObject({ focused: false }),
    );
  });

  test("a change to this chat rereads it", async () => {
    await mount();
    await screen.findByText("Anna greets you and asks about tonight.");
    const reads = () =>
      calls.filter((call) => call.url.endsWith("/chats/user%3A101")).length;
    const before = reads();
    await act(async () =>
      mockEventSources[0]!.emit("change", {
        data: '{"chats":{"1":["1/user:101"]}}',
      }),
    );
    await waitFor(() => expect(reads()).toBe(before + 1));
    await act(async () =>
      mockEventSources[0]!.emit("change", {
        data: '{"chats":{"1":["1/user:999"]}}',
      }),
    );
    expect(reads()).toBe(before + 1);
  });
});
