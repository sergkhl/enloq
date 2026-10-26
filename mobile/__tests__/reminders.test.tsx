import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { Alert } from "react-native";
import {
  mockParams,
  mockPickers,
  mockRouter,
  mockStore,
  reset,
  serve,
} from "./setup";
import { parseRoute, routeHref } from "../../shared/chatRoute";
import {
  keptLine,
  repeatLine,
  reminderDefaults,
  summaryLine,
} from "../../shared/reminders";
import ReminderRoute from "../app/reminder/[account]/[peer]";
import { ReminderBar, RemindersList } from "../src/Reminders";
import { ConnectionProvider, useConnection } from "../src/connection";

function Paired({ children }: { children: React.ReactNode }) {
  return useConnection().connection.status === "paired" ? (
    <>{children}</>
  ) : null;
}

function mount(children: React.ReactNode) {
  return render(
    <ConnectionProvider>
      <Paired>{children}</Paired>
    </ConnectionProvider>,
  );
}

const ID = "a".repeat(32);
const SCHEDULE = "b".repeat(32);
const RULE = {
  ...reminderDefaults("UTC", "English", "2026-10-26"),
  weekday: 0,
  idea: "Ask how the trip went\nwith photos",
};
const ACCOUNTS = [
  {
    account_id: 2,
    name: "Authored second account",
    state: "authorized",
    ai_enabled: true,
  },
  {
    account_id: 3,
    name: "Authored third account",
    state: "authorized",
    ai_enabled: true,
  },
];

beforeEach(() => {
  reset();
  mockStore.set(
    "enloq.credential",
    JSON.stringify({ origin: "https://enloq.example.com", token: "authored" }),
  );
});

test("chat routes name no reminder occurrence, and an old link's goes", () => {
  expect(parseRoute("?tab=reminders", "")).toEqual({ kind: "reminders" });
  expect(routeHref("/", "?account=2&tab=groups", { kind: "reminders" })).toBe(
    "/?tab=reminders",
  );
  expect(parseRoute(`?account=2&reminder=${ID}`, "#42")).toEqual({
    kind: "chat",
    account: 2,
    peer: "user:42",
  });
  expect(
    routeHref("/", `?account=2&reminder=${ID}`, {
      kind: "chat",
      account: 2,
      peer: "user:42",
    }),
  ).toBe("/?account=2#42");
});

test("the shared lines read as the editor and rows show them", () => {
  expect(repeatLine(RULE, "09:00")).toBe("Every Monday · 09:00 digest");
  expect(
    repeatLine({ ...RULE, interval: 2, delivery: "exact", time: "18:30" }, "9"),
  ).toBe("Every 2 weeks on Monday · 18:30");
  expect(
    repeatLine({ ...RULE, repeat: "monthly", month_day: 31 }, "09:00"),
  ).toBe("Monthly on the 31st · 09:00 digest");
  expect(
    repeatLine({ ...RULE, repeat: "yearly", month: 2, month_day: 29 }, "09:00"),
  ).toBe("Yearly on 29 Feb · 09:00 digest");
  const monday = Date.UTC(2026, 10, 2, 9) / 1000;
  expect(summaryLine(RULE, "09:00", monday)).toMatch(
    /^Every Monday · 09:00 digest · next Mon 2 Nov/,
  );
  expect(keptLine({ day: "2026-10-25", state: "kept", count: 12 })).toMatch(
    /^Keeps 12 messages from 25 Oct/,
  );
  expect(keptLine({ day: "2026-10-25", state: "waiting", count: 0 })).toMatch(
    /^Will keep messages from 25 Oct/,
  );
});

