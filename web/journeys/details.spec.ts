import { expect, test } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

// CU2 (ADR 46): message details in the web, in the app's design, on the
// fixture's authored "Message details" chat.
test.beforeEach(async ({ page }) => {
  await trust(page);
  const view = await page.request.post("/api/preferences/reading-view", {
    data: { reading_view: "originals" },
    headers: { Origin: ORIGIN },
  });
  expect(view.status()).toBe(200);
  const seeded = await page.request.post("/api/test/details", {
    data: {},
    headers: { Origin: ORIGIN },
  });
  expect(seeded.status()).toBe(200);
  await page.reload();
  await page.getByRole("button", { name: /^Message details/ }).tap();
  await expect(page.getByText("Later message 39")).toBeVisible();
});

test("forwarded and imported lines, versions, cards and Scroll to latest", async ({
  page,
}) => {
  const forwarded = page.getByText(/^Forwarded from Authored channel · /);
  await forwarded.scrollIntoViewIfNeeded();
  await expect(forwarded).toBeVisible();
  // Away from the latest message, the button shows.
  const latest = page.getByRole("button", { name: "Scroll to latest" });
  await expect(latest).toBeVisible();
  await expect(page.getByText("Imported", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("img", {
      name: "Edited in Telegram; no earlier version captured",
    }),
  ).toBeVisible();
  const versions = page.getByRole("group", { name: "Versions of message 3" });
  await expect(versions.getByText("3/3")).toBeVisible();
  await versions.getByRole("button", { name: "Previous version" }).tap();
  await expect(page.getByText("Second wording")).toBeVisible();
  await versions.getByRole("button", { name: "Previous version" }).tap();
  await expect(page.getByText("First wording")).toBeVisible();
  await expect(versions.getByText("1/3")).toBeVisible();
  await expect(
    versions.getByRole("button", { name: "Previous version" }),
  ).toBeDisabled();
  await versions.getByRole("button", { name: "Next version" }).tap();
  await versions.getByRole("button", { name: "Next version" }).tap();
  await expect(page.getByText("Latest wording")).toBeVisible();
  await expect(page.getByText("Which day works?")).toBeVisible();
  await expect(page.getByText("• Fri")).toBeVisible();
  await expect(page.getByText("Authored Contact")).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Authored cafe/ }),
  ).toHaveAttribute(
    "href",
    "https://www.openstreetmap.org/search?query=Authored%20cafe",
  );
  await expect(
    page.getByRole("link", { name: /Authored page/ }),
  ).toHaveAttribute("href", "https://e.test/page");
  await latest.tap();
  await expect(page.getByText("Later message 39")).toBeInViewport();
  await expect(latest).toBeHidden();
});

test("chips show the reactions and the sheet removes and adds the owner's", async ({
  page,
}) => {
  const bubble = page
    .locator("article.app-bubble")
    .filter({ hasText: "React to me" });
  await bubble.scrollIntoViewIfNeeded();
  await expect(
    bubble.getByRole("listitem", { name: "👍 2, including yours" }),
  ).toBeVisible();
  await expect(
    bubble.getByRole("listitem", { name: "Paid reaction 1" }),
  ).toBeVisible();
  await expect(
    bubble.getByRole("listitem", { name: "Custom reaction 1" }),
  ).toBeVisible();
  await bubble.click({ button: "right" });
  const sheet = page.getByRole("menu", { name: "Message actions" });
  await sheet.getByRole("menuitem", { name: "Remove reaction 👍" }).tap();
  await expect(bubble.getByRole("listitem", { name: "👍 1" })).toBeVisible();
  await bubble.click({ button: "right" });
  await sheet.getByRole("menuitem", { name: "React 🔥" }).tap();
  await expect(
    bubble.getByRole("listitem", { name: "🔥 1, including yours" }),
  ).toBeVisible();
});
