import type { Page } from "@playwright/test";
import {
  chooseView,
  expect,
  MAYA,
  openMayaChat,
  OWNER_ACCOUNT,
  test,
} from "./maya";

// ADR 52 on the live service, in the Maya chat only (AGENTS.md, Live Telegram
// sends): Maya sends a test message and deletes it through Enloq, so it leaves
// her side at once (Q8); the Owner Account's side shows what it last said with
// a trash in both views until hidden (Q7, Q11), and the list's preview gives
// way to the message before it (Q10).
const OWNER = `/api/accounts/${OWNER_ACCOUNT}`;
const OWNER_CHAT = `${OWNER}/chats/user:${MAYA}`;
const MAYA_CHAT = `/api/accounts/${MAYA}/chats/user:${OWNER_ACCOUNT}`;

type Original = {
  message_id: number;
  date: number;
  text: string;
  outgoing: boolean;
  deleted: boolean;
  native_edited: boolean;
};

async function read<T>(page: Page, path: string): Promise<T> {
  const answer = await page.request.get(path);
  expect(answer.status(), path).toBe(200);
  return answer.json();
}

async function preview(page: Page) {
  const rows = await read<
    { peer: string; preview: { original: string; deleted: boolean } }[]
  >(page, `${OWNER}/chats`);
  return rows.find((row) => row.peer === `user:${MAYA}`)!.preview;
}

test("a deletion made elsewhere shows its last words until hidden", async ({
  page,
}) => {
  test.setTimeout(300000);
  const text = `[Enloq live check ADR 52] deleted, then hidden ${new Date().toISOString()}`;
  await openMayaChat(page, "maya");
  await chooseView(page, "originals");
  await page.getByRole("textbox", { name: "Message" }).fill(text);
  await page.getByRole("button", { name: "Send" }).tap();
  // Telegram's copy reaches Maya's store before she deletes it.
  await expect
    .poll(
      async () => {
        const chat = await read<{ messages: Original[] }>(page, MAYA_CHAT);
        const sent = chat.messages.find((m) => m.text === text);
        return !!sent && Number.isInteger(sent.date);
      },
      { timeout: 60000, intervals: [3000] },
    )
    .toBe(true);
  await page.reload();
  await chooseView(page, "originals");
  const mine = page
    .locator("article.app-bubble.outgoing")
    .filter({ hasText: text });
  await mine.click({ button: "right" });
  await page
    .getByRole("menu", { name: "Message actions" })
    .getByRole("menuitem", { name: "Select" })
    .tap();
  await page
    .getByRole("toolbar", { name: "Message selection" })
    .getByRole("button", { name: "Delete selected messages" })
    .tap();
  await page
    .getByRole("dialog", { name: "Delete messages" })
    .getByRole("button", { name: "Delete for both participants" })
    .tap();
  await expect(page.getByText("Deleted · 1 message")).toBeVisible({
    timeout: 60000,
  });
  // Deleted through Enloq: gone from Maya's side at once (Q8).
  await expect(mine).toHaveCount(0, { timeout: 30000 });
  await expect
    .poll(
      async () =>
        (await read<{ messages: Original[] }>(page, MAYA_CHAT)).messages.some(
          (m) => m.text === text,
        ),
      { timeout: 30000, intervals: [3000] },
    )
    .toBe(false);
  await page.screenshot({ path: "../.local/live-results/deleted-maya.png" });

  // Deleted elsewhere for the Owner Account: its last words with a trash.
  await expect
    .poll(
      async () =>
        (await read<{ messages: Original[] }>(page, OWNER_CHAT)).messages.find(
          (m) => m.text === text,
        )?.deleted,
      { timeout: 60000, intervals: [3000] },
    )
    .toBe(true);
  // The list previews it by what it last said, marked deleted (Q10).
  expect(await preview(page)).toEqual(
    expect.objectContaining({ original: text, deleted: true }),
  );
  await openMayaChat(page, "owner");
  const theirs = page.locator("article.app-bubble").filter({ hasText: text });
  for (const view of ["ai", "originals"] as const) {
    await chooseView(page, view);
    await expect(
      theirs.getByRole("img", { name: "Deleted in Telegram" }),
    ).toBeVisible({ timeout: 30000 });
  }
  await page.screenshot({ path: "../.local/live-results/deleted-shown.png" });
  await theirs.click({ button: "right" });
  const sheet = page.getByRole("menu", { name: "Message actions" });
  await expect(sheet.getByRole("menuitem", { name: "Reply" })).toBeDisabled();
  await sheet.getByRole("menuitem", { name: "Hide" }).tap();
  await expect(theirs).toHaveCount(0);
  await page.screenshot({ path: "../.local/live-results/deleted-hidden.png" });
  expect((await preview(page)).original).not.toContain(text);
});

// Q18: Saved notes whose outgoing flag flipped keep no versions after
// schema 35, read fresh from the service.
test("Saved notes keep no outgoing flips as versions", async ({ page }) => {
  const saved = `${OWNER}/chats/user:${OWNER_ACCOUNT}`;
  for (const identity of [80344, 80347, 80350, 80351])
    expect(
      await read<unknown[]>(page, `${saved}/originals/${identity}/versions`),
      String(identity),
    ).toEqual([]);
  const notes = (await read<{ messages: Original[] }>(page, saved)).messages;
  for (const identity of [80344, 80350]) {
    const note = notes.find((m) => m.message_id === identity);
    if (!note) continue;
    expect(note.outgoing, String(identity)).toBe(true);
    expect(note.native_edited, String(identity)).toBe(false);
  }
});
