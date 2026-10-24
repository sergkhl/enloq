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
import Ai from "../app/settings/ai";
import Guidance from "../app/settings/guidance";
import GuidanceField from "../app/settings/guidance/[field]";
import { ConnectionProvider } from "../src/connection";

// ADR 45 ST3: Processing guidance, its paused roles and each field's editor.
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
  guidance = {
    version: 7,
    defaults_revision: 5,
    fields: { ...DEFAULTS, tone_professional: "My own style rules." },
    defaults: DEFAULTS,
    paused: [],
  };
  serve((url, init) => {
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
            guidance,
          }),
        ),
      };
    }
    throw new Error(`unexpected ${url}`);
  });
});

const mount = (element: React.ReactElement) =>
  render(<ConnectionProvider>{element}</ConnectionProvider>);

test("Settings → AI's Guidance row names the version and a pause", async () => {
  guidance.paused = ["incoming_rewrite"];
  await mount(<Ai />);
  await fireEvent.press(
    await screen.findByRole("button", {
      name: "Guidance, Paused, Version 7",
    }),
  );
  expect(mockRouter.push).toHaveBeenCalledWith("/settings/guidance");
});

test("the fields are grouped, each saying whether it was edited", async () => {
  await mount(<Guidance />);
  expect(await screen.findByText(/^Version 7\. Changes apply/)).toBeTruthy();
  expect(screen.getByText("Rewriting")).toBeTruthy();
  expect(screen.getByText("Suggestion styles")).toBeTruthy();
  expect(screen.getByText("Other work")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Professional, Edited" }),
  ).toBeTruthy();
  // Each memory has its own field (ADR 55 Q24).
  expect(
    screen.getByRole("button", { name: "Person memory, Default" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Account memory, Default" }),
  ).toBeTruthy();
  // Idiom sets have their own field, after Response suggestions (ADR 56 Q6).
  const rows = screen
    .getAllByRole("button")
    .map((row) => row.props.accessibilityLabel);
  expect(rows.indexOf("Idioms, Default")).toBe(
    rows.indexOf("Response suggestions, Default") + 1,
  );
  // The first line previews each field.
  expect(screen.getByText("Rewrite clearly.")).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", { name: "Incoming rewriting, Default" }),
  );
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/settings/guidance/[field]",
    params: { field: "incoming" },
  });
});

test("a paused role is kept or reset from its notice", async () => {
  guidance.paused = ["incoming_rewrite", "chat_memory"];
  await mount(<Guidance />);
  expect(
    await screen.findByText(
      /^Incoming rewrites failed the output format three times/,
    ),
  ).toBeTruthy();
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

test("a field saves its text, then resets after a confirmation", async () => {
  mockParams.field = "tone_professional";
  await mount(<GuidanceField />);
  const box = await screen.findByLabelText("Professional");
  expect(box.props.value).toBe("My own style rules.");
  expect(screen.getByText("19/4000")).toBeTruthy();
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
  expect(mockRouter.replace).toHaveBeenCalledWith("/settings/guidance");
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
