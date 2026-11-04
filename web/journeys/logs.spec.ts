import { expect, test } from "@playwright/test";
import { openSettings, trust } from "./fixture";

// ADR 61: Settings → Logs shows this browser's log and the service's, and a
// screen that fails shows its error instead of an empty page.
test.beforeEach(async ({ page }) => {
  await trust(page);
});

test("Settings shows this browser's log, sends it and reads the service's", async ({
  page,
}) => {
  await openSettings(page);
  await page.getByRole("button", { name: "Logs" }).tap();
  const logs = page.getByRole("region", { name: "Logs" });
  await expect(logs.getByText(/api: GET settings 200/).first()).toBeVisible();
  await logs.getByRole("button", { name: "Send to server" }).tap();
  await expect(logs.getByText("Sent to the server's log.")).toBeVisible();
  await logs.getByRole("button", { name: "Server", exact: true }).tap();
  // The service's own line for this browser's post.
  await expect(
    logs.getByText(/POST \/api\/logs\/client 204/).first(),
  ).toBeVisible();
  await logs.getByRole("button", { name: "This browser" }).tap();
  await expect(logs.getByText(/api: GET settings 200/).first()).toBeVisible();
});

test("a screen that fails shows its error and its way back", async ({
  page,
}) => {
  // A row in a shape today's code can't draw.
  await page.route("**/api/reminders", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "b".repeat(32),
            revision: 1,
            account_id: 1,
            account: "Authored",
            peer: "user:42",
            title: "Authored contact",
            ai_enabled: true,
          },
        ],
      },
    }),
  );
  const posted = page.waitForRequest("**/api/logs/client");
  await page.getByRole("tab", { name: "Reminders" }).tap();
  await expect(
    page.getByRole("heading", { name: "This screen hit an error" }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("TypeError");
  // It went to the service's log by itself.
  expect((await posted).postDataJSON().entries[0].source).toBe("screen");
  await page.unroute("**/api/reminders");
  await page.getByRole("button", { name: "Back to chats" }).tap();
  await expect(page.getByRole("tab", { name: "Private" })).toBeVisible();
});
