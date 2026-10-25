import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { mockStore, reset, serve } from "./setup";
import Chats from "../app/chats";
import { ConnectionProvider } from "../src/connection";
import type { Draft } from "../src/drafts";

// ADR 50: Private | Groups | Saved as icons in the header, and the account's
// own Saved Messages in the third tab, with the composer but no AI.
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
const ACCOUNTS = {
  mode: "personal",
  timezone: "UTC",
  budget: [],
  login_available: true,
  accounts: [
    {
      account_id: 1,
      name: "Authored Owner",
      username: "",
      phone: "",
      state: "authorized",
      connected: true,
      status: "",
      ai_enabled: true,
      photo_id: null,
    },
  ],
};

function note(id: number, text: string) {
  return {
    account_id: 1,
    peer: "user:1",
    message_id: id,
    revision: 1,
    date: 1_700_000_000 + id * 60,
    text,
    outgoing: true,
    service: false,
    deleted: false,
    media: null,
  };
}

let draft: Draft;
let calls: ReturnType<typeof serve>;

function server() {
  return serve((url, init) => {
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    if (url.endsWith("/preferences/reading-view"))
      return { status: 200, body: { reading_view: "ai" } };
    if (url.endsWith("/api/preferences"))
      return { status: 200, body: { tone: "neutral", revision: 1 } };
    if (url.endsWith("/accounts/1/chats?kind=private"))
      return { status: 200, body: [] };
    if (url.endsWith("/notifications/attention"))
      return { status: 200, body: { events: [], sound: false } };
    if (url.endsWith("/chats/user%3A1"))
      return {
        status: 200,
        body: {
          timezone: "UTC",
          chat: {
            account_id: 1,
            peer: "user:1",
            title: "Saved Messages",
            kind: "saved",
            photo_id: null,
            read_outbox: 0,
          },
          messages: [note(1, "Pick up the keys")],
          blocks: [],
          unread_sources: [],
          opening_source: null,
          emotion: null,
          suggestions: null,
          draft,
          operations: [],
          reply_holds: {},
          deletions: [],
          progress: null,
          older: { available: 0, notice: null },
        },
      };
    if (init.method === "POST" && url.endsWith("/draft")) {
      const body = JSON.parse(init.body!);
      draft = { ...draft, text: body.text, revision: draft.revision + 1 };
      return { status: 200, body: draft };
    }
    throw new Error(`unexpected ${url}`);
  });
}

beforeEach(() => {
  reset();
  draft = {
    chat_key: "1/user:1",
    revision: 0,
    text: "",
    attachments: [],
    selected_media: null,
    reply_to: null,
    undo: null,
    state: "idle",
    attempt: null,
    error: null,
  };
  mockStore.set("enloq.credential", SAVED);
  calls = server();
});

const mount = () =>
  render(
    <ConnectionProvider>
      <Chats />
    </ConnectionProvider>,
  );

test("the header's icon tabs open Saved Messages in the third tab", async () => {
  await mount();
  expect(
    await screen.findByText("No private conversations in the last seven days."),
  ).toBeTruthy();
  // Icons only, each named for screen readers, in this order.
  expect(
    screen.getAllByRole("tab").map((tab) => tab.props.accessibilityLabel),
  ).toEqual(["Private", "Groups", "Saved", "Reminders"]);
  expect(screen.queryByText("Private")).toBeNull();
  await fireEvent.press(screen.getByRole("tab", { name: "Saved" }));
  expect(await screen.findByText("Pick up the keys")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Saved" }).props).toMatchObject({
    accessibilityState: { selected: true },
  });
  // The composer writes notes, with no Rewrite, tone, Undo or reading switch.
  expect(screen.getByLabelText("Send")).toBeTruthy();
  expect(screen.getByLabelText("Attach")).toBeTruthy();
  expect(screen.queryByLabelText("Rewrite")).toBeNull();
  expect(screen.queryByLabelText("Rewrite tone")).toBeNull();
  expect(screen.queryByLabelText("Undo rewrite")).toBeNull();
  expect(screen.queryByText("Originals")).toBeNull();
  // No list is read while Saved shows, and Saved is read as one chat.
  expect(calls.some((call) => call.url.includes("kind=saved"))).toBe(false);
  await fireEvent.changeText(screen.getByLabelText("Message"), "Call the bank");
  await waitFor(() =>
    expect(
      calls.some(
        (call) =>
          call.method === "POST" &&
          call.url.endsWith("/chats/user%3A1/draft") &&
          JSON.parse(call.body!).text === "Call the bank",
      ),
    ).toBe(true),
  );
});

test("a note's menu offers Copy and Reply but no reactions", async () => {
  await mount();
  await fireEvent.press(await screen.findByRole("tab", { name: "Saved" }));
  await fireEvent(await screen.findByText("Pick up the keys"), "longPress");
  expect(await screen.findByRole("menuitem", { name: "Copy" })).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Reply" })).toBeTruthy();
  // A private chat's sheet has a reaction row; Saved Messages has none.
  expect(screen.queryByLabelText("React")).toBeNull();
  expect(calls.some((call) => call.url.includes("/reactions"))).toBe(false);
});
