import { expect, test, type Locator, type Page } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

// CU6 (ADR 34, ADR 46): group recovery in the web, in the app's design,
// against the fixture's authored retained group and fake transport.
const peer = "chat:941";
const draft = "A newer authored draft.";
const submitted = "The exact paused authored reply.";

type State = {
  operations: { state: string; resumable: boolean }[];
  sends: { peer: string; text: string; reply_to: number; images: string[] }[];
};

async function fixture(page: Page, data: object = {}): Promise<State> {
  const result = await page.request.post("/api/test/group-recovery", {
    headers: { Origin: ORIGIN },
    data: { peer, ...data },
  });
  expect(result.status()).toBe(200);
  return result.json();
}

test.beforeEach(async ({ page }) => {
  await trust(page);
});
test.afterEach(async ({ page }) => {
  await fixture(page, { action: "cleanup" });
});

async function openRetained(page: Page) {
  await page.getByRole("tab", { name: "Groups" }).tap();
  const section = page.getByRole("region", { name: "Retained group work" });
  const toggle = section.getByRole("button", { name: /^Retained group work/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.tap();
  const row = section.getByRole("button", {
    name: /Retained authored discussion/,
  });
  await expect(row).toContainText("Saved draft · 1 unresolved send");
  await row.tap();
}

async function reviewFromSheet(page: Page, card: Locator) {
  await card.getByRole("button", { name: "Not sent: message actions" }).tap();
  await page.getByRole("menuitem", { name: "Review and resume" }).tap();
}

test("retained group work opens read-only, and a paused send resumes after its review", async ({
  page,
}) => {
  await fixture(page, {
    retained: true,
    access: "lost",
    draft,
    accepted: true,
    image: true,
  });
  await page.reload();
  await openRetained(page);
  // The group opens read-only, its newer draft kept.
  const field = page.getByRole("textbox", { name: "Message", exact: true });
  await expect(field).toHaveValue(draft);
  await expect(field).toHaveAttribute("readonly");
  const card = page.getByRole("article", { name: "Not sent message" });
  await expect(card).toContainText(submitted);
  // While access is lost, Review says why and nothing is sent.
  // Its actions are in its sheet, opened from its "!" (ADR 51 Q4).
  await reviewFromSheet(page, card);
  await expect(
    page.getByRole("alert").filter({ hasText: /access|available/i }),
  ).toBeVisible();
  const review = page.getByRole("dialog", { name: "Resume paused send" });
  await expect(review).toHaveCount(0);
  expect((await fixture(page, { action: "state" })).sends).toEqual([]);
  // Access returns: the review shows exactly what Resume sends.
  await fixture(page, { action: "access", access: "available" });
  await page.reload();
  await expect(card).toBeVisible({ timeout: 15000 });
  await reviewFromSheet(page, card);
  await expect(review).toContainText(
    "Demo account · Retained authored discussion",
  );
  await expect(review).toContainText(submitted);
  await expect(review.getByAltText("Reviewed image 1")).toBeVisible();
  await expect(review.getByText(/^Resume within \d:\d\d$/)).toBeVisible();
  await review.getByRole("button", { name: "Cancel review" }).tap();
  await expect(review).toHaveCount(0);
  expect((await fixture(page, { action: "state" })).sends).toEqual([]);
  await reviewFromSheet(page, card);
  await review.getByRole("button", { name: "Resume send" }).tap();
  await expect(review).toHaveCount(0);
  await expect
    .poll(
      async () =>
        (await fixture(page, { action: "state" })).operations[0]?.state,
    )
    .toBe("confirmed");
  const result = await fixture(page, { action: "state" });
  expect(result.sends).toHaveLength(1);
  expect(result.sends[0]).toMatchObject({ peer, text: submitted, reply_to: 1 });
  expect(result.sends[0]!.images).toHaveLength(1);
  // The newer draft stays where it was.
  await expect(field).toHaveValue(draft);
});
