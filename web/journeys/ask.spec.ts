import { expect, test } from "@playwright/test";
import { seedGroups, trust } from "./fixture";

// ADR 59 Q1: Ask this group answers from the group's cached originals with
// checked source links. The fixture's writer answers from the first message
// it is given, and its decision model keeps the citation.
test.beforeEach(async ({ page }) => {
  await trust(page);
});

test("Ask this group answers with a source that opens the group, and Back returns to the answer", async ({
  page,
}) => {
  await seedGroups(page);
  await page.getByRole("tab", { name: "Groups" }).tap();
  await page.getByRole("button", { name: /^Basic discussion/ }).tap();
  // Offered only while Group AI processing is On.
  await page.getByRole("button", { name: "Conversation actions" }).tap();
  const sheet = page.getByRole("dialog", { name: "Conversation actions" });
  await expect(
    sheet.getByRole("button", { name: "Ask this group" }),
  ).toHaveCount(0);
  const processing = sheet.getByRole("switch", { name: "Group AI processing" });
  await processing.tap();
  await expect(processing).toHaveAttribute("aria-checked", "true");
  await page.keyboard.press("Escape");
  // A briefing covers the cited message, so its link reveals it in place as
  // a briefing citation does.
  await page
    .getByRole("button", { name: /^Update briefing/ })
    .first()
    .tap();
  await expect(
    page
      .locator("article.daily-briefing")
      .first()
      .getByRole("button", { name: /^Source \d+$/ })
      .first(),
  ).toBeVisible({ timeout: 20000 });

  await page.getByRole("button", { name: "Conversation actions" }).tap();
  await sheet.getByRole("button", { name: "Ask this group" }).tap();
  const ask = page.getByRole("region", { name: "Ask this group" });
  const question = ask.getByRole("textbox", { name: "Question" });
  const send = ask.getByRole("button", { name: "Ask", exact: true });
  await expect(send).toBeDisabled();
  await question.fill("When is the planning session?");
  await send.tap();
  await expect(ask.getByText("An authored answer.")).toBeVisible({
    timeout: 20000,
  });
  const source = ask.getByRole("button", { name: /^Source \d+$/ });
  await expect(source).toHaveCount(1);
  await expect(source).toHaveText(/^#\d+$/);

  // The source opens the group at its original, highlighted.
  await source.tap();
  await expect(ask).toBeHidden();
  await expect(
    page.getByRole("button", { name: "Return to briefing" }),
  ).toBeVisible();
  await expect(page.locator("article.app-bubble.highlighted")).toHaveCount(1);

  // Back returns to the answer, still shown.
  await page.getByRole("button", { name: "Back to answer" }).tap();
  await expect(ask.getByText("An authored answer.")).toBeVisible();
  await expect(question).toHaveValue("When is the planning session?");

  // Leaving the screen drops the answer.
  await ask.getByRole("button", { name: "Back", exact: true }).tap();
  await expect(ask).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Back to conversations" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Conversation actions" }).tap();
  await sheet.getByRole("button", { name: "Ask this group" }).tap();
  await expect(question).toHaveValue("");
  await expect(ask.getByText("An authored answer.")).toHaveCount(0);
});
