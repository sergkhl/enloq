import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { Text } from "react-native";
import {
  mockEventSources,
  mockFiles,
  mockRedirects,
  mockRouter,
  mockStore,
  reset,
  serve,
} from "./setup";
import Chats from "../app/chats";
import {
  ConnectionProvider,
  REVOKED_NOTICE,
  useConnection,
} from "../src/connection";

const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});

function account(id: number, name: string) {
  return {
    account_id: id,
    name,
    username: "",
    phone: "",
    state: "authorized",
    connected: true,
    status: "",
    ai_enabled: id === 1,
    photo_id: null,
  };
}
const ACCOUNTS = {
  mode: "personal",
  timezone: "UTC",
  budget: [],
  login_available: true,
  accounts: [account(1, "Authored Owner"), account(2, "Second Owner")],
};

function row(account: number, peer: string, title: string, extra = {}) {
  return {
    account_id: account,
    peer,
    title,
    kind: "private",
    muted: false,
    archived: false,
    ai_eligible: true,
    eligibility_source: "default",
    context_revision: 0,
    read_through: 0,
    read_outbox: 0,
    latest_message_id: 1,
    photo_id: null,
    activity: 1,
    processing: 0,
    unseen: false,
    opening_source: null,
    preview: {
      message_id: 1,
      date: null,
      original: `${title} original`,
      ai: `${title} rewritten`,
    },
    ...extra,
  };
}

let latest: ReturnType<typeof useConnection> | null = null;
function Probe() {
  latest = useConnection();
  return <Text testID="status">{latest.connection.status}</Text>;
}

let online = true;
let view = "ai";
let lists: Record<string, unknown[]> = {};
function server() {
  return serve((url, init) => {
    if (!online) return Promise.reject(new TypeError("offline"));
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    if (url.endsWith("/preferences/reading-view")) {
      if (init.method === "POST") view = JSON.parse(init.body!).reading_view;
      return { status: 200, body: { reading_view: view } };
    }
    const list = /\/accounts\/(\d+)\/chats\?kind=private$/.exec(url);
    if (list) return { status: 200, body: lists[list[1]!] ?? [] };
    throw new Error(`unexpected ${url}`);
  });
}

function mount() {
  return render(
    <ConnectionProvider>
      <Probe />
      <Chats />
    </ConnectionProvider>,
  );
}

const listReads = (calls: { url: string }[]) =>
  calls.filter((call) => call.url.includes("kind=private")).length;

beforeEach(() => {
  reset();
  latest = null;
  online = true;
  view = "ai";
  lists = {
    "1": [
      row(1, "user:101", "Anna", { unseen: true }),
      row(1, "user:102", "Boris", { muted: true }),
    ],
    "2": [row(2, "user:201", "Clara")],
  };
});

test("a kept credential lists the account's chats with its bearer", async () => {
  mockStore.set("enloq.credential", SAVED);
  const calls = server();
  await mount();
  expect(await screen.findByText("Anna")).toBeTruthy();
  expect(screen.getByText("Authored Owner")).toBeTruthy();
  expect(screen.getByText("Anna rewritten")).toBeTruthy();
  expect(screen.getByLabelText("Anna, new messages")).toBeTruthy();
  expect(screen.getByLabelText("Boris, muted")).toBeTruthy();
  expect(screen.getByText("enloq.example.com · Connecting…")).toBeTruthy();
  expect(
    calls.every((call) => call.headers.Authorization === "Bearer t0ken"),
  ).toBe(true);
  await fireEvent.press(screen.getByLabelText("Anna, new messages"));
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/chat/[account]/[peer]",
    params: { account: "1", peer: "user:101" },
  });
});

test("previews follow the reading view and this account's changes", async () => {
  mockStore.set("enloq.credential", SAVED);
  const calls = server();
  await mount();
  expect(await screen.findByText("Anna rewritten")).toBeTruthy();
  view = "originals";
  const source = mockEventSources[0]!;
  await act(async () =>
    source.emit("change", { data: '{"shared":["reading_view"]}' }),
  );
  expect(await screen.findByText("Anna original")).toBeTruthy();
  const before = listReads(calls);
  lists["1"] = [row(1, "user:103", "Dina")];
  await act(async () =>
    source.emit("change", { data: '{"chats":{"1":["1/user:103"]}}' }),
  );
  expect(await screen.findByText("Dina")).toBeTruthy();
  expect(listReads(calls)).toBe(before + 1);
  // Another account's change does not reread this list.
  await act(async () =>
    source.emit("change", { data: '{"chats":{"2":["2/user:201"]}}' }),
  );
  expect(listReads(calls)).toBe(before + 1);
});

