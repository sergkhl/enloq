import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
// The module doubles first, so these imports get them.
import { mockParams, mockStore, reset, serve } from "./setup";
import React from "react";
import { messageDate } from "../../shared/messageTime";
import AboutRoute from "../app/about/[account]";
import ProfileRoute from "../app/profile/[account]/[peer]";
import type { Schemas } from "../src/api";
import { writeCached } from "../src/cache";
import { ConnectionProvider } from "../src/connection";

// ADR 55: Remember this chat, the person's Cares about and Traits in the
// Correspondent Profile, and About you, opened from Manage accounts.

type Traits = Schemas["TraitsView"];
type Scale = Schemas["TraitScale"];
type AccountMemory = Schemas["AccountMemoryView"];

const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
const JUDGED = 1_800_000_000;
const EARLIER = JUDGED - 9 * 86400;

const LEVELS: Record<string, string[]> = {
  humor: ["Never jokes", "Rarely", "Sometimes", "Often", "Constantly"],
  warmth: ["Cold", "Reserved", "Neutral", "Friendly", "Very warm"],
  directness: [
    "Very indirect",
    "Mostly indirect",
    "Mixed",
    "Mostly direct",
    "Very direct",
  ],
  formality: ["Very casual", "Casual", "Neutral", "Polite", "Formal"],
};
const TITLES: Record<string, string> = {
  humor: "Humor",
  warmth: "Warmth",
  directness: "Directness",
  formality: "Formality",
};

// One trait of the starter list, unjudged unless a test says.
function scale(trait: string, extra: Partial<Scale> = {}): Scale {
  return {
    trait,
    title: TITLES[trait]!,
    value: null,
    label: null,
    probability: null,
    judged: null,
    source: null,
    current: false,
    options: LEVELS[trait]!.map((label, level) => ({
      value: String(level),
      label,
    })),
    ...extra,
  };
}

function emptyTraits(): Traits {
  return {
    revision: 3,
    enabled: true,
    scales: Object.keys(LEVELS).map((trait) => scale(trait)),
    notes: [],
    habits: null,
    error: null,
  };
}

const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

// The service's answer to an owner's edit of traits: it applies at the
// revision the owner saw and moves the revision on.
function editTraits(
  traits: Traits,
  suffix: string,
  body: { revision: number; value?: string | null; text?: string },
) {
  if (body.revision !== traits.revision)
    return {
      status: 409,
      body: { detail: "Traits changed; reload before editing" },
    };
  traits.revision += 1;
  const set = /^\/scales\/(\w+)$/.exec(suffix);
  if (set) {
    const changed = traits.scales.find((item) => item.trait === set[1])!;
    const value = body.value ?? null;
    const option = changed.options.find((item) => item.value === value);
    Object.assign(changed, {
      value,
      label: option ? option.label : null,
      probability: null,
      source: "owner",
      current: false,
    });
  } else if (suffix === "/notes")
    traits.notes.push({
      id: `n${traits.revision}`,
      text: body.text!,
      confidence: null,
      source: "owner",
      created: JUDGED,
      citations: [],
    });
  else if (suffix.endsWith("/remove"))
    traits.notes = traits.notes.filter(
      (note) => `/notes/${note.id}/remove` !== suffix,
    );
  else
    Object.assign(
      traits.notes.find((note) => `/notes/${note.id}` === suffix)!,
      { text: body.text, source: "owner", confidence: null },
    );
  return { status: 200, body: copy(traits) };
}

const ACCOUNTS = {
  mode: "personal",
  timezone: "UTC",
  budget: [],
  login_available: true,
  accounts: [
    {
      account_id: 1,
      name: "Owner",
      username: "owner",
      phone: "",
      state: "authorized",
      connected: true,
      status: "Connected",
      ai_enabled: true,
      photo_id: null,
    },
  ],
};

