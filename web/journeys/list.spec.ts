import { expect, test } from "@playwright/test";
import { ORIGIN, seedGroups, trust } from "./fixture";

// MW1: the list in the app's design, inside one phone-width column.
test.beforeEach(async ({ page }) => {
  await trust(page);
});

test("the list switches accounts and lists, and opens a chat full screen", async ({
  page,
}) => {
  await seedGroups(page);
  const switcher = page.getByRole("button", {
    name: "Account: Demo account. Switch account",
  });
  await expect(switcher).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^Alex · weekend plans/ }),
  ).toBeVisible();
  // The column is phone width on any screen.
  const width = await page
    .locator(".rewriting-app")
    .evaluate((node) => node.clientWidth);
  expect(width).toBeLessThanOrEqual(480);

  await switcher.tap();
  const sheet = page.getByRole("dialog", { name: "Accounts" });
  await expect(
    sheet.getByRole("button", { name: "Manage accounts" }),
  ).toBeVisible();
  await expect(sheet.getByText("Trusted browsers and devices")).toHaveCount(0);
  await sheet.getByRole("button", { name: /^Second demo account/ }).tap();
  await expect(
    page.getByRole("button", {
      name: "Account: Second demo account. Switch account",
    }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: "Account: Second demo account. Switch account",
    })
    .tap();
  await page
    .getByRole("dialog", { name: "Accounts" })
    .getByRole("button", { name: /^Demo account/ })
    .tap();

  await page.getByRole("tab", { name: "Groups" }).tap();
  await expect(page.getByRole("tab", { name: "Groups" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    page.getByRole("button", { name: /^Basic discussion/ }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Private" }).tap();

  await page.getByRole("button", { name: /^Alex · weekend plans/ }).tap();
  await expect(
    page.getByText("Can you bring the board game on Saturday?").first(),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /^Account: / })).toHaveCount(0);
  await page.getByRole("button", { name: "Back to conversations" }).tap();
  await expect(switcher).toBeVisible();
});

test("the plain address opens the list, not the last chat; a chat link opens its chat", async ({
  page,
}) => {
  await page.getByRole("button", { name: /^Alex · weekend plans/ }).tap();
  await expect(
    page.getByRole("button", { name: "Back to conversations" }),
  ).toBeVisible();
  const link = page.url();
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Account: Demo account. Switch account" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Back to conversations" }),
  ).toHaveCount(0);
  await page.goto(link);
  await expect(
    page.getByRole("button", { name: "Back to conversations" }),
  ).toBeVisible();
  await page.goBack();
  await expect(
    page.getByRole("button", { name: "Account: Demo account. Switch account" }),
  ).toBeVisible();
});

// ADR 50: Private | Groups | Saved as icons in the header row, and the
// account's own Saved Messages in the third tab, writing without Rewrite.
test("the header's icons open Saved Messages, which writes notes without Rewrite", async ({
  page,
}) => {
  const seeded = await page.request.post("/fixture/saved-messages", {
    data: {},
    headers: { Origin: ORIGIN },
  });
  expect(seeded.status()).toBe(200);
  const tabs = page.getByRole("tablist", { name: "Lists" }).getByRole("tab");
  await expect(tabs).toHaveCount(4);
  // Icons only, named for screen readers, in the account's header row.
  await expect(page.getByRole("tab", { name: "Private" })).toHaveText("");
  await expect(
    page.locator(".list-header").getByRole("tab", { name: "Saved" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Saved" }).tap();
  await expect(page.getByRole("tab", { name: "Saved" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page).toHaveURL(/tab=saved/);
  await expect(
    page.getByText("Saved fixture: bring the charger"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Rewrite" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Back to conversations" }),
  ).toHaveCount(0);
  await page
    .getByRole("textbox", { name: "Message" })
    .fill("Saved fixture: a note from the web");
  await page.getByRole("button", { name: "Send" }).tap();
  await expect(
    page.getByText("Saved fixture: a note from the web").first(),
  ).toBeVisible();
  // The tab is in the address, so a reload comes back to it.
  await page.reload();
  await expect(
    page.getByText("Saved fixture: bring the charger"),
  ).toBeVisible();
  // A link to the account's own chat opens the Saved tab too.
  await page.goto("/?account=1#1");
  await expect(page.getByRole("tab", { name: "Saved" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    page.getByText("Saved fixture: bring the charger"),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Private" }).tap();
  await expect(
    page.getByRole("button", { name: /^Alex · weekend plans/ }),
  ).toBeVisible();
});
