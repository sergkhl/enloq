import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import {
  mockClipboard,
  mockImages,
  mockRouter,
  mockStore,
  reset,
  serve,
} from "./setup";
import PairPhone from "../app/settings/pair";
import { ConnectionProvider } from "../src/connection";

// ADR 47 Q1: Pair a phone under Settings → Privacy shows a QR code for Enloq
// on a new phone, then the code that phone shows, with Trust.
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
const ID = "0123456789abcdef0123456789abcdef";

let state: Record<string, unknown>;
let posts: { path: string; body: unknown }[];

function pairing(change: Record<string, unknown>) {
  state = { ...state, ...change };
  return { status: 200, body: state };
}

beforeEach(() => {
  reset();
  mockImages.length = 0;
  mockStore.set("enloq.credential", SAVED);
  posts = [];
  state = {
    id: ID,
    expires_at: Date.now() / 1000 + 120,
    state: "waiting",
    code: "",
    label: "",
    kind: "",
    approver: "browser",
  };
  serve((url, init) => {
    const path = url.replace("https://enloq.example.com/api/", "");
    if (init.method === "POST") {
      const body = JSON.parse(init.body!);
      posts.push({ path, body });
      if (path === "auth/client-pairings") return pairing({});
      if (path === `auth/client-pairings/${ID}`)
        return pairing({
          state:
            body.action === "approve"
              ? "approved"
              : state.state === "complete"
                ? "complete"
                : "cancelled",
        });
    }
    if (path === `auth/client-pairings/${ID}`)
      return { status: 200, body: state };
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
    throw new Error(`unexpected ${url}`);
  });
});

const mount = () =>
  render(
    <ConnectionProvider>
      <PairPhone />
    </ConnectionProvider>,
  );

test("the QR code waits for a phone, whose code is trusted here", async () => {
  jest.useFakeTimers();
  try {
    await mount();
    expect(
      await screen.findByText(/On the new phone, open Enloq, choose Scan QR/),
    ).toBeTruthy();
    expect(posts[0]).toEqual({ path: "auth/client-pairings", body: {} });
    // The service draws the QR code; the phone reads it with its credential.
    expect(mockImages.at(-1)?.uri).toBe(
      `https://enloq.example.com/api/auth/client-pairings/${ID}/qr.svg`,
    );
    await fireEvent.press(screen.getByRole("button", { name: "Copy link" }));
    expect(mockClipboard).toEqual([`https://enloq.example.com/?connect=${ID}`]);
    // A phone claims it; the next poll shows its name and code.
    state = {
      ...state,
      state: "pending",
      code: "482910",
      label: "Enloq app on SM-S938B",
      kind: "device",
    };
    await act(async () => jest.advanceTimersByTime(1100));
    expect(
      await screen.findByText(
        "Enloq app on SM-S938B asks to pair with this server.",
      ),
    ).toBeTruthy();
    expect(screen.getByLabelText("Code to compare")).toHaveTextContent(
      "482910",
    );
    await fireEvent.press(screen.getByRole("button", { name: "Trust" }));
    await waitFor(() =>
      expect(posts.at(-1)).toEqual({
        path: `auth/client-pairings/${ID}`,
        body: { action: "approve", code: "482910" },
      }),
    );
    expect(await screen.findByText("Finishing on the new phone…")).toBeTruthy();
    state = { ...state, state: "complete" };
    await act(async () => jest.advanceTimersByTime(1100));
    expect(
      await screen.findByText(
        "Enloq app on SM-S938B is paired. It now shows in Privacy.",
      ),
    ).toBeTruthy();
    await fireEvent.press(screen.getByRole("button", { name: "Done" }));
    expect(mockRouter.back).toHaveBeenCalled();
  } finally {
    jest.useRealTimers();
  }
});

test("leaving cancels an unfinished pairing, and an expired one starts again", async () => {
  const shown = await mount();
  await screen.findByText(/On the new phone, open Enloq/);
  await shown.unmount();
  await waitFor(() =>
    expect(posts).toContainEqual({
      path: `auth/client-pairings/${ID}`,
      body: { action: "cancel" },
    }),
  );
  state = { ...state, state: "expired" };
  await mount();
  expect(await screen.findByText("The QR code expired.")).toBeTruthy();
  state = { ...state, state: "waiting" };
  await fireEvent.press(screen.getByRole("button", { name: "Start again" }));
  expect(await screen.findByText(/On the new phone, open Enloq/)).toBeTruthy();
});
