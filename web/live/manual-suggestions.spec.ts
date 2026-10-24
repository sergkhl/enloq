import { chmodSync, writeFileSync } from "node:fs";
import {
  expect,
  MAYA,
  openMayaChat,
  ORIGIN,
  OWNER_ACCOUNT,
  test,
} from "./maya";

// Three deliberate generations, with at most three no-work retries per
// writer and one label request per set: at most 15 model requests, 25 minutes
// and $0.15 counted (unknowns included). No Telegram sends, no memory changes.
const CHAT = `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`;

test("manual additions use each Style with automation off, preserving draft and Undo", async ({
  page,
}) => {
  test.setTimeout(1_500_000);
  await openMayaChat(page, "owner");
  const initial = await (await page.request.get(CHAT)).json();
  expect(
    !initial.draft.text &&
      !initial.draft.attachments.length &&
      !initial.draft.selected_media &&
      !initial.draft.reply_to,
    "The authored check needs an empty Maya composer",
  ).toBe(true);
  expect(
    (await (await page.request.get(`${CHAT}/remember`)).json()).enabled,
  ).toBe(false);
  const settings = await (
    await page.request.get(`${CHAT}/suggestions/settings`)
  ).json();
  const budget = (await (await page.request.get("/api/settings")).json())
    .budget;
  expect(budget.reached).toBe(false);
  const evidence: unknown[] = [];
  const seed = "[Enloq authored manual check] Friday does not work for me. ";
  const field = page.getByRole("textbox", { name: "Message" });
  const saveSettings = (automatic: boolean, style: string) =>
    page.request.post(`${CHAT}/suggestions/settings`, {
      data: { automatic, style },
      headers: { Origin: ORIGIN },
    });
  try {
    for (const style of ["friendly", "professional", "idioms"]) {
      const now = (await (await page.request.get("/api/settings")).json())
        .budget;
      expect(now.spent - budget.spent < 150_000_000 && !now.reached).toBe(true);
      expect((await saveSettings(false, style)).status()).toBe(200);
      await field.fill(seed);
      const before = await (await page.request.get(CHAT)).json();
      const previous = before.suggestions.source_time || 0;
      await page.getByRole("button", { name: "Generate suggestions" }).tap();
      let shown: any;
      await expect
        .poll(
          async () => {
            shown = (await (await page.request.get(CHAT)).json()).suggestions;
            return (
              shown.kind === "manual" &&
              shown.source_time > previous &&
              ["ready", "failed"].includes(shown.state)
            );
          },
          { timeout: 420000, intervals: [2000] },
        )
        .toBe(true);
      evidence.push({
        style,
        state: shown.state,
        reason: shown.reason,
        attempt: shown.attempt,
        options: shown.options,
      });
      expect(
        shown.state,
        `${style} generation must pass format and provider checks`,
      ).toBe("ready");
      expect(shown.style).toBe(style);
      expect(shown.options.length).toBeGreaterThan(0);
      await expect(field).toHaveValue(seed);
      await page.reload();
      await expect(field).toHaveValue(seed);
      const area = page.getByRole("region", { name: "Response suggestions" });
      await area.locator(".suggestion-list button").first().tap();
      await expect.poll(() => field.inputValue()).not.toBe(seed);
      expect((await field.inputValue()).startsWith(seed.trimEnd())).toBe(true);
      await page.getByRole("button", { name: "Undo suggestion" }).tap();
      await expect(field).toHaveValue(seed);
      const after = await (await page.request.get(CHAT)).json();
      expect(after.operations.map((op: any) => op.id)).toEqual(
        before.operations.map((op: any) => op.id),
      );
      await field.fill("");
    }
    expect(
      (
        await page.request.post(`${CHAT}/rewrite`, {
          data: { revision: 0 },
          headers: { Origin: ORIGIN },
        })
      ).status(),
    ).toBe(404);
  } finally {
    const current = (await (await page.request.get(CHAT)).json()).draft;
    if (current.text === seed || current.text.startsWith(seed.trimEnd())) {
      await page.request.post(`${CHAT}/draft`, {
        data: {
          revision: current.revision,
          text: "",
          reply_to: current.reply_to,
        },
        headers: { Origin: ORIGIN },
      });
    }
    await saveSettings(settings.automatic, settings.style);
    const path = "../.local/live-results/manual-suggestions.json";
    writeFileSync(path, JSON.stringify(evidence, null, 2) + "\n", {
      mode: 0o600,
    });
    chmodSync(path, 0o600);
  }
});
