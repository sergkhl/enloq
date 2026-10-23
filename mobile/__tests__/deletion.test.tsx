import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
// The module doubles first, so these imports get them.
import { mockParams, mockStore, reset, serve } from "./setup";
import React from "react";
import { BackHandler } from "react-native";
import {
  deletable,
  deletionDetails,
  deletionStatus,
  intentIdentity,
  matchesReview,
  ordinary,
  shownOperations,
  type DeletionOperation,
} from "../../shared/deletion";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { readCached, writeCached } from "../src/cache";
import { ConnectionProvider } from "../src/connection";
import type { NativeMessage, Snapshot } from "../src/conversation";

// ADR 46 CU4: Message Selection and deletion in the app.

const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
const NOW = Date.now() / 1000;

function message(id: number, extra = {}): NativeMessage {
  return {
    account_id: 1,
    peer: "user:101",
    message_id: id,
    revision: 1,
    date: NOW - 600 + id,
    text: `Message ${id}`,
    outgoing: false,
    service: false,
    deleted: false,
    media: null,
    forwarded: false,
    forward_read: true,
    ...extra,
  } as unknown as NativeMessage;
}

function operation(
  id: string,
  state: DeletionOperation["state"],
  targets: number[],
  extra: Partial<DeletionOperation> = {},
): DeletionOperation {
  return {
    id,
    account_id: 1,
    peer: "user:101",
    state,
    created: NOW,
    targets: targets.map((message_id) => ({
      message_id,
      revision: 1,
      preview: "",
      media: null,
      outgoing: false,
      date: NOW,
      outcome: state === "completed" ? "acknowledged" : "unstarted",
      observation: null,
    })),
    counts: {
      total: targets.length,
      acknowledged: state === "completed" ? targets.length : 0,
      failed: 0,
      uncertain: 0,
      unstarted: state === "completed" ? 0 : targets.length,
      dispatching: 0,
      absent: 0,
      resolved: 0,
    },
    checkable: !["queued", "deleting"].includes(state),
    retryable: false,
    retry_targets: [],
    ...extra,
  };
}

describe("the shared rules", () => {
  test("which originals a deletion may take", () => {
    expect(ordinary(message(1), NOW)).toBe(true);
    expect(ordinary(message(1, { service: true }), NOW)).toBe(false);
    expect(ordinary(message(1, { deleted: true }), NOW)).toBe(false);
    // The service deletes nothing older than 30 days.
    expect(ordinary(message(1, { date: NOW - 31 * 86400 }), NOW)).toBe(false);
    const own = message(1, {
      author_peer: "user:1",
      deletion_fingerprint: "a".repeat(64),
    });
    expect(deletable(own, 1, true, NOW)).toBe(true);
    // A group takes only the owner's verified originals.
    expect(
      deletable({ ...own, deletion_fingerprint: null }, 1, true, NOW),
    ).toBe(false);
    expect(deletable(message(1, { outgoing: true }), 1, true, NOW)).toBe(false);
    expect(intentIdentity()).toMatch(/^[0-9a-f]{32}$/);
  });

  test("cards say how a deletion went", () => {
    expect(deletionStatus(operation("a", "deleting", [1, 2]))).toBe(
      "Deleting · 0 of 2 confirmed",
    );
    expect(deletionStatus(operation("a", "completed", [1]))).toBe(
      "Deleted · 1 message",
    );
    const stopped = operation("b", "failed", [1, 2], {
      reason: "Authored native refusal",
    });
    expect(deletionStatus(stopped)).toBe("Deletion stopped");
    expect(deletionDetails(stopped, false)).toEqual([
      "0 confirmed · 0 unconfirmed · 0 failed · 2 not started",
      "Authored native refusal",
    ]);
    // Every unfinished one, and only the newest completed one.
    const older = { ...operation("c", "completed", [3]), created: NOW - 60 };
    expect(
      shownOperations([older, operation("d", "completed", [4]), stopped]).map(
        (value) => value.id,
      ),
    ).toEqual(["d", "b"]);
    expect(
      matchesReview(operation("e", "queued", [1]), "e", 1, "user:101", [
        message(1),
      ]),
    ).toBe(true);
    expect(
      matchesReview(operation("e", "queued", [1]), "e", 1, "user:101", [
        message(1, { revision: 2 }),
      ]),
    ).toBe(false);
  });
});

