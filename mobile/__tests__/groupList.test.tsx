import { fireEvent, render, screen } from "@testing-library/react-native";
import React from "react";
import { mockRouter, mockStore, reset, serve } from "./setup";
import { resumeWithin, retainedLine, reviewFits } from "../../shared/retained";
import Chats from "../app/chats";
import { ConnectionProvider } from "../src/connection";

// ADR 42 AG2: the Groups list beside the Private one.
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
    ai_enabled: true,
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

function row(account: number, peer: string, title: string, preview: string) {
  return {
    account_id: account,
    peer,
    title,
    kind: peer.startsWith("user:") ? "private" : "group",
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
    // Group previews name their speaker in both readings.
    preview: { message_id: 1, date: null, original: preview, ai: preview },
  };
}

const LISTS: Record<string, unknown[]> = {
  "1/private": [row(1, "user:101", "Anna", "Anna original")],
  "1/group": [
    row(1, "chat:42", "Planning group", "Alice: The meeting moved to Friday"),
  ],
  "2/private": [row(2, "user:201", "Clara", "Clara original")],
  "2/group": [],
};

let online = true;
// Groups past the list's seven days that still hold work (ADR 46 CU6).
let retained: unknown[] = [];
function server() {
  return serve((url) => {
    if (!online) return Promise.reject(new TypeError("offline"));
    if (url.endsWith("/accounts/1/retained-groups"))
      return { status: 200, body: retained };
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    if (url.endsWith("/preferences/reading-view"))
      return { status: 200, body: { reading_view: "ai" } };
    const list = /\/accounts\/(\d+)\/chats\?kind=(private|group)$/.exec(url);
    if (list)
      return { status: 200, body: LISTS[`${list[1]}/${list[2]}`] ?? [] };
    throw new Error(`unexpected ${url}`);
  });
}

const mount = () =>
  render(
    <ConnectionProvider>
      <Chats />
    </ConnectionProvider>,
  );

beforeEach(() => {
  reset();
  online = true;
  retained = [];
  mockStore.set("enloq.credential", SAVED);
});

test("the list opens on Private and Groups reads the group list", async () => {
  const calls = server();
  await mount();
  expect(await screen.findByText("Anna")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Private" }).props).toMatchObject({
    accessibilityState: { selected: true },
  });
  expect(calls.some((call) => call.url.includes("kind=group"))).toBe(false);
  await fireEvent.press(screen.getByRole("tab", { name: "Groups" }));
  expect(await screen.findByText("Planning group")).toBeTruthy();
  expect(screen.getByText("Alice: The meeting moved to Friday")).toBeTruthy();
  expect(screen.queryByText("Anna")).toBeNull();
  expect(
    calls.find((call) => call.url.endsWith("/accounts/1/chats?kind=group"))
      ?.headers.Authorization,
  ).toBe("Bearer t0ken");
});

test("each account keeps its own list while the app runs", async () => {
  server();
  await mount();
  await fireEvent.press(await screen.findByRole("tab", { name: "Groups" }));
  expect(await screen.findByText("Planning group")).toBeTruthy();
  await fireEvent.press(
    screen.getByLabelText("Account: Authored Owner. Switch account"),
  );
  await fireEvent.press(screen.getByTestId("account-2"));
  // The second account has not chosen Groups, so it shows its private chats.
  expect(await screen.findByText("Clara")).toBeTruthy();
  await fireEvent.press(screen.getByRole("tab", { name: "Groups" }));
  expect(
    await screen.findByText("No groups in the last seven days."),
  ).toBeTruthy();
  await fireEvent.press(screen.getByRole("tab", { name: "Private" }));
  expect(await screen.findByText("Clara")).toBeTruthy();
  await fireEvent.press(
    screen.getByLabelText("Account: Second Owner. Switch account"),
  );
  await fireEvent.press(screen.getByTestId("account-1"));
  expect(await screen.findByText("Planning group")).toBeTruthy();
});

test("the Groups list stays readable after a restart without the server", async () => {
  server();
  const first = await mount();
  // Both lists are read once, whichever the account showed last.
  await fireEvent.press(await screen.findByRole("tab", { name: "Private" }));
  expect(await screen.findByText("Anna")).toBeTruthy();
  await fireEvent.press(screen.getByRole("tab", { name: "Groups" }));
  expect(await screen.findByText("Planning group")).toBeTruthy();
  await first.unmount();
  online = false;
  await mount();
  // Still Groups: the app has not restarted, only the screen.
  expect(await screen.findByText("Planning group")).toBeTruthy();
  expect(
    await screen.findByText(
      "enloq.example.com can't be reached. Showing what was last loaded.",
    ),
  ).toBeTruthy();
  await fireEvent.press(screen.getByRole("tab", { name: "Private" }));
  expect(await screen.findByText("Anna")).toBeTruthy();
});

test("Groups end with folded retained group work, which opens its group", async () => {
  retained = [
    {
      chat: {
        key: "1/chat:941",
        peer: "chat:941",
        title: "Retained authored discussion",
      },
      reasons: ["draft", "send"],
      draft: true,
      selected_media: false,
      send_count: 1,
      deletion_count: 0,
      cancelled_count: 0,
    },
  ];
  server();
  await mount();
  await fireEvent.press(await screen.findByRole("tab", { name: "Groups" }));
  const toggle = await screen.findByLabelText("Retained group work, 1");
  expect(toggle.props.accessibilityState).toMatchObject({ expanded: false });
  expect(screen.queryByText("Retained authored discussion")).toBeNull();
  await fireEvent.press(toggle);
  expect(screen.getByText("Saved draft · 1 unresolved send")).toBeTruthy();
  await fireEvent.press(screen.getByText("Retained authored discussion"));
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/chat/[account]/[peer]",
    params: { account: "1", peer: "chat:941" },
  });
});

test("the retained line, and a resume review's fit and time", () => {
  expect(
    retainedLine({
      chat: { key: "1/chat:941", peer: "chat:941", title: "Group" },
      draft: false,
      selected_media: true,
      send_count: 2,
      deletion_count: 1,
      cancelled_count: 1,
    }),
  ).toBe(
    "Saved draft · 2 unresolved sends · 1 unresolved deletion · 1 saved cancelled send",
  );
  const review = {
    token: "f".repeat(64),
    expires: 1000,
    account_id: 1,
    peer: "chat:941",
    title: "Group",
    operation: { id: "o" },
  };
  expect(reviewFits(review, 1, "chat:941", "o", 999_000)).toBe(true);
  // Expired, or another group's: no Resume.
  expect(reviewFits(review, 1, "chat:941", "o", 1_000_000)).toBe(false);
  expect(reviewFits({ ...review, peer: "chat:1" }, 1, "chat:941", "o", 0)).toBe(
    false,
  );
  expect(resumeWithin(1000, 880_500)).toBe("Resume within 2:00");
  expect(resumeWithin(1000, 999_000)).toBe("Resume within 0:01");
});
