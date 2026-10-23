import { expect, test, type Page } from "@playwright/test";
import { chooseView, ORIGIN, seedGroups, trust } from "./fixture";

// CU4 (ADR 46): Message Selection and deletion in the web, in the app's
// design, against the fixture's fake transport only.
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

const deletion = (page: Page, data: object) =>
  post(page, "/api/test/deletion", data);

async function openFixture(page: Page) {
  await post(page, "/api/preferences/reading-view", {
    reading_view: "originals",
  });
  await page.reload();
  await page.getByRole("button", { name: /^Deletion fixture/ }).tap();
  await expect(
    page.getByText("Please send the authored layout."),
  ).toBeVisible();
}

async function sheetOf(page: Page, text: string) {
  await page
    .locator("article.app-bubble")
    .filter({ hasText: text })
    .click({ button: "right" });
  return page.getByRole("menu", { name: "Message actions" });
}

test("originals are selected, reviewed and deleted for both participants", async ({
  page,
}) => {
  await deletion(page, { mode: "hold" });
  await openFixture(page);
  const sheet = await sheetOf(page, "Please send the authored layout.");
  await sheet.getByRole("menuitem", { name: "Select" }).tap();
  const bar = page.getByRole("toolbar", { name: "Message selection" });
  await expect(bar.getByText("1 selected")).toBeVisible();
  // The composer steps aside for the bar.
  await expect(page.getByRole("textbox", { name: "Message" })).toBeHidden();
  const revised = page.getByRole("checkbox", {
    name: "Select: Please confirm the revised illustration price.",
  });
  await revised.tap();
  await expect(bar.getByText("2 selected")).toBeVisible();
  await expect(revised).toHaveAttribute("aria-checked", "true");
  await revised.tap();
  await expect(bar.getByText("1 selected")).toBeVisible();
  // A tap on the bubble itself counts too.
  await page
    .locator("article.app-bubble")
    .filter({ hasText: "Please confirm the revised illustration price." })
    .tap();
  await expect(bar.getByText("2 selected")).toBeVisible();
  await bar.getByRole("button", { name: "Delete selected messages" }).tap();
  const review = page.getByRole("dialog", { name: "Delete messages" });
  // The account and chat it deletes in, then each message.
  const destination = review.locator(".deletion-destination");
  await expect(destination.getByText("Demo account")).toBeVisible();
  await expect(destination.getByText("Deletion fixture")).toBeVisible();
  await expect(review.getByText("2 messages")).toBeVisible();
  const previews = review.getByRole("list", { name: "Messages to delete" });
  await expect(previews.getByRole("listitem")).toHaveCount(2);
  await review
    .getByRole("button", { name: "Delete for both participants" })
    .tap();
  await expect(review).toBeHidden();
  // The accepted deletion ends the selection; its messages say Deleting…
  await expect(bar).toBeHidden();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  await expect(page.getByText("Deleting…")).toHaveCount(2);
  await expect(page.getByText("Deleting · 0 of 2 confirmed")).toBeVisible();
  await deletion(page, { action: "release" });
  await expect(page.getByText("Deleted · 2 messages")).toBeVisible({
    timeout: 15000,
  });
});

test("a changed original is reviewed again, and a stopped deletion retries after Check result", async ({
  page,
}) => {
  await deletion(page, { mode: "fail" });
  await openFixture(page);
  const sheet = await sheetOf(page, "I will check the authored proposal.");
  await sheet.getByRole("menuitem", { name: "Select" }).tap();
  await page
    .getByRole("toolbar", { name: "Message selection" })
    .getByRole("button", { name: "Delete selected messages" })
    .tap();
  const review = page.getByRole("dialog", { name: "Delete messages" });
  await expect(review.getByText("1 message", { exact: true })).toBeVisible();
  // The original changes while its review is open.
  await deletion(page, {
    action: "edit",
    identity: 3,
    text: "I will check the updated proposal.",
  });
  await expect(
    review.getByText(
      "A message changed. Review the updated messages before deleting.",
    ),
  ).toBeVisible({ timeout: 15000 });
  await review.getByRole("button", { name: "Review updated messages" }).tap();
  await expect(
    review.getByText("I will check the updated proposal."),
  ).toBeVisible();
  await review
    .getByRole("button", { name: "Delete for both participants" })
    .tap();
  await expect(page.getByText("Deletion stopped")).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByText("Authored native refusal")).toBeVisible();
  // Check result observes the original still present; then the rest can
  // be retried after a fresh review.
  await page.getByRole("button", { name: "Check result" }).tap();
  const retry = page.getByRole("button", { name: "Retry remaining" });
  await expect(retry).toBeVisible({ timeout: 15000 });
  await deletion(page, { action: "mode", mode: "success" });
  await retry.tap();
  await expect(review.getByText("1 message", { exact: true })).toBeVisible();
  await review
    .getByRole("button", { name: "Delete for both participants" })
    .tap();
  await expect(page.getByText("Deleted · 1 message")).toBeVisible({
    timeout: 15000,
  });
});

