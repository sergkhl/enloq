import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
// The module doubles first, so these imports get them.
import { mockClipboard, mockParams, mockStore, reset, serve } from "./setup";
import React from "react";
import { canRetry, olderStatus, unanalysedNote } from "../../shared/aiControls";
import { emotionDetails } from "../../shared/emotion";
import { memoryBytes } from "../../shared/memory";
import Accounts from "../app/accounts";
import ConversationRoute from "../app/chat/[account]/[peer]";
import ProfileRoute from "../app/profile/[account]/[peer]";
import { writeCached } from "../src/cache";
import { ConnectionProvider } from "../src/connection";
import type { Block, NativeMessage, Snapshot } from "../src/conversation";

// ADR 46 CU3: a block's AI controls, the account's AI switch and the
// Correspondent Profile in the app.

const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});

describe("the shared rules", () => {
  test("Retry, the earlier-messages bar, the note and the byte count", () => {
    const block = { state: "failed", revision: "r1", rendered: null };
    expect(canRetry(block)).toBe(true);
    expect(
      canRetry({ ...block, rendered: "Text", rendered_revision: "r1" }),
    ).toBe(false);
    expect(canRetry({ ...block, state: "ready" })).toBe(false);
    expect(olderStatus({ available: 2, notice: null }, null)?.text).toBe(
      "2 earlier blocks are not rewritten",
    );
    expect(olderStatus(null, { done: 0, total: 3, explicit: true })?.text).toBe(
      "Analyzing earlier messages · 1 of 3",
    );
    expect(
      olderStatus(
        { available: 0, notice: { remaining: 4, reason: "budget" } },
        null,
      )?.text,
    ).toBe("4 remaining — keeping budget for new messages");
    expect(
      unanalysedNote(
        [
          { id: 6, kind: "document" },
          { id: 7, kind: "video_note" },
        ],
        [{ message_id: 6, media_info: { file_name: "contract.pdf" } }],
      ),
    ).toBe("Not analysed: File contract.pdf, Video message");
    expect(memoryBytes("Friday")).toBe(6);
    expect(memoryBytes("Пятница")).toBe(14);
  });

  test("the emotion's details name its source and time", () => {
    const now = Date.now() / 1000;
    const details = emotionDetails(
      {
        code: "positive",
        reason: "available",
        source: "reaction",
        reaction: "👍",
        target_id: 9,
        source_time: now,
        expires_at: now + 3600,
      },
      "UTC",
    );
    expect(details.label).toBe("🙂 Positive or warm");
    expect(details.reason).toBe(
      "Inferred from the recipient's reaction and its message.",
    );
    expect(details.source).toBe("A reaction 👍 on message #9");
    expect(details.time).toMatch(/ · /);
    expect(
      emotionDetails({ code: "sad", expires_at: now - 1 }, "UTC").label,
    ).toBe("No current estimate");
  });
});

function message(id: number, extra = {}): NativeMessage {
  return {
    account_id: 1,
    peer: "user:101",
    message_id: id,
    revision: 1,
    date: 1_700_000_000 + id * 60,
    text: `Source ${id}`,
    outgoing: false,
    service: false,
    deleted: false,
    media: null,
    forwarded: false,
    forward_read: true,
    ...extra,
  } as unknown as NativeMessage;
}

