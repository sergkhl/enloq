import { expect, test, type Page } from "@playwright/test";
import { ORIGIN } from "./fixture";

// ADR 48 MB4: a stranger signs in with Telegram, reads the Member Agreement,
// asks to join and waits until the owner decides. The synthetic login signs
// in the account its number ends in, with the code 12345; the fixture's
// /fixture/approve and /fixture/decline decide the newest request as the
// owner would in People.
const account = /^Account: .+\. Switch account$/;

async function askToJoin(page: Page, number: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in with Telegram" }).tap();
  await page.getByLabel("Phone number").fill(number);
  await page.getByRole("button", { name: "Continue" }).tap();
  await page.getByLabel("Login code").fill("12345");
  await expect(
    page.getByRole("heading", { name: "Before you ask to join" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Agree and ask to join" }).tap();
  await expect(
    page.getByRole("heading", { name: "Waiting for approval" }),
  ).toBeVisible();
}

const decide = (page: Page, decision: "approve" | "decline") =>
  page.request.post(`/fixture/${decision}`, {
    data: {},
    headers: { Origin: ORIGIN },
  });

test("a stranger asks to join, waits, and once approved signs in anywhere", async ({
  page,
  browser,
}) => {
  await askToJoin(page, "+1 202 555 0123");
  expect((await decide(page, "approve")).status()).toBe(200);
  // The waiting page moves on by itself.
  await expect(page.getByRole("button", { name: account })).toBeVisible({
    timeout: 15_000,
  });
  // The member's first account proves who they are in another browser,
  // without the agreement again.
  const other = await browser.newContext({
    baseURL: ORIGIN,
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
  });
  try {
    const second = await other.newPage();
    await second.goto("/");
    await second.getByRole("button", { name: "Sign in with Telegram" }).tap();
    await second.getByLabel("Phone number").fill("+1 202 555 0123");
    await second.getByRole("button", { name: "Continue" }).tap();
    await second.getByLabel("Login code").fill("12345");
    await expect(second.getByRole("button", { name: account })).toBeVisible();
  } finally {
    await other.close();
    // Leaving deletes the member, so the journey can run again.
    const left = await page.request.post("/api/members/me/leave", {
      data: { confirm: true },
      headers: { Origin: ORIGIN },
    });
    expect(left.status()).toBe(200);
  }
});

test("a declined request returns the browser to connecting", async ({
  page,
}) => {
  await askToJoin(page, "+1 202 555 0124");
  expect((await decide(page, "decline")).status()).toBe(200);
  await expect(page.getByRole("alert")).toContainText(
    "Your request to join wasn't approved",
    { timeout: 15_000 },
  );
  await expect(
    page.getByRole("heading", { name: "Pair this browser" }),
  ).toBeVisible();
});

test("withdrawing a request returns the browser to connecting", async ({
  page,
}) => {
  await askToJoin(page, "+1 202 555 0125");
  await page.getByRole("button", { name: "Withdraw request" }).tap();
  await expect(page.getByRole("alert")).toContainText(
    "You withdrew your request to join.",
  );
  // Nothing waits any more.
  expect((await decide(page, "decline")).status()).toBe(404);
});
