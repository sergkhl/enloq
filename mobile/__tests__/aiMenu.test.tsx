import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
// The module doubles first, so these imports get them.
import { mockParams, mockRouter, mockStore, reset, serve } from "./setup";
import React from "react";
import AiPageRoute from "../app/ai/[account]/[peer]";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { showChat } from "../src/AiMenu";
import { ConnectionProvider } from "../src/connection";
import type { Snapshot } from "../src/conversation";

// ADR 62: a private chat's AI Menu, opened by the header's robot, and its
// controls' pages.

const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});

type Ai = NonNullable<Snapshot["ai"]>;

const AI: Ai = {
  account: true,
  running: true,
  suggestions: true,
  style: "friendly",
  emotion: true,
  calming: true,
  media: true,
  remember: false,
  server_suggestions: true,
  server_memory: true,
};

const CHAT = "https://enloq.example.com/api/accounts/1/chats/user%3A101";

let ai: Ai;
let view: string;
let online: boolean;
let refused: string | null;
let posts: { url: string; body: unknown }[];

function snapshot(): Snapshot {
  return {
    timezone: "UTC",
    chat: { account_id: 1, peer: "user:101", title: "Anna", photo_id: null },
    messages: [],
    blocks: [],
    unread_sources: [],
    emotion: {
      code: "worried",
      reason: "available",
      source: "incoming",
      source_time: Date.now() / 1000,
      expires_at: Date.now() / 1000 + 3600,
    },
    ai: { ...ai },
  } as unknown as Snapshot;
}

beforeEach(() => {
  reset();
  showChat("1/user:101", null);
  ai = { ...AI };
  view = "ai";
  online = true;
  refused = null;
  posts = [];
  mockStore.set("enloq.credential", SAVED);
  mockParams.account = "1";
  mockParams.peer = "user:101";
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
    if (url.endsWith("/preferences/reading-view")) {
      if (init.method === "POST") {
        view = JSON.parse(init.body!).reading_view;
        posts.push({ url, body: JSON.parse(init.body!) });
      }
      return { status: 200, body: { reading_view: view } };
    }
    if (url === CHAT) return { status: 200, body: snapshot() };
    if (init.method === "POST" && url.startsWith(`${CHAT}/`)) {
      const body = JSON.parse(init.body!);
      posts.push({ url, body });
      if (refused) return { status: 400, body: { detail: refused } } as never;
      if (url.endsWith("/calming")) ai.calming = body.enabled;
      if (url.endsWith("/emotion")) ai.emotion = body.enabled;
      if (url.endsWith("/voice-and-images")) ai.media = body.enabled;
      if (url.endsWith("/remember")) ai.remember = body.enabled;
      if (url.endsWith("/suggestions/settings")) {
        ai.suggestions = body.automatic;
        ai.style = body.style;
        return { status: 200, body };
      }
      return { status: 200, body: { enabled: body.enabled } };
    }
    if (url.includes("/ready-sources"))
      return { status: 200, body: { content: [], next_cursor: null } };
    if (url.endsWith("/notifications/attention"))
      return { status: 200, body: { events: [], sound: false } };
    // The owner's settings, whose Guidance the pages list.
    if (url.endsWith("/api/settings"))
      return {
        status: 200,
        body: {
          memory: true,
          suggestions: true,
          guidance: {
            version: 3,
            fields: {},
            defaults: {},
            paused: [],
          },
        },
      };
    if (url.endsWith("/memory"))
      return {
        status: 200,
        body: {
          entries: [],
          revision: 1,
          coverage: 0,
          error: null,
          enabled: true,
          waiting: 0,
          updating: false,
        },
      };
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
    throw new Error(`unexpected ${url}`);
  });
});

