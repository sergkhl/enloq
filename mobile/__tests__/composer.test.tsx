import {
  act,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "@testing-library/react-native";
import React from "react";
import {
  mockEventSources,
  mockFiles,
  mockParams,
  mockPhotos,
  mockRouter,
  mockStore,
  reset,
  serve,
} from "./setup";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { ConnectionProvider } from "../src/connection";
import type { Draft, Operation } from "../src/drafts";

const ATTEMPT = "a".repeat(32);
const SUGGESTED = "s".repeat(32);
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

function message(id: number, text: string, extra = {}) {
  return {
    account_id: 1,
    peer: "user:101",
    message_id: id,
    revision: 1,
    date: 1_700_000_000 + id * 60,
    text,
    outgoing: false,
    service: false,
    deleted: false,
    media: null,
    ...extra,
  };
}

// An authored stand-in for the service's draft rules: every change names
// the revision it edits, and a stale one is refused.
let draft: Draft;
let operations: Operation[];
let suggestions: unknown;
let messages: unknown[];
let tone = "neutral";
let online = true;
// The service confirms and stores a send before the phone's next read.
let confirmAtOnce = false;
let calls: ReturnType<typeof serve>;
let attention: unknown[] = [];

const posts = (suffix: string) =>
  calls
    .filter((call) => call.method === "POST" && call.url.endsWith(suffix))
    .map((call) => JSON.parse(call.body!));
const keptDraft = () => {
  const key = [...mockFiles.keys()].find((name) =>
    name.includes(encodeURIComponent("draft:1/user:101")),
  );
  return key ? JSON.parse(mockFiles.get(key)!).value : undefined;
};

function next(change: Partial<Draft>) {
  draft = { ...draft, ...change, revision: draft.revision + 1 };
  return { status: 200, body: draft };
}

function service() {
  return serve((url, init) => {
    if (!online) return Promise.reject(new TypeError("offline"));
    const body = init.body ? JSON.parse(init.body) : {};
    if (url.endsWith("/api/accounts")) return { status: 200, body: ACCOUNTS };
    if (url.endsWith("/preferences/reading-view"))
      return { status: 200, body: { reading_view: "ai" } };
    if (url.endsWith("/api/preferences")) {
      if (init.method === "POST") tone = body.tone;
      return { status: 200, body: { tone, revision: 1 } };
    }
    if (url.endsWith("/chats/user%3A101"))
      return {
        status: 200,
        body: {
          timezone: "UTC",
          chat: {
            account_id: 1,
            peer: "user:101",
            title: "Anna",
            photo_id: null,
            read_outbox: 2,
          },
          messages,
          blocks: [],
          unread_sources: [],
          draft,
          operations,
          suggestions,
          reply_holds: {},
        },
      };
    if (url.endsWith("/notifications/attention")) {
      attention.push(JSON.parse(init.body!));
      return { status: 200, body: { events: [], sound: false } };
    }
    if (url.includes("/ready-sources"))
      return { status: 200, body: { content: [], next_cursor: null } };
    if (init.method !== "POST") throw new Error(`unexpected ${url}`);
    if (body.revision !== undefined && body.revision !== draft.revision)
      return {
        status: 409,
        body: { detail: "Draft changed; keep your edits and reload" },
      };
    if (url.endsWith("/draft"))
      return next({
        text: body.text,
        reply_to: body.reply_to,
        undo: null,
        state: "idle",
        attempt: null,
      });
    if (url.endsWith("/rewrite"))
      return next({ state: "rewriting", attempt: ATTEMPT, undo: draft.text });
    if (url.endsWith("/rewrite/stop"))
      return next({
        text: body.text ?? draft.undo!,
        state: "stopped",
        attempt: null,
        partial: undefined,
      });
    if (url.endsWith("/undo"))
      return next({
        text: draft.undo!,
        undo: null,
        state: "idle",
        attempt: null,
      });
    if (url.endsWith("/suggestions/insert")) {
      const text = `${draft.text.slice(0, body.cursor ?? undefined)} Sounds good!`;
      next({ text, undo: draft.text, state: "inserted" });
      return { status: 200, body: { draft, caret: text.length } };
    }
    // ADR 56 Q12 B: the shown set's fold, kept for every client; a set that
    // changed since refuses it.
    if (url.endsWith("/suggestions/fold")) {
      const shown = suggestions as { attempt: string | null } | null;
      if (shown?.attempt !== body.attempt)
        return {
          status: 409,
          body: { detail: "Suggestions changed; reload the conversation" },
        };
      suggestions = { ...shown, folded: body.folded };
      return { status: 200, body: suggestions };
    }
    if (url.endsWith("/send")) {
      const operation: Operation = {
        id: "op1",
        state: "queued",
        text: draft.text,
        images: [],
        reply_to: draft.reply_to,
        created: 1_700_000_500,
        native_ids: [],
        submitted: true,
        retryable: false,
        cancellable: true,
        checkable: false,
      };
      if (confirmAtOnce)
        messages = [
          ...messages,
          message(50, operation.text, { outgoing: true }),
        ];
      else operations = [...operations, operation];
      next({ text: "", reply_to: null, undo: null, state: "idle" });
      return { status: 200, body: { operation, draft } };
    }
    if (url.endsWith("/images"))
      return next({ attachments: [...draft.attachments, `img${body.name}`] });
    const removed = /\/images\/([^/]+)\/remove$/.exec(url);
    if (removed)
      return next({
        attachments: draft.attachments.filter(
          (id) => id !== decodeURIComponent(removed[1]!),
        ),
      });
    const action = /\/operations\/(\w+)\/(\w+)$/.exec(url);
    if (action) {
      const [, id, verb] = action;
      operations = operations.map((operation) =>
        operation.id !== id
          ? operation
          : verb === "cancel"
            ? {
                ...operation,
                state: "cancelled",
                reason: "Cancelled. Nothing was sent.",
                cancellable: false,
              }
            : {
                ...operation,
                check:
                  "No delivery receipt yet. Nothing was resent; check the chat in another Telegram app or try again later.",
              },
      );
      return {
        status: 200,
        body: operations.find((operation) => operation.id === id),
      };
    }
    throw new Error(`unexpected ${url}`);
  });
}

function mount() {
  return render(
    <ConnectionProvider>
      <ConversationRoute />
    </ConnectionProvider>,
  );
}

beforeEach(() => {
  reset();
  online = true;
  confirmAtOnce = false;
  attention = [];
  tone = "neutral";
  draft = {
    chat_key: "1/user:101",
    revision: 0,
    text: "",
    attachments: [],
    selected_media: null,
    reply_to: null,
    undo: null,
    state: "idle",
    attempt: null,
    error: null,
  };
  operations = [];
  suggestions = null;
  messages = [message(1, "Are you coming tonight?")];
  mockStore.set("enloq.credential", SAVED);
  mockParams.account = "1";
  mockParams.peer = "user:101";
  calls = service();
});

test("typing saves against the last revision; the phone's copy goes once saved", async () => {
  await mount();
  await fireEvent.changeText(await screen.findByLabelText("Message"), "Hi");
  await waitFor(() => expect(draft.text).toBe("Hi"));
  expect(posts("/draft")).toEqual([
    { revision: 0, text: "Hi", reply_to: null },
  ]);
  expect(keptDraft()).toBeUndefined();
});

test("a draft typed offline stays on the phone and is saved when the server answers", async () => {
  const first = await mount();
  const field = await screen.findByLabelText("Message");
  online = false;
  await fireEvent.changeText(field, "Draft on the train");
  expect(
    await screen.findByText("Kept on this phone until the server answers."),
  ).toBeTruthy();
  expect(keptDraft()).toEqual({ text: "Draft on the train", reply_to: null });
  await first.unmount();
  online = true;
  await mount();
  expect(
    await screen.findByText("Unsaved draft restored on this phone."),
  ).toBeTruthy();
  await waitFor(() => expect(draft.text).toBe("Draft on the train"));
  expect(keptDraft()).toBeUndefined();
});

test("Rewrite streams into a read-only field; Stop keeps what was shown; Undo restores", async () => {
  draft = { ...draft, revision: 3, text: "can u come" };
  await mount();
  await screen.findByDisplayValue("can u come");
  await fireEvent.press(screen.getByLabelText("Rewrite"));
  await waitFor(() => expect(posts("/rewrite")).toEqual([{ revision: 3 }]));
  draft = { ...draft, text: "Could you", partial: true };
  await act(async () =>
    mockEventSources[0]!.emit("change", {
      data: '{"chats":{"1":["1/user:101"]}}',
    }),
  );
  expect(await screen.findByDisplayValue("Could you")).toBeTruthy();
  expect(screen.getByText("Processing…")).toBeTruthy();
  expect(screen.getByLabelText("Message").props.editable).toBe(false);
  await fireEvent.press(screen.getByLabelText("Stop rewrite"));
  await waitFor(() =>
    expect(posts("/rewrite/stop")).toEqual([
      { revision: 4, attempt: ATTEMPT, text: "Could you" },
    ]),
  );
  expect(await screen.findByText("Stopped · incomplete draft")).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Undo rewrite"));
  await waitFor(() =>
    expect(posts("/undo")).toEqual([{ revision: 5, attempt: null }]),
  );
  expect(await screen.findByDisplayValue("can u come")).toBeTruthy();
});

test("Send shows the message at once as sent, clears the draft, and its sheet offers Cancel", async () => {
  draft = { ...draft, revision: 2, text: "See you at eight" };
  await mount();
  await screen.findByDisplayValue("See you at eight");
  await fireEvent.press(screen.getByLabelText("Send"));
  expect(await screen.findByLabelText("Sending message")).toBeTruthy();
  await waitFor(() => expect(posts("/send")).toEqual([{ revision: 2 }]));
  await waitFor(() =>
    expect(screen.getByLabelText("Message").props.value).toBe(""),
  );
  // No button in the bubble: a clock in its time line (ADR 51 Q3, Q4).
  expect(screen.queryByText("Cancel send")).toBeNull();
  expect(screen.getAllByLabelText("Sending").length).toBeGreaterThan(0);
  await waitFor(() =>
    expect(
      screen.getByLabelText("Sending message").props.accessibilityHint,
    ).toBe("Long press for message actions"),
  );
  await fireEvent(screen.getByLabelText("Sending message"), "longPress");
  await fireEvent.press(
    await screen.findByRole("menuitem", { name: "Cancel send" }),
  );
  expect(await screen.findByLabelText("Cancelled message")).toBeTruthy();
});

test("a send confirmed before the next read leaves only its message", async () => {
  draft = { ...draft, revision: 2, text: "On my way" };
  await mount();
  await screen.findByDisplayValue("On my way");
  confirmAtOnce = true;
  await fireEvent.press(screen.getByLabelText("Send"));
  await waitFor(() => expect(posts("/send")).toHaveLength(1));
  await waitFor(() =>
    expect(screen.queryByLabelText("Sending message")).toBeNull(),
  );
  expect(screen.getAllByText("On my way")).toHaveLength(1);
  expect(screen.getByLabelText("Message").props.value).toBe("");
});

test("an unreachable server sends nothing new and keeps the draft", async () => {
  draft = { ...draft, revision: 2, text: "Running late" };
  await mount();
  await screen.findByDisplayValue("Running late");
  online = false;
  await fireEvent.press(screen.getByLabelText("Send"));
  expect(
    await screen.findByText(
      "enloq.example.com can't be reached. Your draft is kept; check the chat before sending again.",
    ),
  ).toBeTruthy();
  expect(screen.getByDisplayValue("Running late")).toBeTruthy();
  expect(screen.queryByLabelText("Sending message")).toBeNull();
});

test("suggestions waiting for budget open the Daily Budget", async () => {
  suggestions = {
    state: "waiting",
    reason: "budget",
    attempt: null,
    options: [],
    source_time: 1_700_000_060,
    expires_at: null,
  };
  await mount();
  await fireEvent.press(await screen.findByText("Suggestions wait for budget"));
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/settings/budget",
    params: { section: "daily" },
  });
});