describe("the Correspondent Profile's memory and traits", () => {
  let traits: Traits;
  let memory: Schemas["MemoryView"];
  let remember: boolean;
  let posts: { url: string; body: unknown }[];

  beforeEach(() => {
    reset();
    posts = [];
    // Off by default since ADR 56 Q9.
    remember = false;
    traits = emptyTraits();
    memory = {
      entries: [],
      revision: 1,
      coverage: 0,
      error: null,
      enabled: true,
      waiting: 0,
      updating: false,
    };
    mockStore.set("enloq.credential", SAVED);
    mockParams.account = "1";
    mockParams.peer = "user:101";
    writeCached("chat:1/user:101", {
      timezone: "UTC",
      chat: { account_id: 1, peer: "user:101", title: "Anna", photo_id: null },
      messages: [],
      blocks: [],
    });
    serve((url, init) => {
      if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
      if (url.endsWith("/profile"))
        return {
          status: 200,
          body: { name: "Anna Petrova", username: "", phone: null, bio: "" },
        };
      if (url.endsWith("/calming"))
        return { status: 200, body: { enabled: true } };
      if (url.endsWith("/suggestions/settings"))
        return { status: 200, body: { automatic: true, style: "friendly" } };
      if (url.endsWith("/remember")) {
        if (init.method === "POST") {
          const body = JSON.parse(init.body!);
          posts.push({ url, body });
          remember = body.enabled;
        }
        return { status: 200, body: { enabled: remember } };
      }
      if (url.endsWith("/memory")) return { status: 200, body: copy(memory) };
      const base = "/api/accounts/1/chats/user%3A101/traits";
      if (url.includes(base)) {
        if (init.method !== "POST") return { status: 200, body: copy(traits) };
        const body = JSON.parse(init.body!);
        posts.push({ url, body });
        return editTraits(traits, url.split(base)[1]!, body);
      }
      throw new Error(`unexpected ${url}`);
    });
  });

  const mount = () =>
    render(
      <ConnectionProvider>
        <ProfileRoute />
      </ConnectionProvider>,
    );
  const traitsPanel = async () =>
    within(await screen.findByTestId("traits-panel"));

  test("Remember this chat is off by default, and turns on (ADR 56 Q9)", async () => {
    await mount();
    await screen.findByText("Anna Petrova");
    const switchRow = screen.getByLabelText("Remember this chat");
    await waitFor(() => expect(switchRow.props.disabled).toBe(false));
    expect(switchRow.props.value).toBe(false);
    expect(
      screen.getByText(
        "Off by default. On, Enloq notes what this person cares about and their traits, and what you care about from this chat. Off, this chat teaches and uses neither; what's noted stays until you clear it.",
      ),
    ).toBeTruthy();
    await fireEvent(switchRow, "valueChange", true);
    await waitFor(() =>
      expect(posts).toEqual([
        {
          url: "https://enloq.example.com/api/accounts/1/chats/user%3A101/remember",
          body: { enabled: true },
        },
      ]),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Remember this chat").props.value).toBe(
        true,
      ),
    );
  });

  test("Cares about and Traits replace Chat memory (Q25)", async () => {
    await mount();
    // The person's empty memory and traits each say so.
    await waitFor(() =>
      expect(screen.getAllByText("Nothing noted yet.")).toHaveLength(2),
    );
    expect(screen.getByText("Cares about")).toBeTruthy();
    expect(screen.getByText("Traits")).toBeTruthy();
    expect(screen.queryByText("Chat memory")).toBeNull();
  });

  test("scales show their level and confidence, and habits their sentences", async () => {
    traits.scales = [
      scale("humor", {
        value: "3",
        label: "Often",
        probability: 0.82,
        judged: JUDGED,
        source: "model",
        current: true,
      }),
      // Its evidence fell short at the latest judgment.
      scale("warmth", {
        value: "3",
        label: "Friendly",
        probability: 0.7,
        judged: EARLIER,
        source: "model",
        current: false,
      }),
      scale("directness", {
        value: "4",
        label: "Very direct",
        judged: JUDGED,
        source: "owner",
      }),
      scale("formality"),
    ];
    traits.habits = {
      messages: 40,
      reply_seconds: 300,
      active_from: 18,
      active_to: 22,
      voice_share: 0.25,
      starts_share: 0.4,
    };
    await mount();
    const panel = await traitsPanel();
    expect(panel.getByText("Usually replies in about 5 minutes.")).toBeTruthy();
    expect(
      panel.getByText("Writes most between 18:00 and 22:00."),
    ).toBeTruthy();
    expect(panel.getByText("Voice notes: 25% of messages.")).toBeTruthy();
    expect(panel.getByText("Starts 40% of conversations.")).toBeTruthy();
    expect(panel.getByText("Humor: Often")).toBeTruthy();
    expect(
      panel.getByText(`82% · judged ${messageDate(JUDGED, "UTC")}`),
    ).toBeTruthy();
    expect(panel.getByText("Warmth: Friendly")).toBeTruthy();
    expect(
      panel.getByText(`70% · last judged ${messageDate(EARLIER, "UTC")}`),
    ).toBeTruthy();
    expect(panel.getByText("Directness: Very direct")).toBeTruthy();
    expect(panel.getByText("Set by you")).toBeTruthy();
    // An unjudged trait shows only among the changes.
    expect(panel.queryByText(/^Formality/)).toBeNull();
    expect(panel.queryByText("Nothing noted yet.")).toBeNull();
  });

  test("Change traits sets the owner's level and clears it", async () => {
    traits.scales = [
      scale("humor", {
        value: "3",
        label: "Often",
        probability: 0.82,
        judged: JUDGED,
        source: "model",
        current: true,
      }),
    ];
    await mount();
    const panel = await traitsPanel();
    expect(panel.queryByRole("button", { name: "Humor: Rarely" })).toBeNull();
    await fireEvent.press(panel.getByRole("button", { name: "Change traits" }));
    // The model's level is the chosen one until the owner picks another.
    expect(
      panel.getByRole("button", { name: "Humor: Often" }).props
        .accessibilityState,
    ).toMatchObject({ selected: true });
    await fireEvent.press(panel.getByRole("button", { name: "Humor: Rarely" }));
    expect(await panel.findByText("Humor: Rarely")).toBeTruthy();
    expect(panel.getByText("Set by you")).toBeTruthy();
    expect(
      panel.getByRole("button", { name: "Humor: Rarely" }).props
        .accessibilityState,
    ).toMatchObject({ selected: true });
    await fireEvent.press(panel.getByRole("button", { name: "Clear Humor" }));
    await waitFor(() => expect(panel.queryByText("Humor: Rarely")).toBeNull());
    expect(posts).toEqual([
      {
        url: "https://enloq.example.com/api/accounts/1/chats/user%3A101/traits/scales/humor",
        body: { revision: 3, value: "1" },
      },
      {
        url: "https://enloq.example.com/api/accounts/1/chats/user%3A101/traits/scales/humor",
        body: { revision: 4, value: null },
      },
    ]);
    // A level the owner cleared stays cleared; the model never sets it again.
    expect(
      panel.getByRole("button", { name: "Clear Humor" }).props
        .accessibilityState,
    ).toMatchObject({ disabled: true });
    await fireEvent.press(panel.getByRole("button", { name: "Done" }));
    expect(panel.queryByRole("button", { name: "Clear Humor" })).toBeNull();
  });

  test("notes are added, edited and removed, each with its source", async () => {
    traits.notes = [
      {
        id: "n1",
        text: "Answers questions with questions",
        confidence: 0.8,
        source: "model",
        created: JUDGED,
        citations: [4, 9, 12],
      },
    ];
    await mount();
    const panel = await traitsPanel();
    expect(panel.getByText("Answers questions with questions")).toBeTruthy();
    expect(panel.getByText("Confidence 80%")).toBeTruthy();
    await fireEvent.changeText(
      panel.getByLabelText("Add a trait note"),
      "Plans trips months ahead",
    );
    expect(panel.getByText("24/300 bytes")).toBeTruthy();
    await fireEvent.press(
      panel.getByRole("button", { name: "Add trait note" }),
    );
    expect(await panel.findByText("Plans trips months ahead")).toBeTruthy();
    expect(panel.getByText("Added or edited by you")).toBeTruthy();
    await fireEvent.press(
      panel.getByRole("button", {
        name: "Edit: Answers questions with questions",
      }),
    );
    await fireEvent.changeText(
      panel.getByLabelText("Edit trait note"),
      "Answers a question with another",
    );
    await fireEvent.press(panel.getByRole("button", { name: "Save" }));
    expect(
      await panel.findByText("Answers a question with another"),
    ).toBeTruthy();
    expect(panel.getAllByText("Added or edited by you")).toHaveLength(2);
    expect(panel.queryByText("Confidence 80%")).toBeNull();
    await fireEvent.press(
      panel.getByRole("button", { name: "Remove: Plans trips months ahead" }),
    );
    await waitFor(() =>
      expect(panel.queryByText("Plans trips months ahead")).toBeNull(),
    );
    const base =
      "https://enloq.example.com/api/accounts/1/chats/user%3A101/traits";
    expect(posts).toEqual([
      {
        url: `${base}/notes`,
        body: { revision: 3, text: "Plans trips months ahead" },
      },
      {
        url: `${base}/notes/n1`,
        body: { revision: 4, text: "Answers a question with another" },
      },
      { url: `${base}/notes/n4/remove`, body: { revision: 5 } },
    ]);
  });

  test("ten notes fill traits; a stale revision reloads", async () => {
    traits.notes = Array.from({ length: 10 }, (_, index) => ({
      id: `m${index}`,
      text: `Tendency ${index}`,
      confidence: 0.75,
      source: "model" as const,
      created: JUDGED,
      citations: [1, 2, 3],
    }));
    await mount();
    const panel = await traitsPanel();
    expect(
      panel.getByText(
        "Traits hold at most 10 notes; remove one to add another.",
      ),
    ).toBeTruthy();
    expect(panel.queryByLabelText("Add a trait note")).toBeNull();
    // Another client removed one first: the edit is refused and the panel
    // reads the traits again.
    traits.notes = traits.notes.slice(1);
    traits.revision = 9;
    await fireEvent.press(
      panel.getByRole("button", { name: "Remove: Tendency 5" }),
    );
    expect(
      await panel.findByText("Traits changed; reload before editing"),
    ).toBeTruthy();
    expect(await panel.findByLabelText("Add a trait note")).toBeTruthy();
    expect(panel.queryByText("Tendency 0")).toBeNull();
  });

  test("with Memory off and a failed update, both say so", async () => {
    memory.enabled = false;
    traits.enabled = false;
    traits.error = "The decision model is unavailable";
    await mount();
    const panel = await traitsPanel();
    expect(panel.getByText("Memory is Off in Settings.")).toBeTruthy();
    expect(panel.getByText("The decision model is unavailable")).toBeTruthy();
    expect(panel.getByText("Nothing noted yet.")).toBeTruthy();
    const cares = within(screen.getByTestId("memory-panel"));
    expect(cares.getByText("Memory is Off in Settings.")).toBeTruthy();
  });
});