test("a group deletes only the owner's verified originals, for everyone", async ({
  page,
}) => {
  await seedGroups(page);
  await post(page, "/api/test/group-chats", {
    action: "deletion_mode",
    peer: "chat:42",
    mode: "success",
  });
  await page.getByRole("tab", { name: "Groups" }).tap();
  await page.getByRole("button", { name: /^Basic discussion/ }).tap();
  await chooseView(page, "originals");
  // Sent from this account, but with no verified sender: not offered.
  let sheet = await sheetOf(page, "Direction alone does not prove the sender.");
  await expect(sheet.getByRole("menuitem", { name: "Reply" })).toBeVisible();
  await expect(sheet.getByRole("menuitem", { name: "Select" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  sheet = await sheetOf(page, "My authored group message.");
  await sheet.getByRole("menuitem", { name: "Select" }).tap();
  // Only the owner's own originals have a checkbox.
  await expect(
    page.getByRole("checkbox", { name: "Select: My authored group message." }),
  ).toHaveAttribute("aria-checked", "true");
  await expect(
    page.getByRole("checkbox", {
      name: "Select: The planning session is on Friday.",
    }),
  ).toHaveCount(0);
  await page
    .getByRole("toolbar", { name: "Message selection" })
    .getByRole("button", { name: "Delete selected messages" })
    .tap();
  const review = page.getByRole("dialog", { name: "Delete messages" });
  await expect(review.getByText("You", { exact: true })).toBeVisible();
  await review.getByRole("button", { name: "Delete for everyone" }).tap();
  await expect(page.getByText("Deleted · 1 message")).toBeVisible({
    timeout: 15000,
  });
});

test("an unanswered submission stays unconfirmed, across the outage, until it is checked", async ({
  page,
}) => {
  await deletion(page, { mode: "success" });
  await openFixture(page);
  // The connection drops: the request never reaches the service.
  await page.route("**/deletions", (route) =>
    route.request().method() === "POST"
      ? route.abort("connectionreset")
      : route.fallback(),
  );
  const sheet = await sheetOf(page, "Authored original 4");
  await sheet.getByRole("menuitem", { name: "Select" }).tap();
  await page
    .getByRole("toolbar", { name: "Message selection" })
    .getByRole("button", { name: "Delete selected messages" })
    .tap();
  await page
    .getByRole("dialog", { name: "Delete messages" })
    .getByRole("button", { name: "Delete for both participants" })
    .tap();
  // The web shows the outage; the submission's identity outlives it.
  await expect(
    page.getByRole("heading", { name: "Enloq isn’t running" }),
  ).toBeVisible();
  await page.unroute("**/deletions");
  // Retry returns to the conversation that was open.
  await page.getByRole("button", { name: "Retry" }).tap();
  await expect(
    page.getByRole("button", { name: /^Deletion fixture, .*open profile$/ }),
  ).toBeVisible();
  const card = page.getByRole("region", { name: "Unconfirmed deletion" });
  await expect(card.getByText("Submission unconfirmed")).toBeVisible();
  // What was reviewed stayed in the page, so only Check submission remains.
  await expect(
    card.getByRole("button", { name: "Review submission" }),
  ).toHaveCount(0);
  await card.getByRole("button", { name: "Check submission" }).tap();
  await expect(
    card.getByText("Submission not found. Its deletion result is unconfirmed."),
  ).toBeVisible();
  // The original was never deleted and can be selected again.
  await expect(page.getByText("Authored original 4")).toBeVisible();
});

// ADR 52: a message deleted elsewhere keeps what it last said, with a trash,
// until the owner hides it from its sheet or a selection of deleted ones.
test("messages deleted elsewhere show their last words until hidden", async ({
  page,
}) => {
  await deletion(page, {});
  await openFixture(page);
  for (const identity of [2, 4, 5, 6])
    await deletion(page, { action: "external_delete", identity });
  const price = page
    .locator("article.app-bubble")
    .filter({ hasText: "Please confirm the revised illustration price." });
  await expect(
    price.getByRole("img", { name: "Deleted in Telegram" }),
  ).toBeVisible({ timeout: 10000 });
  let sheet = await sheetOf(
    page,
    "Please confirm the revised illustration price.",
  );
  await expect(sheet.getByRole("menuitem", { name: "Reply" })).toBeDisabled();
  await sheet.getByRole("menuitem", { name: "Hide" }).tap();
  await expect(price).toHaveCount(0);
  // A selection started on a deleted message hides; live ones can't join.
  sheet = await sheetOf(page, "Authored original 4");
  await sheet.getByRole("menuitem", { name: "Select" }).tap();
  await expect(
    page.getByRole("checkbox", {
      name: "Select: Please send the authored layout.",
    }),
  ).toHaveCount(0);
  await page
    .getByRole("checkbox", { name: "Select: Authored original 6" })
    .tap();
  const bar = page.getByRole("toolbar", { name: "Message selection" });
  await expect(bar.getByText("2 selected")).toBeVisible();
  await expect(
    bar.getByRole("button", { name: "Delete selected messages" }),
  ).toHaveCount(0);
  await bar.getByRole("button", { name: "Hide selected messages" }).tap();
  await expect(bar).toBeHidden();
  await expect(
    page
      .locator("article.app-bubble")
      .filter({ hasText: /Authored original [456]/ }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Please send the authored layout."),
  ).toBeVisible();
});