describe("the AI Menu in a private chat", () => {
  const mount = () =>
    render(
      <ConnectionProvider>
        <ConversationRoute />
      </ConnectionProvider>,
    );
  const open = async () => {
    await fireEvent.press(
      await screen.findByLabelText("AI Menu, AI on in this chat"),
    );
    return within(screen.getByLabelText("AI Menu"));
  };

  test("the robot opens AI view, then the chat's controls with their lines", async () => {
    await mount();
    const menu = await open();
    const rows = menu
      .getAllByRole("button")
      .map((row) => row.props.accessibilityLabel);
    expect(rows).toEqual([
      "AI view",
      "Automatic suggestions",
      "Style, Friendly",
      "Emotion",
      "Soften hostile messages",
      "Voice and images",
      "Remember this chat",
    ]);
    expect(
      menu.getByText("Show AI text where it exists, in every chat."),
    ).toBeTruthy();
    expect(
      menu.getByText(
        "Note what they and you care about here. What's noted stays when Off.",
      ),
    ).toBeTruthy();
    // Style shows its choice's line.
    expect(
      menu.getByText("Warm, natural wording without forced slang or emojis."),
    ).toBeTruthy();
    // Five switches and Style, which has a page and no switch (Q2).
    expect(
      menu.getAllByRole("switch").map((row) => row.props.accessibilityLabel),
    ).toEqual([
      "AI view",
      "Automatic suggestions",
      "Emotion",
      "Soften hostile messages",
      "Voice and images",
      "Remember this chat",
    ]);
  });

  test("Emotion and Voice and images switch through their own routes", async () => {
    await mount();
    const menu = await open();
    await fireEvent(
      menu.getByRole("switch", { name: "Emotion" }),
      "valueChange",
      false,
    );
    await waitFor(() =>
      expect(posts).toEqual([
        { url: `${CHAT}/emotion`, body: { enabled: false } },
      ]),
    );
    await fireEvent(
      screen.getByRole("switch", { name: "Voice and images" }),
      "valueChange",
      false,
    );
    await waitFor(() =>
      expect(posts.at(-1)).toEqual({
        url: `${CHAT}/voice-and-images`,
        body: { enabled: false },
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("switch", { name: "Voice and images" }).props.value,
      ).toBe(false),
    );
    expect(screen.getByRole("switch", { name: "Emotion" }).props.value).toBe(
      false,
    );
  });

  test("a chat with every switch Off shows the robot's outline (Q14)", async () => {
    ai = {
      ...AI,
      running: false,
      suggestions: false,
      emotion: false,
      calming: false,
      media: false,
      remember: false,
    };
    await mount();
    await fireEvent.press(
      await screen.findByLabelText("AI Menu, AI off in this chat"),
    );
    const menu = within(screen.getByLabelText("AI Menu"));
    expect(
      menu
        .getAllByRole("switch")
        .filter((row) => row.props.accessibilityLabel !== "AI view")
        .map((row) => row.props.value),
    ).toEqual([false, false, false, false, false]);
    // The account's AI is On, so no account note shows.
    expect(
      menu.queryByText("AI processing is Off for this account."),
    ).toBeNull();
  });

  test("a tap on a switch flips it in place, AI view the Reading View", async () => {
    await mount();
    const menu = await open();
    const calm = menu.getByRole("switch", { name: "Soften hostile messages" });
    expect(calm.props.value).toBe(true);
    await fireEvent(calm, "valueChange", false);
    await waitFor(() =>
      expect(posts).toEqual([
        { url: `${CHAT}/calming`, body: { enabled: false } },
      ]),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("switch", { name: "Soften hostile messages" }).props
          .value,
      ).toBe(false),
    );
    // Automatic suggestions keeps the chat's Style.
    await fireEvent(
      screen.getByRole("switch", { name: "Automatic suggestions" }),
      "valueChange",
      false,
    );
    await waitFor(() =>
      expect(posts.at(-1)).toEqual({
        url: `${CHAT}/suggestions/settings`,
        body: { automatic: false, style: "friendly" },
      }),
    );
    await fireEvent(
      screen.getByRole("switch", { name: "AI view" }),
      "valueChange",
      false,
    );
    await waitFor(() => expect(view).toBe("originals"));
    // The menu stays open for another change.
    expect(screen.getByLabelText("AI Menu")).toBeTruthy();
  });

  test("a refused switch says why and keeps its state", async () => {
    refused = "This chat is unavailable";
    await mount();
    const menu = await open();
    await fireEvent(
      menu.getByRole("switch", { name: "Remember this chat" }),
      "valueChange",
      true,
    );
    expect(await screen.findByText("This chat is unavailable")).toBeTruthy();
    expect(
      screen.getByRole("switch", { name: "Remember this chat" }).props.value,
    ).toBe(false);
  });

  test("its notes say why nothing runs: a server-wide switch, the account", async () => {
    ai = { ...AI, server_suggestions: false, server_memory: false };
    const first = await mount();
    const menu = await open();
    expect(
      menu.getByText("Automatic suggestions is Off for all chats."),
    ).toBeTruthy();
    expect(menu.getByText("Memory is Off for all chats.")).toBeTruthy();
    expect(
      menu.queryByText("AI processing is Off for this account."),
    ).toBeNull();
    await first.unmount();
    ai = { ...AI, account: false, running: false };
    await mount();
    await fireEvent.press(
      await screen.findByLabelText("AI Menu, AI off in this chat"),
    );
    expect(
      screen.getByText("AI processing is Off for this account."),
    ).toBeTruthy();
  });

  test("a row opens its control's page", async () => {
    await mount();
    const menu = await open();
    await fireEvent.press(
      menu.getByRole("button", { name: "Remember this chat" }),
    );
    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: "/ai/[account]/[peer]",
      params: { account: "1", peer: "user:101", control: "remember" },
    });
  });

  test("offline, the switches wait for the server", async () => {
    const first = await mount();
    await screen.findByLabelText("AI Menu, AI on in this chat");
    await first.unmount();
    // What the phone kept shows, and the reads that follow fail.
    online = false;
    await mount();
    expect(
      await screen.findByText(
        "enloq.example.com can't be reached. Showing what was last loaded.",
      ),
    ).toBeTruthy();
    const menu = await open();
    expect(
      menu.getByText("The switches can be changed once the server answers."),
    ).toBeTruthy();
    expect(
      menu.getByRole("switch", { name: "Soften hostile messages" }).props
        .disabled,
    ).toBe(true);
  });
});

