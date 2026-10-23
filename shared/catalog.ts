// GIF and sticker sending (ADR 32, ADR 46 CU5): the rules both clients
// share. The service keeps the catalog, the search scope and what a draft
// has staged; a client shows them and asks.

export type PickerKind = "gif" | "sticker";

// A query is searched after the owner pauses typing.
export const SEARCH_PAUSE = 350;

export type CatalogMetadata = {
  kind: string;
  format: string;
  name?: string | null;
  emoji?: string | null;
  keywords: string[];
  requires_premium: boolean;
  unavailable?: string | null;
};

export type CatalogItem = {
  token: string;
  revision: string;
  metadata: CatalogMetadata;
  selectable: boolean;
  unavailable?: string | null;
  preview: { kind: string; format: string };
};

export type CatalogPack = { id: string; title: string; short_name: string };

type Library<T> = { gifs: T[]; recent: T[]; favorites: T[] };

// The tiles a picker shows: GIFs show the saved ones until a query, then
// the search's results; stickers show the chosen collection.
export function pickerItems<T extends CatalogItem>(
  kind: PickerKind,
  {
    library,
    results,
    query,
    collection,
    packs,
  }: {
    library: Library<T> | null;
    results: { items: T[] } | null;
    query: string;
    collection: string;
    packs: Record<string, { items: T[] }>;
  },
): T[] {
  if (kind === "gif")
    return query.trim()
      ? (results?.items ?? [])
      : (library?.gifs ?? results?.items ?? []);
  if (collection === "recent") return library?.recent ?? [];
  if (collection === "favorites") return library?.favorites ?? [];
  return packs[collection]?.items ?? [];
}

// Stickers narrow locally by name, emoji, keywords or the pack's title.
export function stickerFilter<T extends CatalogItem>(
  items: T[],
  filter: string,
  pack?: CatalogPack | null,
) {
  const text = filter.toLocaleLowerCase().trim();
  if (!text) return items;
  const packText = `${pack?.title ?? ""} ${pack?.short_name ?? ""}`;
  return items.filter((item) =>
    `${item.metadata.name ?? ""} ${item.metadata.emoji ?? ""} ${item.metadata.keywords.join(" ")} ${packText}`
      .toLocaleLowerCase()
      .includes(text),
  );
}

// Packs whose title matches the filter, for the collection chips.
export function packsMatching<P extends CatalogPack>(
  packs: P[],
  filter: string,
) {
  const text = filter.toLocaleLowerCase().trim();
  return text
    ? packs.filter((pack) =>
        `${pack.title} ${pack.short_name}`.toLocaleLowerCase().includes(text),
      )
    : packs;
}

// A tile's accessible name, such as "Select GIF Loop MP4".
export function tileName(item: CatalogItem, index: number) {
  const kind = item.metadata.kind === "gif" ? "GIF" : "sticker";
  return `Select ${kind} ${item.metadata.name || item.metadata.emoji || String(index + 1)}`;
}

export type StagedMedia = {
  kind: string;
  status: string;
  error?: string | null;
  retryable: boolean;
};

// What the staged preview says while it isn't ready.
export function stagedStatus(media: StagedMedia) {
  const name = media.kind === "sticker" ? "sticker" : "GIF";
  if (media.status === "preparing") return `Preparing ${name}…`;
  if (media.status === "failed")
    return media.error || `The ${name} is unavailable`;
  return null;
}