describe("selection and deletion in a private chat", () => {
  let served: Snapshot;
  let posts: { url: string; body: unknown }[];
  let answer: (body: { id: string }) => { status: number; body: unknown };
  let view: string;
  // The hardware Back listeners the screen holds.
  let back: Parameters<typeof BackHandler.addEventListener>[1][];

  beforeEach(() => {
    back = [];
    jest
      .spyOn(BackHandler, "addEventListener")
      .mockImplementation((_, handler) => {
        back.push(handler);
        return {
          remove: () => {
            back = back.filter((value) => value !== handler);
          },
        };
      });
    reset();
    posts = [];
    view = "originals";
    mockStore.set("enloq.credential", SAVED);
    mockParams.account = "1";
    mockParams.peer = "user:101";
    served = {
      timezone: "UTC",
      chat: { account_id: 1, peer: "user:101", title: "Anna", photo_id: null },
      messages: [message(1), message(2), message(3, { outgoing: true })],
      blocks: [],
      unread_sources: [],
      deletions: [],
    } as unknown as Snapshot;
    answer = (body) => ({
      status: 200,
      body: operation(body.id, "queued", [1, 3]),
    });
    serve((url, init) => {
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
                username: "owner",
                phone: "",
                state: "authorized",
                connected: true,
                status: "Connected",
                ai_enabled: true,
                photo_id: null,
              },
            ],
          },
        };
      if (url.endsWith("/preferences/reading-view")) {
        if (init.method === "POST") view = JSON.parse(init.body!).reading_view;
        return { status: 200, body: { reading_view: view } };
      }
      if (url.endsWith("/chats/user%3A101"))
        return { status: 200, body: served };
      if (url.endsWith("/originals/current")) {
        const { identities } = JSON.parse(init.body!);
        return {
          status: 200,
          body: {
            messages: served.messages.filter((value) =>
              identities.includes(value.message_id),
            ),
            missing: [],
          },
        };
      }
      if (url.endsWith("/deletions") && init.method === "POST") {
        const body = JSON.parse(init.body!);
        posts.push({ url, body });
        const result = answer(body);
        if (result.status === 200)
          served = {
            ...served,
            deletions: [result.body as DeletionOperation],
          } as Snapshot;
        return result;
      }
      if (/\/deletions\/[0-9a-f]{32}$/.test(url))
        return {
          status: 400,
          body: { detail: "Deletion operation unavailable for this account" },
        };
      if (url.endsWith("/hidden") && init.method === "POST") {
        const body = JSON.parse(init.body!);
        posts.push({ url, body });
        served = {
          ...served,
          messages: served.messages.filter(
            (value) => !body.identities.includes(value.message_id),
          ),
        } as Snapshot;
        return { status: 200, body: { hidden: body.identities } };
      }
      if (url.includes("/ready-sources"))
        return { status: 200, body: { content: [], next_cursor: null } };
      if (url.endsWith("/notifications/attention"))
        return { status: 200, body: { events: [], sound: false } };
      throw new Error(`unexpected ${url}`);
    });
  });

  const mount = () =>
    render(
      <ConnectionProvider>
        <ConversationRoute />
      </ConnectionProvider>,
    );

  // ADR 52: a deleted message keeps what it last said, with a trash, until
  // the owner hides it from its sheet or a selection of deleted messages.
  test("a deleted message shows its last words and hides from its sheet", async () => {
    served = {
      ...served,
      messages: [
        message(1),
        message(2, { deleted: true, text: "Last words", deleted_version: 9 }),
      ],
    } as Snapshot;
    await mount();
    expect(await screen.findByText("Last words")).toBeTruthy();
    expect(screen.getByLabelText("Deleted in Telegram")).toBeTruthy();
    await fireEvent(screen.getByText("Last words"), "longPress");
    expect(
      (await screen.findByRole("menuitem", { name: "Reply" })).props
        .accessibilityState,
    ).toEqual({ disabled: true });
    await fireEvent.press(screen.getByRole("menuitem", { name: "Hide" }));
    await waitFor(() => expect(screen.queryByText("Last words")).toBeNull());
    expect(posts.at(-1)).toEqual({
      url: expect.stringMatching(/\/chats\/user%3A101\/hidden$/),
      body: { identities: [2] },
    });
  });

  test("a selection started on a deleted message hides, never deletes", async () => {
    served = {
      ...served,
      messages: [
        message(1),
        message(2, { deleted: true, text: "Gone one" }),
        message(3, { deleted: true, text: "Gone two" }),
      ],
    } as Snapshot;
    await mount();
    await fireEvent(await screen.findByText("Gone one"), "longPress");
    await fireEvent.press(
      await screen.findByRole("menuitem", { name: "Select" }),
    );
    // A current original can't join a selection of deleted ones.
    expect(
      screen.queryByRole("checkbox", { name: "Select: Message 1" }),
    ).toBeNull();
    await fireEvent.press(
      screen.getByRole("checkbox", { name: "Select: Gone two" }),
    );
    expect(screen.getByText("2 selected")).toBeTruthy();
    expect(screen.queryByLabelText("Delete selected messages")).toBeNull();
    await fireEvent.press(screen.getByLabelText("Hide selected messages"));
    await waitFor(() => expect(screen.queryByText("Gone two")).toBeNull());
    expect(posts.at(-1)?.body).toEqual({ identities: [2, 3] });
    expect(screen.queryByText("2 selected")).toBeNull();
  });

  async function selectFirst() {
    await fireEvent(await screen.findByText("Message 1"), "longPress");
    await fireEvent.press(
      await screen.findByRole("menuitem", { name: "Select" }),
    );
    expect(await screen.findByText("1 selected")).toBeTruthy();
  }

  test("originals are selected, reviewed and deleted for both participants", async () => {
    await mount();
    await selectFirst();
    // Taps add and remove; the composer steps aside for the bar.
    await fireEvent.press(screen.getByLabelText("Select: Message 3"));
    expect(screen.getByText("2 selected")).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Select: Message 3"));
    expect(screen.getByText("1 selected")).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Select: Message 3"));
    await fireEvent.press(screen.getByLabelText("Delete selected messages"));
    expect(await screen.findByText("Delete messages")).toBeTruthy();
    expect(screen.getByText("Owner")).toBeTruthy();
    expect(screen.getByText("2 messages")).toBeTruthy();
    await fireEvent.press(screen.getByText("Delete for both participants"));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{32}$/),
      targets: [
        { message_id: 1, revision: 1 },
        { message_id: 3, revision: 1 },
      ],
    });
    // The accepted deletion ends the selection; its card shows progress.
    expect(await screen.findByText("Deleting · 0 of 2 confirmed")).toBeTruthy();
    expect(screen.queryByText("1 selected")).toBeNull();
    expect(screen.getAllByText("Deleting…")).toHaveLength(2);
  });

  test("an unanswered submission stays unconfirmed, survives a restart and is checked", async () => {
    answer = () => {
      throw new TypeError("Network request failed");
    };
    const first = await mount();
    await selectFirst();
    await fireEvent.press(screen.getByLabelText("Delete selected messages"));
    await fireEvent.press(
      await screen.findByText("Delete for both participants"),
    );
    // The sheet keeps the same intent, to send again or check, and the
    // card under the latest message says the same.
    expect(await screen.findAllByText("Submission unconfirmed")).toHaveLength(
      2,
    );
    expect(screen.getAllByText("Check submission")).toHaveLength(2);
    expect(screen.getByText("Review submission")).toBeTruthy();
    const stored = await readCached<string[]>("deletion:1/user:101");
    expect(stored).toEqual([(posts[0]!.body as { id: string }).id]);
    await first.unmount();
    // After a restart only the identity is kept, which can be checked.
    await mount();
    expect(await screen.findByText("Submission unconfirmed")).toBeTruthy();
    expect(screen.queryByText("Review submission")).toBeNull();
    await fireEvent.press(screen.getByText("Check submission"));
    expect(
      await screen.findByText(
        "Submission not found. Its deletion result is unconfirmed.",
      ),
    ).toBeTruthy();
  });

  test("a refusal leaves nothing pending, and Back ends the selection", async () => {
    answer = () => ({
      status: 400,
      body: { detail: "Message deletion is unavailable" },
    });
    await mount();
    await selectFirst();
    await fireEvent.press(screen.getByLabelText("Delete selected messages"));
    await fireEvent.press(
      await screen.findByText("Delete for both participants"),
    );
    expect(
      await screen.findByText("Message deletion is unavailable"),
    ).toBeTruthy();
    expect(screen.queryByText("Submission unconfirmed")).toBeNull();
    expect(await readCached("deletion:1/user:101")).toBeNull();
    await fireEvent.press(screen.getByText("Cancel"));
    expect(screen.getByText("1 selected")).toBeTruthy();
    // Android's Back ends the selection before it would leave the chat.
    expect(back.at(-1)?.({} as never)).toBe(true);
    await act(async () => undefined);
    expect(screen.queryByText("1 selected")).toBeNull();
    expect(back).toHaveLength(0);
  });

  test("the AI view offers no Select, and a finished card shows once", async () => {
    served.deletions = [operation("f".repeat(32), "completed", [2])];
    writeCached("deletion:1/user:101", []);
    await mount();
    expect(await screen.findByText("Deleted · 1 message")).toBeTruthy();
    await fireEvent.press(
      screen.getByRole("togglebutton", { name: "AI view" }),
    );
    await fireEvent(await screen.findByText("Message 1"), "longPress");
    expect(await screen.findByRole("menuitem", { name: "Copy" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Select" })).toBeNull();
  });
});

describe("selection and deletion in a group", () => {
  const own = message(2, {
    peer: "chat:42",
    text: "My own group message.",
    outgoing: true,
    author: "Owner",
    author_peer: "user:1",
    deletion_fingerprint: "b".repeat(64),
  });
  const theirs = message(1, {
    peer: "chat:42",
    text: "Their group message.",
    author: "Alice",
    author_peer: "user:7",
    deletion_fingerprint: "c".repeat(64),
  });
  const unverified = message(3, {
    peer: "chat:42",
    text: "Sent here, sender unverified.",
    outgoing: true,
    author_peer: null,
    deletion_fingerprint: "d".repeat(64),
  });
  let posts: unknown[];

  beforeEach(() => {
    reset();
    posts = [];
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
      if (url.endsWith("/preferences/reading-view"))
        return { status: 200, body: { reading_view: "originals" } };
      if (url.endsWith("/chats/chat%3A42"))
        return {
          status: 200,
          body: {
            timezone: "UTC",
            chat: {
              account_id: 1,
              peer: "chat:42",
              title: "Planning group",
              kind: "group",
              photo_id: null,
              read_outbox: 0,
            },
            messages: [theirs, own, unverified],
            blocks: [],
            briefings: [],
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
            deletions: [],
          },
        };
      if (url.endsWith("/originals/current"))
        return {
          status: 200,
          body: { messages: [own], missing: [] },
        };
      if (url.endsWith("/deletions") && init.method === "POST") {
        const body = JSON.parse(init.body!);
        posts.push(body);
        return {
          status: 200,
          body: {
            ...operation(body.id, "queued", [2]),
            peer: "chat:42",
          },
        };
      }
      throw new Error(`unexpected ${url}`);
    });
  });

  test("only the owner's verified originals are offered, for everyone", async () => {
    await render(
      <ConnectionProvider>
        <ConversationRoute />
      </ConnectionProvider>,
    );
    // Sent from this account, but without a verified sender: not offered.
    await fireEvent(
      await screen.findByText("Sent here, sender unverified."),
      "longPress",
    );
    expect(await screen.findByRole("menuitem", { name: "Copy" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Select" })).toBeNull();
    await fireEvent.press(screen.getByLabelText("Close"));
    await fireEvent(screen.getByText("My own group message."), "longPress");
    await fireEvent.press(
      await screen.findByRole("menuitem", { name: "Select" }),
    );
    expect(await screen.findByText("1 selected")).toBeTruthy();
    // Other people's originals take no checkbox.
    expect(screen.queryByLabelText("Select: Their group message.")).toBeNull();
    await fireEvent.press(screen.getByLabelText("Delete selected messages"));
    expect(await screen.findByText("Delete for everyone")).toBeTruthy();
    expect(screen.getByText("1 message")).toBeTruthy();
    await fireEvent.press(screen.getByText("Delete for everyone"));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{32}$/),
      targets: [
        {
          message_id: 2,
          revision: 1,
          source_fingerprint: "b".repeat(64),
        },
      ],
    });
  });
});
