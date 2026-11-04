import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { mockFiles, mockRouter, mockStore, reset, serve } from "./setup";
import {
  LOG_PATH,
  Refused,
  ServerFailed,
  Unreachable,
  offline,
  request,
} from "../src/api";
import { readCached, readCachedSync, writeCached } from "../src/cache";
import { ConnectionProvider, useConnection } from "../src/connection";
import {
  logEntries,
  record,
  recordError,
  sendLogs,
  setLogSender,
  type LogPost,
} from "../src/logs";
import { RemindersList } from "../src/Reminders";
import { ScreenBoundary } from "../src/ScreenBoundary";
import LogsRoute from "../app/settings/logs";

const mockCommit = { value: "" };
jest.mock("../src/build", () => ({
  buildCommit: () => mockCommit.value,
  thisBuild: () => `Enloq 0.1.0 · ${mockCommit.value || "development build"}`,
}));

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

const ACCOUNTS = [
  { account_id: 2, name: "Authored", state: "authorized", ai_enabled: true },
];

// The Reminders list as a 25 Oct build kept it: one schedule per chat, with no
// `id` and no Message Idea, which today's rows can't draw.
const OLD_ITEM = {
  revision: 3,
  schedule: {
    frequency: "weekly",
    weekday: 0,
    month_day: 1,
    timezone: "UTC",
    delivery: "exact",
    time: "09:00",
    language: "English",
  },
  next_at: 1793500000,
  status: "active",
  occurrence: null,
  account_id: 2,
  account: "Authored",
  peer: "user:42",
  title: "Authored contact",
  ai_enabled: true,
};
const NEW_ITEM = {
  id: "b".repeat(32),
  revision: 3,
  schedule: {
    repeat: "weeks",
    start: "2026-10-26",
    interval: 1,
    weekday: 0,
    month: 1,
    month_day: 1,
    timezone: "UTC",
    delivery: "exact",
    time: "09:00",
    language: "English",
    idea: "",
  },
  next_at: 1793500000,
  status: "active",
  account_id: 2,
  account: "Authored",
  peer: "user:42",
  title: "Authored contact",
  ai_enabled: true,
};

// As the build before this rule wrote it, with no build in the file.
function keptByAnOldBuild(key: string, value: unknown) {
  mockFiles.set(
    `file:///cache/enloq/${encodeURIComponent(key)}.json`,
    JSON.stringify({ saved: Date.now(), value }),
  );
}

beforeEach(() => {
  reset();
  mockCommit.value = "abc1234";
  setLogSender(null);
  mockStore.set(
    "enloq.credential",
    JSON.stringify({ origin: "https://enloq.example.com", token: "authored" }),
  );
});

describe("the offline copy", () => {
  test("the Reminders tab does not draw a copy another build kept", async () => {
    keptByAnOldBuild("reminders", { items: [OLD_ITEM] });
    const failure = jest.spyOn(console, "error");
    let answer: (value: unknown) => void = () => undefined;
    serve((url) => {
      if (url.endsWith("/api/accounts"))
        return { status: 200, body: { accounts: ACCOUNTS } };
      if (url.endsWith("/api/reminders"))
        return new Promise((resolve) => {
          answer = resolve;
        }) as never;
      throw new Error(`unexpected ${url}`);
    });
    await mount(<RemindersList />);
    expect(await screen.findByText("Loading reminders…")).toBeTruthy();
    // The copy is gone, so nothing can draw it again.
    expect(await readCached("reminders")).toBeNull();
    await act(async () => {
      answer({ status: 200, body: { items: [NEW_ITEM] } });
    });
    expect(await screen.findByText("Authored contact")).toBeTruthy();
    expect(failure).not.toHaveBeenCalledWith(
      expect.stringContaining("reading 'split'"),
      expect.anything(),
    );
    failure.mockRestore();
  });

  test("a copy this build kept stays readable", async () => {
    writeCached("reminders", { items: [NEW_ITEM] });
    expect(await readCached("reminders")).toEqual({ items: [NEW_ITEM] });
    serve((url) => {
      if (url.endsWith("/api/accounts"))
        return { status: 200, body: { accounts: ACCOUNTS } };
      if (url.endsWith("/api/reminders"))
        return new Promise(() => undefined) as never;
      throw new Error(`unexpected ${url}`);
    });
    await mount(<RemindersList />);
    expect(await screen.findByText("Authored contact")).toBeTruthy();
  });

  test("what the phone itself wrote survives a new build", async () => {
    keptByAnOldBuild("draft:2/user:42", {
      text: "Unsent words",
      reply_to: null,
    });
    keptByAnOldBuild("deletion:2/user:42", ["a"]);
    keptByAnOldBuild("list:2", []);
    expect(await readCached("draft:2/user:42")).toEqual({
      text: "Unsent words",
      reply_to: null,
    });
    expect(await readCached("deletion:2/user:42")).toEqual(["a"]);
    expect(await readCached("list:2")).toBeNull();
  });
});

