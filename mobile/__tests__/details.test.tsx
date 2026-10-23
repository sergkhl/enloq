import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
// The module doubles first, so these imports get them.
import { mockParams, mockStore, reset, serve } from "./setup";
import React from "react";
import { Linking } from "react-native";
import { attachmentCard } from "../../shared/attachments";
import { forwardedGroups, forwardedLine } from "../../shared/forwarding";
import {
  chipLabel,
  chipName,
  quickReactions,
  reactionChoice,
  type ReactionChip,
} from "../../shared/reactions";
import { versionCounter } from "../../shared/versions";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { ConnectionProvider } from "../src/connection";
import type { NativeMessage, Snapshot } from "../src/conversation";

// ADR 46 CU2: reactions, forwarded lines, versions, Scroll to latest and
// attachment cards in the app.

const chip = (extra: Partial<ReactionChip> = {}): ReactionChip => ({
  reaction: { emoji: "👍", custom: null, paid: false },
  count: 1,
  mine: false,
  peer: true,
  ...extra,
});

describe("the shared rules", () => {
  test("chips name paid and custom reactions and the owner's own", () => {
    expect(chipLabel(chip())).toBe("👍");
    expect(
      chipLabel(chip({ reaction: { emoji: null, custom: null, paid: true } })),
    ).toBe("⭐");
    expect(
      chipLabel(chip({ reaction: { emoji: null, custom: "5", paid: false } })),
    ).toBe("◇");
    expect(chipName(chip({ count: 2, mine: true }))).toBe(
      "👍 2, including yours",
    );
    expect(
      chipName(chip({ reaction: { emoji: null, custom: null, paid: true } })),
    ).toBe("Paid reaction 1");
  });

  test("the row puts the owner's own first, then six, and a second tap removes", () => {
    const palette = ["👍", "👎", "❤️", "🔥", "🤝", "👌", "🙏", "😁"];
    expect(quickReactions(palette, ["😁"], false)).toEqual([
      "😁",
      "👍",
      "👎",
      "❤️",
      "🔥",
      "🤝",
      "👌",
    ]);
    expect(quickReactions(palette, [], true)).toHaveLength(8);
    const view = { chips: [chip({ mine: true })], own_known: true };
    expect(reactionChoice(view, "👍")).toEqual({
      emoji: "👍",
      selected: false,
    });
    expect(reactionChoice(view, "❤️")).toEqual({ emoji: "❤️", selected: true });
  });

  test("forwarded lines name the origin, and an AI block groups them", () => {
    const date = Date.UTC(2026, 9, 16, 9, 30) / 1000;
    expect(
      forwardedLine(
        {
          forwarded: true,
          forwarded_from: "Authored channel",
          forward_date: date,
        },
        "UTC",
      ),
    ).toEqual({
      text: expect.stringMatching(/^Forwarded from Authored channel · .+ · /),
      imported: false,
    });
    expect(forwardedLine({ forwarded: true, imported: true }, "UTC")).toEqual({
      text: "Imported",
      imported: true,
    });
    expect(forwardedLine({ forwarded: false }, "UTC")).toBeNull();
    expect(
      forwardedGroups(
        [
          { forwarded: true, forwarded_from: "A" },
          { forwarded: true, forwarded_from: "A" },
          { forwarded: false },
        ],
        "UTC",
      ),
    ).toEqual([{ text: "Forwarded from A · 2 messages", imported: false }]);
  });

  test("versions count oldest first and cards keep no phone or place", () => {
    expect(versionCounter({ count: 2, index: -1 })).toBe("3/3");
    expect(versionCounter({ count: 2, index: 0 })).toBe("2/3");
    expect(versionCounter({ count: 2, index: 1 })).toBe("1/3");
    expect(
      attachmentCard({
        media: "link",
        media_info: { site: "Example", title: "Page" },
        text: "See https://e.test/page",
      }),
    ).toEqual({
      kind: "link",
      title: "Page",
      site: "Example",
      url: "https://e.test/page",
    });
    expect(
      attachmentCard({ media: "location", media_info: { live: true } }),
    ).toEqual({ kind: "location", title: "Live location", place: null });
  });
});

