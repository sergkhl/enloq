import { expect, test, type Page } from "@playwright/test";
import { chooseView, seedGroups, trust } from "./fixture";

// Composing in the app's design: generation preserves the draft until selection.
test.beforeEach(async ({ page }) => {
  await trust(page);
});

async function replyTo(page: Page, text: string) {
  await page.getByText(text).first().click({ button: "right" });
  await page
    .getByRole("menu", { name: "Message actions" })
    .getByRole("menuitem", { name: "Reply" })
    .tap();
}

async function suggestUndoSend(page: Page, note: string) {
  const field = page.getByRole("textbox", { name: "Message" });
  await field.fill(note);
  await page.getByRole("button", { name: "Generate suggestions" }).tap();
  await expect(field).toHaveValue(note);
  await page
    .getByRole("region", { name: "Response suggestions" })
    .getByRole("button", { name: /^Acknowledge/ })
    .tap();
  const undo = page.getByRole("button", { name: "Undo suggestion" });
  await expect(undo).toBeEnabled({ timeout: 20000 });
  await undo.tap();
  await expect(field).toHaveValue(note);
  await page.getByRole("button", { name: "Send" }).tap();
  await expect(
    page.locator("article.app-bubble").filter({ hasText: note }),
  ).toBeVisible({ timeout: 20000 });
}

test("a private chat generates, inserts, undoes and sends a reply; Enter adds a line", async ({
  page,
}) => {
  await page.getByRole("button", { name: /^Alex · weekend plans/ }).tap();
  await chooseView(page, "originals");
  const field = page.getByRole("textbox", { name: "Message" });
  await field.fill("line one");
  await field.press("Enter");
  await field.pressSequentially("line two");
  await expect(field).toHaveValue("line one\nline two");
  await expect(page.getByText("Processing…")).toHaveCount(0);
  // The paperclip offers Photos, GIFs and Stickers (CU5).
  await page.getByRole("button", { name: "Attach" }).tap();
  const attach = page.getByRole("menu", { name: "Attach" });
  await expect(attach.getByRole("menuitem")).toHaveText([
    "Photos",
    "GIFs",
    "Stickers",
  ]);
  await page.keyboard.press("Escape");
  await expect(attach).toBeHidden();
  await expect(page.getByRole("button", { name: "Rewrite tone" })).toHaveCount(
    0,
  );

  await replyTo(page, "Can you bring the board game on Saturday?");
  await expect(
    page.getByText(/^Reply to Alex · weekend plans/).first(),
  ).toBeVisible();
  const note = `Bringing the board game ${Date.now()}`;
  await suggestUndoSend(page, note);
  await expect(
    page
      .locator("article.app-bubble")
      .filter({ hasText: note })
      .getByRole("button", { name: /^Reply to Alex · weekend plans/ }),
  ).toBeVisible();
});

test("a group sends a reply without suggestion or outgoing rewrite controls", async ({
  page,
}) => {
  await seedGroups(page);
  await page.getByRole("tab", { name: "Groups" }).tap();
  await page.getByRole("button", { name: /^Basic discussion/ }).tap();
  await chooseView(page, "originals");
  await expect(page.getByRole("button", { name: "Rewrite tone" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "Generate suggestions" }),
  ).toHaveCount(0);
  await replyTo(page, "I will bring the printed agenda.");
  const note = `Group note ${Date.now()}`;
  await page.getByRole("textbox", { name: "Message" }).fill(note);
  await page.getByRole("button", { name: "Send", exact: true }).tap();
  await expect(
    page.locator("article.app-bubble").filter({ hasText: note }),
  ).toBeVisible();
});

test("a send that isn't sent looks sent, and its sheet cancels it", async ({
  page,
}) => {
  // A synthetic flood hold keeps the send from Telegram (ADR 51 Q3, Q4).
  const fault = (held: number) =>
    page.request.post("/api/test/delivery", {
      data: { held },
      headers: { Origin: "http://127.0.0.1:8777" },
    });
  expect((await fault(120)).status()).toBe(200);
  try {
    await page.getByRole("button", { name: /^Alex · weekend plans/ }).tap();
    const note = `A held authored note ${Date.now()}`;
    await page.getByRole("textbox", { name: "Message" }).fill(note);
    await page.getByRole("button", { name: "Send" }).tap();
    const bubble = page.getByRole("article", { name: "Not sent message" });
    await expect(bubble).toContainText(note, { timeout: 20000 });
    // The sent bubble's own look, with no buttons but its "!".
    await expect(bubble).toHaveClass(/outgoing/);
    await expect(bubble.getByRole("button")).toHaveCount(1);
    await bubble
      .getByRole("button", { name: "Not sent: message actions" })
      .tap();
    const sheet = page.getByRole("menu", { name: "Message actions" });
    await expect(sheet.getByRole("menuitem")).toHaveText([
      "Retry",
      "Cancel send",
      "Copy",
    ]);
    await sheet.getByRole("menuitem", { name: "Cancel send" }).tap();
    await expect(
      page.getByRole("article", { name: "Cancelled message" }),
    ).toContainText(note);
  } finally {
    await fault(0);
  }
});

test("swiping a message to the left replies to it, as the sheet's Reply", async ({
  page,
}) => {
  await page.getByRole("button", { name: /^Alex · weekend plans/ }).tap();
  await chooseView(page, "originals");
  // Originals only: an AI block would hold the same wording.
  await expect(page.locator(".bubble-ai-head")).toHaveCount(0);
  const bubble = page
    .locator("article.app-bubble")
    .filter({ hasText: "Can you bring the board game on Saturday?" })
    .first();
  await expect(bubble).toBeVisible();
  await bubble.scrollIntoViewIfNeeded();
  const box = (await bubble.boundingBox())!;
  const x = box.x + box.width * 0.6;
  const y = box.y + box.height / 2;
  // A finger's swipe, as the browser reports it (ADR 51 Q5).
  const touch = (clientX: number, clientY = y) => ({
    pointerType: "touch",
    pointerId: 7,
    isPrimary: true,
    bubbles: true,
    clientX,
    clientY,
  });
  // A short or vertical drag replies to nothing.
  await bubble.dispatchEvent("pointerdown", touch(x));
  await bubble.dispatchEvent("pointermove", touch(x - 30));
  await bubble.dispatchEvent("pointerup", touch(x - 30));
  await bubble.dispatchEvent("pointerdown", touch(x));
  await bubble.dispatchEvent("pointermove", touch(x - 4, y + 60));
  await bubble.dispatchEvent("pointerup", touch(x - 4, y + 60));
  const target = page.getByText(/^Reply to Alex · weekend plans/);
  await expect(target).toHaveCount(0);
  await bubble.dispatchEvent("pointerdown", touch(x));
  for (const dx of [20, 40, 60, 70])
    await bubble.dispatchEvent("pointermove", touch(x - dx));
  await expect(bubble.locator(".swipe-reply-arrow")).toBeVisible();
  await bubble.dispatchEvent("pointerup", touch(x - 70));
  await expect(target.first()).toBeVisible();
  await expect(bubble.locator(".swipe-reply-arrow")).toHaveCount(0);
  await page.getByRole("button", { name: "Remove reply target" }).tap();
  await expect(target).toHaveCount(0);
});
