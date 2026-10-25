import { expect, test } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

const PATH = "/api/accounts/1/chats/user:128/reminder";
const headers = { Origin: ORIGIN };

test("member reminders list makes no model calls; review preserves draft and reuses one set", async ({
  page,
}) => {
  await trust(page);
  const setup = await page.request.post("/api/test/reminders", {
    data: {},
    headers,
  });
  expect(setup.status()).toBe(200);
  const before = await (
    await page.request.post("/api/test/reminders", {
      data: { action: "stats" },
      headers,
    })
  ).json();
  try {
    await page.getByRole("tab", { name: "Reminders", exact: true }).tap();
    await expect(page).toHaveURL(/\?tab=reminders$/);
    await expect(
      page.getByText("All accounts", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Pending", exact: true }),
    ).toBeVisible();
    const listed = await (
      await page.request.post("/api/test/reminders", {
        data: { action: "stats" },
        headers,
      })
    ).json();
    expect(listed.requests).toBe(before.requests);
    await page
      .getByRole("button", { name: "Review reminder", exact: true })
      .tap();
    await expect(page).toHaveURL(/account=1&reminder=[a-f0-9]{32}#128$/);
    const field = page.getByRole("textbox", { name: "Message", exact: true });
    await expect(field).toHaveValue("");
    const option = page.getByRole("button", {
      name: /Thank you for telling me/,
    });
    await expect(option).toBeVisible();
    const ready = await (
      await page.request.post("/api/test/reminders", {
        data: { action: "stats" },
        headers,
      })
    ).json();
    expect(ready.requests).toBe(before.requests + 1);
    await page
      .getByRole("button", { name: "Back to conversations", exact: true })
      .tap();
    await expect(
      page.getByRole("heading", { name: "Reminders", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Review reminder", exact: true })
      .tap();
    await expect(option).toBeVisible();
    const reopened = await (
      await page.request.post("/api/test/reminders", {
        data: { action: "stats" },
        headers,
      })
    ).json();
    expect(reopened.requests).toBe(ready.requests);
    await option.tap();
    await expect(field).toHaveValue("Thank you for telling me.");
    await page
      .getByRole("button", { name: "Undo suggestion", exact: true })
      .tap();
    await expect(field).toHaveValue("");
    await page
      .getByRole("button", { name: "Back to conversations", exact: true })
      .tap();
    await page.getByRole("button", { name: "Dismiss", exact: true }).tap();
    await expect(
      page.getByRole("heading", { name: "Pending", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Upcoming", exact: true }),
    ).toBeVisible();
  } finally {
    await page.request.post("/api/test/reminders", {
      data: { action: "cleanup" },
      headers,
    });
  }
});

test("profile edits weekly/monthly rules with a preview and a saved timezone", async ({
  page,
}) => {
  await trust(page);
  await page.request.post("/api/test/reminders", { data: {}, headers });
  try {
    await page.getByRole("tab", { name: "Reminders", exact: true }).tap();
    await page
      .getByRole("button", { name: "Edit schedule", exact: true })
      .tap();
    await page
      .getByLabel("Reminder frequency", { exact: true })
      .selectOption("monthly");
    await page.getByLabel("Reminder calendar date", { exact: true }).fill("31");
    await page
      .getByLabel("Schedule timezone", { exact: true })
      .fill("Asia/Tokyo");
    await page
      .getByLabel("Fallback language", { exact: true })
      .fill("Japanese");
    await page
      .getByLabel("Reminder alert", { exact: true })
      .selectOption("exact");
    await page.getByLabel("Reminder time", { exact: true }).fill("10:30");
    await expect(page.getByText(/^Next: .*Asia\/Tokyo$/)).toBeVisible();
    await page
      .getByRole("button", { name: "Save reminder", exact: true })
      .tap();
    await expect
      .poll(async () => (await (await page.request.get(PATH)).json()).schedule)
      .toMatchObject({
        frequency: "monthly",
        month_day: 31,
        timezone: "Asia/Tokyo",
        language: "Japanese",
        time: "10:30",
      });
    await page
      .getByRole("button", { name: "Remove reminder", exact: true })
      .tap();
    await expect(
      page.getByRole("button", { name: "Remove reminder", exact: true }),
    ).toHaveCount(0);
  } finally {
    await page.request.post("/api/test/reminders", {
      data: { action: "cleanup" },
      headers,
    });
  }
});

test("a member-scoped reminders URL opens without an account query", async ({
  page,
}) => {
  await trust(page);
  await page.goto("/?tab=reminders");
  await expect(
    page.getByRole("heading", { name: "Reminders", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Private", exact: true }),
  ).toBeEnabled();
  await page.getByRole("tab", { name: "Private", exact: true }).tap();
  await expect(page).toHaveURL(/account=1&tab=private$/);
});
