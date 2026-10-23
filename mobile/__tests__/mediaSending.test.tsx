import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react-native";
// The module doubles first, so these imports get them.
import { mockParams, mockStore, reset, serve } from "./setup";
import React from "react";
import {
  packsMatching,
  pickerItems,
  stagedStatus,
  stickerFilter,
  tileName,
  type CatalogItem,
} from "../../shared/catalog";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { ConnectionProvider } from "../src/connection";
import type { Draft, SelectedMedia } from "../src/drafts";

// ADR 46 CU5: GIF and sticker sending in the app.

const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});

function item(
  token: string,
  kind: "gif" | "sticker",
  name: string,
  extra: Partial<CatalogItem> = {},
): CatalogItem {
  return {
    token,
    revision: "r1",
    metadata: {
      kind,
      format: kind === "gif" ? "mp4" : "static",
      name,
      emoji: kind === "sticker" ? "◇" : null,
      keywords: [name.toLowerCase()],
      requires_premium: false,
      unavailable: null,
    },
    selectable: true,
    unavailable: null,
    preview: { kind: "thumbnail", format: "webp" },
    ...extra,
  } as CatalogItem;
}

const LOOP = item("t".repeat(32), "gif", "Loop MP4");
const WEB = item("w".repeat(32), "gif", "Web loop");
const MORE = item("m".repeat(32), "gif", "Second page");
const STATIC = item("s".repeat(32), "sticker", "Pattern static");
const RECENT = item("r".repeat(32), "sticker", "Recent wave");
const PREMIUM = item("p".repeat(32), "sticker", "Premium spark", {
  selectable: false,
  unavailable: "Telegram Premium is required",
});

describe("the shared rules", () => {
  test("which tiles show, how stickers narrow and what a stage says", () => {
    const library = { gifs: [LOOP], recent: [RECENT], favorites: [STATIC] };
    const state = {
      library,
      results: { items: [WEB] },
      query: "",
      collection: "recent",
      packs: { pack1: { items: [STATIC, PREMIUM] } },
    };
    expect(pickerItems("gif", state)).toEqual([LOOP]);
    expect(pickerItems("gif", { ...state, query: "orbit" })).toEqual([WEB]);
    expect(pickerItems("sticker", state)).toEqual([RECENT]);
    expect(
      pickerItems("sticker", { ...state, collection: "favorites" }),
    ).toEqual([STATIC]);
    expect(pickerItems("sticker", { ...state, collection: "pack1" })).toEqual([
      STATIC,
      PREMIUM,
    ]);
    expect(stickerFilter([STATIC, PREMIUM], "spark")).toEqual([PREMIUM]);
    expect(stickerFilter([STATIC], "pattern")).toEqual([STATIC]);
    expect(
      packsMatching(
        [{ id: "1", title: "Pattern pack", short_name: "authored" }],
        "zzz",
      ),
    ).toEqual([]);
    expect(tileName(LOOP, 0)).toBe("Select GIF Loop MP4");
    expect(
      stagedStatus({ kind: "gif", status: "preparing", retryable: false }),
    ).toBe("Preparing GIF…");
    expect(
      stagedStatus({
        kind: "sticker",
        status: "failed",
        error: "Too large",
        retryable: true,
      }),
    ).toBe("Too large");
    expect(
      stagedStatus({ kind: "gif", status: "ready", retryable: false }),
    ).toBeNull();
  });
});

