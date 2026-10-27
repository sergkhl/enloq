import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
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
import AskRoute from "../app/ask/[account]/[peer]";
import { ConnectionProvider } from "../src/connection";

// ADR 59 Q1: Ask this group's screen over authored answers.
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});
// Today in the service's UTC calendar, and the searched range's first day.
const TODAY = new Date().toISOString().slice(0, 10);
const searched = { start: `${TODAY.slice(0, 4)}-10-03`, end: TODAY };
const NOT_FOUND = "No answer in this group's messages from 3 Oct to today.";

type Answer = { status: number; body: unknown };
const answer = (
  statements: { text: string; citations: number[]; unsupported?: number[] }[],
): Answer => ({
  status: 200,
  body: {
    status: "answer",
    searched,
    statements: statements.map((statement) => ({
      unsupported: [],
      supported: statement.citations.length > 0,
      ...statement,
    })),
  },
});

// The service's answers in order; none left keeps the question waiting.
let answers: Answer[] = [];
let asked: unknown[] = [];
let waiting: ((value: Answer) => void) | null = null;

beforeEach(() => {
  reset();
  answers = [];
  asked = [];
  waiting = null;
  mockStore.set("enloq.credential", SAVED);
  mockParams.account = "1";
  mockParams.peer = "chat:42";
  serve((url, init) => {
    if (url.endsWith("/api/accounts"))
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
    if (url.endsWith("/api/accounts/1/chats/chat%3A42/ask")) {
      asked.push(JSON.parse(init.body!));
      const next = answers.shift();
      if (next) return next;
      return new Promise<Answer>((resolve) => {
        waiting = resolve;
      }) as Promise<never>;
    }
    throw new Error(`unexpected ${url}`);
  });
});

const mount = () =>
  render(
    <ConnectionProvider>
      <AskRoute />
    </ConnectionProvider>,
  );

async function ask(question: string) {
  await fireEvent.changeText(
    await screen.findByLabelText("Question"),
    question,
  );
  await fireEvent.press(screen.getByRole("button", { name: "Ask" }));
}

test("an answer shows its statements with source links that open the group", async () => {
  answers = [
    answer([
      { text: "The planning session is on Friday.", citations: [11, 12] },
      { text: "Bob brings the agenda.", citations: [12] },
    ]),
  ];
  await mount();
  expect(
    (await screen.findByRole("button", { name: "Ask" })).props
      .accessibilityState,
  ).toMatchObject({ disabled: true });
  await ask("  When is the planning session?  ");
  expect(
    await screen.findByText("The planning session is on Friday."),
  ).toBeTruthy();
  expect(asked).toEqual([{ question: "When is the planning session?" }]);
  expect(screen.getByText("Bob brings the agenda.")).toBeTruthy();
  expect(screen.getByLabelText("Source 11")).toBeTruthy();
  expect(screen.getAllByLabelText("Source 12")).toHaveLength(2);
  expect(screen.getByText("#11")).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Source 11"));
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/chat/[account]/[peer]",
    params: { account: "1", peer: "chat:42", source: "11" },
  });
  // The answer stays for Back from the group, and the phone keeps none of it.
  expect(screen.getByText("The planning session is on Friday.")).toBeTruthy();
  expect(
    [...mockFiles.values()].some((value) => value.includes("planning")),
  ).toBe(false);
});

// Provisional while ADR 59 Q6 is open.
test("only statements with a checked citation show, or the not-found line", async () => {
  answers = [
    answer([
      { text: "The planning session is on Friday.", citations: [11] },
      { text: "Nobody objected.", citations: [], unsupported: [14] },
    ]),
    answer([{ text: "Nobody objected.", citations: [], unsupported: [14] }]),
  ];
  await mount();
  await ask("When is the planning session?");
  expect(
    await screen.findByText("The planning session is on Friday."),
  ).toBeTruthy();
  expect(screen.queryByText("Nobody objected.")).toBeNull();
  expect(screen.queryByLabelText("Source 14")).toBeNull();
  // Asking again replaces the answer.
  await ask("Did anyone object?");
  expect(await screen.findByText(NOT_FOUND)).toBeTruthy();
  expect(screen.queryByText("The planning session is on Friday.")).toBeNull();
  expect(screen.queryByText("Nobody objected.")).toBeNull();
});

test("while answering a status shows and Ask waits; not found names the range", async () => {
  await mount();
  await ask("When is the planning session?");
  expect(await screen.findByText("Answering…")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Ask" }).props.accessibilityState,
  ).toMatchObject({ disabled: true, busy: true });
  await act(async () =>
    waiting!({
      status: 200,
      body: { status: "not_found", searched, statements: [] },
    }),
  );
  expect(await screen.findByText(NOT_FOUND)).toBeTruthy();
  expect(screen.queryByText("Answering…")).toBeNull();
  expect(
    screen.getByRole("button", { name: "Ask" }).props.accessibilityState,
  ).toMatchObject({ disabled: false });
});

test("an unchecked answer shows only that it couldn't be checked", async () => {
  answers = [
    {
      status: 200,
      body: { status: "unchecked", searched, statements: [] },
    },
  ];
  await mount();
  await ask("When is the planning session?");
  expect(await screen.findByText("Couldn't check the answer.")).toBeTruthy();
  expect(screen.queryByLabelText(/^Source /)).toBeNull();
  expect(screen.queryByText(NOT_FOUND)).toBeNull();
});

test("a refusal shows its reason and keeps the question", async () => {
  answers = [
    { status: 409, body: { detail: "Another question is being answered" } },
  ];
  await mount();
  await ask("When is the planning session?");
  expect(
    await screen.findByText("Another question is being answered"),
  ).toBeTruthy();
  expect(screen.getByLabelText("Question").props.value).toBe(
    "When is the planning session?",
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Ask" }).props.accessibilityState,
    ).toMatchObject({ disabled: false }),
  );
});
