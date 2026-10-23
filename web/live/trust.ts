import { chromium, expect, type FullConfig } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { STATE } from "../playwright.live.config";

// The newest pairing code the service posted to the Owner Account's Saved
// Messages while it still works, read on this host (ADR 43); null
// until it is there.
function postedCode(): string | null {
  try {
    const code = execFileSync(
      "docker",
      ["exec", "enloq", "python", "-m", "enloq.saved", "--pairing-code"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
    ).trim();
    return /^[0-9]{6}$/.test(code) ? code : null;
  } catch {
    return null;
  }
}

// Every live run starts trusted: the kept trust when the service still
// accepts it, otherwise one ordinary Connect with the code from Saved
// Messages, which records the pairing there as for any browser.
export default async function trust(config: FullConfig) {
  const { baseURL } = config.projects[0].use;
  fs.mkdirSync(path.dirname(STATE), { recursive: true, mode: 0o700 });
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      baseURL,
      storageState: fs.existsSync(STATE) ? STATE : undefined,
    });
    const session = await context.request.get("/api/auth/session");
    if (session.ok() && (await session.json()).authenticated) return;
    const page = await context.newPage();
    await page.goto("/");
    await page.getByRole("button", { name: /^Connect to / }).click();
    const field = page.getByLabel("Pairing code");
    await expect(field).toBeVisible();
    let code: string | null = null;
    for (let tries = 0; tries < 30 && !code; tries++) {
      code = postedCode();
      if (!code) await page.waitForTimeout(1000);
    }
    if (!code) throw new Error("No pairing code came back from Saved Messages");
    // Its last digit submits it.
    await field.fill(code);
    await expect(
      page.getByRole("button", { name: /^Account: .+\. Switch account$/ }),
    ).toBeVisible({ timeout: 30000 });
    await context.storageState({ path: STATE });
    fs.chmodSync(STATE, 0o600);
  } finally {
    await browser.close();
  }
}
