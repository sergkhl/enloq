import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
import React from "react";
import { mockEventSources, mockParams, mockStore, reset, serve } from "./setup";
import Ai from "../app/settings/ai";
import Budget from "../app/settings/budget";
import { ConnectionProvider } from "../src/connection";
import { budgetAmount, characterCount } from "../src/settings";

// ADR 45 ST2: the two budgets and the processing controls, each applied for
// every account and reconciled through a fresh read after each write.
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
const DAY = 1_800_000_000;

function budget(limit: number, spent: number, extra = {}) {
  return {
    day: "2027-01-15",
    limit: limit * 1e9,
    spent: spent * 1e9,
    calls: 14,
    unknown: 0,
    flat_unknown: 0,
    reached: spent >= limit,
    resets_at: DAY,
    changes: [],
    ...extra,
  };
}

let state: Record<string, unknown>;
let posts: { path: string; body: unknown }[];
let refusals: Record<string, string>;
let online: boolean;
let reads: number;

beforeEach(() => {
  reset();
  mockStore.set("enloq.credential", SAVED);
  posts = [];
  refusals = {};
  online = true;
  reads = 0;
  state = {
    budget: budget(1, 0.0123, {
      changes: [{ at: DAY - 3600, old: 2e9, new: 1e9 }],
    }),
    group_budget: budget(2, 0),
    concurrency: 4,
    processing_threshold: 200,
    memory: true,
    suggestions: true,
    guidance: {
      version: 3,
      defaults_revision: 5,
      fields: {},
      defaults: {},
      paused: [],
    },
  };
  serve((url, init) => {
    if (!online) return Promise.reject(new TypeError("offline"));
    const path = url.replace("https://enloq.example.com/api/", "");
    if (path === "accounts")
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
    if (path === "auth/browsers") return { status: 200, body: [] };
    if (path === "settings" && init.method === "GET") {
      reads++;
      // A fresh object per answer, as a real response is.
      return { status: 200, body: JSON.parse(JSON.stringify(state)) };
    }
    if (path.startsWith("settings/") && init.method === "POST") {
      const body = JSON.parse(init.body!);
      posts.push({ path, body });
      if (refusals[path])
        return { status: 400, body: { detail: refusals[path] } };
      if (path === "settings/budget")
        state.budget = budget(Number(body.limit_usd), 0.0123);
      if (path === "settings/group-budget")
        state.group_budget = budget(Number(body.limit_usd), 0);
      if (path === "settings/concurrency") state.concurrency = body.limit;
      if (path === "settings/processing-threshold")
        state.processing_threshold = body.limit;
      if (path === "settings/memory") state.memory = body.enabled;
      if (path === "settings/suggestions") state.suggestions = body.enabled;
      return { status: 200, body: JSON.parse(JSON.stringify(state)) };
    }
    throw new Error(`unexpected ${url}`);
  });
});

// Settings → Budget and Settings → AI (ADR 47 Q6), mounted side by side.
const mount = () =>
  render(
    <ConnectionProvider>
      <Budget />
      <Ai />
    </ConnectionProvider>,
  );

const card = (name: string) => within(screen.getByLabelText(name));

test("amounts and character counts are checked as the service checks them", () => {
  expect(budgetAmount("2")).toBe("2.00");
  expect(budgetAmount(" $1,5 ")).toBe("1.50");
  expect(budgetAmount("0")).toBe("0.00");
  expect(budgetAmount("10.00")).toBe("10.00");
  for (const wrong of ["", "10.01", "1.234", "-1", "abc", "1e1"])
    expect(budgetAmount(wrong)).toBeNull();
  expect(characterCount("0")).toBe(0);
  expect(characterCount("10000")).toBe(10000);
  for (const wrong of ["", "10001", "1.5", "-3", "two"])
    expect(characterCount(wrong)).toBeNull();
});

test("a budget shows today's spending and changes its limit", async () => {
  await mount();
  await screen.findByLabelText("Daily Budget");
  expect(
    card("Daily Budget").getByText(/of \$1\.00 today · 14 calls/),
  ).toBeTruthy();
  expect(card("Daily Budget").getByText("$0.0123")).toBeTruthy();
  expect(card("Daily Budget").getByText(/^Resets /)).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", { name: "Change Daily Budget limit" }),
  );
  const field = screen.getByLabelText("Daily Budget per day in dollars");
  expect(field.props.value).toBe("1.00");
  await fireEvent.changeText(field, "12");
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByText("$0 to $10, in whole cents.")).toBeTruthy();
  expect(posts).toEqual([]);
  await fireEvent.changeText(field, "0");
  expect(screen.getByText(/^At \$0, new private-chat AI work/)).toBeTruthy();
  await fireEvent.changeText(field, "2.5");
  const before = reads;
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(card("Daily Budget").getByText(/of \$2\.50 today/)).toBeTruthy(),
  );
  expect(posts).toEqual([
    { path: "settings/budget", body: { limit_usd: "2.50" } },
  ]);
  expect(reads).toBe(before + 1);
});