describe("GIFs and stickers in the composer", () => {
  let draft: Draft;
  let calls: ReturnType<typeof serve>;
  let sent: unknown[];

  function staged(
    source: CatalogItem,
    status: string,
    extra: Partial<SelectedMedia> = {},
  ): SelectedMedia {
    return {
      id: "media1",
      kind: source.metadata.kind,
      format: source.metadata.format,
      status,
      mime: null,
      size: null,
      width: null,
      height: null,
      duration: null,
      sticker: null,
      emoji: source.metadata.emoji ?? null,
      requires_premium: false,
      error: null,
      retryable: false,
      renderer_supported: true,
      features: [],
      content_url: null,
      animation_url: null,
      ...extra,
    } as SelectedMedia;
  }
  const next = (change: Partial<Draft>) => {
    draft = { ...draft, ...change, revision: draft.revision + 1 };
    return { status: 200, body: draft };
  };
  const posts = (suffix: string) =>
    calls
      .filter((call) => call.method === "POST" && call.url.endsWith(suffix))
      .map((call) => JSON.parse(call.body!));

  beforeEach(() => {
    reset();
    sent = [];
    mockStore.set("enloq.credential", SAVED);
    mockParams.account = "1";
    mockParams.peer = "user:171";
    draft = {
      revision: 0,
      text: "",
      reply_to: null,
      attachments: [],
      state: "idle",
      attempt: null,
      undo: null,
      selected_media: null,
    } as unknown as Draft;
    calls = serve((url, init) => {
      const body = init.body ? JSON.parse(init.body) : {};
      if (url.endsWith("/api/accounts"))
        return {
          status: 200,
          body: {
            mode: "personal",
            timezone: "UTC",
            budget: [],
            login_available: true,
            accounts: [],
          },
        };
      if (url.endsWith("/preferences/reading-view"))
        return { status: 200, body: { reading_view: "ai" } };
      if (url.endsWith("/api/preferences"))
        return { status: 200, body: { tone: "neutral", revision: 1 } };
      if (url.endsWith("/chats/user%3A171"))
        return {
          status: 200,
          body: {
            timezone: "UTC",
            chat: {
              account_id: 1,
              peer: "user:171",
              title: "GIF and sticker fixture",
              photo_id: null,
              read_outbox: 0,
            },
            messages: [],
            blocks: [],
            unread_sources: [],
            draft,
            operations: [],
            reply_holds: {},
          },
        };
      if (url.endsWith("/catalog") || url.endsWith("/catalog/refresh"))
        return {
          status: 200,
          body: {
            revision: "c1",
            updated_at: 1,
            stale: false,
            premium: false,
            gifs: [LOOP],
            recent: [RECENT],
            favorites: [STATIC],
            packs: [
              {
                id: "pack1",
                revision: "p1",
                title: "Pattern pack",
                short_name: "authored_pattern",
                count: 2,
                cached: true,
                stale: false,
              },
            ],
            truncated: false,
            error: null,
          },
        };
      if (url.includes("/catalog/packs/pack1"))
        return {
          status: 200,
          body: {
            pack: {
              id: "pack1",
              revision: "p1",
              title: "Pattern pack",
              short_name: "authored_pattern",
              count: 2,
              cached: true,
              stale: false,
            },
            items: [STATIC, PREMIUM],
            updated_at: 1,
            truncated: false,
            error: null,
          },
        };
      if (url.endsWith("/catalog/search/open"))
        return { status: 200, body: { epoch: "e1" } };
      if (url.endsWith("/catalog/search/close"))
        return { status: 200, body: null };
      if (url.endsWith("/catalog/search"))
        return {
          status: 200,
          body: {
            epoch: "e1",
            request: body.request,
            items: body.offset ? [MORE] : [WEB],
            next_offset: body.offset ? null : "page2",
            truncated: false,
          },
        };
      if (url.includes("/assets?"))
        return {
          status: 200,
          body: { state: "missing", kind: "thumbnail", variant: "thumbnail" },
        };
      if (url.endsWith("/assets/prepare"))
        return {
          status: 200,
          body: { state: "failed", kind: "thumbnail", variant: "thumbnail" },
        };
      if (url.endsWith("/draft"))
        return next({ text: body.text, reply_to: body.reply_to });
      if (url.endsWith("/selected-media")) {
        const source = [LOOP, WEB, MORE, STATIC, RECENT].find(
          (value) => value.token === body.token,
        )!;
        return next({ selected_media: staged(source, "preparing") });
      }
      if (url.endsWith("/selected-media/discard"))
        return next({ selected_media: null });
      if (url.endsWith("/send")) {
        sent.push(draft);
        const operation = {
          id: "op1",
          state: "queued",
          text: draft.selected_media?.kind === "sticker" ? "" : draft.text,
          images: [],
          media: draft.selected_media,
          reply_to: null,
          created: Date.now() / 1000,
          native_ids: [],
          submitted: true,
          retryable: false,
          cancellable: false,
          checkable: false,
        };
        const keep = draft.selected_media?.kind === "sticker";
        next({ text: keep ? draft.text : "", selected_media: null });
        return { status: 200, body: { operation, draft } };
      }
      if (url.endsWith("/notifications/attention"))
        return { status: 200, body: { events: [], sound: false } };
      if (url.includes("/ready-sources"))
        return { status: 200, body: { content: [], next_cursor: null } };
      throw new Error(`unexpected ${url}`);
    });
  });

  const mount = () =>
    render(
      <ConnectionProvider>
        <ConversationRoute />
      </ConnectionProvider>,
    );

  async function openPicker(kind: "GIFs" | "Stickers") {
    await fireEvent.press(await screen.findByLabelText("Attach"));
    await fireEvent.press(screen.getByLabelText(kind));
  }

  test("a saved GIF stages, prepares and sends with the text as its caption", async () => {
    await mount();
    await openPicker("GIFs");
    await fireEvent.press(
      await screen.findByLabelText(
        "Select GIF Loop MP4",
        {},
        { timeout: 3000 },
      ),
    );
    await waitFor(() => expect(posts("/selected-media")).toHaveLength(1));
    expect(posts("/selected-media")[0]).toEqual({
      revision: 0,
      token: LOOP.token,
      catalog_revision: "r1",
    });
    expect(await screen.findByText("Preparing GIF…")).toBeTruthy();
    expect(
      screen.getByLabelText("Send").props.accessibilityState.disabled,
    ).toBe(true);
    // The service finishes preparing it.
    draft = { ...draft, selected_media: staged(LOOP, "ready") };
    await fireEvent.changeText(
      screen.getByLabelText("Message"),
      "A looping caption",
    );
    await waitFor(() =>
      expect(
        screen.getByLabelText("Send").props.accessibilityState.disabled,
      ).toBe(false),
    );
    await fireEvent.press(screen.getByLabelText("Send"));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      text: "A looping caption",
      selected_media: { kind: "gif", status: "ready" },
    });
  });

  test("a search runs after a pause and More adds the next page", async () => {
    await mount();
    await openPicker("GIFs");
    await fireEvent.changeText(
      await screen.findByLabelText("Search GIFs", {}, { timeout: 3000 }),
      "orbit",
    );
    expect(await screen.findByLabelText("Select GIF Web loop")).toBeTruthy();
    expect(posts("/catalog/search")[0]).toMatchObject({
      query: "orbit",
      epoch: "e1",
      offset: "",
    });
    await fireEvent.press(screen.getByText("More GIFs"));
    expect(await screen.findByLabelText("Select GIF Second page")).toBeTruthy();
    expect(screen.getByLabelText("Select GIF Web loop")).toBeTruthy();
  });

  test("stickers narrow locally, premium ones say why, and a sticker hides the text", async () => {
    await mount();
    await fireEvent.changeText(
      await screen.findByLabelText("Message"),
      "Kept for later",
    );
    await openPicker("Stickers");
    expect(
      await screen.findByLabelText(
        "Select sticker Recent wave",
        {},
        { timeout: 3000 },
      ),
    ).toBeTruthy();
    await fireEvent.press(screen.getByText("Pattern pack"));
    expect(
      await screen.findByLabelText("Select sticker Pattern static"),
    ).toBeTruthy();
    expect(screen.getByText("Telegram Premium is required")).toBeTruthy();
    expect(
      screen.getByLabelText("Select sticker Premium spark").props
        .accessibilityState.disabled,
    ).toBe(true);
    await fireEvent.changeText(screen.getByLabelText("Filter stickers"), "zz");
    expect(screen.getByText("No stickers match.")).toBeTruthy();
    await fireEvent.changeText(screen.getByLabelText("Filter stickers"), "");
    await fireEvent.press(
      screen.getByLabelText("Select sticker Pattern static"),
    );
    expect(await screen.findByText("Draft kept")).toBeTruthy();
    expect(screen.getByText("Preparing sticker…")).toBeTruthy();
    // The text field steps aside; Rewrite waits.
    expect(screen.queryByLabelText("Message")).toBeNull();
    expect(
      screen.getByLabelText("Rewrite").props.accessibilityState.disabled,
    ).toBe(true);
    await fireEvent.press(screen.getByLabelText("Remove the sticker"));
    await waitFor(() =>
      expect(posts("/selected-media/discard")).toEqual([
        { revision: 2, id: "media1" },
      ]),
    );
    expect(await screen.findByLabelText("Message")).toBeTruthy();
  });
});
