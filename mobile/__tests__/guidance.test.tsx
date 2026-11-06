import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { BackHandler } from "react-native";
import { mockParams, mockRouter, mockStore, reset, serve } from "./setup";
import AiPage from "../app/ai/[account]/[peer]";
import GuidanceField from "../app/guidance/[field]";
import Ai from "../app/settings/ai";
import { ConnectionProvider } from "../src/connection";

// ADR 45 ST3: Processing guidance, its paused roles in Settings → AI and each
// field's editor, opened from the AI Menu page of the control it shapes
// (ADR 62 Q11, Q17, Q18).
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});

const DEFAULTS: Record<string, string> = {
  faithfulness: "Keep every fact.",
  incoming: "Rewrite clearly.\nKeep the voice.",
  outgoing: "Polish the draft.",
  emotion: "Name the emotion.",
  tone_neutral: "Neutral.",
  tone_professional: "Professional.",
  tone_friendly: "Friendly.",
  memory: "Note what the person cares about.",
  account_memory: "Note what the owner cares about.",
  suggestions: "Suggest replies.",
  idioms: "Use idioms.",
  group_briefing: "Summarize the day.",
};

let guidance: {
  version: number;
  defaults_revision: number;
  fields: Record<string, string>;
  defaults: Record<string, string>;
  paused: string[];
};
let posts: { path: string; body: unknown }[];
let role: "owner" | "member";
let calls: { url: string }[];

function budget() {
  return {
    day: "2027-01-15",
    limit: 1e9,
    spent: 0,
    calls: 0,
    unknown: 0,
    flat_unknown: 0,
    reached: false,
    resets_at: 1_800_000_000,
    changes: [],
  };
}

beforeEach(() => {
  reset();
  mockStore.set("enloq.credential", SAVED);
  posts = [];
  role = "owner";
  guidance = {
    version: 7,
    defaults_revision: 5,
    fields: { ...DEFAULTS, tone_professional: "My own style rules." },
    defaults: DEFAULTS,
    paused: [],
  };
  calls = serve((url, init) => {
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
    if (path === "members/me")
      return {
        status: 200,
        body: { id: "m", role, state: "active", name: "", agreement: null },
      };
    if (path.startsWith("settings")) {
      if (init.method === "POST") {
        const body = JSON.parse(init.body!);
        posts.push({ path, body });
        if (path === "settings/guidance") {
          for (const [name, text] of Object.entries(
            body.fields as Record<string, string | null>,
          ))
            guidance.fields[name] = text ?? DEFAULTS[name]!;
          guidance.version++;
          guidance.paused = [];
        }
        if (path === "settings/guidance/keep")
          guidance.paused = guidance.paused.filter((r) => r !== body.role);
      }
      return {
        status: 200,
        body: JSON.parse(
          JSON.stringify({
            budget: budget(),
            group_budget: budget(),
            concurrency: 4,
            processing_threshold: 200,
            memory: true,
            suggestions: true,
            // Only the owner reads Processing Guidance (ADR 48 Q13).
            guidance: role === "owner" ? guidance : null,
          }),
        ),
      };
    }
    throw new Error(`unexpected ${url}`);
  });
});

const mount = (element: React.ReactElement) =>
  render(<ConnectionProvider>{element}</ConnectionProvider>);

test("Settings → AI holds the paused roles with Keep and Reset, and no Guidance row", async () => {
  guidance.paused = ["incoming_rewrite", "chat_memory"];
  await mount(<Ai />);
  expect(
    await screen.findByText(
      /^Incoming rewrites failed the output format three times/,
    ),
  ).toBeTruthy();
  expect(screen.queryByText("Guidance")).toBeNull();
  await fireEvent.press(
    screen.getByRole("button", {
      name: "Keep guidance for Memory updates",
    }),
  );
  await waitFor(() =>
    expect(posts).toEqual([
      { path: "settings/guidance/keep", body: { role: "chat_memory" } },
    ]),
  );
  guidance.paused = ["incoming_rewrite"];
  await fireEvent.press(
    await screen.findByRole("button", {
      name: "Reset guidance for Incoming rewrites",
    }),
  );
  await waitFor(() =>
    expect(posts.at(-1)).toEqual({
      path: "settings/guidance",
      body: { fields: { faithfulness: null, incoming: null, calming: null } },
    }),
  );
  await waitFor(() =>
    expect(screen.queryByText(/failed the output format/)).toBeNull(),
  );
});

