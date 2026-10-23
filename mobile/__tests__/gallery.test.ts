import {
  albumLayout,
  albumRuns,
  galleryItems,
  step,
  type GalleryMessage,
} from "../../shared/gallery";

// ADR 51 Q10-Q12: the carousel's items and Telegram's albums.
const item = (
  message_id: number,
  extra: Partial<GalleryMessage> = {},
): GalleryMessage => ({
  message_id,
  date: 1_000 + message_id,
  media: "image",
  ...extra,
});

test("the carousel holds each loaded photo and video once, oldest first", () => {
  const items = galleryItems([
    item(3, { media: "video" }),
    item(1),
    item(2, { media: "gif" }),
    item(4, { media: "video_note" }),
    item(5, { deleted: true }),
    item(1),
    item(6, { media: null }),
  ]);
  expect(items.map((value) => value.message_id)).toEqual([1, 3]);
});

test("an album's consecutive items from one sender form one row", () => {
  const rows = albumRuns([
    item(1, { album: "a" }),
    item(2, { album: "a", media: "video" }),
    item(3, { album: "b" }),
    item(4, { album: "b", outgoing: true }),
    item(5, { album: "c", media: null }),
    item(6, { album: "c" }),
    item(7),
  ]);
  expect(rows.map((row) => row.map((value) => value.message_id))).toEqual([
    [1, 2],
    [3],
    [4],
    [5],
    [6],
    [7],
  ]);
});

test("albums lay out as Telegram's grids, and the carousel stops at its ends", () => {
  expect([1, 2, 3, 4, 5, 6, 7, 10].map(albumLayout)).toEqual([
    [1],
    [2],
    [1, 2],
    [2, 2],
    [3, 2],
    [3, 3],
    [3, 2, 2],
    [3, 3, 2, 2],
  ]);
  expect(step(0, 3, -1)).toBe(0);
  expect(step(2, 3, 1)).toBe(2);
  expect(step(1, 3, 1)).toBe(2);
});
