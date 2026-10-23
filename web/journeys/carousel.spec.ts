import { expect, test, type Locator } from "@playwright/test";
import { chooseView, ORIGIN, trust } from "./fixture";

// ADR 51 Q10-Q12: a Telegram album draws as one grid, and a photo opens the
// chat's Media Carousel at itself, which moves by its arrows, the keyboard
// and a swipe, zooms on a double tap and closes.
test.beforeEach(async ({ page }) => {
  await trust(page);
  const seeded = await page.request.post("/fixture/album", {
    data: {},
    headers: { Origin: ORIGIN },
  });
  expect(seeded.status()).toBe(200);
  await page.reload();
});

// A finger on the carousel, as the browser reports it.
async function touch(stage: Locator, path: [number, number][]) {
  const box = (await stage.boundingBox())!;
  const at = ([x, y]: [number, number]) => ({
    pointerType: "touch",
    pointerId: 9,
    isPrimary: true,
    bubbles: true,
    clientX: box.x + box.width / 2 + x,
    clientY: box.y + box.height / 2 + y,
  });
  await stage.dispatchEvent("pointerdown", at(path[0]!));
  for (const point of path.slice(1))
    await stage.dispatchEvent("pointermove", at(point));
  await stage.dispatchEvent("pointerup", at(path.at(-1)!));
}

test("an album is one grid, and its photos open the carousel", async ({
  page,
}) => {
  await page.getByRole("button", { name: /^Photo album/ }).tap();
  await chooseView(page, "originals");
  const album = page.getByLabel("Album, 3 items");
  await expect(album.getByRole("button")).toHaveCount(3);
  await expect(page.getByText("Saturday at the lake")).toHaveCount(1);
  await album.getByRole("button").nth(1).tap();
  const carousel = page.getByRole("dialog", { name: "Media" });
  await expect(carousel.getByText("2 / 4")).toBeVisible();
  await expect(carousel.getByRole("img", { name: "Photo" })).toHaveAttribute(
    "src",
    /\/originals\/3\/media\?revision=1$/,
  );
  await carousel.getByRole("button", { name: "Next" }).tap();
  await expect(carousel.getByText("3 / 4")).toBeVisible();
  const stage = carousel.locator(".carousel-stage");
  // A short drag stays; a swipe to the left moves on, and the last has no
  // Next.
  await touch(stage, [
    [0, 0],
    [-20, 0],
  ]);
  await expect(carousel.getByText("3 / 4")).toBeVisible();
  await touch(stage, [
    [0, 0],
    [-60, 0],
    [-120, 0],
  ]);
  await expect(carousel.getByText("4 / 4")).toBeVisible();
  await expect(carousel.getByRole("button", { name: "Next" })).toHaveCount(0);
  await page.keyboard.press("ArrowLeft");
  await expect(carousel.getByText("3 / 4")).toBeVisible();
  // A double tap zooms the photo in.
  const photo = carousel.getByRole("img", { name: "Photo" });
  await touch(stage, [[0, 0]]);
  await touch(stage, [[0, 0]]);
  await expect(photo).toHaveAttribute("style", /scale\(2\.5\)/);
  await carousel.getByRole("button", { name: "Close" }).tap();
  await expect(carousel).toHaveCount(0);
  // The standalone photo opens at its own place.
  await page.locator("button.bubble-photo").tap();
  await expect(carousel.getByText("4 / 4")).toBeVisible();
  // A swipe down closes it.
  await touch(stage, [
    [0, 0],
    [0, 80],
    [0, 160],
  ]);
  await expect(carousel).toHaveCount(0);
});
