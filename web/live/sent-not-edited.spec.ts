import type { Page } from "@playwright/test";
import {
  chooseView,
  expect,
  MAYA,
  openMayaChat,
  OWNER_ACCOUNT,
  test,
} from "./maya";

// ADR 19 on the live service, in the Maya chat only
// (AGENTS.md, Live Telegram sends): a text, a GIF and an image sent from the
// Owner Account show no edited pencil and no earlier version once Telegram's
// copy is read, and carry Telegram's date, the one Maya's copy has.
const OWNER_CHAT = `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}/history?before=0`;
const MAYA_CHAT = `/api/accounts/${MAYA}/chats/user:${OWNER_ACCOUNT}/history?before=0`;

type Original = {
  message_id: number;
  date: number;
  text: string;
  media: string | null;
  outgoing: boolean;
  native_edited: boolean;
  history_count: number;
};

async function read(page: Page, path: string): Promise<Original[]> {
  const answer = await page.request.get(path);
  expect(answer.status()).toBe(200);
  return answer.json();
}

// A small PNG drawn in the page, so the test image says what it is.
async function testImage(page: Page, label: string) {
  const url = await page.evaluate((label) => {
    const canvas = document.createElement("canvas");
    canvas.width = 480;
    canvas.height = 160;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "#cfe8d5";
    context.fillRect(0, 0, 480, 160);
    context.fillStyle = "#1d2a22";
    context.font = "24px sans-serif";
    context.fillText(label, 20, 90);
    return canvas.toDataURL("image/png");
  }, label);
  return Buffer.from(url.split(",")[1], "base64");
}

async function send(page: Page) {
  await page.getByRole("button", { name: "Send" }).tap();
}

test("sent messages show no pencil or earlier version", async ({ page }) => {
  test.setTimeout(240000);
  const stamp = new Date().toISOString();
  const note = (kind: string) =>
    `[Enloq live check ADR 19] sent ${kind}, not edited ${stamp}`;
  await openMayaChat(page, "owner");
  await chooseView(page, "originals");
  const field = page.getByRole("textbox", { name: "Message" });

  await field.fill(note("text"));
  await send(page);
  await expect(
    page
      .locator("article.app-bubble.outgoing")
      .filter({ hasText: note("text") })
      .getByLabel("Sent"),
  ).toBeVisible({ timeout: 30000 });

  // The first saved GIF, with its caption.
  await page.getByRole("button", { name: "Attach" }).tap();
  await page
    .getByRole("menu", { name: "Attach" })
    .getByRole("menuitem", { name: "GIFs" })
    .tap();
  const picker = page.getByRole("dialog", { name: "GIFs and stickers" });
  await picker
    .getByRole("button", { name: /^Select GIF / })
    .first()
    .tap();
  const staged = page.getByRole("group", { name: "Selected GIF" });
  await expect(staged.getByText("Preparing GIF…")).toBeHidden({
    timeout: 60000,
  });
  await field.fill(note("GIF"));
  await send(page);
  await expect(staged).toBeHidden({ timeout: 90000 });

  await page.getByLabel("Attach images").setInputFiles({
    name: "live-check.png",
    mimeType: "image/png",
    buffer: await testImage(page, "Enloq live check ADR 19"),
  });
  await expect(
    page.getByRole("button", { name: "Remove image 1" }),
  ).toBeVisible({
    timeout: 30000,
  });
  await field.fill(note("image"));
  await send(page);
  await expect(page.getByRole("button", { name: "Remove image 1" })).toBeHidden(
    {
      timeout: 90000,
    },
  );

  const kinds = ["text", "GIF", "image"];
  // Telegram's copies reach the store, then the chat shows them unedited.
  let sent: Original[] = [];
  await expect
    .poll(
      async () => {
        const owner = await read(page, OWNER_CHAT);
        sent = kinds.map((kind) =>
          owner.find((m) => m.text === note(kind) && m.outgoing)!,
        );
        return sent.every((m) => m && Number.isInteger(m.date));
      },
      { timeout: 60000, intervals: [3000] },
    )
    .toBe(true);
  for (const message of sent) {
    expect(message.native_edited, message.text).toBe(false);
    expect(message.history_count, message.text).toBe(0);
  }
  expect(sent.map((m) => m.media)).toEqual([null, "gif", "image"]);
  const maya = await read(page, MAYA_CHAT);
  for (const message of sent) {
    const copy = maya.find((m) => m.text === message.text && !m.outgoing);
    expect(copy?.date, message.text).toBe(message.date);
  }
  await page.reload();
  await chooseView(page, "originals");
  for (const kind of kinds) {
    const bubble = page
      .locator("article.app-bubble.outgoing")
      .filter({ hasText: note(kind) });
    await expect(bubble).toBeVisible();
    await expect(bubble.getByLabel(/^Edited in Telegram/)).toHaveCount(0);
    await expect(bubble.getByLabel(/^Versions of message/)).toHaveCount(0);
  }
  await page.screenshot({ path: "../.local/live-results/sent-not-edited.png" });
});
