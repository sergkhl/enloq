import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { mockRouter, mockStore, reset, serve } from "./setup";
import Pair from "../app/pair";
import Waiting from "../app/waiting";
import { AgreementGate } from "../src/AgreementGate";
import { ConnectionProvider, DECLINED_NOTICE } from "../src/connection";

// ADR 48 MB4: a stranger signs in with Telegram, reads the Member Agreement,
// asks to join and waits until the owner decides; a member whose agreement
// changed reads it again before using the app.
const ATTEMPT = { id: "0123456789abcdef0123456789abcdef", proof: "j01n" };
const AGREEMENT = {
  version: "2026-10-17",
  title: "Before you ask to join",
  paragraphs: ["Authored first paragraph.", "Authored second paragraph."],
};
const login = (stage: string, extra = {}) => ({
  id: "f1",
  method: "phone",
  stage,
  message: stage === "code" ? "Enter the code." : "",
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
const me = (state: string, agreement: unknown = null) => ({
  status: 200,
  body: { id: "m1", role: "member", state, name: "Demo 23", agreement },
});
const ACCOUNTS = {
  accounts: [],
  budget: [],
  mode: "personal",
  timezone: "UTC",
  login_available: true,
};

beforeEach(() => {
  reset();
  jest.useRealTimers();
});

function mount(screenElement: React.ReactElement) {
  return render(<ConnectionProvider>{screenElement}</ConnectionProvider>);
}

test("a stranger asks to join, waits and moves on once approved", async () => {
  let state = "waiting";
  const calls = serve((url, init) => {
    if (url.endsWith("/api/auth/sign-in-pairings"))
      return {
        status: 200,
        body: { ...ATTEMPT, login: login("code") },
      };
    if (url.endsWith(`/sign-in-pairings/${ATTEMPT.id}/code`))
      return {
        status: 200,
        body: {
          id: ATTEMPT.id,
          proof: "",
          login: login("agreement", { account_id: 23 }),
          agreement: AGREEMENT,
        },
      };
    if (url.endsWith(`/sign-in-pairings/${ATTEMPT.id}/agree`))
      return {
        status: 200,
        body: {
          id: ATTEMPT.id,
          proof: "",
          login: login("complete", { account_id: 23 }),
          token: "w4it",
          waiting: true,
        },
      };
    if (url.endsWith("/api/members/me")) return me(state);
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  const view = await mount(<Pair />);
  await fireEvent.press(
    await screen.findByRole("button", { name: "Sign in with Telegram" }),
  );
  await fireEvent.changeText(
    screen.getByLabelText("Phone number"),
    "+1 202 555 0123",
  );
  await fireEvent.press(screen.getByRole("button", { name: "Continue" }));
  await fireEvent.changeText(
    await screen.findByLabelText("Login code"),
    "12345",
  );
  expect(await screen.findByText("Before you ask to join")).toBeTruthy();
  expect(screen.getByText("Authored second paragraph.")).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", { name: "Agree and ask to join" }),
  );
  const agreed = await waitFor(() =>
    calls.find((call) => call.url.endsWith("/agree")),
  );
  expect(JSON.parse(agreed!.body!)).toEqual({ version: "2026-10-17" });
  expect(agreed!.headers["X-Pairing-Proof"]).toBe("j01n");
  await waitFor(() =>
    expect(mockRouter.replace).toHaveBeenCalledWith("/waiting"),
  );
  expect(JSON.parse(mockStore.get("enloq.credential")!).token).toBe("w4it");
  // The waiting screen reads only its member until the owner decides.
  await view.rerender(
    <ConnectionProvider>
      <Waiting />
    </ConnectionProvider>,
  );
  expect(await screen.findByText("Waiting for approval")).toBeTruthy();
  expect(calls.some((call) => call.url.endsWith("/api/accounts"))).toBe(false);
  state = "active";
  await waitFor(
    () =>
      expect(calls.some((call) => call.url.endsWith("/api/accounts"))).toBe(
        true,
      ),
    { timeout: 8000 },
  );
}, 15000);

test("a declined request returns the phone to pairing with a note", async () => {
  mockStore.set(
    "enloq.credential",
    JSON.stringify({ origin: "https://enloq.globesoul.com", token: "w4it" }),
  );
  let declined = false;
  serve((url, init) => {
    if (url.endsWith("/api/members/me"))
      return declined
        ? { status: 401, body: { detail: "This device is no longer trusted." } }
        : me("waiting");
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  await mount(<Pair />);
  await waitFor(() => expect(mockStore.get("enloq.credential")).toBeTruthy());
  declined = true;
  expect(
    await screen.findByText(DECLINED_NOTICE, {}, { timeout: 8000 }),
  ).toBeTruthy();
  expect(mockStore.get("enloq.credential")).toBeUndefined();
}, 15000);

test("withdrawing forgets the request on this phone", async () => {
  mockStore.set(
    "enloq.credential",
    JSON.stringify({ origin: "https://enloq.globesoul.com", token: "w4it" }),
  );
  const calls = serve((url, init) => {
    if (url.endsWith("/api/members/me")) return me("waiting");
    if (url.endsWith("/api/auth/disconnect")) return { status: 200, body: {} };
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  await mount(<Waiting />);
  await fireEvent.press(
    await screen.findByRole("button", { name: "Withdraw request" }),
  );
  await waitFor(() =>
    expect(calls.some((call) => call.url.endsWith("/auth/disconnect"))).toBe(
      true,
    ),
  );
  await waitFor(() =>
    expect(mockStore.get("enloq.credential")).toBeUndefined(),
  );
});

test("a changed agreement covers the app until the member accepts it", async () => {
  mockStore.set(
    "enloq.credential",
    JSON.stringify({ origin: "https://enloq.globesoul.com", token: "m3mb" }),
  );
  let agreed = false;
  const calls = serve((url, init) => {
    if (url.endsWith("/api/members/me/agreement")) {
      agreed = true;
      return me("active");
    }
    if (url.endsWith("/api/members/me"))
      return me("active", agreed ? null : AGREEMENT);
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  await mount(<AgreementGate />);
  expect(
    await screen.findByText(/agreement for using Enloq changed/),
  ).toBeTruthy();
  await act(async () => {
    fireEvent.press(screen.getByRole("button", { name: "Agree" }));
  });
  const accepted = await waitFor(() =>
    calls.find((call) => call.url.endsWith("/members/me/agreement")),
  );
  expect(JSON.parse(accepted!.body!)).toEqual({ version: "2026-10-17" });
  await waitFor(() =>
    expect(screen.queryByText(/agreement for using Enloq changed/)).toBeNull(),
  );
});
