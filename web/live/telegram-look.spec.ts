import { chooseView, expect, openMayaChat, test } from "./maya";

// ADR 51 on the live service, in the Maya chat only (AGENTS.md, Live
// Telegram sends): one recognisable test message from the Owner Account sends,
// its bubble drawn as a sent one in a Message Run with a tail; a swipe on
// it starts a Reply, which is then removed, and its own copy reaches Maya.
test("a live send looks sent, and a swipe replies to it", async ({ page }) => {
  await openMayaChat(page, "owner");
  await chooseView(page, "originals");
  const note = `[Enloq live check ADR 51] Telegram look ${new Date().toISOString()}`;
  await page.getByRole("textbox", { name: "Message" }).fill(note);
  await page.getByRole("button", { name: "Send" }).tap();
  const sent = page
    .locator("article.app-bubble.outgoing")
    .filter({ hasText: note });
  // No buttons in the bubble while it sends, then Telegram's tick.
  await expect(sent.getByRole("button")).toHaveCount(0);
  await expect(sent.getByLabel("Sent")).toBeVisible({ timeout: 30000 });
  await expect(sent).toHaveClass(/run-tail/);
  await page.screenshot({ path: "../.local/live-results/telegram-look.png" });
  // A finger's swipe to the left replies to it.
  const box = (await sent.boundingBox())!;
  const at = (dx: number) => ({
    pointerType: "touch",
    pointerId: 3,
    isPrimary: true,
    bubbles: true,
    clientX: box.x + box.width / 2 + dx,
    clientY: box.y + box.height / 2,
  });
  await sent.dispatchEvent("pointerdown", at(0));
  for (const dx of [-20, -40, -60, -70])
    await sent.dispatchEvent("pointermove", at(dx));
  await sent.dispatchEvent("pointerup", at(-70));
  await expect(page.getByText(/^Reply to You/).first()).toBeVisible();
  await page.getByRole("button", { name: "Remove reply target" }).tap();
  // Maya's side shows it as an incoming message.
  await openMayaChat(page, "maya");
  await chooseView(page, "originals");
  await expect(
    page.locator("article.app-bubble.incoming").filter({ hasText: note }),
  ).toBeVisible({ timeout: 30000 });
});