describe("a screen that fails", () => {
  let broken = true;
  function Failing() {
    if (broken) throw new TypeError("Authored failure");
    return <>{null}</>;
  }
  beforeEach(() => {
    broken = true;
    jest.spyOn(console, "error").mockImplementation(() => undefined);
  });
  afterEach(() => jest.restoreAllMocks());

  test("shows its error and logs it instead of closing the app", async () => {
    await render(
      <ScreenBoundary navigable>
        <Failing />
      </ScreenBoundary>,
    );
    expect(screen.getByText("This screen hit an error")).toBeTruthy();
    expect(screen.getByText("TypeError: Authored failure")).toBeTruthy();
    const [entry] = logEntries();
    expect(entry).toMatchObject({
      level: "error",
      source: "screen",
      message: "TypeError: Authored failure",
    });
    expect(entry!.stack).toContain("Failing");

    // Try again draws it afresh, and failing again stays on this screen.
    await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("This screen hit an error")).toBeTruthy();
    broken = false;
    await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
    expect(screen.queryByText("This screen hit an error")).toBeNull();
  });

  test("leads back to the list", async () => {
    await render(
      <ScreenBoundary navigable>
        <Failing />
      </ScreenBoundary>,
    );
    await fireEvent.press(
      screen.getByRole("button", { name: "Back to chats" }),
    );
    expect(mockRouter.replace).toHaveBeenCalledWith("/chats");
  });

  test("outside the navigator offers only Try again", async () => {
    await render(
      <ScreenBoundary>
        <Failing />
      </ScreenBoundary>,
    );
    expect(screen.queryByRole("button", { name: "Back to chats" })).toBeNull();
  });
});

describe("what a request's failure says", () => {
  const answering = (status: number, body: unknown) =>
    serve(() => ({ status, body }));
  const failing = () =>
    request("https://x.example", "reminders").catch(
      (error: Refused) => error,
    ) as Promise<Refused>;

  test("a 500 is the server's error, not the server gone", async () => {
    answering(500, null);
    const failure = await failing();
    expect(failure).toBeInstanceOf(ServerFailed);
    expect(failure).toBeInstanceOf(Unreachable);
    expect(failure.message).toBe("The server hit an error.");
    expect(offline(failure)).toBe(false);
  });

  test("the edge's 502 and 504 say it isn't answering", async () => {
    for (const status of [502, 504]) {
      answering(status, null);
      const failure = await failing();
      expect(failure.message).toBe("The server isn't answering.");
      expect(offline(failure)).toBe(true);
    }
  });

  test("a busy server says so", async () => {
    answering(503, { detail: "The server is busy. Try again in a moment." });
    const failure = await failing();
    expect(failure).toBeInstanceOf(Refused);
    expect(failure.status).toBe(503);
    expect(failure.message).toBe("The server is busy. Try again in a moment.");
  });
});

