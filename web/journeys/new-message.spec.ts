import { expect, test } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

// ADR 51 Q15-Q19: the pencil in the gear's old place on Private opens New
// Message, which lists chats, then contacts, searches Telegram's people for
// three typed characters, and opens the chosen person's chat outside the
// seven-day list.
test.beforeEach(async ({ page }) => {
  await trust(page);
  const seeded = await page.request.post("/fixture/new-message-people", {
    data: {},
    headers: { Origin: ORIGIN },
  });
  expect(seeded.status()).toBe(200);
});

test("New Message finds people and opens a new chat", async ({ page }) => {
  const pencil = page.getByRole("button", { name: "New message", exact: true });
  await expect(pencil).toBeVisible();
  await expect(page.getByRole("button", { name: "Settings" })).toHaveCount(0);
  // Groups and Saved keep the pencil's place empty.
  await page.getByRole("tab", { name: "Groups" }).tap();
  await expect(pencil).toHaveCount(0);
  await page.getByRole("tab", { name: "Private" }).tap();
  await pencil.tap();
  const screen = page.getByRole("region", { name: "New message" });
  await expect(screen.getByRole("heading", { name: "Chats" })).toBeVisible();
  await expect(
    screen.getByRole("button", { name: /^Alex · weekend plans/ }),
  ).toBeVisible();
  await expect(
    screen.getByRole("button", { name: "Zoe Contact, @zoe_contact" }),
  ).toBeVisible();
  await expect(
    screen.getByRole("heading", { name: "Global search" }),
  ).toHaveCount(0);
  await screen.getByRole("searchbox", { name: "Search people" }).fill("zoe");
  const found = screen.getByRole("button", { name: "Zoe Public, @zoe_public" });
  await expect(found).toBeVisible();
  await expect(
    screen.getByRole("heading", { name: "Global search" }),
  ).toBeVisible();
  await found.tap();
  await expect(page.getByRole("heading", { name: "Zoe Public" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  // Opening alone doesn't put the chat in the seven-day list.
  await page.getByRole("button", { name: "Back to conversations" }).tap();
  await expect(
    page.getByRole("button", { name: /^Alex · weekend plans/ }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /^Zoe Public/ })).toHaveCount(
    0,
  );
});
