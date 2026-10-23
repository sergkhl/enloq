import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
import React from "react";
import {
  mockFiles,
  mockParams,
  mockRouter,
  mockStore,
  reset,
  serve,
} from "./setup";
import Accounts from "../app/accounts";
import Privacy from "../app/settings/privacy";
import Login from "../app/login";
import { writeCached } from "../src/cache";
import { ConnectionProvider } from "../src/connection";

// ADR 43 IB5 and IB6: trusted browsers and devices, and every Manage
// accounts action, in the app.
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});

function account(id: number, name: string, state: string, extra = {}) {
  return {
    account_id: id,
    name,
    username: `user${id}`,
    phone: "",
    state,
    connected: true,
    status: state === "authorized" ? "Connected" : "Sign in again",
    ai_enabled: true,
    photo_id: null,
    ...extra,
  };
}

let accounts: ReturnType<typeof account>[] = [];
let trusted: Record<string, unknown>[] = [];
let posts: { path: string; body: unknown }[] = [];
let answers: Record<string, { status: number; body: unknown }> = {};
let login: Record<string, unknown> = {};

function loginState(stage: string, extra = {}) {
  return {
    id: "f".repeat(32),
    method: "phone",
    stage,
    message:
      stage === "code"
        ? "Enter the code Telegram sent."
        : stage === "qr"
          ? "Scan with Telegram."
          : "",
    expires_at: 0,
    delivery: null,
    next_delivery: null,
    retry_at: null,
    can_resend: true,
    account_id: null,
    qr_revision: 1,
    ...extra,
  };
}

beforeEach(() => {
  reset();
  mockStore.set("enloq.credential", SAVED);
  posts = [];
  answers = {};
  accounts = [
    account(1, "Owner", "authorized"),
    account(2, "Quiet", "authorized", { connected: false }),
    account(3, "Pending", "logout_pending"),
    account(4, "Lapsed", "requires_login"),
    account(5, "Gone", "logged_out"),
  ];
  trusted = [
    {
      id: "a".repeat(32),
      label: "Chromium browser on a Mac",
      approved_at: 1_800_000_000,
      last_access: 1_800_000_000,
      current: false,
      kind: "browser",
    },
    {
      id: "b".repeat(32),
      label: "Enloq app on Pixel 9",
      approved_at: 1_800_000_100,
      last_access: 1_800_000_100,
      current: true,
      kind: "device",
    },
  ];
  login = loginState("qr");
  serve((url, init) => {
    const path = url.replace("https://enloq.example.com/api/", "");
    if (init.method === "POST") {
      const body = JSON.parse(init.body!);
      posts.push({ path, body });
      if (answers[path]) return answers[path]!;
      if (path === "logins") {
        login = loginState(body.method === "qr" ? "qr" : "code");
        return { status: 200, body: login };
      }
      if (path.endsWith("/code")) {
        login = loginState("complete", { account_id: 6 });
        return { status: 200, body: login };
      }
      if (path.endsWith("/revoke")) {
        trusted = trusted.filter((row) => !path.includes(row.id as string));
        return { status: 200, body: { ok: true } };
      }
      return { status: 200, body: path.startsWith("logins") ? login : {} };
    }
    if (path === "accounts")
      return {
        status: 200,
        body: {
          mode: "personal",
          timezone: "UTC",
          budget: [],
          login_available: true,
          accounts,
        },
      };
    if (path === "auth/browsers") return { status: 200, body: trusted };
    if (path.startsWith("logins/")) return { status: 200, body: login };
    throw new Error(`unexpected ${url}`);
  });
});

const mount = (screenElement: React.ReactElement) =>
  render(<ConnectionProvider>{screenElement}</ConnectionProvider>);

