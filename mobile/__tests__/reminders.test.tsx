import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { mockRouter, mockStore, reset, serve } from "./setup";
import { parseRoute, routeHref } from "../../shared/chatRoute";
import { RemindersList } from "../src/Reminders";
import { ConnectionProvider, useConnection } from "../src/connection";

function PairedReminders() {
  return useConnection().connection.status === "paired" ? (
    <RemindersList />
  ) : null;
}

const ID = "a".repeat(32);
beforeEach(() => {
  reset();
  mockStore.set(
    "enloq.credential",
    JSON.stringify({ origin: "https://enloq.example.com", token: "authored" }),
  );
});

test("member route preserves exact occurrence without binding the list to an account", () => {
  expect(parseRoute("?tab=reminders", "")).toEqual({ kind: "reminders" });
  expect(routeHref("/", "?account=2&tab=groups", { kind: "reminders" })).toBe(
    "/?tab=reminders",
  );
  expect(parseRoute(`?account=2&reminder=${ID}`, "#42")).toEqual({
    kind: "chat",
    account: 2,
    peer: "user:42",
    occurrence: ID,
  });
  expect(parseRoute(`?reminder=${ID}`, "#42")).toEqual({ kind: "invalid" });
});

test("combined list makes no generation calls and reviews the row's exact account", async () => {
  const item = {
    account_id: 2,
    account: "Authored second account",
    peer: "user:42",
    title: "Authored contact",
    ai_enabled: true,
    status: "active",
    revision: 1,
    next_at: 2_000_000_000,
    schedule: { timezone: "UTC" },
    occurrence: { id: ID, due_at: 1_999_000_000, reviewed: false },
  };
  let dismissed = false;
  const calls = serve((url, init) => {
    if (url.endsWith("/api/accounts"))
      return {
        status: 200,
        body: {
          accounts: [
            {
              account_id: 2,
              name: "Authored second account",
              state: "authorized",
              ai_enabled: true,
            },
          ],
        },
      };
    if (url.endsWith("/api/reminders/settings"))
      return {
        status: 200,
        body: { revision: 1, time: "09:00", timezone: "UTC" },
      };
    if (url.endsWith("/api/reminders"))
      return {
        status: 200,
        body: {
          items: [{ ...item, occurrence: dismissed ? null : item.occurrence }],
        },
      };
    if (url.endsWith("/reminder/dismiss")) {
      expect(JSON.parse(init.body!)).toEqual({ occurrence: ID });
      dismissed = true;
      return { status: 200, body: {} };
    }
    throw new Error(`unexpected ${url}`);
  });
  await render(
    <ConnectionProvider>
      <PairedReminders />
    </ConnectionProvider>,
  );
  expect(await screen.findByText("All accounts")).toBeTruthy();
  expect(await screen.findByText("Authored second account")).toBeTruthy();
  expect(
    calls.some((call) => /suggestions|reminder\/review/.test(call.url)),
  ).toBe(false);
  await fireEvent.press(
    screen.getByRole("button", { name: "Review reminder" }),
  );
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/chat/[account]/[peer]",
    params: { account: "2", peer: "user:42", reminder: ID },
  });
  await fireEvent.press(screen.getByRole("button", { name: "Dismiss" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Review reminder" }),
    ).toBeNull(),
  );
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
});
