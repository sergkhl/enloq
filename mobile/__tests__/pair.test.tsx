import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { mockRouter, mockStore, reset, serve } from "./setup";
import Pair from "../app/pair";
import { ConnectionProvider } from "../src/connection";

const ID = "0123456789abcdef0123456789abcdef";
const LINK = `https://enloq.example.com/?connect=${ID}`;

function claimed(state = "pending") {
  return {
    id: ID,
    expires_at: 0,
    state,
    code: "482910",
    label: "Enloq app on Pixel 9",
    kind: "device",
    approver: "browser",
  };
}

beforeEach(reset);

async function pasteAndConnect(link = LINK) {
  await render(
    <ConnectionProvider>
      <Pair />
    </ConnectionProvider>,
  );
  await fireEvent.changeText(
    await screen.findByLabelText("Pairing link"),
    link,
  );
  await fireEvent.press(screen.getByText("Connect"));
}

test("pairs with a pasted link, shows the code and keeps the credential", async () => {
  let polls = 0;
  const calls = serve((url, init) => {
    if (url.endsWith(`/device`) && init.method === "POST")
      return { status: 200, body: { ...claimed(), proof: "pr00f" } };
    if (url.endsWith(`/pairings/${ID}`))
      return {
        status: 200,
        body: claimed(++polls > 1 ? "approved" : "pending"),
      };
    if (url.endsWith("/device/complete"))
      return { status: 200, body: { token: "t0ken" } };
    if (url.endsWith("/api/accounts"))
      return {
        status: 200,
        body: {
          accounts: [],
          budget: [],
          mode: "personal",
          timezone: "UTC",
          login_available: true,
        },
      };
    throw new Error(`unexpected ${url}`);
  });
  await pasteAndConnect();
  expect(
    await screen.findByLabelText("Pairing matching code"),
  ).toHaveTextContent("482910");
  expect(screen.getByText(/phone or browser showing the QR code/)).toBeTruthy();
  await waitFor(
    () => expect(mockRouter.replace).toHaveBeenCalledWith("/chats"),
    {
      timeout: 5000,
    },
  );
  expect(JSON.parse(mockStore.get("enloq.credential")!)).toEqual({
    origin: "https://enloq.example.com",
    token: "t0ken",
  });
  const claim = calls.find((call) => call.url.endsWith("/device"))!;
  expect(claim.url).toBe(
    `https://enloq.example.com/api/auth/pairings/${ID}/device`,
  );
  expect(JSON.parse(claim.body!)).toEqual({ name: "Pixel 9" });
  expect(claim.headers.Authorization).toBeUndefined();
  const complete = calls.find((call) => call.url.endsWith("/device/complete"))!;
  expect(complete.headers["X-Pairing-Proof"]).toBe("pr00f");
  // The new credential is used from then on.
  await waitFor(() =>
    expect(calls.some((call) => call.url.endsWith("/api/accounts"))).toBe(true),
  );
  const accounts = calls.find((call) => call.url.endsWith("/api/accounts"))!;
  expect(accounts.headers.Authorization).toBe("Bearer t0ken");
}, 10000);

test("refuses a link from elsewhere without contacting anything", async () => {
  const calls = serve(() => {
    throw new Error("no request expected");
  });
  await pasteAndConnect(`http://enloq.example.com/?connect=${ID}`);
  expect(
    await screen.findByText("That isn't a pairing link from your server."),
  ).toBeTruthy();
  expect(calls).toHaveLength(0);
});

test("an expired attempt ends with its reason and keeps nothing", async () => {
  serve((url, init) => {
    if (url.endsWith(`/device`) && init.method === "POST")
      return { status: 200, body: { ...claimed(), proof: "pr00f" } };
    return { status: 200, body: claimed("expired") };
  });
  await pasteAndConnect();
  expect(
    await screen.findByText(
      "The pairing code expired. Start again.",
      {},
      { timeout: 4000 },
    ),
  ).toBeTruthy();
  expect(mockStore.size).toBe(0);
  expect(mockRouter.replace).not.toHaveBeenCalled();
});

test("a used link shows the server's refusal", async () => {
  serve(() => ({
    status: 400,
    body: { detail: "This pairing was already used. Start a new one." },
  }));
  await pasteAndConnect();
  expect(
    await screen.findByText("This pairing was already used. Start a new one."),
  ).toBeTruthy();
});