describe("trusted browsers and devices", () => {
  test("Privacy offers Pair a phone above the list", async () => {
    await mount(<Privacy />);
    expect(await screen.findByText("Chromium browser on a Mac")).toBeTruthy();
    await fireEvent.press(screen.getByRole("button", { name: "Pair a phone" }));
    expect(mockRouter.push).toHaveBeenCalledWith("/settings/pair");
  });

  test("lists each entry with this phone marked and forgets one", async () => {
    await mount(<Privacy />);
    expect(await screen.findByText("Chromium browser on a Mac")).toBeTruthy();
    expect(screen.getByText(/^This phone · Last used/)).toBeTruthy();
    expect(screen.getByText(/^Browser · Last used/)).toBeTruthy();
    await fireEvent.press(
      screen.getByRole("button", { name: "Forget Chromium browser on a Mac" }),
    );
    expect(screen.getByText(/loses access at once/)).toBeTruthy();
    await fireEvent.press(screen.getByRole("button", { name: "Forget" }));
    await waitFor(() =>
      expect(screen.queryByText("Chromium browser on a Mac")).toBeNull(),
    );
    expect(posts).toEqual([
      { path: `auth/browsers/${"a".repeat(32)}/revoke`, body: {} },
    ]);
  });

  test("forgetting this phone is Disconnect", async () => {
    await mount(<Privacy />);
    await fireEvent.press(
      await screen.findByRole("button", { name: "Forget this phone" }),
    );
    await fireEvent.press(screen.getByRole("button", { name: "Forget" }));
    await waitFor(() => expect(mockStore.has("enloq.credential")).toBe(false));
    expect(posts).toEqual([{ path: "auth/disconnect", body: {} }]);
  });
});

describe("Manage accounts", () => {
  test("offers each account's actions by its state", async () => {
    await mount(<Accounts />);
    await screen.findByText("Owner");
    const actions = (name: string) =>
      within(screen.getByLabelText(name))
        .queryAllByRole("button")
        // A button is named by its label's text.
        .map((node) => within(node).queryAllByText(/.+/)[0]?.props.children);
    expect(actions("Owner")).toEqual(["About you", "Log out"]);
    expect(actions("Quiet")).toEqual(["About you", "Reconnect", "Log out"]);
    expect(actions("Pending")).toEqual(["Retry logout"]);
    expect(actions("Lapsed")).toEqual(["Sign in again", "Log out"]);
    expect(actions("Gone")).toEqual(["Sign in again", "Remove local data"]);
    await fireEvent.press(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() =>
      expect(posts).toEqual([{ path: "accounts/2/reconnect", body: {} }]),
    );
    await fireEvent.press(screen.getByRole("button", { name: "Add account" }));
    expect(mockRouter.push).toHaveBeenCalledWith("/login");
  });

  test("logs out after its confirmation", async () => {
    await mount(<Accounts />);
    await fireEvent.press(
      (await screen.findAllByRole("button", { name: "Log out" }))[0]!,
    );
    expect(screen.getByText("Log out of Owner?")).toBeTruthy();
    expect(screen.getByText(/Only this app signs out/)).toBeTruthy();
    await fireEvent.press(
      screen.getByRole("button", { name: "Confirm logout" }),
    );
    await waitFor(() =>
      expect(posts).toEqual([
        { path: "accounts/1/logout", body: { confirm: true } },
      ]),
    );
  });

  test("removes local data and the phone's own copy, or says why not", async () => {
    await mount(<Accounts />);
    writeCached("list:5", []);
    writeCached("chat:5/user:9", {});
    writeCached("list:1", []);
    answers["accounts/5/remove-local-data"] = {
      status: 400,
      body: { detail: "Resolve this account's sends before removing it" },
    };
    await fireEvent.press(
      await screen.findByRole("button", { name: "Remove local data" }),
    );
    expect(screen.getByText("Remove local data for Gone?")).toBeTruthy();
    expect(screen.getByText(/Budget records remain/)).toBeTruthy();
    await fireEvent.press(
      screen.getByRole("button", { name: "Remove local data" }),
    );
    expect(
      await screen.findByText(
        "Resolve this account's sends before removing it",
      ),
    ).toBeTruthy();
    expect([...mockFiles.keys()].some((key) => key.includes("list%3A5"))).toBe(
      true,
    );
    delete answers["accounts/5/remove-local-data"];
    await fireEvent.press(
      screen.getByRole("button", { name: "Remove local data" }),
    );
    await waitFor(() =>
      expect(
        [...mockFiles.keys()].some((key) => key.includes("list%3A5")),
      ).toBe(false),
    );
    const kept = [...mockFiles.keys()];
    expect(kept.some((key) => key.includes("chat%3A5"))).toBe(false);
    expect(kept.some((key) => key.includes("list%3A1"))).toBe(true);
  });

  test("About you opens a signed-in account's memory and traits (ADR 55 Q21)", async () => {
    await mount(<Accounts />);
    await fireEvent.press(
      await screen.findByRole("button", { name: "About you: Quiet" }),
    );
    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: "/about/[account]",
      params: { account: "2" },
    });
    expect(
      screen.queryByRole("button", { name: "About you: Lapsed" }),
    ).toBeNull();
  });

  test("Sign in again opens the login for that account", async () => {
    await mount(<Accounts />);
    await fireEvent.press(
      (await screen.findAllByRole("button", { name: "Sign in again" }))[0]!,
    );
    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: "/login",
      params: { account: "4" },
    });
  });
});