function block(id: string, sources: number[], extra = {}): Block {
  return {
    id: `1/user:101/${id}`,
    revision: "a".repeat(64),
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

describe("AI controls in the conversation", () => {
  let served: Snapshot;
  let aiEnabled: boolean;
  let posts: { url: string; body: unknown }[];

  beforeEach(() => {
    reset();
    aiEnabled = true;
    posts = [];
    mockStore.set("enloq.credential", SAVED);
    mockParams.account = "1";
    mockParams.peer = "user:101";
    served = {
      timezone: "UTC",
      chat: { account_id: 1, peer: "user:101", title: "Anna", photo_id: null },
      messages: [],
      blocks: [],
      unread_sources: [],
    } as unknown as Snapshot;
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
                ai_enabled: aiEnabled,
                photo_id: null,
              },
            ],
          },
        };
      if (url.endsWith("/preferences/reading-view"))
        return { status: 200, body: { reading_view: "ai" } };
      if (url.endsWith("/chats/user%3A101"))
        return { status: 200, body: served };
      if (init.method === "POST" && url.includes("/chats/user%3A101/")) {
        posts.push({ url, body: init.body ? JSON.parse(init.body) : null });
        return { status: 200, body: {} };
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
  const posted = (suffix: string) =>
    posts.filter((post) => post.url.endsWith(suffix));

  test("a failed block retries from its status and its sheet", async () => {
    served.messages = [message(1)];
    served.blocks = [block("1", [1], { state: "failed" })];
    await mount();
    await fireEvent.press(await screen.findByText("Retry"));
    await waitFor(() => expect(posted("/blocks/1/retry")).toHaveLength(1));
    expect(posted("/blocks/1/retry")[0]!.body).toEqual({
      revision: "a".repeat(64),
    });
    await fireEvent(screen.getByText("Source 1"), "longPress");
    await fireEvent.press(
      await screen.findByRole("menuitem", { name: "Retry" }),
    );
    await waitFor(() => expect(posted("/blocks/1/retry")).toHaveLength(2));
  });

  test("a block shows its originals alone, and back; the note names what was skipped", async () => {
    served.messages = [
      message(1),
      message(2, {
        text: "",
        media: "document",
        media_info: { file_name: "contract.pdf" },
      }),
      message(3, { text: "Another block's source" }),
    ];
    served.blocks = [
      block("1", [1, 2], {
        rendered: "Anna sends a file.",
        rendered_revision: "a".repeat(64),
        unanalysed: [{ id: 2, kind: "document" }],
      }),
      block("3", [3], {
        rendered: "Anna writes again.",
        rendered_revision: "a".repeat(64),
      }),
    ];
    await mount();
    expect(
      await screen.findByText("Not analysed: File contract.pdf"),
    ).toBeTruthy();
    await fireEvent(screen.getByText("Anna sends a file."), "longPress");
    await fireEvent.press(
      await screen.findByRole("menuitem", { name: "Show original" }),
    );
    expect(await screen.findByText("Source 1")).toBeTruthy();
    // The other block keeps its AI text.
    expect(screen.getByText("Anna writes again.")).toBeTruthy();
    await fireEvent(screen.getByText("Source 1"), "longPress");
    await fireEvent.press(
      await screen.findByRole("menuitem", { name: "Show AI text" }),
    );
    expect(await screen.findByText("Anna sends a file.")).toBeTruthy();
  });

  test("earlier messages rewrite and stop, and how a run ended", async () => {
    served.messages = [message(1)];
    served.older = { available: 2, notice: null };
    const view = await mount();
    await fireEvent.press(
      await screen.findByLabelText("Rewrite earlier messages"),
    );
    await waitFor(() => expect(posted("/process-older")).toHaveLength(1));
    expect(screen.getByText("2 earlier blocks are not rewritten")).toBeTruthy();
    await view.unmount();
    served.older = { available: 2, notice: null };
    served.progress = { done: 0, total: 2, explicit: true };
    const running = await mount();
    expect(
      await screen.findByText("Analyzing earlier messages · 1 of 2"),
    ).toBeTruthy();
    await fireEvent.press(
      screen.getByLabelText("Stop rewriting earlier messages"),
    );
    await waitFor(() => expect(posted("/stop")).toHaveLength(1));
    await running.unmount();
    served.progress = null;
    served.older = { available: 0, notice: { remaining: 3, reason: "off" } };
    await mount();
    expect(
      await screen.findByText("3 remaining — AI processing was turned Off"),
    ).toBeTruthy();
  });

  test("text that isn't rewritten shows its originals together, unlabelled (ADR 54)", async () => {
    served.messages = [message(1), message(2)];
    served.blocks = [block("1", [1, 2], { state: "original" })];
    await mount();
    expect(await screen.findByText("Source 1")).toBeTruthy();
    expect(screen.getByText("Source 2")).toBeTruthy();
    expect(screen.getByText(/^2 messages · /)).toBeTruthy();
    expect(screen.queryByText(/^AI · /)).toBeNull();
    expect(screen.queryByText("Original text")).toBeNull();
  });

  test("a calmed rewrite says so in its head (ADR 53 Q5)", async () => {
    served.messages = [message(1), message(2)];
    served.blocks = [
      block("1", [1], {
        rendered: "Anna is angry about the late refund of 240.",
        rendered_revision: "a".repeat(64),
        calmed: true,
      }),
      block("2", [2], {
        rendered: "Anna says thanks.",
        rendered_revision: "a".repeat(64),
      }),
    ];
    await mount();
    expect(
      await screen.findByText("Anna is angry about the late refund of 240."),
    ).toBeTruthy();
    expect(screen.getAllByText(/^AI · 1 message · .+ · Calmed$/)).toHaveLength(
      1,
    );
  });

  test("the header says AI off and opens the profile", async () => {
    aiEnabled = false;
    served.messages = [message(1)];
    await mount();
    expect(await screen.findByText("AI off")).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Anna, open profile"));
    const { mockRouter } = require("./setup");
    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: "/profile/[account]/[peer]",
      params: { account: "1", peer: "user:101" },
    });
  });
});

