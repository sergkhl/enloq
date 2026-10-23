import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import {
  mockFiles,
  mockRedirects,
  mockRouter,
  mockStore,
  reset,
  serve,
} from "./setup";
import Pair from "../app/pair";
import Settings from "../app/settings";
import { ConnectionProvider } from "../src/connection";
import { writeCached } from "../src/cache";

// ADR 43 IB3 and IB4: Connect with a code from Saved Messages, the local
// test server in Enloq Dev, and Disconnect. ADR 47: the code submits itself on
// its last digit (Q3), and a signed-out Owner Account turns Connect into Sign
// in with Telegram (Q11, Q13).
const ATTEMPT = {
  id: "0123456789abcdef0123456789abcdef",
  proof: "pr00f",
  expires_at: 0,
};
const ACCOUNTS = {
  accounts: [
    {
      account_id: 1,
      name: "Authored Owner",
      username: "",
      phone: "",
      state: "authorized",
      connected: true,
      status: "",
      ai_enabled: true,
      photo_id: null,
    },
  ],
  budget: [],
  mode: "personal",
  timezone: "UTC",
  login_available: true,
};

beforeEach(() => {
  reset();
  (global as unknown as { __DEV__: boolean }).__DEV__ = true;
});

const mountPair = () =>
  render(
    <ConnectionProvider>
      <Pair />
    </ConnectionProvider>,
  );

