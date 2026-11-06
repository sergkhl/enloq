import { expect, request, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// The journeys' own fixture server (playwright.journeys.config.ts).
export const ORIGIN = "http://127.0.0.1:8777";

// Only the disposable fixture server's control reference, read from the file
// it records. Never send it through page JavaScript, storage or URLs.
function secret() {
  const directory = fs
    .readFileSync("../.local/browser-test-location-8777", "utf8")
    .trim();
  return fs.readFileSync(path.join(directory, "bootstrap"), "utf8").trim();
}

// Trusts this browser the way the Mac launcher does: the pairing locator,
// then the fixture's own approval of the code the browser shows. A server
// with a public origin takes no such pairing (ADR 47 Q1).
export async function trust(page: Page) {
  const control = await request.newContext({
    baseURL: ORIGIN,
    extraHTTPHeaders: { Origin: ORIGIN },
  });
  try {
    const key = secret();
    const started = await control.post("/api/auth/pairings", {
      data: { secret: key },
    });
    expect(started.status()).toBe(200);
    const { id } = await started.json();
    await page.goto(`/?connect=${id}`);
    const shown = page.getByLabel("Browser matching code");
    await expect(shown).toBeVisible();
    const approved = await control.post(`/api/auth/pairings/${id}/control`, {
      data: {
        secret: key,
        action: "approve",
        code: (await shown.innerText()).trim(),
      },
    });
    expect(approved.status()).toBe(200);
    await expect(
      page.getByRole("button", { name: /^Account: .+\. Switch account$/ }),
    ).toBeVisible();
    // The journeys share one fixture: each starts in AI reading, whatever an
    // earlier one chose.
    const view = await page.request.post("/api/preferences/reading-view", {
      data: { reading_view: "ai" },
      headers: { Origin: ORIGIN },
    });
    expect(view.status()).toBe(200);
  } finally {
    await control.dispose();
  }
}

// The fixture's authored groups, Group AI processing Off and unbriefed.
export async function seedGroups(page: Page) {
  const seeded = await page.request.post("/api/test/group-chats", {
    data: {},
    headers: { Origin: ORIGIN },
  });
  expect(seeded.status()).toBe(200);
}

// Settings is the account sheet's last row since ADR 51 Q19, Q21.
export async function openSettings(page: Page) {
  await page
    .getByRole("button", { name: /^Account: .+\. Switch account$|^Accounts$/ })
    .tap();
  await page
    .getByRole("dialog", { name: "Accounts" })
    .getByRole("button", { name: "Settings" })
    .tap();
}

// The header's robot opens the chat's AI Menu (ADR 62 Q1).
export async function openAiMenu(page: Page) {
  await page.getByRole("button", { name: /^AI Menu/ }).tap();
  const menu = page.getByRole("dialog", { name: "AI Menu" });
  await expect(menu).toBeVisible();
  return menu;
}

// AI view, the AI Menu's first row, switches the Reading View; the menu then
// closes.
export async function chooseView(page: Page, view: "ai" | "originals") {
  const menu = await openAiMenu(page);
  const toggle = menu.getByRole("switch", { name: "AI view" });
  const ai = String(view === "ai");
  if ((await toggle.getAttribute("aria-checked")) !== ai) await toggle.tap();
  await expect(toggle).toHaveAttribute("aria-checked", ai);
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
}

// A control's page from the AI Menu, such as Remember this chat's (Q16).
export async function openAiPage(page: Page, control: string) {
  const menu = await openAiMenu(page);
  await menu.getByRole("button", { name: control }).tap();
  await expect(page.getByRole("heading", { name: control })).toBeVisible();
}
