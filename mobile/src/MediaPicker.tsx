import Ionicons from "@expo/vector-icons/Ionicons";
import { Image } from "expo-image";
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  SEARCH_PAUSE,
  packsMatching,
  pickerItems,
  stickerFilter,
  tileName,
  type PickerKind,
} from "../../shared/catalog";
import {
  catalogApi,
  catalogImage,
  type CatalogAsset,
  type CatalogItem,
  type CatalogPack,
  type CatalogPackView,
  type CatalogView,
  type SearchScope,
  type SearchView,
} from "./catalog";
import { useConnection } from "./connection";
import { usePalette, type Palette } from "./theme";

export type { PickerKind };

// A tile reads its still once the list draws it (ADR 46 CU5: tiles are
// still images).
function Tile({
  account,
  peer,
  item,
  index,
  sticker,
  disabled,
  palette,
  onSelect,
}: {
  account: number;
  peer: string;
  item: CatalogItem;
  index: number;
  sticker: boolean;
  disabled: boolean;
  palette: Palette;
  onSelect: () => void;
}) {
  const { api, connection } = useConnection();
  const [still, setStill] = useState<CatalogAsset | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (item.preview.kind !== "thumbnail") return;
    const controller = new AbortController();
    void catalogApi(api, account, peer)
      .still(item, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setStill(value);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [api, account, peer, item.token, item.revision]);
  const off = disabled || !item.selectable;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={tileName(item, index)}
      accessibilityState={{ disabled: off }}
      disabled={off}
      onPress={onSelect}
      style={[
        styles.tile,
        { backgroundColor: sticker ? "transparent" : palette.panel },
        off ? styles.off : null,
      ]}
    >
      {still?.state === "ready" && connection.status === "paired" ? (
        <Image
          source={catalogImage(
            connection.credential,
            account,
            peer,
            item,
            "thumbnail",
          )}
          contentFit={sticker ? "contain" : "cover"}
          style={StyleSheet.absoluteFill}
        />
      ) : (
        <Text style={[styles.neutral, { color: palette.muted }]}>
          {item.metadata.emoji || (sticker ? "◇" : "GIF")}
        </Text>
      )}
      {item.unavailable || failed ? (
        <Text
          numberOfLines={2}
          style={[
            styles.tileNote,
            { color: palette.text, backgroundColor: palette.background },
          ]}
        >
          {item.unavailable || "Preview unavailable"}
        </Text>
      ) : null}
    </Pressable>
  );
}

