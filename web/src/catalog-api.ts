import type { components } from "./api-schema";
import { chatApi, type Draft } from "./conversation-api";

export type CatalogItem = components["schemas"]["CatalogItem"];
export type CatalogView = components["schemas"]["CatalogView"];
export type CatalogPack = components["schemas"]["CatalogPack"];
export type CatalogPackView = components["schemas"]["CatalogPackView"];
export type CatalogAsset = components["schemas"]["CatalogAssetView"];
export type SearchScope = components["schemas"]["GifSearchScope"];
export type SearchView = components["schemas"]["GifSearchView"];
export type AssetVariant = CatalogAsset["variant"];

function base(account: number, peer: string) {
  return `/api/accounts/${account}/chats/${encodeURIComponent(peer)}`;
}

export const catalogApi = {
  cached(account: number, peer: string, signal?: AbortSignal) {
    return chatApi<CatalogView>(account, peer, "/catalog", undefined, signal);
  },
  refresh(
    account: number,
    peer: string,
    revision: string,
    signal?: AbortSignal,
  ) {
    return chatApi<CatalogView>(
      account,
      peer,
      "/catalog/refresh",
      { revision },
      signal,
    );
  },
  pack(account: number, peer: string, pack: CatalogPack, signal?: AbortSignal) {
    return chatApi<CatalogPackView>(
      account,
      peer,
      `/catalog/packs/${pack.id}?revision=${pack.revision}`,
      undefined,
      signal,
    );
  },
  refreshPack(
    account: number,
    peer: string,
    pack: CatalogPack,
    signal?: AbortSignal,
  ) {
    return chatApi<CatalogPackView>(
      account,
      peer,
      `/catalog/packs/${pack.id}/refresh`,
      { revision: pack.revision },
      signal,
    );
  },
  openSearch(account: number, peer: string, signal?: AbortSignal) {
    return chatApi<SearchScope>(
      account,
      peer,
      "/catalog/search/open",
      {},
      signal,
    );
  },
  async closeSearch(account: number, peer: string, scope: SearchScope) {
    // Cleanup returns 204. It cannot use the ordinary JSON-response helper and
    // must not carry the owner's query into a pagehide/keepalive request.
    const response = await fetch(
      `${base(account, peer)}/catalog/search/close`,
      {
        method: "POST",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(scope),
      },
    );
    if (!response.ok) throw new Error("Picker scope could not be closed");
  },
  search(
    account: number,
    peer: string,
    body: components["schemas"]["GifSearch"],
    signal?: AbortSignal,
  ) {
    return chatApi<SearchView>(account, peer, "/catalog/search", body, signal);
  },
  select(
    account: number,
    peer: string,
    body: components["schemas"]["SelectMedia"],
    signal?: AbortSignal,
  ) {
    return chatApi<Draft>(account, peer, "/selected-media", body, signal);
  },
  act(
    account: number,
    peer: string,
    action: "retry" | "discard",
    body: components["schemas"]["SelectedMediaAction"],
    signal?: AbortSignal,
  ) {
    return chatApi<Draft>(
      account,
      peer,
      `/selected-media/${action}`,
      body,
      signal,
    );
  },
  async asset(
    account: number,
    peer: string,
    item: CatalogItem,
    variant: AssetVariant,
    signal: AbortSignal,
  ) {
    const suffix = `/catalog/items/${item.token}/assets`;
    const current = await chatApi<CatalogAsset>(
      account,
      peer,
      `${suffix}?revision=${item.revision}&variant=${variant}`,
      undefined,
      signal,
    );
    if (current.state !== "missing") return current;
    return chatApi<CatalogAsset>(
      account,
      peer,
      `${suffix}/prepare`,
      { revision: item.revision, variant },
      signal,
    );
  },
};

export function catalogAssetSource(
  account: number,
  peer: string,
  item: CatalogItem,
  asset: CatalogAsset,
) {
  const source = `${base(account, peer)}/catalog/items/${item.token}/assets`;
  return {
    ...asset,
    content_url:
      asset.state === "ready"
        ? `${source}/file?revision=${item.revision}&variant=${asset.variant}`
        : null,
    animation_url:
      asset.state === "ready" && asset.format === "tgs"
        ? `${source}/animation?revision=${item.revision}`
        : null,
  };
}