test("a suggestion goes in at the caret, with Undo suggestion", async () => {
  suggestions = {
    state: "ready",
    reason: null,
    attempt: SUGGESTED,
    options: [{ purpose: "acknowledge", text: "Sounds good!" }],
    source_time: 1_700_000_060,
    expires_at: null,
  };
  draft = { ...draft, revision: 1, text: "Hi there" };
  await mount();
  const field = await screen.findByDisplayValue("Hi there");
  await fireEvent(field, "selectionChange", {
    nativeEvent: { selection: { start: 2, end: 2 } },
  });
  await fireEvent.press(screen.getByLabelText("Acknowledge: Sounds good!"));
  await waitFor(() =>
    expect(posts("/suggestions/insert")).toEqual([
      { revision: 1, attempt: SUGGESTED, index: 0, cursor: 2 },
    ]),
  );
  expect(await screen.findByDisplayValue("Hi Sounds good!")).toBeTruthy();
  expect(screen.getByLabelText("Undo suggestion")).toBeTruthy();
});

// ADR 49 Q8: contrasting answer stances.
const ANSWERS = {
  state: "ready",
  reason: null,
  attempt: SUGGESTED,
  options: [
    { purpose: "accept", text: "Yes, I'll be there." },
    { purpose: "decline", text: "Sorry, I can't make it." },
    { purpose: "later", text: "Let me check and get back to you." },
  ],
  folded: false,
  source_time: 1_700_000_060,
  expires_at: null,
};
const changed = () =>
  act(async () =>
    mockEventSources[0]!.emit("change", {
      data: '{"chats":{"1":["1/user:101"]}}',
    }),
  );

