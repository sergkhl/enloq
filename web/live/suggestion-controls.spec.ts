import type { Page } from "@playwright/test";
import {
  expect,
  MAYA,
  openMayaChat,
  ORIGIN,
  OWNER_ACCOUNT,
  test,
} from "./maya";

// ADR 56 IS7 on the live service, in the Maya chat only (AGENTS.md, Live
// Telegram sends). Maya asks a calm question that needs an answer: under either
// signal the Owner Account gets suggestions though its emotion is neutral. A
// fold holds across a reload; the profile's Suggestions choice turns them Off,
// then Idioms, and Maya's good news then gets an idiom set (Q20 A). the Owner
// Account's answer withdraws it. The chat's choice is read first and put back;
// Remember this chat is only read.
const CHAT = `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`;
const stamp = new Date().toISOString();
const MARK = `Enloq live check ADR 56, ${stamp}`;

type Suggestions = {
  state: string;
  reason: string | null;
  attempt: string | null;
  options: { purpose: string; text: string }[];
  folded: boolean;
};

async function suggestions(page: Page): Promise<Suggestions> {
  const answer = await page.request.get(CHAT);
  expect(answer.status()).toBe(200);
  return (await answer.json()).suggestions;
}

// The next ready set other than `before`, as the API shows it.
async function readySet(page: Page, before: string | null = null) {
  let shown: Suggestions | undefined;
  await expect
    .poll(
      async () => {
        shown = await suggestions(page);
        return shown.state === "ready" && shown.attempt !== before;
      },
      { timeout: 420000, intervals: [5000] },
    )
    .toBe(true);
  console.log(
    `Suggestions: ${shown!.options.map((o) => `[${o.purpose}] ${o.text}`).join(" | ")}`,
  );
  return shown!;
}

async function send(page: Page, text: string) {
  const field = page.getByRole("textbox", { name: "Message" });
  await field.fill(text);
  await page.getByRole("button", { name: "Send" }).tap();
  await expect(field).toHaveValue("");
}

test("a calm question gets suggestions; fold, choice and idioms hold", async ({
  page,
}) => {
  test.setTimeout(900000);
  const read = await page.request.get(`${CHAT}/suggestions/settings`);
  expect(read.status()).toBe(200);
  const choice = await read.json();
  expect(choice.automatic).toBe(true);
  const remembered = (await (await page.request.get(`${CHAT}/remember`)).json())
    .enabled;

  await openMayaChat(page, "maya");
  await send(
    page,
    `Do you know what time the library opens tomorrow? (${MARK})`,
  );
  await page.goto("about:blank");
  const shown = await readySet(page);
  const count = shown.options.length;

  try {
    await openMayaChat(page, "owner");
    const area = page.getByRole("region", { name: "Response suggestions" });
    await area.getByRole("button", { name: "Hide suggestions" }).tap();
    const reopen = area.getByRole("button", { name: `Suggestions (${count})` });
    await expect(reopen).toBeVisible();
    await expect.poll(async () => (await suggestions(page)).folded).toBe(true);
    await page.reload();
    await expect(reopen).toBeVisible({ timeout: 30000 });
    await reopen.tap();
    await expect.poll(async () => (await suggestions(page)).folded).toBe(false);

    // The profile: Off hides the set; Idioms shows it again, as written.
    const profile = page.getByRole("button", { name: /open profile$/ });
    const radios = page.getByRole("radiogroup", { name: "Style" });
    await profile.tap();
    await expect(radios.getByRole("radio")).toHaveCount(3);
    await expect(
      page.getByRole("switch", { name: "Remember this chat" }),
    ).toHaveAttribute("aria-checked", String(remembered));
    const automatic = page.getByRole("switch", {
      name: "Automatic suggestions",
    });
    await automatic.tap();
    await expect(automatic).toHaveAttribute("aria-checked", "false");
    await page.getByRole("button", { name: "Back", exact: true }).tap();
    await expect(area).toHaveCount(0);
    await profile.tap();
    await automatic.tap();
    await expect(automatic).toHaveAttribute("aria-checked", "true");
    await radios.getByRole("radio", { name: "Idioms" }).tap();
    await expect(radios.getByRole("radio", { name: "Idioms" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await page.getByRole("button", { name: "Back", exact: true }).tap();
    expect((await suggestions(page)).attempt).toBe(shown.attempt);
    await expect(area.getByRole("button").nth(1)).toBeVisible();
    await page.screenshot({
      path: "../.local/live-results/suggestion-controls.png",
    });

    // Maya's next message gets an idiom set (Q5 A: from the next block).
    await openMayaChat(page, "maya");
    await send(page, `I passed my driving test today!! (${MARK}, idioms)`);
    await page.goto("about:blank");
    await readySet(page, shown.attempt);
    await openMayaChat(page, "owner");
    await expect(area.getByRole("button").nth(1)).toBeVisible();
    await page.screenshot({
      path: "../.local/live-results/suggestion-controls-idioms.png",
    });
  } finally {
    // The chat's own choice is the owner's: put back what was read.
    await page.request.post(`${CHAT}/suggestions/settings`, {
      data: { automatic: choice.automatic, style: choice.style },
      headers: { Origin: ORIGIN },
    });
  }

  // the Owner Account's answer withdraws the set.
  await send(page, `Congrats! (Enloq live check, ${stamp})`);
  await expect
    .poll(async () => (await suggestions(page)).state, { timeout: 60000 })
    .toBe("none");
});