test("the group budget has its own route, and a reached budget says until when", async () => {
  state.group_budget = budget(2, 2);
  await mount();
  await screen.findByLabelText("Group Briefing Budget");
  expect(
    card("Group Briefing Budget").getByText(
      /^Today's group briefing budget is reached\. New briefing work waits until .+ or a higher limit\.$/,
    ),
  ).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", { name: "Change Group Briefing Budget limit" }),
  );
  await fireEvent.changeText(
    screen.getByLabelText("Group Briefing Budget per day in dollars"),
    "3",
  );
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(posts).toEqual([
      { path: "settings/group-budget", body: { limit_usd: "3.00" } },
    ]),
  );
});

test("recent changes unfold under their budget", async () => {
  await mount();
  await fireEvent.press(
    await screen.findByRole("button", {
      name: "Recent changes to the Daily Budget",
    }),
  );
  expect(card("Daily Budget").getByText(/· \$2\.00 → \$1\.00$/)).toBeTruthy();
});

test("a refused limit says why and keeps the typed amount", async () => {
  refusals["settings/budget"] = "Budget changes are paused";
  await mount();
  await fireEvent.press(
    await screen.findByRole("button", { name: "Change Daily Budget limit" }),
  );
  const field = screen.getByLabelText("Daily Budget per day in dollars");
  await fireEvent.changeText(field, "3");
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Budget changes are paused")).toBeTruthy();
  expect(field.props.value).toBe("3");
});

test("minimum characters, model calls and both switches apply for every account", async () => {
  await mount();
  await fireEvent.press(
    await screen.findByRole("button", {
      name: "Minimum characters, 200. Change",
    }),
  );
  const field = screen.getByLabelText("Minimum characters");
  await fireEvent.changeText(field, "10001");
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByText("A whole number from 0 to 10,000.")).toBeTruthy();
  await fireEvent.changeText(field, "350");
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  expect(
    await screen.findByRole("button", {
      name: "Minimum characters, 350. Change",
    }),
  ).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", { name: "More model calls at once" }),
  );
  expect(await screen.findByLabelText("5 model calls at once")).toBeTruthy();
  // Memory, for both memories since ADR 55 Q7.
  await fireEvent(screen.getByLabelText("Memory"), "valueChange", false);
  await fireEvent(screen.getByLabelText("Suggestions"), "valueChange", false);
  await waitFor(() =>
    expect(posts).toEqual([
      { path: "settings/processing-threshold", body: { limit: 350 } },
      { path: "settings/concurrency", body: { limit: 5 } },
      { path: "settings/memory", body: { enabled: false } },
      { path: "settings/suggestions", body: { enabled: false } },
    ]),
  );
});

test("the stepper stops at one and at eight", async () => {
  state.concurrency = 8;
  await mount();
  expect(
    (await screen.findByRole("button", { name: "More model calls at once" }))
      .props.accessibilityState,
  ).toMatchObject({ disabled: true });
  state.concurrency = 1;
  await act(async () =>
    mockEventSources[0]!.emit("change", { data: '{"shared":["settings"]}' }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Fewer model calls at once" }).props
        .accessibilityState,
    ).toMatchObject({ disabled: true }),
  );
});

test("another client's change rereads them and keeps an unsaved edit", async () => {
  await mount();
  await fireEvent.press(
    await screen.findByRole("button", { name: "Change Daily Budget limit" }),
  );
  await fireEvent.changeText(
    screen.getByLabelText("Daily Budget per day in dollars"),
    "4",
  );
  state.suggestions = false;
  await act(async () =>
    mockEventSources[0]!.emit("change", { data: '{"shared":["settings"]}' }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Suggestions").props.value).toBe(false),
  );
  expect(
    screen.getByLabelText("Daily Budget per day in dollars").props.value,
  ).toBe("4");
});

test("an unreachable server leaves the last values shown and unchangeable", async () => {
  await mount();
  await screen.findByLabelText("Daily Budget");
  online = false;
  await act(async () =>
    mockEventSources[0]!.emit("change", { data: '{"shared":["settings"]}' }),
  );
  expect(
    await screen.findAllByText(
      "Settings can be changed once the server answers.",
    ),
  ).toHaveLength(2);
  expect(card("Daily Budget").getByText(/of \$1\.00 today/)).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Change Daily Budget limit" }).props
      .accessibilityState,
  ).toMatchObject({ disabled: true });
  expect(screen.getByLabelText("Suggestions").props.disabled).toBe(true);
});

test("with nothing read yet, Settings waits for the server", async () => {
  online = false;
  await mount();
  expect(
    await screen.findAllByText("Settings show once the server answers."),
  ).toHaveLength(2);
});

test("a budget message's link outlines its budget for a moment", async () => {
  jest.useFakeTimers();
  try {
    mockParams.section = "group";
    await mount();
    const group = await screen.findByLabelText("Group Briefing Budget");
    const outlined = () =>
      [group.props.style].flat(3).some((style) => style?.borderWidth === 2);
    expect(outlined()).toBe(true);
    expect(
      [screen.getByLabelText("Daily Budget").props.style]
        .flat(3)
        .some((style) => style?.borderWidth === 2),
    ).toBe(false);
    await act(async () => jest.advanceTimersByTime(2600));
    expect(
      [screen.getByLabelText("Group Briefing Budget").props.style]
        .flat(3)
        .some((style) => style?.borderWidth === 2),
    ).toBe(false);
  } finally {
    jest.useRealTimers();
  }
});