test("a set of answers is labelled by stance and folds on every client", async () => {
  // ADR 56 Q12 B: the server keeps the fold, so each client shows it.
  suggestions = ANSWERS;
  await mount();
  expect(
    await screen.findByLabelText("Accept: Yes, I'll be there."),
  ).toBeTruthy();
  expect(
    screen.getByLabelText("Decline: Sorry, I can't make it."),
  ).toBeTruthy();
  expect(
    screen.getByLabelText("Later: Let me check and get back to you."),
  ).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Hide suggestions"));
  expect(screen.queryByLabelText("Accept: Yes, I'll be there.")).toBeNull();
  expect(screen.getByText("Suggestions (3)")).toBeTruthy();
  await waitFor(() =>
    expect(posts("/suggestions/fold")).toEqual([
      { attempt: SUGGESTED, folded: true },
    ]),
  );
  // The next read carries the fold; another client's unfolding shows too.
  await changed();
  expect(screen.getByLabelText("Show 3 suggestions")).toBeTruthy();
  suggestions = { ...ANSWERS, folded: false };
  await changed();
  expect(
    await screen.findByLabelText("Accept: Yes, I'll be there."),
  ).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Hide suggestions"));
  await fireEvent.press(await screen.findByLabelText("Show 3 suggestions"));
  expect(screen.getByLabelText("Accept: Yes, I'll be there.")).toBeTruthy();
  await waitFor(() =>
    expect(posts("/suggestions/fold").at(-1)).toEqual({
      attempt: SUGGESTED,
      folded: false,
    }),
  );
});

