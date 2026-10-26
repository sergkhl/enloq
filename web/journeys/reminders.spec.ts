import { expect, test, type Page } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

// ADR 58 in the app's design: the Reminders tab's chat-like rows, a pending
// reminder's chat with its Reminder Bar and set, the reminder editor with
// its pickers and Remove's question, and Add reminder through New Message.
const CHAT = "/api/accounts/1/chats/user:130/reminders";
const headers = { Origin: ORIGIN };

async function fixture(page: Page, action?: string) {
  const answer = await page.request.post("/api/test/reminders", {
    data: action ? { action } : {},
    headers,
  });
  expect(answer.status()).toBe(200);
  return answer.json();
}

async function requests(page: Page) {
  return (await fixture(page, "stats")).requests as number;
}

test.beforeEach(async ({ page }) => {
  await trust(page);
});

test("the tab's rows make no model calls; a pending row opens its chat with the bar and its set", async ({
  page,
}) => {
  await fixture(page);
  // The pending reminder's set is prepared by the timing tick, not by reads.
  await expect
    .poll(() => requests(page), { timeout: 20000 })
    .toBeGreaterThan(0);
  const before = await requests(page);
  try {
    await page.getByRole("tab", { name: "Reminders", exact: true }).tap();
    await expect(page).toHaveURL(/\?tab=reminders$/);
    await expect(
      page.getByRole("button", { name: "Daily digest · 09:00" }),
    ).toBeVisible();
    const list = page.getByRole("region", { name: "Reminders" });
    const pending = list.getByRole("button", {
      name: /^Reminder fixture, .*Ask how the trip went, Due /,
    });
    await expect(pending).toBeVisible();
    await expect(
      list.getByRole("button", {
        name: /^Reminder fixture, .*Check in, Next /,
      }),
    ).toBeVisible();
    await expect(list.getByText(/^Every \w+day · /).first()).toBeVisible();
    // With several accounts, the account label has a line of its own.
    await expect(
      list.getByText("Demo account", { exact: true }).first(),
    ).toBeVisible();
    expect(await requests(page)).toBe(before);
    await pending.tap();
    await expect(page).toHaveURL(/account=1#130$/);
    const bar = page.getByRole("region", { name: "Reminders" });
    await expect(
      bar.getByRole("button", {
        name: /^Edit reminder: Ask how the trip went/,
      }),
    ).toBeVisible();
    const field = page.getByRole("textbox", { name: "Message", exact: true });
    await expect(field).toHaveValue("");
    const option = page
      .getByRole("region", { name: "Response suggestions" })
      .getByRole("button", { name: /Thank you for telling me/ });
    await expect(option).toBeVisible();
    // Opening reuses the set that still fits.
    expect(await requests(page)).toBe(before);
    await option.tap();
    await expect(field).toHaveValue("Thank you for telling me.");
    await page.getByRole("button", { name: "Undo suggestion" }).tap();
    await expect(field).toHaveValue("");
    await bar
      .getByRole("button", { name: "Dismiss reminder: Ask how the trip went" })
      .tap();
    await expect(bar).toHaveCount(0);
    await page.getByRole("button", { name: "Back to conversations" }).tap();
    await expect(page).toHaveURL(/\?tab=reminders$/);
    await expect(list.getByRole("button", { name: /, Due / })).toHaveCount(0);
  } finally {
    await fixture(page, "cleanup");
  }
});

test("the editor uses its pickers and lists, saves, and Remove asks first", async ({
  page,
}) => {
  await fixture(page);
  try {
    await page.getByRole("tab", { name: "Reminders", exact: true }).tap();
    const list = page.getByRole("region", { name: "Reminders" });
    await list
      .getByRole("button", { name: "More for Reminder fixture" })
      .last()
      .tap();
    await page.getByRole("menuitem", { name: "Edit" }).tap();
    const editor = page.getByRole("region", { name: "Edit reminder" });
    await expect(editor.getByText("Reminder fixture")).toBeVisible();
    await editor.getByRole("radio", { name: "Monthly" }).tap();
    await editor.getByRole("radio", { name: "Day 31" }).tap();
    await editor.getByRole("radio", { name: "Exact time" }).tap();
    await editor.getByLabel("Time", { exact: true }).fill("10:30");
    await editor
      .getByRole("button", { name: /^Timezone and language: / })
      .tap();
    const sheet = page.getByRole("dialog", { name: "Timezone and language" });
    await sheet
      .getByRole("searchbox", { name: "Search timezones" })
      .fill("tok");
    await sheet.getByRole("option", { name: "Asia/Tokyo" }).tap();
    await sheet.getByRole("tab", { name: "Fallback language" }).tap();
    await sheet
      .getByRole("searchbox", { name: "Search languages" })
      .fill("rus");
    await sheet.getByRole("option", { name: /^Russian/ }).tap();
    await sheet.getByRole("button", { name: "Close" }).tap();
    await expect(
      editor.getByRole("button", {
        name: "Timezone and language: Asia/Tokyo, Russian",
      }),
    ).toBeVisible();
    await editor
      .getByRole("textbox", { name: "Message Idea" })
      .fill("Ask about 2026-12-31 [briefly]");
    await expect(
      editor.getByText(/^Will keep messages from 31 Dec/),
    ).toBeVisible();
    await expect(
      editor.getByText(/^Monthly on the 31st · 10:30 · next /),
    ).toBeVisible();
    await editor.getByRole("button", { name: "Save reminder" }).tap();
    await expect(editor).toHaveCount(0);
    await expect(
      list.getByText("Monthly on the 31st · 10:30", { exact: true }),
    ).toBeVisible();
    const saved = (await (await page.request.get(CHAT)).json()).items;
    expect(
      saved
        .map((item: any) => item.schedule)
        .find((rule: any) => rule.repeat === "monthly"),
    ).toMatchObject({
      month_day: 31,
      delivery: "exact",
      time: "10:30",
      timezone: "Asia/Tokyo",
      language: "Russian",
      idea: "Ask about 2026-12-31 [briefly]",
    });
    // Remove asks first; declining keeps the reminder.
    await list
      .getByRole("button", {
        name: /^Reminder fixture, .*Ask about 2026-12-31/,
      })
      .tap();
    const again = page.getByRole("region", { name: "Edit reminder" });
    const asked: string[] = [];
    page.once("dialog", (dialog) => {
      asked.push(dialog.message());
      void dialog.dismiss();
    });
    await again.getByRole("button", { name: "Remove reminder" }).tap();
    await expect.poll(() => asked.length).toBe(1);
    expect(asked[0]).toContain("Kept Messages");
    await expect(again).toBeVisible();
    page.once("dialog", (dialog) => void dialog.accept());
    await again.getByRole("button", { name: "Remove reminder" }).tap();
    await expect(again).toHaveCount(0);
    await expect(
      list.getByText("Monthly on the 31st · 10:30", { exact: true }),
    ).toHaveCount(0);
  } finally {
    await fixture(page, "cleanup");
  }
});

test("Add reminder chooses the account, then the person with New Message's picker", async ({
  page,
}) => {
  const seeded = await page.request.post("/fixture/new-message-people", {
    data: {},
    headers,
  });
  expect(seeded.status()).toBe(200);
  await page.getByRole("tab", { name: "Reminders", exact: true }).tap();
  await page.getByRole("button", { name: "Add reminder" }).tap();
  const accounts = page.getByRole("dialog", { name: "Add reminder for" });
  await accounts
    .getByRole("button", { name: "Demo account", exact: true })
    .tap();
  const picker = page.getByRole("region", { name: "Add reminder" });
  await picker.getByRole("button", { name: "Zoe Contact, @zoe_contact" }).tap();
  const editor = page.getByRole("region", { name: "New reminder" });
  await expect(editor.getByText("Zoe Contact")).toBeVisible();
  await editor.getByRole("radio", { name: "Every N days" }).tap();
  await editor.getByLabel("Days between reminders").fill("3");
  await expect(
    editor.getByText(/^Every 3 days · 09:00 digest · next /),
  ).toBeVisible();
  await editor.getByRole("button", { name: "Save reminder" }).tap();
  const list = page.getByRole("region", { name: "Reminders" });
  const row = list.getByRole("button", {
    name: /^Zoe Contact, .*Check in, Next /,
  });
  await expect(row).toBeVisible();
  const path = "/api/accounts/1/chats/user:113/reminders";
  const saved = (await (await page.request.get(path)).json()).items;
  expect(saved.map((item: any) => item.schedule.repeat)).toEqual(["days"]);
  for (const item of saved)
    expect(
      (
        await page.request.delete(`${path}/${item.id}`, {
          data: { revision: item.revision },
          headers,
        })
      ).status(),
    ).toBe(200);
});

test("a member-scoped reminders URL opens without an account query", async ({
  page,
}) => {
  await page.goto("/?tab=reminders");
  await expect(
    page.getByRole("button", { name: "Daily digest · 09:00" }),
  ).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Private", exact: true }),
  ).toBeEnabled();
  await page.getByRole("tab", { name: "Private", exact: true }).tap();
  await expect(page).toHaveURL(/account=1&tab=private$/);
});
