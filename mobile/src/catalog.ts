import type { Schemas } from "./api";
import type { Credential } from "./credential";
import type { Draft } from "./drafts";

// The account's GIF and sticker catalog for one chat (ADR 32, ADR 46 CU5),
// as the web's `catalog-api.ts` reads it.
export type CatalogItem = Schemas["CatalogItem"];
export type CatalogView = Schemas["CatalogView"];
export type CatalogPack = Schemas["CatalogPack"];
export type CatalogPackView = Schemas["CatalogPackView"];
export type CatalogAsset = Schemas["CatalogAssetView"];
export type SearchScope = Schemas["GifSearchScope"];
export type SearchView = Schemas["GifSearchView"];

type Api = <T>(
  path: string,
  options?: { body?: unknown; signal?: AbortSignal },
) => Promise<T>;

export function catalogApi(api: Api, account: number, peer: string) {
  const base = `accounts/${account}/chats/${encodeURIComponent(peer)}`;
  return {
    cached: (signal?: AbortSignal) =>
      api<CatalogView>(`${base}/catalog`, { signal }),
    refresh: (revision: string, signal?: AbortSignal) =>
      api<CatalogView>(`${base}/catalog/refresh`, {
        body: { revision },
        signal,
      }),
    pack: (pack: CatalogPack, signal?: AbortSignal) =>
      api<CatalogPackView>(
        `${base}/catalog/packs/${pack.id}?revision=${pack.revision}`,
        { signal },
      ),
    refreshPack: (pack: CatalogPack, signal?: AbortSignal) =>
      api<CatalogPackView>(`${base}/catalog/packs/${pack.id}/refresh`, {
        body: { revision: pack.revision },
        signal,
      }),
    openSearch: (signal?: AbortSignal) =>
      api<SearchScope>(`${base}/catalog/search/open`, { body: {}, signal }),
    // The scope ends without the owner's query.
    closeSearch: (scope: SearchScope) =>
      api<null>(`${base}/catalog/search/close`, { body: scope }),
    search: (body: Schemas["GifSearch"], signal?: AbortSignal) =>
      api<SearchView>(`${base}/catalog/search`, { body, signal }),
    select: (body: Schemas["SelectMedia"]) =>
      api<Draft>(`${base}/selected-media`, { body }),
    act: (kind: "retry" | "discard", body: Schemas["SelectedMediaAction"]) =>
      api<Draft>(`${base}/selected-media/${kind}`, { body }),
    // A tile's still: read, or prepared when the service has none yet.
    async still(item: CatalogItem, signal: AbortSignal) {
      const suffix = `${base}/catalog/items/${item.token}/assets`;
      const current = await api<CatalogAsset>(
        `${suffix}?revision=${item.revision}&variant=thumbnail`,
        { signal },
      );
      if (current.state !== "missing") return current;
      return api<CatalogAsset>(`${suffix}/prepare`, {
        body: { revision: item.revision, variant: "thumbnail" },
        signal,
      });
    },
  };
}

// An image source for a catalog asset, with this phone's credential.
export function catalogImage(
  credential: Credential,
  account: number,
  peer: string,
  item: CatalogItem,
  variant: string,
) {
  return {
    uri: `${credential.origin}/api/accounts/${account}/chats/${encodeURIComponent(peer)}/catalog/items/${item.token}/assets/file?revision=${item.revision}&variant=${variant}`,
    headers: { Authorization: `Bearer ${credential.token}` },
  };
}

// The service's own media paths start at /api/; the phone reads them from
// its server with its credential.
export function servicePath(path: string) {
  return path.replace(/^\/api\//, "");
}

export function serviceImage(credential: Credential, path: string) {
  return {
    uri: `${credential.origin}${path}`,
    headers: { Authorization: `Bearer ${credential.token}` },
  };
}