describe("the Correspondent Profile", () => {
  let memory: Record<string, unknown>;
  let posts: { url: string; body: unknown }[];
  let online: boolean;
  let calming: boolean;
  let suggesting: { choice: string; idioms: boolean };

  beforeEach(() => {
    reset();
    online = true;
    calming = true;
    suggesting = { choice: "on", idioms: false };
    posts = [];
    mockStore.set("enloq.credential", SAVED);
    mockParams.account = "1";
    mockParams.peer = "user:101";
    memory = {
      entries: [],
      revision: "m1",
      coverage: null,
      error: null,
      enabled: true,
      waiting: false,
      updating: false,
    };
    writeCached("chat:1/user:101", {
      timezone: "UTC",
      chat: { account_id: 1, peer: "user:101", title: "Anna", photo_id: null },
      messages: [],
      blocks: [],
      emotion: {
        code: "worried",
        reason: "available",
        source: "incoming",
        source_time: Date.now() / 1000,
        expires_at: Date.now() / 1000 + 3600,
      },
    });
    serve((url, init) => {
      if (!online) return Promise.reject(new TypeError("offline")) as never;
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
      if (url.endsWith("/profile"))
        return {
          status: 200,
          body: {
            name: "Anna Petrova",
            username: "anna_p",
            phone: null,
            bio: "Designer in Riga.",
          },
        };
      if (url.endsWith("/calming")) {
        if (init.method === "POST") {
          const body = JSON.parse(init.body!);
          posts.push({ url, body });
          calming = body.enabled;
        }
        return { status: 200, body: { enabled: calming } };
      }
      // ADR 56 Q12 A: the chat's Suggestions choice; Idioms is refused
      // while it can't be chosen.
      if (url.endsWith("/suggestions/choice")) {
        if (init.method === "POST") {
          const body = JSON.parse(init.body!);
          posts.push({ url, body });
          if (body.choice === "idioms" && !suggesting.idioms)
            return {
              status: 400,
              body: { detail: "Idioms can't be chosen yet" },
            };
          suggesting = { ...suggesting, choice: body.choice };
        }
        return { status: 200, body: suggesting };
      }
      // ADR 55: Remember this chat, Off by default since ADR 56, and the
      // person's traits, untouched here.
      if (url.endsWith("/remember"))
        return { status: 200, body: { enabled: false } };
      if (url.endsWith("/traits"))
        return {
          status: 200,
          body: {
            revision: 0,
            enabled: true,
            scales: [],
            notes: [],
            habits: null,
            error: null,
          },
        };
      if (url.endsWith("/memory") && init.method === "GET")
        return { status: 200, body: memory };
      if (url.includes("/memory")) {
        const body = JSON.parse(init.body!);
        posts.push({ url, body });
        if (url.endsWith("/memory/entries"))
          memory = {
            ...memory,
            revision: "m2",
            entries: [
              {
                id: "e1",
                text: body.text,
                citations: [],
                source: "owner",
                outdated: null,
                sources: [],
              },
            ],
          };
        if (url.endsWith("/memory/clear"))
          memory = { ...memory, revision: "m3", entries: [] };
        return { status: 200, body: memory };
      }
      throw new Error(`unexpected ${url}`);
    });
  });

  const mount = () =>
    render(
      <ConnectionProvider>
        <ProfileRoute />
      </ConnectionProvider>,
    );

  test("it reads the person's details and edits what they care about (ADR 55)", async () => {
    await mount();
    expect(await screen.findByText("Anna Petrova")).toBeTruthy();
    expect(screen.getByText("@anna_p")).toBeTruthy();
    expect(screen.getByText("Designer in Riga.")).toBeTruthy();
    expect(screen.getByText("😟 Worried")).toBeTruthy();
    const cares = within(await screen.findByTestId("memory-panel"));
    expect(cares.getByText("Nothing noted yet.")).toBeTruthy();
    await fireEvent.changeText(
      cares.getByLabelText("Add a note"),
      "Prefers Friday",
    );
    expect(cares.getByText("14/300 bytes")).toBeTruthy();
    await fireEvent.press(cares.getByRole("button", { name: "Add note" }));
    expect(await cares.findByText("Prefers Friday")).toBeTruthy();
    expect(posts[0]).toEqual({
      url: expect.stringContaining("/chats/user%3A101/memory/entries"),
      body: { revision: "m1", text: "Prefers Friday" },
    });
    expect(cares.getByText("Added or edited by you")).toBeTruthy();
    await fireEvent.press(cares.getByText("Clear…"));
    expect(
      cares.getByText("Remove every note about this person?"),
    ).toBeTruthy();
    await fireEvent.press(cares.getByText("Clear memory"));
    expect(await cares.findByText("Nothing noted yet.")).toBeTruthy();
    expect(posts[1]!.body).toEqual({ revision: "m2" });
  });

  test("Calm this chat is on, and turns calming off (ADR 53 Q4)", async () => {
    await mount();
    await screen.findByText("Anna Petrova");
    const calm = screen.getByLabelText("Calm this chat");
    await waitFor(() => expect(calm.props.value).toBe(true));
    await fireEvent(calm, "valueChange", false);
    await waitFor(() =>
      expect(posts).toEqual([
        {
          url: expect.stringContaining("/chats/user%3A101/calming"),
          body: { enabled: false },
        },
      ]),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Calm this chat").props.value).toBe(false),
    );
  });

  test("the Suggestions choice is On, and turns Off (ADR 56 Q12 A)", async () => {
    await mount();
    await screen.findByText("Anna Petrova");
    expect(
      await screen.findByRole("radio", { name: "On", checked: true }),
    ).toBeTruthy();
    // Idioms shows only once it can be chosen (Q17).
    expect(screen.queryByRole("radio", { name: "Idioms" })).toBeNull();
    expect(
      screen.getByText(
        "Short reply suggestions appear above the composer when a message is emotional or calls for a reply.",
      ),
    ).toBeTruthy();
    await fireEvent.press(screen.getByTestId("suggestions-off"));
    await waitFor(() =>
      expect(posts).toEqual([
        {
          url: expect.stringContaining("/chats/user%3A101/suggestions/choice"),
          body: { choice: "off" },
        },
      ]),
    );
    expect(
      await screen.findByRole("radio", { name: "Off", checked: true }),
    ).toBeTruthy();
    expect(screen.getByText("No reply suggestions in this chat.")).toBeTruthy();
  });

  test("Idioms shows once it can be chosen, and a refusal shows beneath", async () => {
    suggesting = { choice: "on", idioms: true };
    await mount();
    const idioms = await screen.findByRole("radio", { name: "Idioms" });
    // The service stopped offering it after the profile opened.
    suggesting = { choice: "on", idioms: false };
    await fireEvent.press(idioms);
    expect(await screen.findByText("Idioms can't be chosen yet")).toBeTruthy();
    expect(
      screen.getByRole("radio", { name: "On", checked: true }),
    ).toBeTruthy();
    suggesting = { choice: "on", idioms: true };
    await fireEvent.press(screen.getByRole("radio", { name: "Idioms" }));
    expect(
      await screen.findByRole("radio", { name: "Idioms", checked: true }),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Suggestions use idioms, proverbs, set expressions and phrasal verbs in this chat's language.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("Idioms can't be chosen yet")).toBeNull();
  });

  test("it copies the chat link, which left the header (ADR 47 Q8)", async () => {
    await mount();
    await screen.findByText("Anna Petrova");
    await fireEvent.press(
      screen.getByRole("button", { name: "Copy chat link" }),
    );
    expect(mockClipboard).toEqual(["https://enloq.example.com/?account=1#101"]);
    expect(await screen.findByText("Chat link copied")).toBeTruthy();
  });

  test("offline it shows what the phone kept and says the rest needs the server", async () => {
    online = false;
    await mount();
    expect(await screen.findByText("Anna")).toBeTruthy();
    expect(screen.getByText("😟 Worried")).toBeTruthy();
    expect(
      await screen.findByText(
        "The person's Telegram details, memory and traits need the server.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("Nothing noted yet.")).toBeNull();
    expect(screen.queryByLabelText("Remember this chat")).toBeNull();
    expect(screen.queryByRole("radio", { name: "On" })).toBeNull();
  });
});

// ADR 47 Q7: a group's photo and name open its Group Profile.
describe("the Group Profile", () => {
  let online: boolean;
  let reads: string[];

  beforeEach(() => {
    reset();
    online = true;
    reads = [];
    mockStore.set("enloq.credential", SAVED);
    mockParams.account = "1";
    mockParams.peer = "channel:42";
    writeCached("chat:1/channel:42", {
      timezone: "UTC",
      chat: {
        account_id: 1,
        peer: "channel:42",
        title: "Supergroup discussion",
        kind: "group",
        photo_id: null,
      },
      messages: [],
      blocks: [],
    });
    serve((url) => {
      if (!online) return Promise.reject(new TypeError("offline")) as never;
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
      if (url.endsWith("/group-profile")) {
        reads.push(url);
        return {
          status: 200,
          body: {
            title: "Supergroup discussion",
            username: "weekend_live",
            members: 1200,
            description: "Plans for the weekend.",
          },
        };
      }
      throw new Error(`unexpected ${url}`);
    });
  });

  const mount = () =>
    render(
      <ConnectionProvider>
        <ProfileRoute />
      </ConnectionProvider>,
    );

  test("it reads the group's details and copies its link", async () => {
    await mount();
    expect(await screen.findByText("@weekend_live")).toBeTruthy();
    expect(screen.getByText("Supergroup discussion")).toBeTruthy();
    expect(screen.getByText("1,200 members")).toBeTruthy();
    expect(screen.getByText("Plans for the weekend.")).toBeTruthy();
    expect(reads).toEqual([
      "https://enloq.example.com/api/accounts/1/chats/channel%3A42/group-profile",
    ]);
    // Groups have no emotion (ADR 34), memory or traits (ADR 55).
    expect(screen.queryByText("Emotion")).toBeNull();
    expect(screen.queryByText("Cares about")).toBeNull();
    expect(screen.queryByText("Traits")).toBeNull();
    await fireEvent.press(
      screen.getByRole("button", { name: "Copy chat link" }),
    );
    expect(mockClipboard).toHaveLength(1);
    expect(mockClipboard[0]).toMatch(
      /^https:\/\/enloq\.example\.com\/\?account=1#/,
    );
  });

  test("offline it shows the kept title and says the rest needs the server", async () => {
    online = false;
    await mount();
    expect(await screen.findByText("Supergroup discussion")).toBeTruthy();
    expect(
      await screen.findByText("The group's Telegram details need the server."),
    ).toBeTruthy();
  });
});

describe("a group member's profile", () => {
  // ADR 51 Q14: a member's avatar or name opens their profile.
  test("it reads the member's details from the group", async () => {
    reset();
    mockStore.set("enloq.credential", SAVED);
    mockParams.account = "1";
    mockParams.peer = "channel:42";
    mockParams.member = "user:7";
    const reads: string[] = [];
    serve((url) => {
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
      if (url.endsWith("/profile")) {
        reads.push(url);
        return {
          status: 200,
          body: {
            name: "Alice Member",
            username: "alice_authored",
            phone: null,
            bio: "Authored bio.",
            photo_id: null,
          },
        };
      }
      throw new Error(`unexpected ${url}`);
    });
    try {
      await render(
        <ConnectionProvider>
          <ProfileRoute />
        </ConnectionProvider>,
      );
      expect(await screen.findByText("@alice_authored")).toBeTruthy();
      expect(screen.getByText("Alice Member")).toBeTruthy();
      expect(screen.getByText("Authored bio.")).toBeTruthy();
      expect(reads).toEqual([
        "https://enloq.example.com/api/accounts/1/chats/channel%3A42/members/user%3A7/profile",
      ]);
      expect(screen.queryByText("Cares about")).toBeNull();
      expect(screen.queryByText("Traits")).toBeNull();
    } finally {
      delete mockParams.member;
    }
  });
});

describe("Manage accounts", () => {
  test("each signed-in account's AI processing switches", async () => {
    reset();
    mockStore.set("enloq.credential", SAVED);
    let enabled = true;
    const posts: unknown[] = [];
    serve((url, init) => {
      if (url.endsWith("/accounts/1/processing")) {
        posts.push(JSON.parse(init.body!));
        enabled = JSON.parse(init.body!).ai_enabled;
        return { status: 200, body: { account_id: 1, ai_enabled: enabled } };
      }
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
                ai_enabled: enabled,
                photo_id: null,
              },
            ],
          },
        };
      throw new Error(`unexpected ${url}`);
    });
    await render(
      <ConnectionProvider>
        <Accounts />
      </ConnectionProvider>,
    );
    const ai = await screen.findByLabelText("AI processing for Owner");
    expect(ai.props.value).toBe(true);
    await fireEvent(ai, "valueChange", false);
    await waitFor(() => expect(posts).toEqual([{ ai_enabled: false }]));
    await waitFor(() =>
      expect(screen.getByLabelText("AI processing for Owner").props.value).toBe(
        false,
      ),
    );
  });
});
