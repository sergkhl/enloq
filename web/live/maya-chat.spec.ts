import { openMayaChat, test } from "./maya";

// The Maya chat opens by its link as both accounts, and nothing is sent.
test("the Maya chat opens by its link as both accounts", async ({ page }) => {
  await openMayaChat(page, "owner");
  await openMayaChat(page, "maya");
});