test("a set folded elsewhere opens folded; a fold the server didn't take shows its state", async () => {
  suggestions = { ...ANSWERS, folded: true };
  await mount();
  expect(await screen.findByLabelText("Show 3 suggestions")).toBeTruthy();
  expect(screen.queryByLabelText("Accept: Yes, I'll be there.")).toBeNull();
  // Unreachable, the set stays as the server keeps it.
  online = false;
  await fireEvent.press(screen.getByLabelText("Show 3 suggestions"));
  expect(await screen.findByLabelText("Show 3 suggestions")).toBeTruthy();
  expect(screen.queryByLabelText("Accept: Yes, I'll be there.")).toBeNull();
  // A newer set refuses the stale fold, and the reread shows it.
  online = true;
  suggestions = {
    ...ANSWERS,
    attempt: "b".repeat(32),
    options: [{ purpose: "acknowledge", text: "Sounds good!" }],
  };
  await fireEvent.press(screen.getByLabelText("Show 3 suggestions"));
  expect(
    await screen.findByLabelText("Acknowledge: Sounds good!"),
  ).toBeTruthy();
  expect(screen.queryByLabelText("Show 3 suggestions")).toBeNull();
  expect(posts("/suggestions/fold")).toEqual([
    { attempt: SUGGESTED, folded: false },
    { attempt: SUGGESTED, folded: false },
  ]);
});