test("the tab lists chat-like rows without generation; a pending row opens its chat", async () => {
  const item = {
    id: SCHEDULE,
    account_id: 2,
    account: "Authored second account",
    peer: "user:42",
    title: "Authored contact",
    photo_id: null,
    ai_enabled: true,
    status: "active",
    revision: 1,
    next_at: 2_000_000_000,
    schedule: RULE,
    kept: null,
    occurrence: { id: ID, due_at: 1_999_000_000, calendar_date: "2033-05-15" },
  };
  let dismissed = false;
  const calls = serve((url, init) => {
    if (url.endsWith("/api/accounts"))
      return { status: 200, body: { accounts: ACCOUNTS } };
    if (url.endsWith("/api/reminders"))
      return {
        status: 200,
        body: {
          items: [{ ...item, occurrence: dismissed ? null : item.occurrence }],
        },
      };
    if (url.endsWith(`/reminders/${SCHEDULE}/dismiss`)) {
      expect(JSON.parse(init.body!)).toEqual({ occurrence: ID });
      dismissed = true;
      return { status: 200, body: { items: [], language: null } };
    }
    throw new Error(`unexpected ${url}`);
  });
  await mount(<RemindersList />);
  expect(await screen.findByText("Daily digest · 09:00")).toBeTruthy();
  expect(await screen.findByText("Authored contact")).toBeTruthy();
  // Several accounts: the row names its account.
  expect(screen.getByText("Authored second account")).toBeTruthy();
  expect(screen.getByText("Every Monday · 09:00 digest")).toBeTruthy();
  expect(screen.getByText("Ask how the trip went")).toBeTruthy();
  expect(calls.some((call) => /suggestions|\/open$/.test(call.url))).toBe(
    false,
  );
  await fireEvent.press(
    screen.getByRole("button", { name: /^Authored contact, Authored second/ }),
  );
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/chat/[account]/[peer]",
    params: { account: "2", peer: "user:42" },
  });
  await fireEvent.press(
    screen.getByRole("button", { name: "More for Authored contact" }),
  );
  await fireEvent.press(await screen.findByRole("button", { name: "Edit" }));
  expect(mockRouter.push).toHaveBeenLastCalledWith({
    pathname: "/reminder/[account]/[peer]",
    params: {
      account: "2",
      peer: "user:42",
      title: "Authored contact",
      schedule: SCHEDULE,
    },
  });
  await fireEvent.press(
    screen.getByRole("button", {
      name: "Dismiss reminder for Authored contact",
    }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", {
        name: "Dismiss reminder for Authored contact",
      }),
    ).toBeNull(),
  );
});

test("Add reminder chooses the account, then New Message's picker", async () => {
  serve((url) => {
    if (url.endsWith("/api/accounts"))
      return { status: 200, body: { accounts: ACCOUNTS } };
    if (url.endsWith("/api/reminders"))
      return { status: 200, body: { items: [] } };
    throw new Error(`unexpected ${url}`);
  });
  await mount(<RemindersList />);
  expect(await screen.findByText("No reminders.")).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Add reminder" }));
  await fireEvent.press(
    await screen.findByRole("button", { name: "Authored third account" }),
  );
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/new-message",
    params: { account: "3", purpose: "reminder" },
  });
});

test("the editor chooses a repeat, the day grid, an exact time and a timezone, then saves", async () => {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  serve((url, init) => {
    if (url.endsWith("/api/accounts"))
      return { status: 200, body: { accounts: ACCOUNTS } };
    if (url.endsWith("/reminders/preview")) {
      const body = JSON.parse(init.body!);
      return {
        status: 200,
        body: {
          next_at: 2_000_000_000,
          kept: body.idea.includes("25 oct")
            ? { day: "2026-10-25", state: "waiting", count: 0 }
            : null,
        },
      };
    }
    if (init.method === "POST" && url.endsWith("/chats/user%3A42/reminders")) {
      posts.push({ url, body: JSON.parse(init.body!) });
      return { status: 200, body: { id: SCHEDULE } };
    }
    throw new Error(`unexpected ${url}`);
  });
  Object.assign(mockParams, {
    account: "2",
    peer: "user:42",
    title: "Authored contact",
  });
  await mount(<ReminderRoute />);
  expect(await screen.findByText("Authored contact")).toBeTruthy();
  await fireEvent.press(await screen.findByRole("radio", { name: "Monthly" }));
  await fireEvent.press(screen.getByRole("radio", { name: "Day 31" }));
  await fireEvent.press(screen.getByRole("radio", { name: "Exact time" }));
  await fireEvent.press(screen.getByRole("button", { name: /^Time: / }));
  const picker = mockPickers.at(-1)!;
  expect(picker.mode).toBe("time");
  await act(async () =>
    picker.onChange({ type: "set" }, new Date(2026, 9, 26, 18, 30)),
  );
  // iOS shows its wheels in a sheet with Done (jest-expo runs as iOS).
  await fireEvent.press(screen.getByRole("button", { name: "Done" }));
  await fireEvent.press(
    screen.getByRole("button", { name: /^Timezone and language: / }),
  );
  await fireEvent.changeText(
    await screen.findByLabelText("Search timezones"),
    "tokyo",
  );
  await fireEvent.press(screen.getByRole("button", { name: "Asia/Tokyo" }));
  await fireEvent.press(screen.getAllByRole("button", { name: "Close" })[0]!);
  await fireEvent.changeText(
    screen.getByLabelText("Message Idea"),
    "Ask about 25 oct [kindly]",
  );
  expect(
    await screen.findByText(/^Will keep messages from 25 Oct/),
  ).toBeTruthy();
  expect(await screen.findByText(/^Monthly on the 31st · 18:30/)).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Save reminder" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]!.body).toMatchObject({
    repeat: "monthly",
    month_day: 31,
    delivery: "exact",
    time: "18:30",
    timezone: "Asia/Tokyo",
    idea: "Ask about 25 oct [kindly]",
  });
});

