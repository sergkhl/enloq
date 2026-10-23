// The Media Carousel and Albums (ADR 51 Q10-Q12): pure rules both clients
// draw. The carousel holds every photo and ordinary video the open chat has
// loaded, in date order; GIFs, stickers and Video Circles play inline. An
// album is Telegram's group of photos and videos sent together, drawn as
// one grid.

export type GalleryMessage = {
  message_id: number;
  date: number;
  media?: string | null;
  deleted?: boolean;
  service?: boolean | null;
  outgoing?: boolean;
  author_peer?: string | null;
  // Telegram's `grouped_id`, which the service serves as `album`.
  album?: string | null;
};

export function inGallery(message: GalleryMessage) {
  return (
    !message.deleted &&
    !message.service &&
    (message.media === "image" || message.media === "video")
  );
}

// The carousel's items: each loaded photo and video once, oldest first.
export function galleryItems<T extends GalleryMessage>(messages: T[]): T[] {
  const seen = new Set<number>();
  return messages
    .filter((message) => {
      if (!inGallery(message) || seen.has(message.message_id)) return false;
      seen.add(message.message_id);
      return true;
    })
    .sort((a, b) => a.date - b.date || a.message_id - b.message_id);
}

// Consecutive messages of one album, from one sender, as one row; everything
// else on its own. Input and output are oldest first.
export function albumRuns<T extends GalleryMessage>(messages: T[]): T[][] {
  const rows: T[][] = [];
  for (const message of messages) {
    const last = rows.at(-1);
    const previous = last?.at(-1);
    if (
      last &&
      previous &&
      message.album &&
      previous.album === message.album &&
      !message.deleted &&
      !previous.deleted &&
      inGallery(message) &&
      inGallery(previous) &&
      !!previous.outgoing === !!message.outgoing &&
      (previous.author_peer ?? null) === (message.author_peer ?? null)
    )
      last.push(message);
    else rows.push([message]);
  }
  return rows;
}

// The grid's rows for `count` items, as Telegram lays out up to ten: one or
// two to a row for few, then rows of two and three.
export function albumLayout(count: number): number[] {
  if (count <= 0) return [];
  if (count <= 2) return [count];
  if (count === 3) return [1, 2];
  if (count === 4) return [2, 2];
  const rows: number[] = [];
  let left = count;
  while (left > 0) {
    const take = left === 4 ? 2 : Math.min(3, left);
    rows.push(take);
    left -= take;
  }
  return rows;
}

// One item's place in the carousel, wrapping nothing at either end.
export function step(index: number, count: number, by: number) {
  return Math.max(0, Math.min(count - 1, index + by));
}