describe("an AI Menu page's Guidance", () => {
  beforeEach(() => {
    mockParams.account = "1";
    mockParams.peer = "user:101";
  });
  const fields = () =>
    screen
      .getAllByRole("button")
      .map((row) => row.props.accessibilityLabel as string)
      .filter((label) => /for all chats$/i.test(label ?? ""));

  test.each([
    [
      "style",
      [
        "Response suggestions, For all chats",
        "Friendly, For all chats",
        "Professional, Edited, for all chats",
        "Idioms, For all chats",
      ],
    ],
    ["suggestions", ["When to suggest, For all chats"]],
    ["emotion", ["Emotion extraction, For all chats"]],
    [
      "calming",
      [
        "Soften hostile messages, For all chats",
        "Faithfulness rules, For all chats",
      ],
    ],
    [
      "media",
      [
        "Incoming rewriting, For all chats",
        "Faithfulness rules, For all chats",
      ],
    ],
    [
      "remember",
      ["Person memory, For all chats", "Account memory, For all chats"],
    ],
  ])(
    "%s's page lists its fields, each for all chats",
    async (control, names) => {
      mockParams.control = control;
      await mount(<AiPage />);
      expect(
        await screen.findByText(/^Version 7\. Changes apply/),
      ).toBeTruthy();
      expect(fields()).toEqual(names);
    },
  );

  test("a field opens its editor", async () => {
    mockParams.control = "style";
    await mount(<AiPage />);
    await fireEvent.press(
      await screen.findByRole("button", {
        name: "Professional, Edited, for all chats",
      }),
    );
    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: "/guidance/[field]",
      params: { field: "tone_professional" },
    });
  });

  test("a member's page has none (ADR 48 Q13)", async () => {
    role = "member";
    mockParams.control = "style";
    await mount(<AiPage />);
    expect(await screen.findAllByRole("radio")).toHaveLength(3);
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith("/api/members/me"))).toBe(
        true,
      ),
    );
    await act(async () => undefined);
    expect(screen.queryByText("Guidance")).toBeNull();
    expect(fields()).toEqual([]);
  });
});

test("a field saves its text, then resets after a confirmation", async () => {
  mockParams.field = "tone_professional";
  await mount(<GuidanceField />);
  const box = await screen.findByLabelText("Professional");
  expect(box.props.value).toBe("My own style rules.");
  expect(screen.getByText("19/4000")).toBeTruthy();
  // One text for all chats (ADR 62 Q11).
  expect(screen.getByText("For all chats · Version 7")).toBeTruthy();
  const save = () => screen.getByRole("button", { name: "Save" });
  expect(save().props.accessibilityState).toMatchObject({ disabled: true });
  await fireEvent.changeText(box, "Shorter.");
  await fireEvent.press(save());
  await waitFor(() =>
    expect(posts).toEqual([
      {
        path: "settings/guidance",
        body: { fields: { tone_professional: "Shorter." } },
      },
    ]),
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Reset to default" }),
  );
  expect(screen.getByText(/^Reset Professional to its default\?/)).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Confirm reset" }));
  await waitFor(() =>
    expect(posts.at(-1)).toEqual({
      path: "settings/guidance",
      body: { fields: { tone_professional: null } },
    }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Professional").props.value).toBe(
      "Professional.",
    ),
  );
  expect(
    screen.getByRole("button", { name: "Reset to default" }).props
      .accessibilityState,
  ).toMatchObject({ disabled: true });
});

test("emptied faithfulness rules warn, and the default text unfolds", async () => {
  mockParams.field = "faithfulness";
  await mount(<GuidanceField />);
  await fireEvent.changeText(
    await screen.findByLabelText("Faithfulness rules"),
    "  ",
  );
  expect(
    screen.getByText(/^Without faithfulness rules, rewrites may drop/),
  ).toBeTruthy();
  await fireEvent.press(screen.getByText("Default text"));
  expect(screen.getAllByText("Keep every fact.").length).toBeGreaterThan(0);
});

test("leaving with unsaved text asks first, from Back and from Android's back", async () => {
  const added = jest.spyOn(BackHandler, "addEventListener");
  mockParams.field = "incoming";
  await mount(<GuidanceField />);
  await fireEvent.changeText(
    await screen.findByLabelText("Incoming rewriting"),
    "Unsaved idea",
  );
  await fireEvent.press(screen.getByRole("button", { name: "Back" }));
  expect(
    screen.getByText("Discard your changes to Incoming rewriting?"),
  ).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Keep editing" }));
  expect(screen.getByLabelText("Incoming rewriting").props.value).toBe(
    "Unsaved idea",
  );
  const handler = added.mock.calls.at(-1)![1] as () => boolean;
  let handled = false;
  await act(async () => {
    handled = handler();
  });
  expect(handled).toBe(true);
  expect(
    screen.getByText("Discard your changes to Incoming rewriting?"),
  ).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Discard" }));
  expect(mockRouter.replace).toHaveBeenCalledWith("/chats");
  added.mockRestore();
});

test("the Idioms field says what it shapes (ADR 56 Q6)", async () => {
  mockParams.field = "idioms";
  await mount(<GuidanceField />);
  expect((await screen.findByLabelText("Idioms")).props.value).toBe(
    "Use idioms.",
  );
  expect(
    screen.getByText("How suggestions are written in chats set to Idioms."),
  ).toBeTruthy();
});

test("an unknown field says so", async () => {
  mockParams.field = "nonsense";
  await mount(<GuidanceField />);
  expect(
    await screen.findByText("This guidance field doesn't exist."),
  ).toBeTruthy();
});