test("switching accounts shows the other list and is remembered", async () => {
  mockStore.set("enloq.credential", SAVED);
  server();
  await mount();
  await fireEvent.press(
    await screen.findByLabelText("Account: Authored Owner. Switch account"),
  );
  await fireEvent.press(screen.getByText("Second Owner"));
  expect(await screen.findByText("Clara")).toBeTruthy();
  expect(mockStore.get("enloq.account")).toBe("2");
  expect(screen.queryByText("Anna")).toBeNull();
});

test("a remembered account that signed out falls back to the first", async () => {
  mockStore.set("enloq.credential", SAVED);
  mockStore.set("enloq.account", "7");
  server();
  await mount();
  expect(await screen.findByText("Anna")).toBeTruthy();
});

test("a forgotten device drops its credential and asks to pair again", async () => {
  mockStore.set("enloq.credential", SAVED);
  serve(() => ({
    status: 401,
    body: { detail: "This device is no longer trusted. Pair it again." },
  }));
  await mount();
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("unpaired"),
  );
  expect(mockStore.has("enloq.credential")).toBe(false);
  expect(latest!.connection).toEqual({
    status: "unpaired",
    notice: REVOKED_NOTICE,
  });
  expect(mockRedirects).toContain("/pair");
});

test("an unreachable server keeps what was loaded, read-only", async () => {
  mockStore.set("enloq.credential", SAVED);
  server();
  await mount();
  expect(await screen.findByText("Anna")).toBeTruthy();
  online = false;
  await act(async () => {
    await latest!.refresh();
  });
  expect(
    await screen.findByText(
      "enloq.example.com can't be reached. Showing what was last loaded.",
    ),
  ).toBeTruthy();
  expect(screen.getByText("Anna")).toBeTruthy();
  expect(mockStore.has("enloq.credential")).toBe(true);
});

test("after a restart without the server, the last list read shows", async () => {
  mockStore.set("enloq.credential", SAVED);
  server();
  const first = await mount();
  expect(await screen.findByText("Anna rewritten")).toBeTruthy();
  await first.unmount();
  online = false;
  await mount();
  expect(await screen.findByText("Anna")).toBeTruthy();
  expect(screen.getByText("Authored Owner")).toBeTruthy();
  expect(screen.getByText("Anna rewritten")).toBeTruthy();
  expect(
    await screen.findByText(
      "enloq.example.com can't be reached. Showing what was last loaded.",
    ),
  ).toBeTruthy();
});

test("a forgotten phone deletes what it kept for reading offline", async () => {
  mockStore.set("enloq.credential", SAVED);
  server();
  await mount();
  expect(await screen.findByText("Anna")).toBeTruthy();
  expect(mockFiles.size).toBeGreaterThan(0);
  await act(async () => mockEventSources[0]!.emit("revoked"));
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("unpaired"),
  );
  expect(mockFiles.size).toBe(0);
});

test("the server answering again reopens a waiting stream", async () => {
  mockStore.set("enloq.credential", SAVED);
  server();
  await mount();
  expect(await screen.findByText("Anna")).toBeTruthy();
  await act(async () => {
    mockEventSources[0]!.emit("open");
    mockEventSources[0]!.emit("error", { message: "network lost" });
  });
  expect(latest!.connection).toMatchObject({ stream: "reconnecting" });
  online = false;
  await act(() => latest!.api("accounts").catch(() => undefined));
  expect(latest!.connection).toMatchObject({ unreachable: true });
  online = true;
  await act(() => latest!.api("accounts"));
  expect(mockEventSources).toHaveLength(2);
  expect(latest!.connection).toMatchObject({ unreachable: false });
});

test("without a credential the app starts unpaired", async () => {
  serve(() => {
    throw new Error("no request expected");
  });
  await mount();
  await waitFor(() =>
    expect(screen.getByTestId("status")).toHaveTextContent("unpaired"),
  );
  expect(latest!.connection).toEqual({ status: "unpaired", notice: "" });
});
