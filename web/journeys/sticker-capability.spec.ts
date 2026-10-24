import { expect, test, type Page } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

declare global {
  interface Window {
    stickerProbe: {
      blocked: boolean;
      samples: number;
      video: HTMLVideoElement | null;
    };
  }
}

// Reproduce Chromium reporting loadeddata before the canary can be drawn.
// Only the detached 32px canary is held blank; released samples use the real
// browser decoder, canvas pixels and unchanged public canary bytes.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window.stickerProbe = {
      blocked: true,
      samples: 0,
      video: null as HTMLVideoElement | null,
    });
    const draw = CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage = function (
      source: CanvasImageSource,
      ...coordinates: number[]
    ) {
      if (
        source instanceof HTMLVideoElement &&
        !source.isConnected &&
        this.canvas.width === 32 &&
        this.canvas.height === 32
      ) {
        state.video = source;
        state.samples++;
        if (state.blocked) return;
      }
      return Reflect.apply(draw, this, [source, ...coordinates]);
    };
  });
  await trust(page);
  const view = await page.request.post("/api/preferences/reading-view", {
    data: { reading_view: "originals" },
    headers: { Origin: ORIGIN },
  });
  expect(view.status()).toBe(200);
  const seeded = await page.request.post("/api/test/media-kinds", {
    data: {},
    headers: { Origin: ORIGIN },
  });
  expect(seeded.status()).toBe(200);
  await page.reload();
});

async function openSticker(page: Page) {
  await page.getByRole("button", { name: /^Media kinds/ }).tap();
  const sticker = page.getByRole("img", { name: "Sticker 😺" });
  await sticker.scrollIntoViewIfNeeded();
  await expect
    .poll(() => page.evaluate(() => window.stickerProbe.samples))
    .toBeGreaterThan(0);
  return sticker;
}

async function expectReleased(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => window.stickerProbe.video?.getAttribute("src")),
    )
    .toBe(null);
  const samples = await page.evaluate(() => window.stickerProbe.samples);
  // A finished attempt must not leave a frame-sampling loop behind.
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  expect(await page.evaluate(() => window.stickerProbe.samples)).toBe(samples);
}

test("a blank first canary frame stays pending until real pixels are readable", async ({
  page,
}) => {
  const sticker = await openSticker(page);
  await expect(sticker.locator("video")).toHaveCount(0);
  await page.evaluate(() => {
    window.stickerProbe.blocked = false;
  });
  await expect(sticker.locator("video[loop]")).toHaveAttribute(
    "src",
    /\/originals\/23\/media\/preview\?revision=1$/,
  );
  await expect
    .poll(() =>
      sticker
        .locator("video")
        .evaluate((video: HTMLVideoElement) => video.paused),
    )
    .toBe(false);
  await expectReleased(page);
});

test("an unreadable canary stops at its deadline and keeps the still fallback", async ({
  page,
}) => {
  const sticker = await openSticker(page);
  await expect
    .poll(() =>
      page.evaluate(() => window.stickerProbe.video?.getAttribute("src")),
    )
    .toMatch(/^blob:/);
  await expect
    .poll(
      () => page.evaluate(() => window.stickerProbe.video?.getAttribute("src")),
      { timeout: 7_000 },
    )
    .toBe(null);
  await expect(sticker.locator("video")).toHaveCount(0);
  await expect(sticker.locator("img")).toBeVisible();
  await expectReleased(page);
});

test("leaving a pending sticker cancels sampling and a new visit can verify it", async ({
  page,
}) => {
  await openSticker(page);
  await page
    .getByRole("button", { name: "Back to conversations", exact: true })
    .tap();
  await expectReleased(page);
  await page.evaluate(() => {
    window.stickerProbe.blocked = false;
  });
  const sticker = await openSticker(page);
  await expect(sticker.locator("video[loop]")).toHaveAttribute(
    "src",
    /\/originals\/23\/media\/preview\?revision=1$/,
  );
});
