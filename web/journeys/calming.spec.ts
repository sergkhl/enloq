import { expect, test } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

// ADR 53: a calmed message carries a small Calmed label (Q5), and the
// Correspondent Profile's Calm this chat turns calming off for that chat
// (Q4), leaving what was calmed as it is (Q9).
test.beforeEach(async ({ page }) => {
  await trust(page);
});

test("an angry message shows Calmed, and its profile turns calming off", async ({
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
  await expect(bubble.locator(".calmed-mark")).toHaveText(" · Calmed");

  const profile = page.getByRole("button", {
    name: /^Calming fixture,.* open profile$/,
  });
  await profile.tap();
  const calm = page.getByRole("switch", { name: "Calm this chat" });
  await expect(calm).toHaveAttribute("aria-checked", "true");
  await expect(calm).toBeEnabled();
  await calm.tap();
  await expect(calm).toHaveAttribute("aria-checked", "false");
  await page.getByRole("button", { name: "Back", exact: true }).tap();
  // Switching off leaves the calmed message calmed.
  await expect(bubble.locator(".calmed-mark")).toHaveText(" · Calmed");

  // The choice is kept, and turns calming back on.
  await profile.tap();
  await expect(calm).toHaveAttribute("aria-checked", "false");
  await expect(calm).toBeEnabled();
  await calm.tap();
  await expect(calm).toHaveAttribute("aria-checked", "true");
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
