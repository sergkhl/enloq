import type { Page } from "@playwright/test";
import {
  expect,
  MAYA,
  openAiMenu,
  openTestGroup,
  TEST_GROUP,
  test,
} from "./maya";

// ADR 59 and ADR 60 in the test group of the Owner Account and Maya, on the
// real routes, from Maya's side: an answer with a source that opens the group,
// the not-found line, and a question that ends when its screen does. Asks in a
// row show that nothing resets them (502, "The server isn't answering.").
// Sends the authored messages once, recognisable as tests. At most 60 model
// requests, within the live test allowance.
test.setTimeout(600000);

const FACTS: ["owner" | "maya", string][] = [
  ["maya", "(test) The shed key is with Eli until Friday."],
  ["owner", "(test) Thanks. Who waters the garden this weekend?"],
  [
    "maya",
    "(test) Watering moved from Saturday to Sunday morning. Anna does it.",
  ],
  ["owner", "(test) The plumber charged 40 euros for the kitchen tap."],
  ["maya", "(test) Встреча жильцов перенесена на четверг, 19:00, в холле."],
  ["owner", "(test) Хорошо, я принесу чайник и печенье."],
  ["maya", "(test) The bins now go out on Tuesday night, not Wednesday."],
  ["owner", "(test) Noted. The parcel for flat 12 is at the front desk."],
];

// The authored messages the service holds from Maya's side.
async function held(page: Page) {
  const answer = await page.request.get(
    `/api/accounts/${MAYA}/chats/${encodeURIComponent(TEST_GROUP!)}`,
  );
  expect(answer.status()).toBe(200);
  const { messages } = (await answer.json()) as {
    messages: { text: string }[];
  };
  return new Set(messages.map((message) => message.text));
}

async function ensureMessages(page: Page) {
  const present = await held(page);
  let side: "owner" | "maya" | null = null;
  for (const [author, text] of FACTS) {
    if (present.has(text)) continue;
    if (side !== author) {
      await openTestGroup(page, author);
      side = author;
    }
    await page.getByRole("textbox", { name: "Message" }).fill(text);
    await page.getByRole("button", { name: "Send", exact: true }).tap();
    await expect
      .poll(async () => (await held(page)).has(text), { timeout: 60000 })
      .toBe(true);
  }
}

// Ask this group, in the group's AI Menu (ADR 62 Q13).
async function openAsk(page: Page) {
  const sheet = await openAiMenu(page);
  const offered = sheet.getByRole("button", { name: "Ask this group" });
  if (!(await offered.isVisible({ timeout: 2000 }).catch(() => false))) {
    const processing = sheet.getByRole("switch", {
      name: "Group AI processing",
    });
    await processing.tap();
    await expect(processing).toHaveAttribute("aria-checked", "true");
  }
  await offered.tap();
  return page.getByRole("region", { name: "Ask this group" });
}

test("Ask this group answers in the test group", async ({ page }) => {
  await ensureMessages(page);
  await openTestGroup(page, "maya");
  const ask = await openAsk(page);
  const question = ask.getByRole("textbox", { name: "Question" });
  const send = ask.getByRole("button", { name: "Ask", exact: true });
  const asking = async (words: string) => {
    await question.fill(words);
    await send.tap();
    await expect(ask.getByText("Answering…")).toBeVisible();
    await expect(ask.getByText("Answering…")).toHaveCount(0, {
      timeout: 180000,
    });
    await expect(ask.getByRole("alert")).toHaveCount(0);
  };

  await asking("Who has the shed key, and until when?");
  await expect(ask).toContainText("Eli");
  const source = ask.getByRole("button", { name: /^Source \d+$/ }).first();
  await expect(source).toBeVisible();

  await asking("Когда встреча жильцов?");
  await expect(ask).toContainText(/четверг/i);

  await asking("What is the wifi password?");
  await expect(ask).toContainText("No answer in this group's messages from");

  // A question ends with its screen: the next one is not refused.
  await question.fill("How much did the plumber charge?");
  await send.tap();
  await ask.getByRole("button", { name: "Back", exact: true }).tap();
  const again = await openAsk(page);
  await again
    .getByRole("textbox", { name: "Question" })
    .fill("How much did the plumber charge?");
  await again.getByRole("button", { name: "Ask", exact: true }).tap();
  await expect(again.getByText("Answering…")).toHaveCount(0, {
    timeout: 180000,
  });
  await expect(again.getByRole("alert")).toHaveCount(0);
  await expect(again).toContainText("40");
});

test("asks in a row around the keep-alive all answer", async ({ page }) => {
  await openTestGroup(page, "maya");
  const ask = await openAsk(page);
  const question = ask.getByRole("textbox", { name: "Question" });
  const send = ask.getByRole("button", { name: "Ask", exact: true });
  for (const wait of [4500, 5000, 5500, 6000]) {
    await page.waitForTimeout(wait);
    await question.fill("Which night do the bins go out?");
    await send.tap();
    await expect(ask.getByText("Answering…")).toHaveCount(0, {
      timeout: 180000,
    });
    await expect(ask.getByRole("alert")).toHaveCount(0);
    await expect(ask).toContainText(/Tuesday/);
  }
});