describe("an AI Menu page", () => {
  // The open chat's snapshot, as the chat screen beneath the page reads it.
  const publish = (live = true): void =>
    showChat("1/user:101", {
      snapshot: snapshot(),
      readAt: Date.now(),
      live,
      reload: async () => publish(live),
    });
  const mount = (control: string) => {
    mockParams.control = control;
    publish();
    return render(
      <ConnectionProvider>
        <AiPageRoute />
      </ConnectionProvider>,
    );
  };

  test("Emotion's page holds its switch and the emotion's details (Q12)", async () => {
    await mount("emotion");
    expect(await screen.findByText("😟 Worried")).toBeTruthy();
    expect(
      screen.getByText(/^On, Enloq estimates how their latest messages feel/),
    ).toBeTruthy();
    const emotion = screen.getByLabelText("Emotion");
    await waitFor(() => expect(emotion.props.disabled).toBe(false));
    expect(emotion.props.value).toBe(true);
  });

  test("with Emotion Off its page says so and shows no estimate (Q5)", async () => {
    ai = { ...AI, emotion: false };
    const off = snapshot();
    showChat("1/user:101", {
      snapshot: { ...off, emotion: { code: null, reason: "off" } } as Snapshot,
      readAt: Date.now(),
      live: true,
      reload: async () => undefined,
    });
    mockParams.control = "emotion";
    await render(
      <ConnectionProvider>
        <AiPageRoute />
      </ConnectionProvider>,
    );
    expect(
      await screen.findByText("Emotion is Off for this chat."),
    ).toBeTruthy();
    expect(screen.queryByText("😟 Worried")).toBeNull();
    expect(screen.getByLabelText("Emotion").props.value).toBe(false);
  });

  test("a switch's page flips it, with its explanation", async () => {
    await mount("calming");
    const calm = await screen.findByLabelText("Soften hostile messages");
    await waitFor(() => expect(calm.props.disabled).toBe(false));
    expect(calm.props.value).toBe(true);
    expect(
      screen.getByText(/^Hostile wording is softened while keeping every fact/),
    ).toBeTruthy();
    await fireEvent(calm, "valueChange", false);
    await waitFor(() =>
      expect(posts).toEqual([
        { url: `${CHAT}/calming`, body: { enabled: false } },
      ]),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Soften hostile messages").props.value).toBe(
        false,
      ),
    );
  });

  test("Style's page keeps Automatic suggestions as it is", async () => {
    ai = { ...AI, suggestions: false, style: "professional" };
    await mount("style");
    const idioms = await screen.findByRole("radio", { name: "Idioms" });
    await waitFor(() =>
      expect(idioms.props.accessibilityState.disabled).toBe(false),
    );
    await fireEvent.press(idioms);
    await waitFor(() =>
      expect(posts).toEqual([
        {
          url: `${CHAT}/suggestions/settings`,
          body: { automatic: false, style: "idioms" },
        },
      ]),
    );
    expect(
      await screen.findByRole("radio", { name: "Idioms", checked: true }),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Idioms, proverbs, set expressions and phrasal verbs in this chat's language.",
      ),
    ).toBeTruthy();
  });

  test("a server-wide switch that is Off shows under the chat's", async () => {
    ai = { ...AI, server_memory: false };
    await mount("remember");
    expect(
      await screen.findByText("Memory is Off for all chats."),
    ).toBeTruthy();
    expect(screen.getByText("Cares about")).toBeTruthy();
    expect(screen.getByText("Traits")).toBeTruthy();
  });

  test("offline, its switch waits for the server", async () => {
    mockParams.control = "remember";
    publish(false);
    await render(
      <ConnectionProvider>
        <AiPageRoute />
      </ConnectionProvider>,
    );
    expect(
      await screen.findByText(
        "The switches can be changed once the server answers.",
      ),
    ).toBeTruthy();
    expect(screen.getByLabelText("Remember this chat").props.disabled).toBe(
      true,
    );
  });

  test("an unknown control returns to the list", async () => {
    mockParams.control = "nonsense";
    const { mockRedirects } = require("./setup");
    await render(
      <ConnectionProvider>
        <AiPageRoute />
      </ConnectionProvider>,
    );
    await waitFor(() => expect(mockRedirects).toContain("/chats"));
  });
});
