import type { Page } from "@playwright/test";
import { expect, MAYA, openMayaChat, OWNER_ACCOUNT, test } from "./maya";

// ADR 53 CA5 and ADR 54 on the live service, in the Maya chat only (AGENTS.md,
// Live Telegram sends). Maya sends an angry authored message, a plain one and
// an image with a caption, the Owner Account answering each so that each is its
// own block. In the Owner Account's AI view the angry one shows Calmed, the
// plain one shows as sent with no AI label, and the image is still rewritten.
// The remembered Reading View and Calm this chat are the owner's: they are
// read, and the view is put back if the check needed AI. LIVE_STAMP reruns the
// reading alone for a run whose sends are done.
const CHAT = `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`;
let stamp = process.env.LIVE_STAMP ?? "";
const MARKS = {
  angry: () => `Enloq live check ADR 53, ${stamp}`,
  plain: () => `Enloq live check ADR 54, ${stamp}`,
  image: () => `Enloq live check ADR 54 image, ${stamp}`,
};

test.describe.configure({ mode: "serial" });

type Block = {
  id: number;
  sources: number[];
  state: string;
  calmed: boolean;
  rendered: string | null;
  deciding?: boolean;
};

type Snapshot = {
  calming: boolean;
  blocks: Block[];
  messages: { message_id: number; text: string; outgoing: boolean }[];
};

async function snapshot(page: Page): Promise<Snapshot> {
  const answer = await page.request.get(CHAT);
  expect(answer.status()).toBe(200);
  return answer.json();
}

// Why a page would say "Enloq isn't running": a failed or refused request,
// or a server error.
function diagnose(page: Page) {
  page.on("requestfailed", (request) =>
    console.log(
      `Failed: ${request.method()} ${new URL(request.url()).pathname} ${request.failure()?.errorText}`,
    ),
  );
  page.on("response", (response) => {
    if (response.status() >= 500)
      console.log(
        `HTTP ${response.status()}: ${new URL(response.url()).pathname}`,
      );
  });
}

// The block of Maya's message carrying `mark`, once `done` holds for it.
async function settled(
  page: Page,
  mark: string,
  done: (block: Block) => boolean,
  timeout: number,
) {
  let found: Block | undefined;
  await expect
    .poll(
      async () => {
        const value = await snapshot(page);
        const message = value.messages.find(
          (m) => !m.outgoing && m.text.includes(mark),
        );
        found =
          message &&
          value.blocks.find((b) => b.sources.includes(message.message_id));
        return !!found && done(found);
      },
      { timeout, intervals: [5000] },
    )
    .toBe(true);
  return found!;
}

async function send(page: Page, text: string) {
  const field = page.getByRole("textbox", { name: "Message" });
  await field.fill(text);
  await page.getByRole("button", { name: "Send" }).tap();
  await expect(field).toHaveValue("");
}

async function reply(page: Page, text: string) {
  await openMayaChat(page, "owner");
  await send(page, text);
  await expect(
    page
      .locator("article.app-bubble.outgoing")
      .filter({ hasText: text })
      .getByLabel("Sent"),
  ).toBeVisible({ timeout: 30000 });
  // The waits that follow read the API; a page left open for minutes once
  // lost a background request and showed "Enloq isn't running".
  await page.goto("about:blank");
}

function bubble(page: Page, block: Block) {
  return page.locator(
    `article.app-bubble.incoming[data-source-ids="${block.sources.join(" ")}"]`,
  );
}

// A small PNG drawn in the page, so the test image says what it is.
async function testImage(page: Page, label: string) {
  const url = await page.evaluate((label) => {
    const canvas = document.createElement("canvas");
    canvas.width = 480;
    canvas.height = 160;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "#d9e4f2";
    context.fillRect(0, 0, 480, 160);
    context.fillStyle = "#1d2433";
    context.font = "24px sans-serif";
    context.fillText(label, 20, 90);
    return canvas.toDataURL("image/png");
  }, label);
  return Buffer.from(url.split(",")[1], "base64");
}