function message(id: number, extra = {}): NativeMessage {
  return {
    account_id: 1,
    peer: "user:101",
    message_id: id,
    revision: 1,
    date: 1_700_000_000 + id * 60,
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

describe("message details in the conversation", () => {
  let served: Snapshot;
  let view: string;
  let posts: { url: string; body: unknown }[];

  beforeEach(() => {
    reset();
    view = "originals";
    posts = [];
    mockStore.set(
      "enloq.credential",
      JSON.stringify({ origin: "https://enloq.example.com", token: "t0ken" }),
    );
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
            accounts: [],
          },
        };
      if (url.endsWith("/preferences/reading-view"))
        return { status: 200, body: { reading_view: view } };
      if (url.endsWith("/chats/user%3A101"))
        return { status: 200, body: served };
      if (url.includes("/originals/3/versions"))
        return {
          status: 200,
          body: [
            {
              id: 31,
              message: message(3, { text: "Second wording", revision: 1 }),
              transcript: null,
              observed: 1,
              reason: "edited",
              media_available: false,
            },
            {
              id: 30,
              message: message(3, { text: "First wording", revision: 1 }),
              transcript: null,
              observed: 1,
              reason: "edited",
              media_available: false,
            },
          ],
        };
      if (url.endsWith("/reactions")) {
        posts.push({ url, body: JSON.parse(init.body!) });
        return { status: 200, body: { reactions: null } };
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

  test("chips show under a message and the sheet reacts and removes", async () => {
    served.messages = [
      message(1, {
        reactions: {
          chips: [
            chip({ count: 2, mine: true }),
            chip({ reaction: { emoji: null, custom: null, paid: true } }),
          ],
          own_known: true,
        },
      }),
    ];
    await mount();
    expect(await screen.findByLabelText("👍 2, including yours")).toBeTruthy();
    expect(screen.getByLabelText("Paid reaction 1")).toBeTruthy();
    await fireEvent(screen.getByText("Message 1"), "longPress");
    // The owner's own first: choosing it removes it.
    await fireEvent.press(
      await screen.findByRole("menuitem", { name: "Remove reaction 👍" }),
    );
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      url: expect.stringContaining("/originals/1/reactions"),
      body: { emoji: "👍", selected: false },
    });
    await fireEvent(screen.getByText("Message 1"), "longPress");
    await fireEvent.press(
      await screen.findByRole("menuitem", { name: "More reactions" }),
    );
    await fireEvent.press(screen.getByRole("menuitem", { name: "React 😁" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]!.body).toEqual({ emoji: "😁", selected: true });
  });

  test("an AI block with several messages asks which one to react to", async () => {
    view = "ai";
    served.messages = [message(1), message(2)];
    served.blocks = [
      {
        id: "a",
        revision: "r1",
        sources: [1, 2],
        rendered: "Anna writes twice.",
        rendered_revision: "r1",
        stream: null,
        state: "ready",
        stage: null,
        error: null,
      },
    ];
    await mount();
    await fireEvent(await screen.findByText("Anna writes twice."), "longPress");
    await fireEvent.press(
      await screen.findByRole("menuitem", { name: "React ❤️" }),
    );
    expect(screen.getByText("React ❤️ to which message?")).toBeTruthy();
    await fireEvent.press(
      screen.getByRole("menuitem", { name: "React ❤️: Message 2" }),
    );
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.url).toContain("/originals/2/reactions");
  });

  test("forwarded and imported lines, and versions with arrows in Originals", async () => {
    served.messages = [
      message(1, {
        forwarded: true,
        forwarded_from: "Authored channel",
        forward_date: Date.UTC(2026, 9, 16, 9, 30) / 1000,
      }),
      message(2, { forwarded: true, imported: true }),
      message(3, {
        text: "Latest wording",
        native_edited: true,
        history_count: 2,
      }),
      message(4, { text: "Edited once", native_edited: true }),
    ];
    await mount();
    expect(
      await screen.findByText(/^Forwarded from Authored channel · /),
    ).toBeTruthy();
    expect(screen.getByText("Imported")).toBeTruthy();
    expect(
      screen.getByLabelText("Edited in Telegram; no earlier version captured"),
    ).toBeTruthy();
    const versions = screen.getByLabelText("Versions of message 3");
    expect(within(versions).getByText("3/3")).toBeTruthy();
    await fireEvent.press(within(versions).getByLabelText("Previous version"));
    expect(await screen.findByText("Second wording")).toBeTruthy();
    expect(within(versions).getByText("2/3")).toBeTruthy();
    await fireEvent.press(within(versions).getByLabelText("Previous version"));
    expect(await screen.findByText("First wording")).toBeTruthy();
    // While a version shows, its sheet offers only Copy.
    await fireEvent(screen.getByText("First wording"), "longPress");
    expect(await screen.findByRole("menuitem", { name: "Copy" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: /^React/ })).toBeNull();
    await fireEvent.press(screen.getByLabelText("Close"));
    await fireEvent.press(within(versions).getByLabelText("Next version"));
    await fireEvent.press(within(versions).getByLabelText("Next version"));
    expect(await screen.findByText("Latest wording")).toBeTruthy();
    expect(within(versions).getByText("3/3")).toBeTruthy();
  });

  test("a message missing its forward header has it read and filled in", async () => {
    served.messages = [message(1, { forwarded: null, forward_read: false })];
    serve((url) => {
      if (url.endsWith("/originals/forwarding"))
        return {
          status: 200,
          body: [
            {
              message_id: 1,
              forwarded: true,
              forwarded_from: "Late source",
              forward_date: null,
              forward_signature: null,
              imported: false,
              forward_read: true,
            },
          ],
        };
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
      if (url.includes("/ready-sources"))
        return { status: 200, body: { content: [], next_cursor: null } };
      if (url.endsWith("/notifications/attention"))
        return { status: 200, body: { events: [], sound: false } };
      throw new Error(`unexpected ${url}`);
    });
    await mount();
    expect(await screen.findByText("Forwarded from Late source")).toBeTruthy();
  });

  test("cards show a poll, a location that opens maps, a contact and a link", async () => {
    const open = jest
      .spyOn(Linking, "openURL")
      .mockResolvedValue(undefined as never);
    served.messages = [
      message(1, {
        text: "",
        media: "poll",
        media_info: { question: "Which day works?", options: ["Mon", "Fri"] },
      }),
      message(2, {
        text: "",
        media: "location",
        media_info: { title: "Authored cafe" },
      }),
      message(3, {
        text: "",
        media: "contact",
        media_info: { title: "Authored Contact" },
      }),
      message(4, {
        text: "https://e.test/page",
        media: "link",
        media_info: { site: "Example", title: "Authored page" },
      }),
    ];
    await mount();
    expect(await screen.findByText("Which day works?")).toBeTruthy();
    expect(screen.getByText("• Mon")).toBeTruthy();
    expect(screen.getByText("Authored Contact")).toBeTruthy();
    await fireEvent.press(screen.getByText("Authored cafe"));
    // The tests run as iOS: Apple Maps' search for the venue.
    expect(open).toHaveBeenLastCalledWith(
      "https://maps.apple.com/?q=Authored%20cafe",
    );
    await fireEvent.press(screen.getByText("Authored page"));
    expect(open).toHaveBeenLastCalledWith("https://e.test/page");
    open.mockRestore();
  });

  test("Scroll to latest shows away from the latest message and goes back", async () => {
    served.messages = [message(1), message(2)];
    await mount();
    await screen.findByText("Message 2");
    expect(screen.queryByLabelText("Scroll to latest")).toBeNull();
    await fireEvent.scroll(screen.getByTestId("messages"), {
      nativeEvent: { contentOffset: { y: 900 } },
    });
    await fireEvent.press(screen.getByLabelText("Scroll to latest"));
    expect(screen.queryByLabelText("Scroll to latest")).toBeNull();
  });
});
