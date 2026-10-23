import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import { Platform } from "react-native";
import {
  mockEventSources,
  mockNotifications,
  mockStore,
  reset,
  serve,
} from "./setup";
// After ./setup, which installs the module doubles.
import * as Notifications from "expo-notifications";
import Chats from "../app/chats";
import NotificationsPage from "../app/settings/notifications";
import { ConnectionProvider } from "../src/connection";
import { usePushUpkeep } from "../src/push";

// The Android settings (ADR 38) under test here; the
// test platform is iOS, which shows none.
jest.mock("../src/phonePush", () => ({ phonePush: true }));

const native = Notifications as jest.Mocked<typeof Notifications>;

const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
const ACCOUNTS = {
  mode: "personal",
  timezone: "UTC",
  budget: [],
  login_available: true,
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
};

let device: { push: boolean; sound: boolean; available: boolean };
let posts: { url: string; body: unknown }[];
let browsers: unknown[];

function service() {
  return serve((url, init) => {
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    if (url.endsWith("/preferences/reading-view"))
      return { status: 200, body: { reading_view: "ai" } };
    if (url.includes("/chats?kind=private")) return { status: 200, body: [] };
    if (url.endsWith("/auth/browsers")) return { status: 200, body: browsers };
    if (url.includes("/notifications/device")) {
      const body = init.body ? JSON.parse(init.body) : undefined;
      if (init.method === "POST") posts.push({ url, body });
      if (url.endsWith("/device/test"))
        return { status: 200, body: { state: "sent" } };
      if (url.endsWith("/device") && init.method === "POST")
        device = { ...device, push: true };
      if (url.endsWith("/device/preferences"))
        device = {
          ...device,
          ...(body.push === false ? { push: false } : {}),
          ...(body.sound !== undefined ? { sound: body.sound } : {}),
        };
      return { status: 200, body: { ...device, failure: null } };
    }
    throw new Error(`unexpected ${url}`);
  });
}

// Settings → Notifications holds this phone's switch (ADR 45 Q3, ADR 47 Q6).
async function openSheet() {
  await render(
    <ConnectionProvider>
      <NotificationsPage />
    </ConnectionProvider>,
  );
  await screen.findByText("This phone");
}

const DEVICE = "https://enloq.example.com/api/notifications/device";

beforeEach(() => {
  reset();
  jest.clearAllMocks();
  device = { push: false, sound: true, available: true };
  posts = [];
  browsers = [];
  mockStore.set("enloq.credential", SAVED);
  service();
});

test("turning notifications on registers with FCM and gives the server the token", async () => {
  await openSheet();
  await fireEvent(
    await screen.findByLabelText("Notifications"),
    "valueChange",
    true,
  );
  await waitFor(() =>
    expect(posts).toContainEqual({
      url: DEVICE,
      body: { token: mockNotifications.token },
    }),
  );
  expect(native.registerTaskAsync).toHaveBeenCalledWith("enloq-push");
  expect(native.setNotificationChannelAsync).toHaveBeenCalled();
  await fireEvent.press(await screen.findByText("Send a test notification"));
  expect(await screen.findByText("Test sent.")).toBeTruthy();
  await fireEvent(screen.getByLabelText("Sound"), "valueChange", false);
  await waitFor(() => expect(posts.at(-1)?.body).toEqual({ sound: false }));
});

test("a refused permission says where to allow notifications", async () => {
  mockNotifications.granted = false;
  await openSheet();
  await fireEvent(
    await screen.findByLabelText("Notifications"),
    "valueChange",
    true,
  );
  expect(
    await screen.findByText(
      "Allow notifications for Enloq in Android Settings.",
    ),
  ).toBeTruthy();
  expect(native.getDevicePushTokenAsync).not.toHaveBeenCalled();
  expect(posts).toEqual([]);
});

test("a phone Google can't register says so and sends nothing", async () => {
  native.getDevicePushTokenAsync.mockRejectedValueOnce(new Error("SERVICE"));
  await openSheet();
  await fireEvent(
    await screen.findByLabelText("Notifications"),
    "valueChange",
    true,
  );
  expect(
    await screen.findByText(
      "This phone couldn't register for notifications with Google Play services. Try again later.",
    ),
  ).toBeTruthy();
  expect(posts).toEqual([]);
});

test("a server without FCM offers no switch", async () => {
  device = { push: false, sound: true, available: false };
  await openSheet();
  expect(
    await screen.findByText("This server doesn't send phone notifications."),
  ).toBeTruthy();
  expect(screen.queryByLabelText("Notifications")).toBeNull();
});

test("turning off deletes the phone's FCM token", async () => {
  device = { push: true, sound: true, available: true };
  await openSheet();
  await fireEvent(
    await screen.findByLabelText("Notifications"),
    "valueChange",
    false,
  );
  await waitFor(() => expect(posts.at(-1)?.body).toEqual({ push: false }));
  expect(native.unregisterForNotificationsAsync).toHaveBeenCalled();
});

