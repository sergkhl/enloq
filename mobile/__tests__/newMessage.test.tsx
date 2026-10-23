import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { mockParams, mockRouter, mockStore, reset, serve } from "./setup";
import NewMessage from "../app/new-message";
import { ConnectionProvider } from "../src/connection";

// ADR 51 Q7, Q16, Q17: New Message lists the account's chats, then its
// contacts, adds Telegram's people search as one types, and opens the chosen
// person's chat.
const person = (
  peer: string,
  name: string,
  username: string | null = null,
) => ({
  peer,
  name,
  username,
  photo_id: null,
  chat: false,
});

let reads: string[];
let opened: string[];

beforeEach(() => {
  reset();
  reads = [];
  opened = [];
  mockStore.set(
    "enloq.credential",
    JSON.stringify({ origin: "https://enloq.example.com", token: "t0ken" }),
  );
  mockParams.account = "1";
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
    if (url.includes("/new-message?")) {
      reads.push(url);
      const query = new URL(url).searchParams.get("query") ?? "";
      return {
        status: 200,
        body: {
          chats: query ? [] : [{ ...person("user:42", "Alex"), chat: true }],
          contacts: query ? [] : [person("user:50", "Zoe Contact", "zoe_c")],
          search: query ? [person("user:60", "Zoe Public", "zoe_public")] : [],
        },
      };
    }
    if (url.includes("/new-message/") && init.method === "POST") {
      opened.push(url);
      return { status: 200, body: { peer: "user:60", title: "Zoe Public" } };
    }
    throw new Error(`unexpected ${url}`);
  });
});

test("it lists chats then contacts, searches as one types and opens the chat", async () => {
  await render(
    <ConnectionProvider>
      <NewMessage />
    </ConnectionProvider>,
  );
  expect(await screen.findByText("Alex")).toBeTruthy();
  expect(screen.getByText("Chats")).toBeTruthy();
  expect(screen.getByText("Contacts")).toBeTruthy();
  expect(screen.getByText("@zoe_c")).toBeTruthy();
  expect(screen.queryByText("Global search")).toBeNull();
  await fireEvent.changeText(screen.getByLabelText("Search people"), "zoe");
  expect(await screen.findByText("Zoe Public")).toBeTruthy();
  expect(screen.getByText("Global search")).toBeTruthy();
  expect(reads.at(-1)).toBe(
    "https://enloq.example.com/api/accounts/1/new-message?query=zoe",
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Zoe Public, @zoe_public" }),
  );
  await waitFor(() =>
    expect(opened).toEqual([
      "https://enloq.example.com/api/accounts/1/new-message/user%3A60",
    ]),
  );
  await act(async () => undefined);
  expect(mockRouter.replace).toHaveBeenCalledWith({
    pathname: "/chat/[account]/[peer]",
    params: { account: "1", peer: "user:60" },
  });
});
