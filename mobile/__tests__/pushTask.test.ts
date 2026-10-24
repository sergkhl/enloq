import { mockNotifications, mockStore, reset, serve } from "./setup";
// After ./setup, which installs the module doubles.
import * as Notifications from "expo-notifications";
import { readPush } from "../src/pushTask";

// ADR 38: a push carries only the opaque event and the
// phone fetches sender and preview itself. The test platform is iOS, which
// runs no task; these cases run it as Android.
jest.mock("../src/phonePush", () => ({ phonePush: true }));

const native = Notifications as jest.Mocked<typeof Notifications>;
// Set when the task module loaded, before each case clears the doubles.
const handler = native.setNotificationHandler.mock.calls[0]![0]!;
const ID = "0123456789abcdef0123456789abcdef";
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});

function push(fields: Record<string, unknown>) {
  return {
    notification: null,
    data: {
      payload: JSON.stringify({
        id: ID,
        expires: Date.now() / 1000 + 300,
        silent: false,
        ...fields,
      }),
    },
  };
}

async function run(data: unknown) {
  await mockNotifications.tasks.get("enloq-push")!({ data, error: null });
}

beforeEach(() => {
  reset();
  jest.clearAllMocks();
  mockStore.set("enloq.credential", SAVED);
});

test("only a live, well-formed event counts", () => {
  expect(readPush(push({}))).toEqual({ id: ID, silent: false });
  expect(readPush(push({ silent: true }))).toEqual({ id: ID, silent: true });
  expect(readPush(push({ expires: Date.now() / 1000 - 1 }))).toBeNull();
  expect(readPush(push({ id: "not-an-event" }))).toBeNull();
  expect(readPush({ data: { payload: "{" } })).toBeNull();
  expect(readPush({ data: {} })).toBeNull();
  expect(readPush(null)).toBeNull();
});

test("the task fetches sender and preview and shows them on the chat's notice", async () => {
  const calls = serve((url) => {
    expect(url).toBe("https://enloq.example.com/api/notifications/validate");
    return {
      status: 200,
      body: {
        id: ID,
        account_id: 1,
        peer: "user:42",
        sender: "Authored sender",
        account: "Owner",
        count: 1,
        text: "Validated rewrite",
        expires: 0,
        silent: false,
      },
    };
  });
  await run(push({}));
  expect(calls[0]!.headers.Authorization).toBe("Bearer t0ken");
  expect(JSON.parse(calls[0]!.body!)).toEqual({ id: ID });
  expect(mockNotifications.scheduled).toEqual([
    {
      identifier: "chat:1:user:42",
      content: {
        title: "Authored sender",
        body: "Validated rewrite",
        data: { account: 1, peer: "user:42" },
        sound: "default",
        priority: "high",
      },
      trigger: { channelId: "messages" },
    },
  ]);
});

test("a join request's push becomes a notice that opens People", async () => {
  serve(() => ({
    status: 200,
    body: {
      id: ID,
      kind: "join",
      account_id: null,
      peer: null,
      sender: "Enloq",
      account: "People",
      count: 1,
      text: "Demo 23 asks to join Enloq",
      expires: 0,
      silent: false,
    },
  }));
  await run(push({}));
  expect(mockNotifications.scheduled).toEqual([
    {
      identifier: "people",
      content: {
        title: "Enloq",
        body: "Demo 23 asks to join Enloq",
        data: { target: "people" },
        sound: "default",
        priority: "high",
      },
      trigger: { channelId: "messages" },
    },
  ]);
});

test("an unreachable server still leaves a generic notice", async () => {
  serve(() => {
    throw new TypeError("Network request failed");
  });
  await run(push({ silent: true }));
  expect(mockNotifications.scheduled).toEqual([
    {
      identifier: ID,
      content: {
        title: "Enloq",
        body: "New messages are ready",
        data: {},
        sound: false,
        priority: "high",
      },
      trigger: { channelId: "messages-quiet" },
    },
  ]);
});

test.each([400, 401, 403])(
  "a refused or revoked locator (%s) shows no duplicate generic banner",
  async (status) => {
    serve(() => ({
      status,
      body: { detail: "Notification is no longer available" },
    }));
    await run(push({}));
    expect(mockNotifications.scheduled).toHaveLength(0);
  },
);

test("later burst updates replace the chat notice without sound", async () => {
  serve(() => ({
    status: 200,
    body: {
      account_id: 1,
      peer: "user:42",
      sender: "Authored sender",
      text: "Newer preview",
      silent: true,
    },
  }));
  await run(push({ id: "b".repeat(32), silent: true }));
  expect(mockNotifications.scheduled[0]).toMatchObject({
    identifier: "chat:1:user:42",
    content: { sound: false, body: "Newer preview" },
    trigger: { channelId: "messages-quiet" },
  });
  await expect(
    handler.handleNotification({
      request: {
        content: {
          title: "Authored sender",
          body: "Newer preview",
          sound: null,
        },
      },
    } as unknown as Notifications.Notification),
  ).resolves.toMatchObject({ shouldPlaySound: false });
});

test("redelivery of the same event alerts once across concurrent task callbacks", async () => {
  serve(() => ({
    status: 200,
    body: {
      account_id: 1,
      peer: "user:42",
      sender: "Authored sender",
      text: "Authored preview",
      silent: false,
    },
  }));
  await Promise.all([run(push({})), run(push({}))]);
  await run(push({}));
  expect(mockNotifications.scheduled).toHaveLength(1);
  await run(push({ id: "c".repeat(32), silent: true }));
  expect(mockNotifications.scheduled).toHaveLength(2);
  expect(mockNotifications.scheduled[1]).toMatchObject({
    content: { sound: false },
  });
});

test("a phone without its credential shows only the generic notice", async () => {
  mockStore.clear();
  const calls = serve(() => ({ status: 500, body: null }));
  await run(push({}));
  expect(calls).toEqual([]);
  expect(mockNotifications.scheduled).toHaveLength(1);
  expect(mockNotifications.scheduled[0]!.identifier).toBe(ID);
});

test("expired or foreign pushes show nothing", async () => {
  serve(() => ({ status: 500, body: null }));
  await run(push({ expires: 1 }));
  await run({ notification: null, data: { body: "{}" } });
  expect(mockNotifications.scheduled).toEqual([]);
});

test("the data push stays hidden while its notice shows in the open app", async () => {
  const notice = (content: Record<string, unknown>) =>
    ({ request: { content } }) as unknown as Notifications.Notification;
  await expect(
    handler.handleNotification(notice({ title: null, body: null })),
  ).resolves.toEqual({
    shouldShowBanner: false,
    shouldShowList: false,
    shouldPlaySound: false,
    shouldSetBadge: false,
  });
  await expect(
    handler.handleNotification(notice({ title: "Authored sender", body: "x" })),
  ).resolves.toMatchObject({ shouldShowBanner: true, shouldSetBadge: false });
});
