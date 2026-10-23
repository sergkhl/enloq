import { expect, test, type Page } from "@playwright/test";
import { trust, openSettings } from "./fixture";

// ADR 47 Q5: a newly paired browser asks once for notifications, with a sheet
// whose Turn on starts the browser's prompt. Headless Chromium reports
// notifications as denied, so these journeys stand in a browser that hasn't
// decided, whose prompt is then refused.
async function undecided(page: Page) {
  await page.addInitScript(() => {
    class Undecided {
      static permission: NotificationPermission = "default";
      static async requestPermission() {
        Undecided.permission = "denied";
        return Undecided.permission;
      }
    }
    Object.defineProperty(window, "Notification", {
      value: Undecided,
      configurable: true,
    });
  });
}

test("a newly paired browser asks once, and Not now keeps it from asking again", async ({
  page,
}) => {
  await undecided(page);
  await trust(page);
  const ask = page.getByRole("dialog", { name: "Turn on notifications?" });
  await expect(ask).toBeVisible();
  await expect(
    ask.getByText(
      "New private messages alert this browser. Groups never alert.",
    ),
  ).toBeVisible();
  await ask.getByRole("button", { name: "Not now" }).tap();
  await expect(ask).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("button", { name: /^Account: .+\. Switch account$/ }),
  ).toBeVisible();
  await expect(ask).toHaveCount(0);
});

test("Turn on starts the browser's prompt, and a refusal shows in Settings", async ({
  page,
}) => {
  await undecided(page);
  await trust(page);
  const ask = page.getByRole("dialog", { name: "Turn on notifications?" });
  await ask.getByRole("button", { name: "Turn on" }).tap();
  await expect(ask).toHaveCount(0);
  await openSettings(page);
  await page.getByRole("button", { name: /^Notifications/ }).tap();
  await expect(
    page.getByText(
      "Notifications are blocked for this site in the browser's settings.",
    ),
  ).toBeVisible();
});