describe("Add account", () => {
  test("starts with a QR code read with the phone's credential", async () => {
    await mount(<Login />);
    const image = await screen.findByLabelText(
      "Scan this QR code with Telegram to log in",
    );
    expect(image.props.source[0]).toEqual({
      uri: `https://enloq.example.com/api/logins/${"f".repeat(32)}/qr?v=1`,
      headers: { Authorization: "Bearer t0ken" },
    });
    expect(posts[0]).toEqual({
      path: "logins",
      body: { method: "qr", phone: "", expected_id: null },
    });
  });

  test("signs in by phone number and code, keeping neither", async () => {
    await mount(<Login />);
    await screen.findByLabelText("Scan this QR code with Telegram to log in");
    await fireEvent.press(
      screen.getByRole("button", { name: "Log in by phone number" }),
    );
    await waitFor(() =>
      expect(posts).toContainEqual({
        path: `logins/${"f".repeat(32)}/cancel`,
        body: {},
      }),
    );
    await fireEvent.changeText(
      screen.getByLabelText("Phone number"),
      "+12025550006",
    );
    await fireEvent.press(screen.getByRole("button", { name: "Continue" }));
    await fireEvent.changeText(
      await screen.findByLabelText("Login code"),
      "12345",
    );
    await fireEvent.press(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() =>
      expect(mockRouter.replace).toHaveBeenCalledWith("/chats"),
    );
    expect(posts).toContainEqual({
      path: `logins/${"f".repeat(32)}/code`,
      body: { value: "12345" },
    });
    expect(mockStore.get("enloq.account")).toBe("6");
    // The code reached the service only.
    expect(
      [...mockFiles.values(), ...mockStore.values()].some((value) =>
        value.includes("12345"),
      ),
    ).toBe(false);
  });

  test("a code of known length submits itself on its last digit", async () => {
    await mount(<Login />);
    await screen.findByLabelText("Scan this QR code with Telegram to log in");
    await fireEvent.press(
      screen.getByRole("button", { name: "Log in by phone number" }),
    );
    answers["logins"] = {
      status: 200,
      body: loginState("code", { code_length: 5 }),
    };
    await fireEvent.changeText(
      screen.getByLabelText("Phone number"),
      "+12025550006",
    );
    await fireEvent.press(screen.getByRole("button", { name: "Continue" }));
    const field = await screen.findByLabelText("Login code");
    await fireEvent.changeText(field, "1234");
    expect(posts.some((post) => post.path.endsWith("/code"))).toBe(false);
    // A pasted code with spaces keeps its digits (ADR 47 Q3).
    await fireEvent.changeText(field, "12 345");
    await waitFor(() =>
      expect(mockRouter.replace).toHaveBeenCalledWith("/chats"),
    );
    expect(posts).toContainEqual({
      path: `logins/${"f".repeat(32)}/code`,
      body: { value: "12345" },
    });
    expect(posts.filter((post) => post.path.endsWith("/code"))).toHaveLength(1);
  });

  test("leaving an unfinished login cancels it", async () => {
    const shown = await mount(<Login />);
    await screen.findByLabelText("Scan this QR code with Telegram to log in");
    await shown.unmount();
    await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
    expect(posts).toContainEqual({
      path: `logins/${"f".repeat(32)}/cancel`,
      body: {},
    });
  });

  test("Sign in again names the account it expects", async () => {
    mockParams.account = "4";
    await mount(<Login />);
    expect(await screen.findByText("Sign in again · Lapsed")).toBeTruthy();
    await waitFor(() =>
      expect(posts[0]).toEqual({
        path: "logins",
        body: { method: "qr", phone: "", expected_id: 4 },
      }),
    );
  });
});
