import { expect, request, test } from "@playwright/test";
import { ORIGIN, seedGroups, trust, openSettings } from "./fixture";

// MW4: Settings and the account screens in the app's design (ADR 45), a menu
// of Notifications, Budget, AI and Privacy since ADR 47 Q6.
test.beforeEach(async ({ page }) => {
  await trust(page);
});

test("Settings changes and restores the controls, lists trust and disconnects", async ({
  page,
}) => {
  await openSettings(page);
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await page.getByRole("button", { name: /^Notifications/ }).tap();
  await expect(page.getByText("This browser", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Back" }).tap();

  await page.getByRole("button", { name: /^Budget/ }).tap();
  await expect(page.getByRole("heading", { name: "Budget" })).toBeVisible();
  const group = page.getByRole("group", { name: "Group Briefing Budget" });
  await page
    .getByRole("button", { name: "Change Group Briefing Budget limit" })
    .tap();
  const amount = page.getByLabel("Group Briefing Budget per day in dollars");
  await amount.fill("12");
  await group.getByRole("button", { name: "Save" }).tap();
  await expect(group.getByText("$0 to $10, in whole cents.")).toBeVisible();
  await amount.fill("3");
  await group.getByRole("button", { name: "Save" }).tap();
  await expect(group.getByText(/of \$3\.00 today/)).toBeVisible();
  await page
    .getByRole("button", { name: "Change Group Briefing Budget limit" })
    .tap();
  await amount.fill("2");
  await group.getByRole("button", { name: "Save" }).tap();
  await expect(group.getByText(/of \$2\.00 today/)).toBeVisible();
  await page.getByRole("button", { name: "Back" }).tap();

  await page.getByRole("button", { name: /^AI/ }).tap();
  await expect(page.getByRole("heading", { name: "AI" })).toBeVisible();
  const suggestions = page.getByRole("switch", { name: "Suggestions" });
  await suggestions.tap();
  await expect(suggestions).toHaveAttribute("aria-checked", "false");
  await suggestions.tap();
  await expect(suggestions).toHaveAttribute("aria-checked", "true");

  await page.getByRole("button", { name: /^Guidance/ }).tap();
  await page.getByRole("button", { name: "Neutral, Default" }).tap();
  const text = page.getByRole("textbox", { name: "Neutral" });
  await text.fill(`${await text.inputValue()} Journey edit.`);
  await page.getByRole("button", { name: "Save", exact: true }).tap();
  const reset = page.getByRole("button", { name: "Reset to default" });
  await expect(reset).toBeEnabled();
  await reset.tap();
  await page.getByRole("button", { name: "Confirm reset" }).tap();
  await expect(reset).toBeDisabled();
  await page.getByRole("button", { name: "Back" }).tap();
  await expect(
    page.getByRole("button", { name: "Neutral, Default" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Back" }).tap();
  await page.getByRole("button", { name: "Back" }).tap();

  await page.getByRole("button", { name: /^Privacy/ }).tap();
  await expect(page.getByText(/^This browser · Last used/)).toBeVisible();
  await page.getByRole("button", { name: "Back" }).tap();

  await page.getByRole("button", { name: /^Disconnect from / }).tap();
  await page.getByRole("button", { name: "Disconnect", exact: true }).tap();
  await expect(
    page.getByRole("heading", { name: "Pair this browser" }),
  ).toBeVisible();
});

test("a spent group budget opens it in Settings, outlined", async ({
  page,
}) => {
  await seedGroups(page);
  const set = (limit: string) =>
    page.request.post("/api/settings/group-budget", {
      data: { limit_usd: limit },
      headers: { Origin: ORIGIN },
    });
  expect((await set("0")).status()).toBe(200);
  try {
    await page.getByRole("tab", { name: "Groups" }).tap();
    await page.getByRole("button", { name: /^Basic discussion/ }).tap();
    await page.getByRole("button", { name: "Conversation actions" }).tap();
    const processing = page.getByRole("switch", {
      name: "Group AI processing",
    });
    await processing.tap();
    await expect(processing).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("Escape");
    await page
      .getByRole("button", { name: "Group Briefing Budget spent" })
      .first()
      .tap();
    await expect(page.getByRole("heading", { name: "Budget" })).toBeVisible();
    await expect(page.locator(".budget-card.outlined")).toHaveAttribute(
      "aria-label",
      "Group Briefing Budget",
    );
  } finally {
    await set("2");
  }
});

test("Manage accounts adds an account by the synthetic login, then removes it", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: /^Account: .+\. Switch account$/ })
    .tap();
  await page
    .getByRole("dialog", { name: "Accounts" })
    .getByRole("button", { name: "Manage accounts" })
    .tap();
  await expect(
    page.getByRole("heading", { name: "Manage accounts" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Add account" }).tap();
  await expect(
    page.getByText(/^Synthetic login for this offline demo/),
  ).toBeVisible();
  await page.getByRole("button", { name: /phone number/i }).tap();
  await page.getByLabel("Phone number").fill("+12025550008");
  await page.getByRole("button", { name: "Continue" }).tap();
  // Telegram's five-digit code submits itself (ADR 47 Q3).
  await page.getByLabel("Login code").fill("12345");
  await expect(
    page.getByRole("button", { name: /^Account: Demo 8\. Switch account$/ }),
  ).toBeVisible({ timeout: 20000 });
  await page
    .getByRole("button", { name: /^Account: Demo 8\. Switch account$/ })
    .tap();
  await page
    .getByRole("dialog", { name: "Accounts" })
    .getByRole("button", { name: "Manage accounts" })
    .tap();
  await page.getByRole("button", { name: "Log out" }).last().tap();
  await page.getByRole("button", { name: "Confirm logout" }).tap();
  await page.getByRole("button", { name: "Remove local data" }).tap();
  await page.getByRole("button", { name: "Remove local data" }).tap();
  await expect(
    page.getByRole("button", { name: "Remove local data" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Back" }).tap();
  await expect(
    page.getByRole("button", { name: /^Account: .+\. Switch account$/ }),
  ).toBeVisible();
});

// ADR 47 Q1: Privacy's Pair a phone shows a QR code; a phone (the fixture's
// claim, with no cookie, as Enloq makes it) asks, and this browser trusts it
// after comparing the code.
test("Pair a phone shows a QR code and trusts the phone that claims it", async ({
  page,
}) => {
  await openSettings(page);
  await page.getByRole("button", { name: /^Privacy/ }).tap();
  await page.getByRole("button", { name: "Pair a phone" }).tap();
  const qr = page.getByRole("img", { name: "Pairing QR code" });
  await expect(qr).toBeVisible();
  const id = /client-pairings\/([a-f0-9]{32})\/qr\.svg/.exec(
    (await qr.getAttribute("src")) ?? "",
  )![1]!;
  const phone = await request.newContext({ baseURL: ORIGIN });
  try {
    const claim = await phone.post(`/api/auth/pairings/${id}/device`, {
      data: { name: "Journey phone" },
    });
    expect(claim.status()).toBe(200);
    const { code, proof } = await claim.json();
    await expect(
      page.getByText(
        "Enloq app on Journey phone asks to pair with this server.",
      ),
    ).toBeVisible();
    await expect(page.getByLabel("Code to compare")).toHaveText(code);
    await page.getByRole("button", { name: "Trust" }).tap();
    await expect(page.getByText("Finishing on the new phone…")).toBeVisible();
    const done = await phone.post(`/api/auth/pairings/${id}/device/complete`, {
      data: {},
      headers: { "X-Pairing-Proof": proof },
    });
    expect(done.status()).toBe(200);
    await expect(
      page.getByText(
        "Enloq app on Journey phone is paired. It now shows in Privacy.",
      ),
    ).toBeVisible();
    await page.getByRole("button", { name: "Done" }).tap();
    await expect(
      page.getByText("Enloq app on Journey phone", { exact: true }),
    ).toBeVisible();
    // Forgotten again, so later journeys see the trusted list as before.
    await page
      .getByRole("button", { name: "Forget Enloq app on Journey phone" })
      .tap();
    await page.getByRole("button", { name: "Forget", exact: true }).tap();
    await expect(
      page.getByText("Enloq app on Journey phone", { exact: true }),
    ).toHaveCount(0);
  } finally {
    await phone.dispose();
  }
});