describe("About you", () => {
  let accountMemory: AccountMemory;
  let traits: Traits;
  let posts: { url: string; body: unknown }[];
  let reads: string[];

  beforeEach(() => {
    reset();
    posts = [];
    reads = [];
    accountMemory = {
      revision: 2,
      enabled: true,
      entries: [
        {
          id: "a1",
          text: "Training for a half marathon in May",
          citations: [7],
          source: "model",
          outdated: null,
          created: JUDGED,
          guidance: 14,
          defaults: null,
          sources: [{ id: 7, availability: "cached", chat: "user:101" }],
        },
      ],
    };
    traits = emptyTraits();
    traits.scales = traits.scales.filter((item) => item.trait !== "formality");
    traits.habits = {
      messages: 120,
      reply_seconds: 300,
      active_from: 18,
      active_to: 22,
      voice_share: 0,
      starts_share: 0.4,
    };
    mockStore.set("enloq.credential", SAVED);
    mockParams.account = "1";
    serve((url, init) => {
      if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
      const prefix = "https://enloq.example.com/api/accounts/1/";
      if (!url.startsWith(prefix)) throw new Error(`unexpected ${url}`);
      const path = `/${url.slice(prefix.length)}`;
      const body = init.body ? JSON.parse(init.body) : null;
      if (init.method === "POST") posts.push({ url: path, body });
      else reads.push(path);
      if (path === "/memory") return { status: 200, body: copy(accountMemory) };
      if (path === "/memory/entries") {
        accountMemory.revision += 1;
        accountMemory.entries.push({
          id: "a2",
          text: body.text,
          citations: [],
          source: "owner",
          outdated: null,
          created: JUDGED,
          guidance: null,
          defaults: null,
          sources: [],
        });
        return { status: 200, body: copy(accountMemory) };
      }
      if (path === "/memory/clear") {
        accountMemory.revision += 1;
        accountMemory.entries = [];
        return { status: 200, body: copy(accountMemory) };
      }
      if (path === "/traits") return { status: 200, body: copy(traits) };
      if (path.startsWith("/traits/"))
        return editTraits(traits, path.slice("/traits".length), body);
      throw new Error(`unexpected ${url}`);
    });
  });

  const mount = () =>
    render(
      <ConnectionProvider>
        <AboutRoute />
      </ConnectionProvider>,
    );

  test("it shows what you care about and your traits (Q21)", async () => {
    await mount();
    expect(await screen.findByText("Owner")).toBeTruthy();
    expect(screen.getByRole("header", { name: "About you" })).toBeTruthy();
    expect(screen.getByText("You care about")).toBeTruthy();
    expect(screen.getByText("Your traits")).toBeTruthy();
    const cares = within(await screen.findByTestId("memory-panel"));
    expect(cares.getByText("Training for a half marathon in May")).toBeTruthy();
    expect(cares.getByText("Noted by Enloq")).toBeTruthy();
    // Account Memory has no Retry or updating state.
    expect(cares.queryByRole("button", { name: "Retry" })).toBeNull();
    const yours = within(await screen.findByTestId("traits-panel"));
    expect(
      yours.getByText("You usually reply in about 5 minutes."),
    ).toBeTruthy();
    expect(
      yours.getByText("You write most between 18:00 and 22:00."),
    ).toBeTruthy();
    expect(yours.getByText("You start 40% of conversations.")).toBeTruthy();
    expect(yours.queryByText(/^Voice notes/)).toBeNull();
    expect(reads.sort()).toEqual(["/memory", "/traits"]);
  });

  test("you add to and clear your own memory, and set your own traits", async () => {
    await mount();
    const cares = within(await screen.findByTestId("memory-panel"));
    await fireEvent.changeText(
      cares.getByLabelText("Add a note"),
      "Learning Portuguese",
    );
    await fireEvent.press(cares.getByRole("button", { name: "Add note" }));
    expect(await cares.findByText("Learning Portuguese")).toBeTruthy();
    expect(cares.getByText("Added or edited by you")).toBeTruthy();
    await fireEvent.press(cares.getByRole("button", { name: "Clear…" }));
    expect(cares.getByText("Remove every note about you?")).toBeTruthy();
    await fireEvent.press(cares.getByRole("button", { name: "Clear memory" }));
    expect(await cares.findByText("Nothing noted yet.")).toBeTruthy();
    const yours = within(screen.getByTestId("traits-panel"));
    await fireEvent.press(yours.getByRole("button", { name: "Change traits" }));
    await fireEvent.press(
      yours.getByRole("button", { name: "Warmth: Very warm" }),
    );
    expect(await yours.findByText("Warmth: Very warm")).toBeTruthy();
    expect(posts).toEqual([
      {
        url: "/memory/entries",
        body: { revision: 2, text: "Learning Portuguese" },
      },
      { url: "/memory/clear", body: { revision: 3 } },
      { url: "/traits/scales/warmth", body: { revision: 3, value: "4" } },
    ]);
  });
});
