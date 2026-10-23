import { expect, test, type Page } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

// CU1 (ADR 46): the web plays media in the app's design. The journeys'
// Chromium has no H.264, so the playable video and circle are VP9 and the
// fixture's H.264 GIF and circle are checked up to their players.
test.beforeEach(async ({ page }) => {
  await trust(page);
  const view = await page.request.post("/api/preferences/reading-view", {
    data: { reading_view: "originals" },
    headers: { Origin: ORIGIN },
  });
  expect(view.status()).toBe(200);
});

async function seed(page: Page, path: string, data: object = {}) {
  const seeded = await page.request.post(path, {
    data,
    headers: { Origin: ORIGIN },
  });
  expect(seeded.status()).toBe(200);
  await page.reload();
}

const paused = (page: Page, selector: string) =>
  page.evaluate(
    (css) => (document.querySelector(css) as HTMLMediaElement | null)?.paused,
    selector,
  );

test("voice notes play on into the next at the browser's speed, and a circle takes over", async ({
  page,
}) => {
  await seed(page, "/api/test/voice-player");
  await page.getByRole("button", { name: /^Voice player/ }).tap();
  const notes = page.locator(".voice-note");
  // Three voice notes; the music file is a card.
  await expect(notes).toHaveCount(3);
  await expect(
    page.getByRole("link", { name: /^Authored performer – Authored tone/ }),
  ).toBeVisible();
  await notes.nth(0).getByRole("button", { name: "Play voice message" }).tap();
  await expect(
    notes.nth(0).getByRole("button", { name: "Pause voice message" }),
  ).toBeVisible();
  await notes.nth(0).getByRole("button", { name: "Playback speed 1×" }).tap();
  await expect(
    notes.nth(0).getByRole("button", { name: "Playback speed 1.5×" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      localStorage.getItem("reply-assistant.playback-speed"),
    ),
  ).toBe("1.5");
  // End skips to the end: the next note plays on, then the one past the text.
  await notes
    .nth(0)
    .getByRole("slider", { name: "Voice message position" })
    .press("End");
  await expect(
    notes.nth(1).getByRole("button", { name: "Pause voice message" }),
  ).toBeVisible();
  await expect(
    notes.nth(1).getByRole("button", { name: "Playback speed 1.5×" }),
  ).toBeVisible();
  await notes
    .nth(1)
    .getByRole("slider", { name: "Voice message position" })
    .press("End");
  await expect(
    notes.nth(2).getByRole("button", { name: "Pause voice message" }),
  ).toBeVisible();
  // A video message takes the slot from the voice note.
  await page.getByRole("button", { name: "Play video message" }).tap();
  await expect(
    page.getByRole("button", { name: "Pause voice message" }),
  ).toHaveCount(0);
  // The first note's transcript, shown and copied from its sheet.
  const first = page
    .locator("article.app-bubble")
    .filter({ has: page.locator(".voice-note") })
    .first();
  await first.getByRole("button", { name: "Show transcript" }).tap();
  await expect(
    first.getByText("Authored first note transcript."),
  ).toBeVisible();
});

test("a video prepares on Play and plays with the browser's controls; files download", async ({
  page,
}) => {
  await seed(page, "/api/test/video", { action: "setup", case: "playable" });
  await page.getByRole("button", { name: /^Video player/ }).tap();
  // Play opens the chat's Media Carousel, which plays it (ADR 51 Q11).
  await page.getByRole("button", { name: "Play video", exact: true }).tap();
  const carousel = page.getByRole("dialog", { name: "Media" });
  const video = carousel.locator("video[controls]");
  await expect(video).toBeVisible();
  await expect(video).toHaveAttribute(
    "src",
    /\/originals\/18\/media\?revision=1$/,
  );
  await expect(carousel.getByText("1 / 1")).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (document.querySelector(".media-carousel video") as HTMLVideoElement)
            .currentTime,
      ),
    )
    .toBeGreaterThan(0);
  await carousel.getByRole("button", { name: "Close" }).tap();
  await expect(carousel).toHaveCount(0);
  // The circle still plays in place.
  await page.getByRole("button", { name: "Play video message" }).tap();
  await expect(
    page.getByRole("button", { name: "Pause video message" }),
  ).toBeVisible();
  // A voice note's sheet downloads it, as the music file's card does.
  const note = page
    .locator("article.app-bubble")
    .filter({ has: page.locator(".voice-note") });
  await note.click({ button: "right" });
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page
      .getByRole("menu", { name: "Message actions" })
      .getByRole("menuitem", { name: "Download" })
      .tap(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^audio_.+\.ogg$/);
  const [card] = await Promise.all([
    page.waitForEvent("download"),
    page
      .getByRole("link", { name: /^authored tone\.ogg, .+, download$/ })
      .tap(),
  ]);
  expect(card.suggestedFilename()).toMatch(/\.ogg$/);
});

test("GIFs and stickers load on screen: GIFs loop, TGS and video stickers animate", async ({
  page,
}) => {
  await seed(page, "/api/test/media-kinds");
  await page.getByRole("button", { name: /^Media kinds/ }).tap();
  const gif = page.getByRole("img", { name: "GIF" });
  await gif.scrollIntoViewIfNeeded();
  const loop = gif.locator("video[loop]");
  await expect(loop).toHaveAttribute(
    "src",
    /\/originals\/18\/media\/preview\?revision=1$/,
  );
  const still = page.getByRole("img", { name: "Sticker 🙂" });
  await still.scrollIntoViewIfNeeded();
  await expect(still.locator("img")).toHaveAttribute(
    "src",
    /\/originals\/15\/media\/preview\?revision=1$/,
  );
  const animated = page.getByRole("img", { name: "Sticker 🎉" });
  await animated.scrollIntoViewIfNeeded();
  await expect(animated.locator(".tgs-animation svg")).toBeVisible();
  // Chromium decodes VP9's transparency, so the video sticker loops here.
  const moving = page.getByRole("img", { name: "Sticker 😺" });
  await moving.scrollIntoViewIfNeeded();
  await expect(moving.locator("video[loop]")).toHaveAttribute(
    "src",
    /\/originals\/23\/media\/preview\?revision=1$/,
  );
  await expect.poll(() => paused(page, ".sticker-box video")).toBe(false);
  // With reduced motion they hold still.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect.poll(() => paused(page, ".sticker-box video")).toBe(true);
  await expect(
    page.getByRole("link", { name: /^report\.pdf, 47 KB, download$/ }),
  ).toBeVisible();
});
