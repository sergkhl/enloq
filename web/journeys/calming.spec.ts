import { expect, test } from "@playwright/test";
import { ORIGIN, openAiMenu, trust } from "./fixture";

// ADR 53: a calmed message carries a small Calmed label (Q5), and the chat's
// AI Menu's Soften hostile messages turns calming off for that chat (Q4, ADR
// 62), leaving what was calmed as it is (Q9).
test.beforeEach(async ({ page }) => {
  await trust(page);
});

test("a hostile message shows Softened, and its AI Menu turns softening off", async ({
  page,
}) => {
  const seeded = await page.request.post("/api/test/calming", {
    data: {},
    headers: { Origin: ORIGIN },
  });
  expect(seeded.status()).toBe(200);
  expect((await seeded.json()).calmed).toBe(true);
  await page.reload();
  await page.getByRole("button", { name: /^Calming fixture/ }).tap();
  const bubble = page
    .locator("article.app-bubble")
    .filter({ hasText: "WHERE IS MY REFUND" })
    .last();
  await expect(bubble.locator(".calmed-mark")).toHaveText(" · Softened");

  // A tap on the menu's switch flips it in place (ADR 62 Q16).
  let menu = await openAiMenu(page);
  const calm = menu.getByRole("switch", { name: "Soften hostile messages" });
  await expect(calm).toHaveAttribute("aria-checked", "true");
  await expect(calm).toBeEnabled();
  await calm.tap();
  await expect(calm).toHaveAttribute("aria-checked", "false");
  await page.keyboard.press("Escape");
  // Switching off leaves the calmed message calmed.
  await expect(bubble.locator(".calmed-mark")).toHaveText(" · Softened");

  // The choice is kept, and its page turns calming back on, under its
  // Guidance fields for all chats (Q17).
  menu = await openAiMenu(page);
  await expect(calm).toHaveAttribute("aria-checked", "false");
  await menu.getByRole("button", { name: "Soften hostile messages" }).tap();
  const pageCalm = page.getByRole("switch", {
    name: "Soften hostile messages",
  });
  await expect(pageCalm).toHaveAttribute("aria-checked", "false");
  await expect(
    page.getByRole("button", { name: /^Soften hostile messages, / }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^Faithfulness rules, / }),
  ).toBeVisible();
  await expect(pageCalm).toBeEnabled();
  await pageCalm.tap();
  await expect(pageCalm).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "Back", exact: true }).tap();
});

// ADR 54: text that isn't calmed stays combined, its originals together in
// one bubble with no AI label or status.
test("text that isn't rewritten shows as its originals together", async ({
  page,
}) => {
  const post = async (path: string, data: object = {}) => {
    const answer = await page.request.post(path, {
      data,
      headers: { Origin: ORIGIN },
    });
    expect(answer.status()).toBe(200);
    return answer.json();
  };
  await post("/api/test/text-rewriting", { enabled: false });
  try {
    expect((await post("/api/test/combined")).state).toBe("original");
    await page.reload();
    await page.getByRole("button", { name: /^Combined fixture/ }).tap();
    const bubble = page
      .locator("article.app-bubble")
      .filter({ hasText: "Combined fixture:" })
      .last();
    await expect(bubble.getByText("Or Friday.")).toBeVisible();
    await expect(bubble.locator(".bubble-ai-head")).toHaveText(
      /^2 messages · /,
    );
    await expect(bubble.getByText("Original text")).toHaveCount(0);
  } finally {
    await post("/api/test/text-rewriting", { enabled: true });
  }
});