test("a forgotten phone deletes its FCM token too", async () => {
  await render(
    <ConnectionProvider>
      <Chats />
    </ConnectionProvider>,
  );
  await screen.findByLabelText("New message");
  await act(async () => mockEventSources[0]!.emit("revoked"));
  await waitFor(() =>
    expect(native.unregisterForNotificationsAsync).toHaveBeenCalled(),
  );
});

function Upkeep() {
  usePushUpkeep();
  return null;
}

test("a phone with push on registers at start and passes on rotated tokens", async () => {
  device = { push: true, sound: true, available: true };
  await render(
    <ConnectionProvider>
      <Upkeep />
    </ConnectionProvider>,
  );
  await waitFor(() =>
    expect(posts).toContainEqual({
      url: DEVICE,
      body: { token: mockNotifications.token },
    }),
  );
  await act(async () =>
    mockNotifications.tokenListeners[0]!({
      data: "rotatedAuthoredToken0123456789ab",
    }),
  );
  await waitFor(() =>
    expect(posts.at(-1)).toEqual({
      url: DEVICE,
      body: { token: "rotatedAuthoredToken0123456789ab" },
    }),
  );
});

test("a phone with push off never asks Google for a token", async () => {
  await render(
    <ConnectionProvider>
      <Upkeep />
    </ConnectionProvider>,
  );
  await waitFor(() =>
    expect(global.fetch).toHaveBeenCalledWith(
      DEVICE,
      expect.objectContaining({ method: "GET" }),
    ),
  );
  expect(native.getDevicePushTokenAsync).not.toHaveBeenCalled();
  expect(native.addPushTokenListener).not.toHaveBeenCalled();
  expect(posts).toEqual([]);
});

// ADR 47 Q4: the list asks once per pairing, while this phone's notifications
// are off. The pairing is this phone's trusted entry.
describe("the ask on opening", () => {
  const CURRENT = {
    id: "c".repeat(32),
    label: "Enloq app on Pixel 9",
    approved_at: 0,
    last_access: 0,
    current: true,
    kind: "device",
  };
  const list = () =>
    render(
      <ConnectionProvider>
        <Chats />
      </ConnectionProvider>,
    );

  beforeEach(() => {
    browsers = [CURRENT];
  });

  test("Android 12 and older ask in a sheet, once per pairing", async () => {
    const shown = await list();
    expect(await screen.findByText("Turn on notifications?")).toBeTruthy();
    expect(
      screen.getByText(
        "New private messages alert this phone. Groups never alert.",
      ),
    ).toBeTruthy();
    await fireEvent.press(screen.getByRole("button", { name: "Not now" }));
    // No yes, so Google was never contacted (ADR 38 Q42).
    expect(native.getDevicePushTokenAsync).not.toHaveBeenCalled();
    expect(mockStore.get("enloq.notifications-asked")).toBe(CURRENT.id);
    await shown.unmount();
    await list();
    await screen.findByLabelText("New message");
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(screen.queryByText("Turn on notifications?")).toBeNull();
    // A new pairing is a new trusted entry, which asks again.
    browsers = [{ ...CURRENT, id: "d".repeat(32) }];
    await list();
    expect(await screen.findByText("Turn on notifications?")).toBeTruthy();
  });

  // Pairing again over an earlier list leaves two lists mounted (ADR 48's
  // re-pairing after Leave Enloq or Disconnect).
  test("two lists mounted at once ask once", async () => {
    await render(
      <ConnectionProvider>
        <Chats />
        <Chats />
      </ConnectionProvider>,
    );
    expect(await screen.findByText("Turn on notifications?")).toBeTruthy();
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(screen.getAllByText("Turn on notifications?")).toHaveLength(1);
  });

  test("the sheet's Turn on registers with FCM", async () => {
    await list();
    await fireEvent.press(
      await screen.findByRole("button", { name: "Turn on" }),
    );
    await waitFor(() =>
      expect(posts).toContainEqual({
        url: DEVICE,
        body: { token: mockNotifications.token },
      }),
    );
    await waitFor(() =>
      expect(screen.queryByText("Turn on notifications?")).toBeNull(),
    );
  });

  test("Android 13 and later show Android's own dialog at once", async () => {
    const os = jest.replaceProperty(Platform, "OS", "android");
    const version = jest.spyOn(Platform, "Version", "get").mockReturnValue(34);
    try {
      await list();
      await waitFor(() =>
        expect(posts).toContainEqual({
          url: DEVICE,
          body: { token: mockNotifications.token },
        }),
      );
      expect(native.requestPermissionsAsync).toHaveBeenCalled();
      expect(screen.queryByText("Turn on notifications?")).toBeNull();
    } finally {
      os.restore();
      version.mockRestore();
    }
  });

  test("nothing is asked while notifications are on", async () => {
    device = { push: true, sound: true, available: true };
    await list();
    await screen.findByLabelText("New message");
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(screen.queryByText("Turn on notifications?")).toBeNull();
    expect(mockStore.has("enloq.notifications-asked")).toBe(false);
  });
});
