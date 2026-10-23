import { defineConfig } from "@playwright/test";
import { ORIGIN } from "./live/maya";

// Live checks against the real service, with the owner's two test accounts
// only: the Owner Account and Maya (AGENTS.md, Live Telegram sends), named in
// the untracked `.local/live-targets.json`. They may send real Telegram
// messages, so `./scripts/check` never runs them; run one file at a time, e.g.
// `npm --prefix web run live -- live/maya-chat.spec.ts`. The browser trusts
// itself once with the pairing code the host reads back from Saved Messages
// (ADR 43) and keeps that trust in `.local/live-browser/`.
export const STATE = "../.local/live-browser/state.json";

export default defineConfig({
  testDir: "./live",
  globalSetup: "./live/trust.ts",
  workers: 1,
  retries: 0,
  reporter: "list",
  timeout: 120000,
  use: {
    baseURL: ORIGIN,
    browserName: "chromium",
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
    deviceScaleFactor: 3,
    storageState: STATE,
  },
  outputDir: "../.local/live-results",
});
