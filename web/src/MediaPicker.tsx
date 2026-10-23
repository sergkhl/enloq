import React, { useEffect, useRef, useState } from "react";
import { LoaderCircle, RotateCw, X } from "lucide-react";
import {
  catalogApi,
  catalogAssetSource,
  type CatalogAsset,
  type CatalogItem,
  type CatalogPack,
  type CatalogPackView,
  type CatalogView,
  type SearchScope,
  type SearchView,
} from "./catalog-api";
import {
  SEARCH_PAUSE,
  packsMatching,
  pickerItems,
  stickerFilter,
  tileName,
  type PickerKind,
} from "../../shared/catalog";
import "./media-picker.css";

export type { PickerKind };

// A tile shows a still image once it is near the screen (ADR 46 CU5).
function Tile({
  account,
  peer,
  item,
  index,
  disabled,
  onSelect,
}: {
  account: number;
  peer: string;
  item: CatalogItem;
  index: number;
  disabled: boolean;
  onSelect: () => void;
}) {
  const root = useRef<HTMLButtonElement>(null);
  const [near, setNear] = useState(false);
  const [still, setStill] = useState<CatalogAsset | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const observer = new IntersectionObserver(([entry]) =>
      setNear(!!entry?.isIntersecting),
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!near || still || item.preview.kind !== "thumbnail") return;
    const controller = new AbortController();
    void catalogApi
      .asset(account, peer, item, "thumbnail", controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setStill(value);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [near, item.token, item.revision, still]);
  const source =
    still?.state === "ready"
      ? catalogAssetSource(account, peer, item, still).content_url
      : null;
  const name = tileName(item, index);
  return (
    <button
      ref={root}
      type="button"
      className="media-tile"
      aria-label={name}
      disabled={disabled || !item.selectable}
      onClick={onSelect}
    >
      {source ? (
        <img src={source} alt="" draggable={false} />
      ) : (
        <span className="media-tile-neutral" aria-hidden>
          {item.metadata.emoji || (item.metadata.kind === "gif" ? "GIF" : "◇")}
        </span>
      )}
      {item.unavailable ? (
        <small>{item.unavailable}</small>
      ) : failed ? (
        <small>Preview unavailable</small>
      ) : null}
    </button>
  );
}

