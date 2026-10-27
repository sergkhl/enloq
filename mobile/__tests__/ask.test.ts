import {
  shownAnswer,
  type AskAnswer,
  type AskStatement,
} from "../../shared/ask";

// ADR 59: what the Ask screen shows of an answer, on 2026-10-27.
const TODAY = "2026-10-27";
const searched = { start: "2026-10-03", end: TODAY };

function statement(
  text: string,
  citations: number[],
  extra: Partial<AskStatement> = {},
): AskStatement {
  return { text, citations, unsupported: [], supported: true, ...extra };
}

const answer = (statements: AskStatement[]): AskAnswer => ({
  status: "answer",
  searched,
  statements,
});

beforeEach(() => jest.useFakeTimers({ now: Date.UTC(2026, 9, 27, 12) }));
afterEach(() => jest.useRealTimers());

test("an answer shows its statements with their checked citations", () => {
  const first = statement("The session is on Friday.", [11, 12]);
  const second = statement("Bob brings the agenda.", [12], {
    unsupported: [13],
    supported: false,
  });
  expect(shownAnswer(answer([first, second]), TODAY)).toEqual({
    statements: [first, second],
    line: null,
  });
});

// ADR 59 Q6.
test("a statement without a checked citation is left out", () => {
  const kept = statement("The session is on Friday.", [11]);
  expect(
    shownAnswer(
      answer([kept, statement("Nobody objected.", [], { unsupported: [14] })]),
      TODAY,
    ),
  ).toEqual({ statements: [kept], line: null });
});

test("an answer left with no checked citation abstains", () => {
  expect(
    shownAnswer(
      answer([statement("Nobody objected.", [], { unsupported: [14] })]),
      TODAY,
    ),
  ).toEqual({
    statements: [],
    line: "No answer in this group's messages from 3 Oct to today.",
  });
});

test("not found names the searched range, with today as today", () => {
  expect(shownAnswer({ status: "not_found", searched }, TODAY)).toEqual({
    statements: [],
    line: "No answer in this group's messages from 3 Oct to today.",
  });
  // An answer read before midnight names its end day after it.
  expect(
    shownAnswer({ status: "not_found", searched }, "2026-10-28").line,
  ).toBe("No answer in this group's messages from 3 Oct to 27 Oct.");
  // A day outside the current year names its year.
  expect(
    shownAnswer(
      {
        status: "not_found",
        searched: { start: "2025-12-30", end: TODAY },
        statements: [],
      },
      TODAY,
    ).line,
  ).toBe("No answer in this group's messages from 30 Dec 2025 to today.");
});

test("an unchecked answer shows only that it couldn't be checked", () => {
  expect(
    shownAnswer(
      {
        status: "unchecked",
        searched,
        statements: [statement("The session is on Friday.", [11])],
      },
      TODAY,
    ),
  ).toEqual({ statements: [], line: "Couldn't check the answer." });
});