describe("the log", () => {
  test("a request's line says what happened, but not what a pairing named", async () => {
    serve((url) =>
      url.includes("reminders")
        ? { status: 400, body: { detail: "Authored refusal" } }
        : { status: 200, body: {} },
    );
    await request("https://x.example", "reminders").catch(() => undefined);
    await request("https://x.example", "auth/client-pairings/secret");
    await request("https://x.example", LOG_PATH, {
      body: { build: "", entries: [] },
    });
    const lines = logEntries().map((entry) => entry.message);
    expect(lines).toHaveLength(2);
    expect(lines[1]).toMatch(/^GET reminders 400: Authored refusal \d+ ms$/);
    expect(lines[0]).toMatch(/^GET auth\/client-pairings 200 \d+ ms$/);
  });

  test("errors go to the service as they happen, the rest on request", async () => {
    const posts: LogPost[] = [];
    setLogSender(async (post) => {
      posts.push(post);
    });
    record("info", "api", "GET chats 200 12 ms");
    expect(posts).toEqual([]);
    recordError("screen", new TypeError("Authored failure"));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.build).toBe("Enloq 0.1.0 · abc1234");
    expect(posts[0]!.entries.map((entry) => entry.source)).toEqual(["screen"]);
    expect(await sendLogs(true)).toBe(true);
    expect(posts[1]!.entries.map((entry) => entry.source)).toEqual(["api"]);
    // Nothing goes twice.
    expect(await sendLogs(true)).toBe(true);
    expect(posts).toHaveLength(2);
  });

  test("what could not go waits, on disk, for the next try", async () => {
    let up = false;
    const posts: LogPost[] = [];
    setLogSender(async (post) => {
      if (!up) throw new Error("offline");
      posts.push(post);
    });
    record("error", "fatal", "Authored crash");
    await waitFor(() => expect(readCachedSync("logs")).toBeTruthy());
    expect(readCachedSync<{ message: string }[]>("logs")![0]!.message).toBe(
      "Authored crash",
    );
    await act(async () => undefined);
    up = true;
    expect(await sendLogs(true)).toBe(true);
    expect(posts[0]!.entries[0]!.message).toBe("Authored crash");
  });

  test("a long stack is cut to what the service takes", () => {
    record("error", "screen", "m".repeat(30000), "s".repeat(30000));
    const [entry] = logEntries();
    expect(entry!.message).toHaveLength(20000);
    expect(entry!.stack).toHaveLength(20000);
  });
});

describe("Settings → Logs", () => {
  const me = (role: string) =>
    serve((url) => {
      if (url.endsWith("/api/accounts"))
        return { status: 200, body: { accounts: ACCOUNTS } };
      if (url.endsWith("/api/members/me"))
        return { status: 200, body: { role, state: "active" } };
      if (url.endsWith("/api/logs?limit=500"))
        return {
          status: 200,
          body: { lines: ["older service line", "newest service line"] },
        };
      throw new Error(`unexpected ${url}`);
    });

  test("shows this phone's log and sends it", async () => {
    me("member");
    await mount(<LogsRoute />);
    await act(async () => record("warn", "api", "GET chats 502 12 ms"));
    expect(await screen.findByText(/api: GET chats 502 12 ms$/)).toBeTruthy();
    // A member has no service log to read.
    expect(screen.queryByRole("button", { name: "Server" })).toBeNull();
    await fireEvent.press(
      screen.getByRole("button", { name: "Send to server" }),
    );
    expect(await screen.findByText("Sent to the server's log.")).toBeTruthy();
  });

  test("shows the owner the service's last lines, newest first", async () => {
    const calls = me("owner");
    await mount(<LogsRoute />);
    await fireEvent.press(
      await screen.findByRole("button", { name: "Server" }),
    );
    const newest = await screen.findByText("newest service line");
    expect(newest).toBeTruthy();
    expect(screen.getByText("older service line")).toBeTruthy();
    expect(calls.some((call) => call.url.endsWith("/api/logs?limit=500"))).toBe(
      true,
    );
  });
});
