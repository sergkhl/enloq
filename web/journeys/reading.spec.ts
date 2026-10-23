import { expect, test, type Locator, type Page } from "@playwright/test";
import { chooseView, seedGroups, trust } from "./fixture";

// MW2: reading in the app's design.
test.use({ permissions: ["clipboard-read", "clipboard-write"] });

test.beforeEach(async ({ page }) => {
  await trust(page);
});

// A touch long press, as a phone browser delivers it.
async function longPress(page: Page, target: Locator) {
  const box = (await target.boundingBox())!;
  const at = { clientX: box.x + 20, clientY: box.y + 20, pointerType: "touch" };
  await target.dispatchEvent("pointerdown", at);
  await page.waitForTimeout(650);
  await target.dispatchEvent("pointerup", at);
}

test("a private chat reads in both views, opens its sheet and shows links", async ({
  page,
}) => {
  await page.getByRole("button", { name: /^Alex · weekend plans/ }).tap();
  const bubble = page.locator("article.app-bubble").first();
  // Not rewritten, the block shows its originals together under one head,
  // without the AI label (ADR 54).
  await expect(bubble.locator(".bubble-ai-head")).toHaveText(
    /^\d+ messages? · /,
  );
  await chooseView(page, "originals");
  await expect(page.locator(".bubble-ai-head")).toHaveCount(0);
  await expect(page.getByText(/^AI · /)).toHaveCount(0);
  await expect(
    page
      .locator(".bubble-text")
      .filter({ hasText: "Can you bring the board game on Saturday?" })
      .first(),
  ).toBeVisible();

  await longPress(page, page.locator("article.app-bubble").first());
  const sheet = page.getByRole("menu", { name: "Message actions" });
  await sheet.getByRole("menuitem", { name: "Copy" }).tap();
  await expect(
    page.getByRole("status").filter({ hasText: "Copied" }),
  ).toBeVisible();
  // A right-click opens the same sheet.
  await page.locator("article.app-bubble").first().click({ button: "right" });
  await page
    .getByRole("menu", { name: "Message actions" })
    .getByRole("menuitem", { name: "Reply" })
    .tap();
  await expect(page.getByText(/^Reply to /).first()).toBeVisible();

  // Copy chat link left the header for the profile (ADR 47 Q8).
  await expect(
    page.getByRole("button", { name: "Copy chat link" }),
  ).toHaveCount(0);

  // Links are tappable (ADR 46 Q9).
  await page
    .getByRole("textbox", { name: "Message" })
    .fill("See https://example.com/plan");
  await page.getByRole("button", { name: "Send" }).tap();
  await expect(
    page.getByRole("link", { name: "https://example.com/plan" }),
  ).toBeVisible({ timeout: 15000 });
  await chooseView(page, "ai");
});

test("a group's briefing opens a citation in place and returns", async ({
  page,
}) => {
  await seedGroups(page);
  await page.getByRole("tab", { name: "Groups" }).tap();
  await page.getByRole("button", { name: /^Basic discussion/ }).tap();
  await expect(page.getByText("AI off")).toBeVisible();
  await page.getByRole("button", { name: "Conversation actions" }).tap();
  const processing = page.getByRole("switch", { name: "Group AI processing" });
  await processing.tap();
  await expect(processing).toHaveAttribute("aria-checked", "true");
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: /^Update briefing/ })
    .first()
    .tap();
  const card = page.locator("article.daily-briefing").first();
  await expect(
    card.getByRole("button", { name: /^Source \d+$/ }).first(),
  ).toBeVisible({
    timeout: 20000,
  });
  await card
    .getByRole("button", { name: /^Source \d+$/ })
    .first()
    .tap();
  await expect(
    page.getByRole("button", { name: "Return to briefing" }),
  ).toBeVisible();
  await expect(page.locator("article.app-bubble.highlighted")).toHaveCount(1);
  await page.getByRole("button", { name: "Return to briefing" }).tap();
  await expect(
    card.getByRole("button", { name: /^Source \d+$/ }).first(),
  ).toBeVisible();
});

// ADR 47 Q7, Q8: a group's photo and name open its Group Profile, read from
// Telegram (the fixture's authored answer), with Copy chat link.
test("a group's header opens its Group Profile, which copies the chat link", async ({
  page,
}) => {
  await seedGroups(page);
  await page.getByRole("tab", { name: "Groups" }).tap();
  await page.getByRole("button", { name: /^Supergroup discussion/ }).tap();
  await expect(
    page.getByRole("button", { name: "Copy chat link" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Supergroup discussion, open profile" })
    .tap();
  await expect(
    page.getByRole("heading", { name: "Group", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("@authored_supergroup")).toBeVisible();
  await expect(page.getByText("1,200 members")).toBeVisible();
  await expect(
    page.getByText("Authored description of Supergroup discussion."),
  ).toBeVisible();
  await expect(page.getByText("Cares about")).toHaveCount(0);
  await page.getByRole("button", { name: "Copy chat link" }).tap();
  await expect(page.getByText("Chat link copied")).toBeVisible();
  await page.getByRole("button", { name: "Back", exact: true }).tap();
  await expect(
    page.getByRole("button", { name: "Conversation actions" }),
  ).toBeVisible();
});

// ADR 51 Q13, Q14: in a group another person's run has their name and avatar,
// the owner's own messages neither, and the avatar opens their profile.
test("a group member's avatar opens their profile", async ({ page }) => {
  await seedGroups(page);
  await page.getByRole("tab", { name: "Groups" }).tap();
  await page.getByRole("button", { name: /^Basic discussion/ }).tap();
  await chooseView(page, "originals");
  const own = page
    .locator("article.app-bubble")
    .filter({ hasText: "My authored group message." });
  await expect(own).toBeVisible();
  await expect(own.locator(".bubble-author")).toHaveCount(0);
  await page.getByRole("button", { name: "Profile: Bob" }).tap();
  await expect(
    page.getByRole("heading", { name: "Bob Authored" }),
  ).toBeVisible();
  await expect(page.getByText("@bob_authored")).toBeVisible();
  await expect(page.getByText("Authored member bio.")).toBeVisible();
  await page.getByRole("button", { name: "Back", exact: true }).tap();
  await expect(own).toBeVisible();
  // Message opens the private chat with them, as New Message does.
  await page.getByRole("button", { name: "Profile: Bob" }).tap();
  await page
    .getByRole("region", { name: "Profile" })
    .getByRole("button", { name: "Message", exact: true })
    .tap();
  await expect(
    page.getByRole("heading", { name: "Bob Authored" }),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  await page.getByRole("button", { name: "Back to conversations" }).tap();
  await page.getByRole("tab", { name: "Groups" }).tap();
  await page.getByRole("button", { name: /^Basic discussion/ }).tap();
  // An unnamed author has no profile to open.
  await expect(
    page.getByRole("button", { name: "Profile: Unknown participant" }),
  ).toHaveCount(0);
});