test("angry text is calmed, plain text shows as sent, images are rewritten", async ({
  page,
}) => {
  test.skip(!!process.env.LIVE_STAMP, "That run's sends are done");
  test.setTimeout(900000);
  diagnose(page);
  stamp = new Date().toISOString();
  const angry = `WHERE IS MY REFUND OF 240?? You're useless. Answer me by Friday or I complain to the bank. (${MARKS.angry()})`;
  const plain = `The plumber can come on Thursday at ten, the key is with the neighbour. (${MARKS.plain()})`;
  const caption = `This is the shelf I meant for the hallway. (${MARKS.image()})`;
  expect((await snapshot(page)).calming).toBe(true);

  await openMayaChat(page, "maya");
  await send(page, angry);
  await reply(page, `Got it, looking. (Enloq live check, ${stamp})`);
  const calmed = await settled(
    page,
    MARKS.angry(),
    (b) => b.state === "ready" && b.calmed,
    300000,
  );
  console.log(`Calmed: ${calmed.rendered}`);
  expect(calmed.rendered).toContain("240");

  await openMayaChat(page, "maya");
  await send(page, plain);
  await reply(page, `Thursday works. (Enloq live check, ${stamp})`);
  const combined = await settled(
    page,
    MARKS.plain(),
    (b) => !b.deciding && b.state === "original",
    300000,
  );
  expect(combined.calmed).toBe(false);
  expect(combined.rendered).toBeNull();

  await openMayaChat(page, "maya");
  await page.getByLabel("Attach images").setInputFiles({
    name: "live-check.png",
    mimeType: "image/png",
    buffer: await testImage(page, "Enloq live check ADR 54"),
  });
  await expect(
    page.getByRole("button", { name: "Remove image 1" }),
  ).toBeVisible({ timeout: 30000 });
  await send(page, caption);
  await expect(page.getByRole("button", { name: "Remove image 1" })).toBeHidden(
    { timeout: 90000 },
  );
  await reply(page, `Nice shelf. (Enloq live check, ${stamp})`);
  const image = await settled(
    page,
    MARKS.image(),
    (b) => b.state === "ready",
    420000,
  );
  console.log(`Image block: ${image.rendered}`);
  expect(image.rendered).toBeTruthy();
  // Still plain after the image's rewrite: nothing rewrote it later.
  const again = (await snapshot(page)).blocks.find((b) => b.id === combined.id);
  expect(again?.state).toBe("original");
});

test("the AI view shows them as the Owner Account reads them", async ({
  page,
}) => {
  test.setTimeout(180000);
  diagnose(page);
  expect(stamp).not.toBe("");
  const [calmed, combined, image] = await Promise.all(
    [MARKS.angry(), MARKS.plain(), MARKS.image()].map((mark) =>
      settled(page, mark, () => true, 10000),
    ),
  );
  expect(calmed.calmed && combined.state === "original").toBe(true);
  expect(image.state).toBe("ready");
  await openMayaChat(page, "owner");
  const toggle = page.getByRole("button", { name: "AI view" });
  const remembered = await toggle.getAttribute("aria-pressed");
  try {
    if (remembered !== "true") await toggle.tap();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    const head = (block: Block) =>
      bubble(page, block).locator(".bubble-ai-head");
    await expect(head(calmed)).toHaveText(/^AI · 1 message · .*Calmed/);
    await expect(head(combined)).toHaveText(/^1 message · /);
    await expect(bubble(page, combined)).toContainText(
      "The plumber can come on Thursday at ten",
    );
    await expect(bubble(page, combined).getByText("Original text")).toHaveCount(
      0,
    );
    await expect(head(image)).toHaveText(/^AI · 1 message · /);
    await bubble(page, calmed).scrollIntoViewIfNeeded();
    await page.screenshot({ path: "../.local/live-results/calming.png" });
  } finally {
    if (remembered !== "true") {
      await toggle.tap();
      await expect(toggle).toHaveAttribute("aria-pressed", String(remembered));
    }
  }
});
