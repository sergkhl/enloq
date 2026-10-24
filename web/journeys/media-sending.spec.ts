import { expect, test, type Page } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

// CU5 (ADR 32, ADR 46): GIF and sticker sending in the web, in the app's
// design, against the fixture's authored catalog and fake transport.
test.beforeEach(async ({ page }) => {
  await trust(page);
});

async function post(page: Page, path: string, data: object = {}) {
  const answer = await page.request.post(path, {
    data,
    headers: { Origin: ORIGIN },
  });
  expect(answer.status()).toBe(200);
  return answer.json();
}

const fixture = (page: Page, data: object) =>
  post(page, "/api/test/gif-sticker", data);

async function openPicker(page: Page, kind: "GIFs" | "Stickers") {
  await fixture(page, { action: "setup", case: "composer" });
  await page.reload();
  await page.getByRole("button", { name: /^GIF and sticker fixture/ }).tap();
  await page.getByRole("button", { name: "Attach" }).tap();
  await page
    .getByRole("menu", { name: "Attach" })
    .getByRole("menuitem", { name: kind })
    .tap();
  return page.getByRole("dialog", { name: "GIFs and stickers" });
}

test("a saved GIF stages above the composer and sends with the text as its caption", async ({
  page,
}) => {
  const picker = await openPicker(page, "GIFs");
  await expect(picker.getByRole("tab", { name: "GIFs" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await picker.getByRole("button", { name: "Select GIF Loop MP4" }).tap();
  await expect(picker).toBeHidden();
  const staged = page.getByRole("group", { name: "Selected GIF" });
  await expect(staged).toBeVisible();
  await expect(staged.getByText("Preparing GIF…")).toBeHidden({
    timeout: 15000,
  });
  await page
    .getByRole("textbox", { name: "Message" })
    .fill("A looping caption");
  await page.getByRole("button", { name: "Send" }).tap();
  await expect(staged).toBeHidden({ timeout: 15000 });
  await expect
    .poll(async () => (await fixture(page, { action: "stats" })).sends.length)
    .toBe(1);
});

test("a search runs after a pause and More adds the next page", async ({
  page,
}) => {
  const picker = await openPicker(page, "GIFs");
  await picker.getByRole("searchbox", { name: "Search GIFs" }).fill("orbit");
  await expect(
    picker.getByRole("button", { name: "Select GIF Web loop" }),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    picker.getByRole("button", { name: "Select GIF Loop GIF" }),
  ).toBeVisible();
  await picker.getByRole("button", { name: "More GIFs" }).tap();
  await expect(
    picker.getByRole("button", { name: "Select GIF Loop MP4" }),
  ).toBeVisible();
  await picker.getByRole("button", { name: "Close GIFs and stickers" }).tap();
  await expect(picker).toBeHidden();
});

test("stickers narrow locally, and a sticker sends on its own with the draft kept", async ({
  page,
}) => {
  const picker = await openPicker(page, "Stickers");
  await expect(picker.getByRole("tab", { name: "Stickers" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    picker.getByRole("button", { name: "Recent", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await picker
    .getByRole("searchbox", { name: "Filter stickers" })
    .fill("nothing like this");
  await expect(picker.getByText("No stickers match.")).toBeVisible();
  await picker.getByRole("searchbox", { name: "Filter stickers" }).fill("");
  await picker.getByRole("button", { name: "Pattern pack" }).tap();
  await expect(
    picker.getByRole("button", { name: "Select sticker Pattern static" }),
  ).toBeVisible({ timeout: 15000 });
  await picker.getByRole("button", { name: "Close GIFs and stickers" }).tap();
  // The text written before the sticker waits as the draft.
  await page.getByRole("textbox", { name: "Message" }).fill("Kept for later");
  await page.getByRole("button", { name: "Attach" }).tap();
  await page
    .getByRole("menu", { name: "Attach" })
    .getByRole("menuitem", { name: "Stickers" })
    .tap();
  await picker.getByRole("button", { name: "Pattern pack" }).tap();
  await picker
    .getByRole("button", { name: "Select sticker Pattern static" })
    .tap();
  const staged = page.getByRole("group", { name: "Selected sticker" });
  await expect(staged.getByText("Draft kept")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeHidden();
  await expect(
    page.getByRole("button", { name: "Generate suggestions", exact: true }),
  ).toBeDisabled();
  await expect(staged.getByText("Preparing sticker…")).toBeHidden({
    timeout: 15000,
  });
  await page.getByRole("button", { name: "Send" }).tap();
  await expect(staged).toBeHidden({ timeout: 15000 });
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveValue(
    "Kept for later",
  );
  await expect
    .poll(async () => (await fixture(page, { action: "stats" })).sends.length)
    .toBe(1);
});