// GIFs and Stickers in a tall sheet (ADR 32, ADR 46 CU5): saved GIFs and a
// search through the configured bot after a pause, with More; recent,
// favorite and installed stickers with a local filter. It stays mounted
// while closed, so its search scope belongs to the open chat.
export function MediaPicker({
  account,
  peer,
  open,
  disabled,
  onTab,
  onClose,
  onSelect,
}: {
  account: number;
  peer: string;
  open: PickerKind | null;
  disabled: boolean;
  onTab: (kind: PickerKind) => void;
  onClose: () => void;
  onSelect: (item: CatalogItem, current: () => boolean) => void;
}) {
  const { api } = useConnection();
  const palette = usePalette();
  const catalog = useMemo(
    () => catalogApi(api, account, peer),
    [api, account, peer],
  );
  const life = useRef(0);
  const openNow = useRef(open);
  openNow.current = open;
  const scope = useRef<SearchScope | null>(null);
  const opening = useRef<Promise<SearchScope> | null>(null);
  const controllers = useRef(new Set<AbortController>());
  const sequence = useRef(0);
  const queryVersion = useRef(0);
  const queryNow = useRef("");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
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
  // Leaving the chat ends the search scope, never carrying the query along.
  function retire() {
    life.current++;
    queryVersion.current++;
    clearTimeout(timer.current);
    for (const request of controllers.current) request.abort();
    controllers.current.clear();
    searchController.current = null;
    const previous = scope.current;
    scope.current = null;
    opening.current = null;
    queryNow.current = "";
    sequence.current = 0;
    if (previous) void catalog.closeSearch(previous).catch(() => undefined);
  }
  useEffect(() => retire, [account, peer]);

  async function ensureScope(): Promise<SearchScope> {
    if (scope.current) return scope.current;
    if (opening.current) return opening.current;
    const generation = life.current;
    const request = controller();
    const promise = catalog
      .openSearch(request.signal)
      .then((value) => {
        if (request.signal.aborted || generation !== life.current) {
          void catalog.closeSearch(value).catch(() => undefined);
          throw new Error("Picker closed");
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
    const cached = await catalog.cached(request.signal);
    if (request.signal.aborted || generation !== life.current) return;
    setLibrary(cached);
    if (!openNow.current) return;
    const fresh = await catalog.refresh(cached.revision, request.signal);
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
    if (!isOpen) return;
    const request = startLibrary();
    return () => request.abort();
  }, [isOpen, catalog]);

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
      const value = await catalog.search(
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
  // Retry opens a new scope and reloads what the old one held.
  function retry() {
    const previous = scope.current;
    life.current++;
    clearTimeout(timer.current);
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
    if (previous) void catalog.closeSearch(previous).catch(() => undefined);
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
    clearTimeout(timer.current);
    searchController.current?.abort();
    if (!text.trim()) {
      setSearching(false);
      return;
    }
    timer.current = setTimeout(() => {
      if (openNow.current) void runSearch(text, version);
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
      const cached = await catalog.pack(pack, request.signal);
      if (request.signal.aborted || generation !== life.current) return;
      setPacks((values) => ({ ...values, [pack.id]: cached }));
      if (!openNow.current || collectionNow.current !== pack.id) return;
      const fresh = await catalog.refreshPack(pack, request.signal);
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
      generation === life.current &&
      epoch === scope.current?.epoch &&
      version === queryVersion.current &&
      shown.current.some(
        (value) =>
          value.token === item.token && value.revision === item.revision,
      );
    onSelect(item, current);
  }
  const sticker = open === "sticker";
  const pack = packs[collection]?.pack;
  const tiles = sticker ? stickerFilter(items, filter, pack) : items;
  const note =
    error ||
    library?.error ||
    packs[collection]?.error ||
    (sticker ? "" : searchError);
  const busy = loading || loadingPack || searching;
  const field = [
    styles.field,
    {
      borderColor: palette.line,
      backgroundColor: palette.panel,
      color: palette.text,
    },
  ];
  const chips = [
    { id: "recent", title: "Recent", pack: null as CatalogPack | null },
    { id: "favorites", title: "Favorites", pack: null },
    ...packsMatching(library?.packs ?? [], filter).map((value) => ({
      id: value.id,
      title: value.title,
      pack: value,
    })),
  ];
  return (
    <Modal
      visible={isOpen}
      transparent
      animationType="slide"
      onRequestClose={onClose}
    >
      <View style={styles.backdrop}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close"
          style={StyleSheet.absoluteFill}
          onPress={onClose}
        />
        <SafeAreaView
          edges={["bottom"]}
          accessibilityLabel="GIFs and stickers"
          style={[styles.sheet, { backgroundColor: palette.background }]}
        >
          <View style={styles.head}>
            <View
              accessibilityRole="tablist"
              style={[styles.tabs, { borderColor: palette.line }]}
            >
              {(["gif", "sticker"] as const).map((kind) => {
                const chosen = open === kind;
                return (
                  <Pressable
                    key={kind}
                    accessibilityRole="tab"
                    accessibilityState={{ selected: chosen }}
                    onPress={() => onTab(kind)}
                    style={[
                      styles.tab,
                      chosen ? { backgroundColor: palette.accent } : null,
                    ]}
                  >
                    <Text
                      style={[
                        styles.tabLabel,
                        { color: chosen ? palette.onAccent : palette.text },
                      ]}
                    >
                      {kind === "gif" ? "GIFs" : "Stickers"}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close GIFs and stickers"
              hitSlop={8}
              onPress={onClose}
            >
              <Ionicons name="close" size={26} color={palette.accent} />
            </Pressable>
          </View>
          {sticker ? (
            <>
              <TextInput
                testID="sticker-filter"
                accessibilityLabel="Filter stickers"
                placeholder="Filter stickers"
                placeholderTextColor={palette.muted}
                value={filter}
                onChangeText={setFilter}
                style={field}
              />
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.chips}
                style={styles.chipRow}
              >
                {chips.map((chip) => {
                  const chosen = collection === chip.id;
                  return (
                    <Pressable
                      key={chip.id}
                      accessibilityRole="button"
                      accessibilityState={{ selected: chosen }}
                      onPress={() =>
                        chip.pack
                          ? void loadPack(chip.pack)
                          : setCollection(chip.id)
                      }
                      style={[
                        styles.chip,
                        {
                          borderColor: chosen ? palette.accent : palette.line,
                          backgroundColor: chosen
                            ? palette.accent
                            : "transparent",
                        },
                      ]}
                    >
                      <Text
                        style={{
                          color: chosen ? palette.onAccent : palette.text,
                        }}
                      >
                        {chip.title}
                      </Text>
                    </Pressable>
                  );
                })}
              </ScrollView>
            </>
          ) : (
            <TextInput
              testID="gif-search"
              accessibilityLabel="Search GIFs"
              placeholder="Search GIFs"
              placeholderTextColor={palette.muted}
              value={query}
              onChangeText={changeQuery}
              style={field}
            />
          )}
          {busy ? (
            <View style={styles.note}>
              <ActivityIndicator color={palette.accent} />
              <Text style={{ color: palette.muted }}>
                {searching ? "Searching…" : "Loading…"}
              </Text>
            </View>
          ) : null}
          {note ? (
            <View style={styles.note}>
              <Text
                accessibilityRole="alert"
                style={[styles.fill, { color: palette.error }]}
              >
                {note}
              </Text>
              <Pressable accessibilityRole="button" onPress={retry}>
                <Text style={[styles.action, { color: palette.accent }]}>
                  Retry
                </Text>
              </Pressable>
            </View>
          ) : null}
          <FlatList
            key={open ?? "closed"}
            data={tiles}
            numColumns={sticker ? 4 : 3}
            keyExtractor={(item) => `${item.token}/${item.revision}`}
            columnWrapperStyle={styles.row}
            contentContainerStyle={styles.grid}
            keyboardShouldPersistTaps="handled"
            renderItem={({ item, index }) => (
              <View style={styles.cell}>
                <Tile
                  account={account}
                  peer={peer}
                  item={item}
                  index={index}
                  sticker={sticker}
                  disabled={disabled}
                  palette={palette}
                  onSelect={() => choose(item)}
                />
              </View>
            )}
            ListEmptyComponent={
              busy ? null : (
                <Text style={[styles.empty, { color: palette.muted }]}>
                  {sticker
                    ? filter.trim()
                      ? "No stickers match."
                      : "No stickers here."
                    : query.trim()
                      ? "No GIFs found."
                      : "No saved GIFs."}
                </Text>
              )
            }
            ListFooterComponent={
              <View style={styles.footer}>
                {!sticker && query.trim() && results?.next_offset ? (
                  <Pressable
                    accessibilityRole="button"
                    disabled={searching}
                    onPress={() =>
                      void runSearch(
                        queryNow.current,
                        queryVersion.current,
                        results.next_offset || "",
                      )
                    }
                    style={[styles.more, { borderColor: palette.line }]}
                  >
                    <Text style={[styles.action, { color: palette.accent }]}>
                      More GIFs
                    </Text>
                  </Pressable>
                ) : null}
                {library?.truncated ||
                results?.truncated ||
                packs[collection]?.truncated ? (
                  <Text style={{ color: palette.muted }}>
                    More are available in Telegram.
                  </Text>
                ) : null}
              </View>
            }
          />
        </SafeAreaView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  sheet: {
    height: "78%",
    gap: 10,
    paddingHorizontal: 14,
    paddingTop: 12,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
  },
  head: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  tabs: {
    flexDirection: "row",
    borderWidth: 1,
    borderRadius: 8,
    overflow: "hidden",
  },
  tab: { paddingHorizontal: 16, paddingVertical: 7 },
  tabLabel: { fontSize: 15, fontWeight: "600" },
  field: {
    minHeight: 42,
    paddingHorizontal: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    fontSize: 16,
  },
  chipRow: { flexGrow: 0 },
  chips: { gap: 6 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderWidth: 1,
    borderRadius: 16,
  },
  note: { flexDirection: "row", alignItems: "center", gap: 8 },
  fill: { flex: 1 },
  action: { fontSize: 15, fontWeight: "600" },
  grid: { gap: 6, paddingBottom: 12 },
  row: { gap: 6 },
  cell: { flex: 1 },
  tile: {
    aspectRatio: 1,
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
    borderRadius: 10,
  },
  off: { opacity: 0.5 },
  neutral: { fontSize: 20, fontWeight: "600" },
  tileNote: {
    position: "absolute",
    left: 4,
    right: 4,
    bottom: 4,
    paddingHorizontal: 4,
    borderRadius: 6,
    fontSize: 11,
  },
  empty: { paddingVertical: 16, textAlign: "center" },
  footer: { alignItems: "center", gap: 8, paddingTop: 6 },
  more: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderWidth: 1,
    borderRadius: 12,
  },
});