// GIFs and Stickers in the app's design (ADR 32, ADR 46 CU5): saved GIFs and
// a search through the configured bot after a pause, with More; recent,
// favorite and installed stickers with a local filter. It stays mounted
// while closed, so its search belongs to the open chat's lifetime.
export function MediaPicker({
  account,
  peer,
  open,
  visible,
  disabled,
  onTab,
  onClose,
  onSelect,
}: {
  account: number;
  peer: string;
  open: PickerKind | null;
  visible: boolean;
  disabled: boolean;
  onTab: (kind: PickerKind) => void;
  onClose: () => void;
  onSelect: (item: CatalogItem, current: () => boolean) => Promise<void>;
}) {
  const life = useRef(0);
  const openNow = useRef(open);
  openNow.current = open;
  const visibleNow = useRef(visible);
  visibleNow.current = visible;
  const scope = useRef<SearchScope | null>(null);
  const opening = useRef<Promise<SearchScope> | null>(null);
  const controllers = useRef(new Set<AbortController>());
  const sequence = useRef(0);
  const queryVersion = useRef(0);
  const queryNow = useRef("");
  const timer = useRef<number | undefined>(undefined);
  const searchController = useRef<AbortController | null>(null);
  const [library, setLibrary] = useState<CatalogView | null>(null);
  const [packs, setPacks] = useState<Record<string, CatalogPackView>>({});
  const [collection, setCollection] = useState("recent");
  const collectionNow = useRef(collection);
  collectionNow.current = collection;
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [results, setResults] = useState<SearchView | null>(null);
  const [searching, setSearching] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingPack, setLoadingPack] = useState(false);
  const [error, setError] = useState("");
  const [searchError, setSearchError] = useState("");
  const isOpen = open !== null;
  const items = open
    ? pickerItems(open, { library, results, query, collection, packs })
    : [];
  const shown = useRef(items);
  shown.current = items;

  function controller() {
    const value = new AbortController();
    controllers.current.add(value);
    return value;
  }
  // Leaving the chat or the page ends the search scope, never carrying the
  // owner's query along.
  function retire() {
    life.current++;
    queryVersion.current++;
    window.clearTimeout(timer.current);
    for (const request of controllers.current) request.abort();
    controllers.current.clear();
    searchController.current = null;
    const previous = scope.current;
    scope.current = null;
    opening.current = null;
    queryNow.current = "";
    sequence.current = 0;
    setQuery("");
    setResults(null);
    setFilter("");
    setLibrary(null);
    setPacks({});
    setCollection("recent");
    setSearching(false);
    setLoading(false);
    setLoadingPack(false);
    setError("");
    setSearchError("");
    onClose();
    if (previous)
      void catalogApi.closeSearch(account, peer, previous).catch(() => {});
  }
  useEffect(() => {
    window.addEventListener("pagehide", retire);
    window.addEventListener("browser-auth-lost", retire);
    return () => {
      window.removeEventListener("pagehide", retire);
      window.removeEventListener("browser-auth-lost", retire);
      retire();
    };
  }, [account, peer]);
  useEffect(() => {
    if (!visible) retire();
  }, [visible]);

  async function ensureScope(): Promise<SearchScope> {
    if (scope.current) return scope.current;
    if (opening.current) return opening.current;
    const generation = life.current;
    const request = controller();
    const promise = catalogApi
      .openSearch(account, peer, request.signal)
      .then((value) => {
        if (
          request.signal.aborted ||
          generation !== life.current ||
          !visibleNow.current
        ) {
          void catalogApi.closeSearch(account, peer, value).catch(() => {});
          throw new DOMException("Picker closed", "AbortError");
        }
        scope.current = value;
        return value;
      })
      .finally(() => {
        controllers.current.delete(request);
        if (opening.current === promise) opening.current = null;
      });
    opening.current = promise;
    return promise;
  }
  async function loadLibrary(request: AbortController, generation: number) {
    await ensureScope();
    if (request.signal.aborted || generation !== life.current) return;
    const cached = await catalogApi.cached(account, peer, request.signal);
    if (request.signal.aborted || generation !== life.current) return;
    setLibrary(cached);
    if (!openNow.current || !visibleNow.current) return;
    const fresh = await catalogApi.refresh(
      account,
      peer,
      cached.revision,
      request.signal,
    );
    if (!request.signal.aborted && generation === life.current)
      setLibrary(fresh);
  }
  function startLibrary() {
    const generation = life.current;
    const request = controller();
    setLoading(true);
    setError("");
    void loadLibrary(request, generation)
      .catch((failure) => {
        if (!request.signal.aborted && generation === life.current)
          setError((failure as Error).message);
      })
      .finally(() => {
        controllers.current.delete(request);
        if (!request.signal.aborted && generation === life.current)
          setLoading(false);
      });
    return request;
  }
  useEffect(() => {
    if (!isOpen || !visible) return;
    const request = startLibrary();
    return () => request.abort();
  }, [isOpen, visible, account, peer]);
  useEffect(() => {
    if (!isOpen || !visible) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [isOpen, visible]);

  async function runSearch(text: string, version: number, offset = "") {
    const generation = life.current;
    searchController.current?.abort();
    const request = controller();
    searchController.current = request;
    const identity = ++sequence.current;
    setSearching(true);
    setSearchError("");
    try {
      const epoch = await ensureScope();
      if (
        request.signal.aborted ||
        generation !== life.current ||
        version !== queryVersion.current
      )
        return;
      const value = await catalogApi.search(
        account,
        peer,
        { query: text, epoch: epoch.epoch, request: identity, offset },
        request.signal,
      );
      if (
        request.signal.aborted ||
        generation !== life.current ||
        version !== queryVersion.current ||
        value.epoch !== epoch.epoch ||
        value.request !== identity
      )
        return;
      setResults((previous) =>
        offset && previous?.epoch === value.epoch
          ? { ...value, items: [...previous.items, ...value.items] }
          : value,
      );
    } catch (failure) {
      if (
        !request.signal.aborted &&
        generation === life.current &&
        version === queryVersion.current
      )
        setSearchError((failure as Error).message);
    } finally {
      controllers.current.delete(request);
      if (searchController.current === request) {
        searchController.current = null;
        setSearching(false);
      }
    }
  }
  // An explicit retry opens a new scope and reloads what the old one held.
  function retry() {
    const previous = scope.current;
    life.current++;
    window.clearTimeout(timer.current);
    for (const request of controllers.current) request.abort();
    controllers.current.clear();
    searchController.current = null;
    scope.current = null;
    opening.current = null;
    sequence.current = 0;
    setResults(null);
    setLibrary(null);
    setPacks({});
    setCollection("recent");
    if (previous)
      void catalogApi.closeSearch(account, peer, previous).catch(() => {});
    startLibrary();
    if (queryNow.current.trim())
      void runSearch(queryNow.current, queryVersion.current);
  }
  function changeQuery(text: string) {
    queryNow.current = text;
    setQuery(text);
    setResults(null);
    setSearchError("");
    const version = ++queryVersion.current;
    window.clearTimeout(timer.current);
    searchController.current?.abort();
    if (!text.trim()) {
      setSearching(false);
      return;
    }
    timer.current = window.setTimeout(() => {
      if (visibleNow.current) void runSearch(text, version);
    }, SEARCH_PAUSE);
  }
  async function loadPack(pack: CatalogPack) {
    const generation = life.current;
    const request = controller();
    setCollection(pack.id);
    collectionNow.current = pack.id;
    setLoadingPack(true);
    setError("");
    try {
      await ensureScope();
      if (request.signal.aborted || generation !== life.current) return;
      const cached = await catalogApi.pack(account, peer, pack, request.signal);
      if (request.signal.aborted || generation !== life.current) return;
      setPacks((values) => ({ ...values, [pack.id]: cached }));
      if (!openNow.current || collectionNow.current !== pack.id) return;
      const fresh = await catalogApi.refreshPack(
        account,
        peer,
        pack,
        request.signal,
      );
      if (!request.signal.aborted && generation === life.current)
        setPacks((values) => ({ ...values, [pack.id]: fresh }));
    } catch (failure) {
      if (
        !request.signal.aborted &&
        generation === life.current &&
        collectionNow.current === pack.id
      )
        setError((failure as Error).message);
    } finally {
      controllers.current.delete(request);
      if (generation === life.current && collectionNow.current === pack.id)
        setLoadingPack(false);
    }
  }
  function choose(item: CatalogItem) {
    if (disabled || !item.selectable) return;
    const generation = life.current;
    const version = queryVersion.current;
    const epoch = scope.current?.epoch;
    // Still the same picker, query and tile when the choice lands.
    const current = () =>
      visibleNow.current &&
      generation === life.current &&
      epoch === scope.current?.epoch &&
      version === queryVersion.current &&
      shown.current.some(
        (value) =>
          value.token === item.token && value.revision === item.revision,
      );
    void onSelect(item, current);
  }
  if (!isOpen || !visible) return null;
  const pack = packs[collection]?.pack;
  const tiles = open === "sticker" ? stickerFilter(items, filter, pack) : items;
  const note =
    error ||
    library?.error ||
    packs[collection]?.error ||
    (open === "gif" ? searchError : "");
  const busy = loading || loadingPack || searching;
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div
        className="sheet-bottom media-picker"
        role="dialog"
        aria-label="GIFs and stickers"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="media-picker-head">
          <div className="segmented" role="tablist" aria-label="Media">
            {(["gif", "sticker"] as const).map((kind) => (
              <button
                key={kind}
                type="button"
                role="tab"
                aria-selected={open === kind}
                onClick={() => onTab(kind)}
              >
                {kind === "gif" ? "GIFs" : "Stickers"}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="Close GIFs and stickers"
            onClick={onClose}
          >
            <X size={22} aria-hidden />
          </button>
        </header>
        {open === "gif" ? (
          <input
            className="media-picker-field"
            type="search"
            aria-label="Search GIFs"
            placeholder="Search GIFs"
            value={query}
            onChange={(event) => changeQuery(event.target.value)}
          />
        ) : (
          <>
            <input
              className="media-picker-field"
              type="search"
              aria-label="Filter stickers"
              placeholder="Filter stickers"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
            <div
              className="sticker-collections"
              role="group"
              aria-label="Sticker collections"
            >
              {[
                { id: "recent", title: "Recent" },
                { id: "favorites", title: "Favorites" },
              ].map((choice) => (
                <button
                  key={choice.id}
                  type="button"
                  aria-pressed={collection === choice.id}
                  onClick={() => setCollection(choice.id)}
                >
                  {choice.title}
                </button>
              ))}
              {packsMatching(library?.packs ?? [], filter).map((value) => (
                <button
                  key={value.id}
                  type="button"
                  aria-pressed={collection === value.id}
                  onClick={() => void loadPack(value)}
                >
                  {value.title}
                </button>
              ))}
            </div>
          </>
        )}
        {busy && (
          <p className="media-picker-note" role="status">
            <LoaderCircle size={16} className="work-spinner" aria-hidden />
            {searching ? "Searching…" : "Loading…"}
          </p>
        )}
        {note && (
          <p className="media-picker-note error" role="alert">
            {note}
            <button type="button" className="status-link" onClick={retry}>
              <RotateCw size={14} aria-hidden /> Retry
            </button>
          </p>
        )}
        <div
          className={`media-grid ${open === "gif" ? "gifs" : "stickers"}`}
          role="list"
          aria-label={open === "gif" ? "GIFs" : "Stickers"}
        >
          {tiles.map((item, index) => (
            <div role="listitem" key={`${item.token}/${item.revision}`}>
              <Tile
                account={account}
                peer={peer}
                item={item}
                index={index}
                disabled={disabled}
                onSelect={() => choose(item)}
              />
            </div>
          ))}
        </div>
        {!tiles.length && !busy && (
          <p className="media-picker-note">
            {open === "gif"
              ? query.trim()
                ? "No GIFs found."
                : "No saved GIFs."
              : filter.trim()
                ? "No stickers match."
                : "No stickers here."}
          </p>
        )}
        {open === "gif" && query.trim() && results?.next_offset && (
          <button
            type="button"
            className="sheet-button media-more"
            disabled={searching}
            onClick={() =>
              void runSearch(
                queryNow.current,
                queryVersion.current,
                results.next_offset || "",
              )
            }
          >
            More GIFs
          </button>
        )}
        {(library?.truncated ||
          results?.truncated ||
          packs[collection]?.truncated) && (
          <p className="media-picker-note">More are available in Telegram.</p>
        )}
      </div>
    </div>
  );
}
