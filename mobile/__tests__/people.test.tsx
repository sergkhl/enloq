import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { Alert } from "react-native";
import {
  mockNotifications,
  mockRouter,
  mockStore,
  reset,
  serve,
} from "./setup";
import Budget from "../app/settings/budget";
import People from "../app/settings/people";
import Settings from "../app/settings";
import { ConnectionProvider } from "../src/connection";

// ADR 48 MB5: the owner's People and a member's own Settings.
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
const budget = (limit: number, spent = 0) => ({
  day: "2026-10-17",
  limit,
  spent,
  calls: 0,
  unknown: 0,
  flat_unknown: 10_000_000,
  reached: false,
  resets_at: 2_000_000_000,
  changes: [],
});
const ANNA = {
  id: "m1",
  name: "Authored Anna",
  username: "anna",
  phone: "••• 0123",
  requested_at: 1_800_000_000,
};
const MEMBER = {
  member: "m1",
  name: "Authored Anna",
  username: "anna",
  phone: "••• 0123",
  approved_at: 1_800_000_100,
  accounts: 1,
  last_active: null,
  budget: budget(500_000_000),
  group_budget: budget(500_000_000),
};
const ACCOUNTS = {
  mode: "personal",
  timezone: "UTC",
  budget: [],
  login_available: true,
  accounts: [],
};
const SETTINGS = {
  budget: budget(500_000_000, 120_000_000),
  group_budget: budget(500_000_000),
  concurrency: 4,
  processing_threshold: 200,
  memory: true,
  suggestions: true,
  guidance: null,
};
const me = (role: string) => ({
  status: 200,
  body: {
    id: role === "owner" ? "owner" : "m1",
    role,
    state: "active",
    name: "",
    agreement: null,
  },
});

beforeEach(() => {
  reset();
  mockStore.set("enloq.credential", SAVED);
});

const mount = (element: React.ReactElement) =>
  render(<ConnectionProvider>{element}</ConnectionProvider>);

test("the owner approves a request, sets the member's budgets and removes them", async () => {
  let people = { requests: [ANNA], members: [] as unknown[] };
  const calls = serve((url, init) => {
    if (url.endsWith("/api/members/me")) return me("owner");
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    if (url.endsWith("/people/m1/approve")) {
      people = { requests: [], members: [MEMBER] };
      return { status: 200, body: people };
    }
    if (url.endsWith("/people/m1/budget"))
      return {
        status: 200,
        body: { member: "m1", budget: budget(1), group_budget: budget(1) },
      };
    if (url.endsWith("/people/m1/remove")) {
      people = { requests: [], members: [] };
      return { status: 200, body: { confirmed: true } };
    }
    if (url.endsWith("/api/people")) return { status: 200, body: people };
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  jest
    .spyOn(Alert, "alert")
    .mockImplementation((_title, _message, buttons) =>
      buttons?.find((button) => button.style === "destructive")?.onPress?.(),
    );
  await mount(<People />);
  expect(await screen.findByText("Authored Anna")).toBeTruthy();
  expect(screen.getByText(/@anna · ••• 0123/)).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", { name: "Approve Authored Anna" }),
  );
  expect(
    await screen.findByText(/Daily Budget: \$0\.0000 of \$0\.50 today/),
  ).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", { name: "Edit Authored Anna's budgets" }),
  );
  await fireEvent.changeText(
    screen.getByLabelText("Authored Anna Daily Budget per day in dollars"),
    "1.25",
  );
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(
      calls.filter((call) => call.url.endsWith("/people/m1/budget")),
    ).toHaveLength(2),
  );
  const [daily, group] = calls
    .filter((call) => call.url.endsWith("/people/m1/budget"))
    .map((call) => JSON.parse(call.body!));
  expect(daily).toEqual({ family: "daily", limit_usd: "1.25" });
  expect(group).toEqual({ family: "group_briefing", limit_usd: "0.50" });
  await fireEvent.press(
    await screen.findByRole("button", { name: "Remove Authored Anna" }),
  );
  expect(
    await screen.findByText(/Nobody else uses Enloq on this server yet/),
  ).toBeTruthy();
  const removed = calls.find((call) => call.url.endsWith("/people/m1/remove"))!;
  expect(JSON.parse(removed.body!)).toEqual({ confirm: true });
});

test("the owner's Settings shows People with the requests waiting", async () => {
  serve((url, init) => {
    if (url.endsWith("/api/members/me")) return me("owner");
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    if (url.endsWith("/api/people"))
      return { status: 200, body: { requests: [ANNA], members: [] } };
    if (url.endsWith("/api/auth/browsers")) return { status: 200, body: [] };
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  await mount(<Settings />);
  const row = await screen.findByRole("button", {
    name: "People, 1 waiting, 0",
  });
  await fireEvent.press(row);
  expect(mockRouter.push).toHaveBeenCalledWith("/settings/people");
  expect(screen.getByRole("button", { name: "AI" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Leave Enloq" })).toBeNull();
});

test("a member's Settings has their read-only budgets and Leave, without AI or People", async () => {
  const calls = serve((url, init) => {
    if (url.endsWith("/api/members/me")) return me("member");
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    if (url.endsWith("/api/settings")) return { status: 200, body: SETTINGS };
    if (url.endsWith("/api/auth/browsers")) return { status: 200, body: [] };
    if (url.endsWith("/api/members/me/leave"))
      return { status: 200, body: { confirmed: true } };
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  jest
    .spyOn(Alert, "alert")
    .mockImplementation((_title, _message, buttons) =>
      buttons?.find((button) => button.style === "destructive")?.onPress?.(),
    );
  const view = await mount(<Settings />);
  expect(
    await screen.findByRole("button", { name: "Leave Enloq" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "AI" })).toBeNull();
  expect(screen.queryByRole("button", { name: /^People/ })).toBeNull();
  await view.rerender(
    <ConnectionProvider>
      <Budget />
    </ConnectionProvider>,
  );
  expect(
    await screen.findByText("The server's owner sets your budgets."),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Change Daily Budget limit" }),
  ).toBeNull();
  await view.rerender(
    <ConnectionProvider>
      <Settings />
    </ConnectionProvider>,
  );
  await fireEvent.press(
    await screen.findByRole("button", { name: "Leave Enloq" }),
  );
  await waitFor(() =>
    expect(calls.some((call) => call.url.endsWith("/members/me/leave"))).toBe(
      true,
    ),
  );
  await waitFor(() =>
    expect(mockStore.get("enloq.credential")).toBeUndefined(),
  );
});