test("the release build offers Connect, Scan and a link, and Enloq Dev the test server", async () => {
  serve(() => {
    throw new Error("nothing is contacted");
  });
  const first = await mountPair();
  expect(
    await screen.findByRole("button", {
      name: "Connect to enloq.globesoul.com",
    }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Scan QR code" })).toBeTruthy();
  expect(screen.getByLabelText("Pairing link")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Use the local test server" }),
  ).toBeTruthy();
  await first.unmount();
  (global as unknown as { __DEV__: boolean }).__DEV__ = false;
  await mountPair();
  expect(
    await screen.findByRole("button", {
      name: "Connect to enloq.globesoul.com",
    }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Use the local test server" }),
  ).toBeNull();
});

function codeServer(answers: {
  code: () => { status: number; body: unknown };
}) {
  return serve((url, init) => {
    if (url.endsWith("/api/auth/code-pairings"))
      return { status: 200, body: ATTEMPT };
    if (url.endsWith(`/code-pairings/${ATTEMPT.id}/resend`))
      return { status: 200, body: { id: ATTEMPT.id, expires_at: 120 } };
    if (url.endsWith(`/code-pairings/${ATTEMPT.id}/code`))
      return answers.code();
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    throw new Error(`unexpected ${init.method} ${url}`);
  });
}

test("Connect pairs with the code from Saved Messages", async () => {
  const calls = codeServer({
    code: () => ({ status: 200, body: { token: "t0ken" } }),
  });
  await mountPair();
  await fireEvent.press(
    await screen.findByRole("button", {
      name: "Connect to enloq.globesoul.com",
    }),
  );
  expect(
    await screen.findByText(/Enter the code sent to your Saved Messages/),
  ).toBeTruthy();
  const started = calls.find((call) =>
    call.url.endsWith("/api/auth/code-pairings"),
  )!;
  expect(started.url).toBe(
    "https://enloq.globesoul.com/api/auth/code-pairings",
  );
  expect(JSON.parse(started.body!)).toEqual({ name: "Pixel 9" });
  expect(started.headers.Authorization).toBeUndefined();
  await fireEvent.changeText(screen.getByLabelText("Pairing code"), "48291");
  expect(calls.some((call) => call.url.endsWith("/code"))).toBe(false);
  // The sixth digit, here from a pasted code with a space, submits it.
  await fireEvent.changeText(screen.getByLabelText("Pairing code"), "48 2910");
  await waitFor(() =>
    expect(mockRouter.replace).toHaveBeenCalledWith("/chats"),
  );
  expect(calls.filter((call) => call.url.endsWith("/code"))).toHaveLength(1);
  const entered = calls.find((call) => call.url.endsWith("/code"))!;
  expect(JSON.parse(entered.body!)).toEqual({ code: "482910" });
  expect(entered.headers["X-Pairing-Proof"]).toBe("pr00f");
  expect(JSON.parse(mockStore.get("enloq.credential")!)).toEqual({
    origin: "https://enloq.globesoul.com",
    token: "t0ken",
  });
});

test("a wrong code stays to try again; too many end the attempt", async () => {
  let tries = 0;
  codeServer({
    code: () =>
      ++tries < 5
        ? {
            status: 400,
            body: { detail: "That code doesn't match. 4 tries left." },
          }
        : {
            status: 400,
            body: { detail: "Too many wrong codes. Start again." },
          },
  });
  await mountPair();
  await fireEvent.press(
    await screen.findByRole("button", {
      name: "Connect to enloq.globesoul.com",
    }),
  );
  await fireEvent.changeText(
    await screen.findByLabelText("Pairing code"),
    "111111",
  );
  expect(
    await screen.findByText("That code doesn't match. 4 tries left."),
  ).toBeTruthy();
  // The field empties for the next try, which Pair can also send.
  expect(screen.getByLabelText("Pairing code").props.value).toBe("");
  expect(screen.getByRole("button", { name: "Pair" })).toBeTruthy();
  tries = 4;
  await fireEvent.changeText(screen.getByLabelText("Pairing code"), "222222");
  expect(
    await screen.findByText("Too many wrong codes. Start again."),
  ).toBeTruthy();
  expect(screen.queryByLabelText("Pairing code")).toBeNull();
  expect(mockStore.has("enloq.credential")).toBe(false);
});

test("an expired code asks for a new one, which Send a new code posts", async () => {
  const calls = codeServer({
    code: () => ({
      status: 400,
      body: { detail: "This code expired. Send a new one." },
    }),
  });
  await mountPair();
  await fireEvent.press(
    await screen.findByRole("button", {
      name: "Connect to enloq.globesoul.com",
    }),
  );
  await fireEvent.changeText(
    await screen.findByLabelText("Pairing code"),
    "333333",
  );
  expect(
    await screen.findByText("This code expired. Send a new one."),
  ).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", { name: "Send a new code" }),
  );
  expect(
    await screen.findByText("A new code is in your Saved Messages."),
  ).toBeTruthy();
  const resent = calls.find((call) => call.url.endsWith("/resend"))!;
  expect(resent.headers["X-Pairing-Proof"]).toBe("pr00f");
});

test("a server without an Owner Account says so", async () => {
  serve((url) => {
    if (url.endsWith("/api/auth/code-pairings"))
      return {
        status: 400,
        body: { detail: "No Owner Account is set on this server" },
      };
    throw new Error(`unexpected ${url}`);
  });
  await mountPair();
  await fireEvent.press(
    await screen.findByRole("button", {
      name: "Connect to enloq.globesoul.com",
    }),
  );
  expect(
    await screen.findByText("No Owner Account is set on this server"),
  ).toBeTruthy();
});

test("a signed-out Owner Account turns Connect into Sign in with Telegram", async () => {
  const calls = serve((url, init) => {
    if (url.endsWith("/api/auth/code-pairings"))
      return {
        status: 409,
        body: { detail: "The Owner Account is not signed in on this server" },
      };
    const login = (stage: string, extra = {}) => ({
      id: "f".repeat(32),
      method: "phone",
      stage,
      message:
        stage === "code"
          ? "Enter the code Telegram sent."
          : "Two-step password.",
      expires_at: 0,
      delivery: "app",
      next_delivery: null,
      retry_at: null,
      can_resend: false,
      account_id: null,
      qr_revision: 0,
      code_length: 5,
      ...extra,
    });
    if (url.endsWith("/api/auth/sign-in-pairings"))
      return {
        status: 200,
        body: { id: ATTEMPT.id, proof: "s1gn", login: login("code") },
      };
    if (url.endsWith(`/sign-in-pairings/${ATTEMPT.id}/code`))
      return {
        status: 200,
        body: { id: ATTEMPT.id, proof: "", login: login("password") },
      };
    if (url.endsWith(`/sign-in-pairings/${ATTEMPT.id}/password`))
      return {
        status: 200,
        body: {
          id: ATTEMPT.id,
          proof: "",
          login: login("complete", { account_id: 1 }),
          token: "t0ken",
        },
      };
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  await mountPair();
  await fireEvent.press(
    await screen.findByRole("button", {
      name: "Connect to enloq.globesoul.com",
    }),
  );
  expect(
    await screen.findByText(/Sign in with your Telegram account/),
  ).toBeTruthy();
  await fireEvent.changeText(
    screen.getByLabelText("Phone number"),
    "+1 202 555 0111",
  );
  await fireEvent.press(screen.getByRole("button", { name: "Continue" }));
  const started = calls.find((call) =>
    call.url.endsWith("/api/auth/sign-in-pairings"),
  )!;
  expect(JSON.parse(started.body!)).toEqual({
    phone: "+1 202 555 0111",
    name: "Pixel 9",
  });
  // Telegram's five-digit code submits itself.
  await fireEvent.changeText(
    await screen.findByLabelText("Login code"),
    "12345",
  );
  const code = await waitFor(() =>
    calls.find((call) => call.url.endsWith("/code")),
  );
  expect(JSON.parse(code!.body!)).toEqual({ value: "12345" });
  expect(code!.headers["X-Pairing-Proof"]).toBe("s1gn");
  await fireEvent.changeText(
    await screen.findByLabelText("Telegram two-step password"),
    "synthetic-password",
  );
  await fireEvent.press(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() =>
    expect(mockRouter.replace).toHaveBeenCalledWith("/chats"),
  );
  expect(JSON.parse(mockStore.get("enloq.credential")!)).toEqual({
    origin: "https://enloq.globesoul.com",
    token: "t0ken",
  });
});

test("Use the local test server pairs with the fixture's own approval", async () => {
  const id = "fedcba9876543210fedcba9876543210";
  const calls = serve((url, init) => {
    if (url === "http://127.0.0.1:8776/fixture/pairing")
      return { status: 200, body: { id } };
    if (url.endsWith(`/pairings/${id}/device`) && init.method === "POST")
      return {
        status: 200,
        body: {
          id,
          expires_at: 0,
          state: "approved",
          code: "123456",
          label: "Enloq app on Pixel 9",
          kind: "device",
          approver: "server",
          proof: "pr00f",
        },
      };
    if (url.endsWith(`/pairings/${id}`))
      return {
        status: 200,
        body: {
          id,
          expires_at: 0,
          state: "approved",
          code: "123456",
          label: "",
          kind: "device",
          approver: "server",
        },
      };
    if (url.endsWith("/device/complete"))
      return { status: 200, body: { token: "l0cal" } };
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    throw new Error(`unexpected ${url}`);
  });
  await mountPair();
  await fireEvent.press(
    await screen.findByRole("button", { name: "Use the local test server" }),
  );
  await waitFor(
    () => expect(mockRouter.replace).toHaveBeenCalledWith("/chats"),
    {
      timeout: 5000,
    },
  );
  // Approved at once, so no code is shown to compare.
  expect(screen.queryByLabelText("Pairing matching code")).toBeNull();
  expect(JSON.parse(mockStore.get("enloq.credential")!)).toEqual({
    origin: "http://127.0.0.1:8776",
    token: "l0cal",
  });
  expect(calls[0]!.url).toBe("http://127.0.0.1:8776/fixture/pairing");
});

describe("Disconnect", () => {
  const SAVED = JSON.stringify({
    origin: "https://enloq.example.com",
    token: "t0ken",
  });
  let online = true;
  let disconnected = 0;

  beforeEach(() => {
    online = true;
    disconnected = 0;
    mockStore.set("enloq.credential", SAVED);
    serve((url, init) => {
      if (url.endsWith("/api/auth/disconnect")) {
        if (!online) return Promise.reject(new TypeError("offline"));
        expect(init.headers.Authorization).toBe("Bearer t0ken");
        disconnected++;
        return { status: 200, body: { ok: true } };
      }
      if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
      if (url.endsWith("/preferences/reading-view"))
        return { status: 200, body: { reading_view: "ai" } };
      if (url.includes("/chats?kind=")) return { status: 200, body: [] };
      if (url.endsWith("/api/auth/browsers")) return { status: 200, body: [] };
      throw new Error(`unexpected ${url}`);
    });
  });

  // Disconnect sits at the foot of Settings (ADR 45 Q4).
  const mountChats = () =>
    render(
      <ConnectionProvider>
        <Settings />
      </ConnectionProvider>,
    );

  async function openSheet() {
    await screen.findByRole("button", {
      name: "Disconnect from enloq.example.com",
    });
  }

  test("asks first, then leaves the server and forgets the phone's copy", async () => {
    await mountChats();
    writeCached("list:1", []);
    await openSheet();
    await fireEvent.press(
      screen.getByRole("button", { name: "Disconnect from enloq.example.com" }),
    );
    expect(screen.getByText(/forgets its pairing/)).toBeTruthy();
    await fireEvent.press(screen.getByRole("button", { name: "Cancel" }));
    expect(disconnected).toBe(0);
    await fireEvent.press(
      screen.getByRole("button", { name: "Disconnect from enloq.example.com" }),
    );
    await fireEvent.press(screen.getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(disconnected).toBe(1));
    await waitFor(() => expect(mockStore.has("enloq.credential")).toBe(false));
    expect([...mockFiles.keys()]).toEqual([]);
    expect(mockRedirects).toContain("/pair");
  });

  test("an unreachable server is left on the phone, which says so", async () => {
    await mountChats();
    await openSheet();
    online = false;
    await fireEvent.press(
      screen.getByRole("button", { name: "Disconnect from enloq.example.com" }),
    );
    await fireEvent.press(screen.getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(mockStore.has("enloq.credential")).toBe(false));
    expect(disconnected).toBe(0);
  });
});