test("Reply from a long press sets the target; Remove clears it", async () => {
  await mount();
  await userEvent
    .setup()
    .longPress(await screen.findByText("Are you coming tonight?"));
  await fireEvent.press(screen.getByRole("menuitem", { name: "Reply" }));
  // ADR 41: the target as a quote, named by its sender, with the date of
  // its earlier day.
  expect(await screen.findByText(/^Reply to Anna · .*2023$/)).toBeTruthy();
  expect(
    screen.getByLabelText(/^Reply to Anna, .*: Are you coming tonight\?$/),
  ).toBeTruthy();
  await waitFor(() =>
    expect(posts("/draft").at(-1)).toEqual({
      revision: 0,
      text: "",
      reply_to: 1,
    }),
  );
  await fireEvent.press(screen.getByLabelText("Remove reply target"));
  await waitFor(() =>
    expect(posts("/draft").at(-1)).toEqual({
      revision: 1,
      text: "",
      reply_to: null,
    }),
  );
  expect(screen.queryByText(/^Reply to Anna/)).toBeNull();
});

test("the tone is the remembered global choice", async () => {
  await mount();
  expect(await screen.findByText("Rewrite · Neutral")).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Rewrite tone"));
  await fireEvent.press(screen.getByRole("radio", { name: "Professional" }));
  await waitFor(() => expect(tone).toBe("professional"));
  expect(await screen.findByText("Rewrite · Professional")).toBeTruthy();
});

test("unknown delivery offers Check delivery, which resends nothing", async () => {
  operations = [
    {
      id: "op9",
      state: "unknown",
      text: "Hello?",
      images: [],
      reply_to: null,
      created: 1_700_000_400,
      native_ids: [],
      submitted: true,
      retryable: false,
      cancellable: false,
      checkable: true,
    },
  ];
  await mount();
  expect(await screen.findByLabelText("Delivery unknown message")).toBeTruthy();
  // Its "!" opens its sheet, which offers Check delivery and no Retry.
  await fireEvent.press(
    screen.getByLabelText("Delivery unknown: message actions"),
  );
  expect(screen.queryByRole("menuitem", { name: "Retry" })).toBeNull();
  await fireEvent.press(
    screen.getByRole("menuitem", { name: "Check delivery" }),
  );
  expect(
    await screen.findByText(/No delivery receipt yet\. Nothing was resent/),
  ).toBeTruthy();
  expect(posts("/send")).toEqual([]);
});

test("outgoing messages carry Telegram's Sent and Read marks", async () => {
  messages = [
    message(2, "Read already", { outgoing: true }),
    message(3, "Not read yet", { outgoing: true }),
  ];
  await mount();
  expect(await screen.findByText("Not read yet")).toBeTruthy();
  expect(screen.getByLabelText("Read")).toBeTruthy();
  expect(screen.getByLabelText("Sent")).toBeTruthy();
});

test("library photos are downscaled, attached one at a time and removable", async () => {
  mockPhotos.assets = [
    {
      uri: "file:///a.heic",
      width: 8000,
      height: 6000,
      fileName: "IMG_1.HEIC",
    },
    { uri: "file:///b.png", width: 1200, height: 900 },
  ];
  await mount();
  await fireEvent.press(await screen.findByLabelText("Attach"));
  await fireEvent.press(screen.getByLabelText("Photo library"));
  await waitFor(() => expect(posts("/images")).toHaveLength(2), {
    timeout: 3000,
  });
  expect(posts("/images")).toEqual([
    { revision: 0, name: "IMG_1.jpg", data: "AAAA" },
    { revision: 1, name: "photo-2.jpg", data: "AAAA" },
  ]);
  // Only the photo over 4096 px is resized, along its longer side.
  expect(mockPhotos.resizes).toEqual([{ width: 4096 }]);
  expect(await screen.findByLabelText("Attached image 2")).toBeTruthy();
  // Photos alone can be sent.
  expect(screen.getByLabelText("Send").props.accessibilityState.disabled).toBe(
    false,
  );
  await fireEvent.press(screen.getByLabelText("Remove image 1"));
  await waitFor(() => expect(posts("/remove")).toEqual([{ revision: 2 }]));
  expect(draft.attachments).toEqual(["imgphoto-2.jpg"]);
});

test("a refused camera permission says why and attaches nothing", async () => {
  mockPhotos.camera = false;
  await mount();
  await fireEvent.press(await screen.findByLabelText("Attach"));
  await fireEvent.press(screen.getByLabelText("Take photo"));
  expect(
    await screen.findByText(
      "Camera access is off for Enloq. Allow it in Settings to take photos here.",
      {},
      { timeout: 3000 },
    ),
  ).toBeTruthy();
  expect(posts("/images")).toEqual([]);
});
