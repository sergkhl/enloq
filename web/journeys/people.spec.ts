import { expect, test, type Page } from "@playwright/test";
import { ORIGIN, trust, openSettings } from "./fixture";

// ADR 48 MB5: the owner's People and a member's own Settings. The fixture
// seeds Join Requests from authored strangers (/fixture/requests), and a
// stranger signs in through the synthetic login like anyone else.
const accountButton = /^Account: .+\. Switch account$/;

const seed = (page: Page, name: string, account_id: number) =>
  page.request.post("/fixture/requests", {
    data: { name, account_id },
    headers: { Origin: ORIGIN },
  });

async function openPeople(page: Page) {
  await openSettings(page);
  await page.getByRole("button", { name: /^People/ }).tap();
}

test("the owner approves a request, sets the member's budgets and removes them", async ({
  page,
}) => {
  await trust(page);
  expect((await seed(page, "Authored Anna", 81)).status()).toBe(200);
  await openSettings(page);
  await expect(
    page.getByRole("button", { name: /^People, 1 waiting/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^People/ }).tap();
  await page.getByRole("button", { name: "Approve Authored Anna" }).tap();
  const anna = page.getByRole("group", { name: "Authored Anna" });
  await expect(
    anna.getByText("Daily Budget: $0.0000 of $0.50 today"),
  ).toBeVisible();
  await anna
    .getByRole("button", { name: "Edit Authored Anna's budgets" })
    .tap();
  await anna
    .getByLabel("Authored Anna Daily Budget per day in dollars")
    .fill("1.25");
  await anna.getByRole("button", { name: "Save" }).tap();
  await expect(
    anna.getByText("Daily Budget: $0.0000 of $1.25 today"),
  ).toBeVisible();
  page.once("dialog", (dialog) => void dialog.accept());
  await anna.getByRole("button", { name: "Remove Authored Anna" }).tap();
  await expect(
    page.getByText(/Nobody else uses Enloq on this server yet/),
  ).toBeVisible();
});

test("the owner declines a request", async ({ page }) => {
  await trust(page);
  expect((await seed(page, "Authored Boris", 82)).status()).toBe(200);
  await openPeople(page);
  await page.getByRole("button", { name: "Decline Authored Boris" }).tap();
  await expect(
    page.getByText(/Nobody else uses Enloq on this server yet/),
  ).toBeVisible();
});

test("a join push opens People", async ({ page }) => {
  await trust(page);
  const pushed = await page.request.post("/api/test/join-push", {
    data: { name: "Authored Vera", account_id: 83 },
    headers: { Origin: ORIGIN },
  });
  expect(pushed.status()).toBe(200);
  const { id } = await pushed.json();
  await page.goto(`/?notification=${id}`);
  await expect(page.getByRole("region", { name: "People" })).toBeVisible();
  await page.getByRole("button", { name: "Decline Authored Vera" }).tap();
  await expect(
    page.getByText(/Nobody else uses Enloq on this server yet/),
  ).toBeVisible();
});

test("a member's Settings has their own read-only budgets and Leave", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in with Telegram" }).tap();
  await page.getByLabel("Phone number").fill("+1 202 555 0127");
  await page.getByRole("button", { name: "Continue" }).tap();
  await page.getByLabel("Login code").fill("12345");
  await page.getByRole("button", { name: "Agree and ask to join" }).tap();
  await expect(
    page.getByRole("heading", { name: "Waiting for approval" }),
  ).toBeVisible();
  const approved = await page.request.post("/fixture/approve", {
    data: {},
    headers: { Origin: ORIGIN },
  });
  expect(approved.status()).toBe(200);
  await expect(page.getByRole("button", { name: accountButton })).toBeVisible({
    timeout: 15_000,
  });
  await openSettings(page);
  await expect(page.getByRole("button", { name: /^Privacy/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^AI/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^People/ })).toHaveCount(0);
  await page.getByRole("button", { name: /^Budget/ }).tap();
  await expect(
    page.getByText("The server's owner sets your budgets."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Change Daily Budget limit" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Back" }).tap();
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "Leave Enloq" }).tap();
  await expect(
    page.getByRole("heading", { name: "Pair this browser" }),
  ).toBeVisible();
});