test("Remove reminder asks first", async () => {
  const deleted: string[] = [];
  serve((url, init) => {
    if (url.endsWith("/api/accounts"))
      return { status: 200, body: { accounts: ACCOUNTS } };
    if (url.endsWith(`/reminders/${SCHEDULE}`) && init.method === "DELETE") {
      deleted.push(init.body!);
      return { status: 200, body: { items: [], language: null } };
    }
    if (url.endsWith(`/reminders/${SCHEDULE}`))
      return {
        status: 200,
        body: {
          id: SCHEDULE,
          revision: 4,
          schedule: RULE,
          next_at: 2_000_000_000,
          status: "active",
          occurrence: null,
          kept: { day: "2026-10-25", state: "kept", count: 3 },
        },
      };
    if (url.endsWith("/reminders/preview"))
      return { status: 200, body: { next_at: 2_000_000_000, kept: null } };
    throw new Error(`unexpected ${url}`);
  });
  const answers: string[] = [];
  const alert = jest
    .spyOn(Alert, "alert")
    .mockImplementation((_title, _message, buttons) => {
      const choice = answers.shift();
      buttons?.find((button) => button.text === choice)?.onPress?.();
    });
  Object.assign(mockParams, {
    account: "2",
    peer: "user:42",
    title: "Authored contact",
    schedule: SCHEDULE,
  });
  await mount(<ReminderRoute />);
  expect(await screen.findByText(/^Keeps 3 messages from 25 Oct/)).toBeTruthy();
  answers.push("Cancel");
  await fireEvent.press(
    screen.getByRole("button", { name: "Remove reminder" }),
  );
  expect(alert).toHaveBeenCalledWith(
    "Remove reminder?",
    expect.stringContaining("Kept Messages"),
    expect.any(Array),
  );
  expect(deleted).toHaveLength(0);
  answers.push("Remove");
  await fireEvent.press(
    screen.getByRole("button", { name: "Remove reminder" }),
  );
  await waitFor(() =>
    expect(deleted).toEqual([JSON.stringify({ revision: 4 })]),
  );
  alert.mockRestore();
});

test("the Reminder Bar lists each pending reminder with Dismiss and its editor", async () => {
  const dismissed: string[] = [];
  serve((url, init) => {
    if (url.endsWith("/api/accounts"))
      return { status: 200, body: { accounts: ACCOUNTS } };
    if (url.endsWith(`/reminders/${SCHEDULE}/dismiss`)) {
      dismissed.push(init.body!);
      return { status: 200, body: { items: [], language: null } };
    }
    throw new Error(`unexpected ${url}`);
  });
  const changed = jest.fn();
  await mount(
    <ReminderBar
      account={2}
      peer="user:42"
      title="Authored contact"
      reminders={[
        {
          schedule: SCHEDULE,
          occurrence: ID,
          idea: "",
          due_at: 1_999_000_000,
        },
      ]}
      onChange={changed}
    />,
  );
  await fireEvent.press(
    await screen.findByRole("button", { name: /^Edit reminder: Check-in/ }),
  );
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/reminder/[account]/[peer]",
    params: {
      account: "2",
      peer: "user:42",
      title: "Authored contact",
      schedule: SCHEDULE,
    },
  });
  await fireEvent.press(
    screen.getByRole("button", { name: "Dismiss reminder: Check-in reminder" }),
  );
  await waitFor(() => expect(changed).toHaveBeenCalled());
  expect(dismissed).toEqual([JSON.stringify({ occurrence: ID })]);
});
