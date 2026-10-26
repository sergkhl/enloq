import { useEffect, useReducer, useRef } from "react";
import { Refused, Unreachable, type Schemas } from "./api";
import { readCached, removeCached, writeCached } from "./cache";
import { useConnection } from "./connection";
import type { CatalogItem } from "./catalog";
import type { Snapshot } from "./conversation";
import { MAX_IMAGES, pickPhotos, preparePhoto } from "./images";

export type SelectedMedia = Schemas["SelectedMediaView"];

// The service-held draft as the web types it (`conversation-api.ts`); the
// service's schema declares no model for it. Every change names the revision
// it edits, and a stale one is refused with 409.
export type Draft = {
  chat_key: string;
  revision: number;
  text: string;
  attachments: string[];
  selected_media?: SelectedMedia | null;
  reply_to: number | null;
  undo: string | null;
  state: string;
  attempt?: string | null;
  // Present while a rewrite streams: `text` is its provisional output.
  partial?: boolean;
  error?: string | null;
};

// A saved submission (ADR 26), as the web types it.
export type Operation = {
  id: string;
  state:
    | "queued"
    | "prepared"
    | "dispatching"
    | "confirmed"
    | "refused"
    | "unknown"
    | "cancelled";
  reason?: string | null;
  cause?: string | null;
  retry_after?: number | null;
  check?: string | null;
  text: string;
  images: string[];
  media?: SelectedMedia | null;
  reply_to: number | null;
  created: number;
  native_ids: number[];
  submitted: boolean;
  retryable: boolean;
  cancellable: boolean;
  checkable: boolean;
  resumable?: boolean;
};

export type ReplyHold = "pending" | "unresolved" | "deleted";

export const REPLY_HOLDS: Record<ReplyHold, string> = {
  pending: "Reply target deletion pending",
  unresolved: "Reply target deletion unconfirmed",
  deleted: "Reply target deleted",
};

export type Action =
  "generate" | "undo" | "send" | "insert" | "attach" | "media";

// What the phone keeps of an unsaved draft until the service holds it (Q20).
type Kept = { text: string; reply_to: number | null };

const message = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

// The chat's composer (ADR 13, 19, 22, 25, 26): the draft lives on the
// service, edits are saved one request at a time against the last revision
// seen, and every action first saves, then checks that nothing changed since
// the press. What is typed stays on the phone until the service accepts it.
export function useComposer(
  account: number,
  peer: string,
  snapshot: Snapshot | null,
  reload: () => Promise<void>,
  // When the read behind `snapshot` started.
  readAt = 0,
) {
  const { api, connection } = useConnection();
  const path = `accounts/${account}/chats/${encodeURIComponent(peer)}`;
  const keep = `draft:${account}/${peer}`;
  const [, render] = useReducer((count: number) => count + 1, 0);
  const s = useRef({
    server: null as Draft | null,
    text: "",
    reply: null as number | null,
    dirty: false,
    edits: 0,
    saving: null as Promise<void> | null,
    // The last save failed in a way the next server answer can resolve.
    retry: false,
    action: null as Action | null,
    note: "",
    saveNote: "",
    caret: null as number | null,
    // A send shown before the conversation lists it, and when it was saved.
    pending: null as Operation | null,
    sentAt: 0,
    // The shown set's fold before the conversation carries it, and when the
    // server saved it (ADR 56 Q12 B).
    fold: null as { attempt: string; folded: boolean } | null,
    foldedAt: 0,
    // The phone's unsaved copy: undefined until read, null when none.
    kept: undefined as Kept | null | undefined,
    restored: false,
  }).current;
  const origin =
    connection.status === "paired"
      ? new URL(connection.credential.origin).host
      : "the server";

  function failed(error: unknown) {
    return error instanceof Unreachable
      ? `${origin} can't be reached.`
      : message(error);
  }

  // The local copy is restored once, against the first draft the phone
  // shows, when it differs (the web's "Unsaved draft restored" rule).
  function restore() {
    if (s.restored || s.kept === undefined || !s.server) return;
    s.restored = true;
    const kept = s.kept;
    if (!kept) return;
    if (kept.text === s.server.text && kept.reply_to === s.server.reply_to) {
      removeCached(keep);
      return;
    }
    if (
      s.server.state === "rewriting" ||
      s.server.selected_media?.kind === "sticker"
    )
      return;
    s.text = kept.text;
    s.reply = kept.reply_to;
    s.dirty = true;
    s.edits++;
    s.note = "Unsaved draft restored on this phone.";
    void save().catch(() => undefined);
  }

  function adopt(next: Draft) {
    if (s.server && next.revision < s.server.revision) return;
    s.server = next;
    if (!s.dirty) {
      s.text = next.text;
      s.reply = next.reply_to;
    } else if (s.retry && !s.saving && next.state !== "rewriting")
      void save().catch(() => undefined);
    restore();
    render();
  }

  // One save at a time; a running save picks up later edits before it ends.
  function save(): Promise<void> {
    if (s.saving) return s.saving;
    if (!s.dirty || !s.server) return Promise.resolve();
    s.retry = false;
    const run = (async () => {
      try {
        while (s.dirty && s.server) {
          const edits = s.edits;
          const next = await api<Draft>(`${path}/draft`, {
            body: {
              revision: s.server.revision,
              text: s.text,
              reply_to: s.reply,
            },
          });
          if (!s.server || next.revision >= s.server.revision) s.server = next;
          if (s.edits === edits) {
            s.dirty = false;
            removeCached(keep);
          }
        }
        s.saveNote = "";
      } catch (error) {
        const conflict = error instanceof Refused && error.status === 409;
        s.retry = conflict || error instanceof Unreachable;
        s.saveNote =
          error instanceof Unreachable
            ? "Kept on this phone until the server answers."
            : message(error);
        // A newer draft lets the next save write these edits over it.
        if (conflict) void reload();
        throw error;
      } finally {
        s.saving = null;
        render();
      }
    })();
    s.saving = run;
    return run;
  }

  useEffect(() => {
    void readCached<Kept>(keep).then((kept) => {
      s.kept = kept;
      restore();
      render();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const draft = snapshot?.draft;
  useEffect(() => {
    if (draft) adopt(draft);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  // A read that started after the send was saved lists it, or its message
  // once Telegram confirmed it, so the phone's copy is no longer needed.
  if (s.pending && s.pending.id !== "local" && readAt >= s.sentAt)
    s.pending = null;
  // Likewise a read that started after the fold was saved carries it.
  if (s.fold && s.foldedAt && readAt >= s.foldedAt) s.fold = null;
  const suggestions = snapshot?.suggestions;
  const folded =
    s.fold && s.fold.attempt === suggestions?.attempt
      ? s.fold.folded
      : !!suggestions?.folded;

  const server = s.server;
  const sticker = server?.selected_media?.kind === "sticker";
  const locked = server?.state === "rewriting";
  const hold = s.reply
    ? (snapshot?.reply_holds?.[String(s.reply)] as ReplyHold | undefined)
    : undefined;

  function edit(change: { text?: string; reply?: number | null }) {
    if (!server || locked) return false;
    if (s.action === "send" || s.action === "attach" || s.action === "undo")
      return false;
    if (change.text !== undefined && sticker) return false;
    if (change.text !== undefined) s.text = change.text;
    if (change.reply !== undefined) s.reply = change.reply;
    s.dirty = true;
    s.edits++;
    s.note = "";
    writeCached(keep, { text: s.text, reply_to: s.reply });
    render();
    void save().catch(() => undefined);
    return true;
  }

  // Runs one deliberate action: saves first, then refuses if the draft
  // changed while saving, as the web does.
  async function act(
    action: Action,
    changed: string,
    run: () => Promise<void>,
  ) {
    if (s.action || !s.server) return;
    s.action = action;
    s.note = "";
    render();
    const before = [s.text, s.reply, s.server.attachments.join()].join("\n");
    try {
      await save();
      const after = [s.text, s.reply, s.server.attachments.join()].join("\n");
      if (before !== after) {
        s.note = changed;
        return;
      }
      await run();
    } catch (error) {
      s.note = failed(error);
    } finally {
      s.action = null;
      render();
    }
  }

  const ready = !!server && !s.action && !locked;
  const canGenerate =
    ready && !sticker && server.selected_media?.status !== "preparing";
  // A staged GIF or sticker sends once ready, a GIF with the text as its
  // caption (ADR 32, ADR 46 CU5).
  const canSend =
    ready &&
    !hold &&
    (server.selected_media
      ? server.selected_media.status === "ready"
      : !!s.text.trim() || !!server.attachments.length);
  // While a rewrite streams, Undo stops it and restores the original.
  const canUndo =
    !!server && server.undo !== null && !s.dirty && !sticker && !s.action;
  const canInsert = ready && !sticker;

  return {
    ready: !!server,
    text: s.text,
    replyTo: s.reply,
    hold: hold ? REPLY_HOLDS[hold] : null,
    draft: server,
    action: s.action,
    locked,
    // The latest outcome or refusal, the save state, else the draft's error.
    note: s.note || s.saveNote || server?.error || "",
    pending: s.pending,
    canGenerate,
    canSend,
    canUndo,
    canInsert,
    edit,
    setCaret: (caret: number | null) => {
      s.caret = caret;
    },
    // With pending reminders the service prepares their set (ADR 58 Q5).
    generate: () =>
      act(
        "generate",
        "The draft changed before generating suggestions. Review it and try again.",
        async () => {
          await api<Schemas["SuggestionsView"]>(
            `${path}/suggestions/generate`,
            { body: { revision: s.server!.revision } },
          );
          void reload();
        },
      ),
    undo: async () => {
      const shown = s.server;
      if (!shown || shown.undo === null || s.dirty || s.action) return;
      s.action = "undo";
      render();
      try {
        adopt(
          await api<Draft>(`${path}/undo`, {
            body: { revision: shown.revision, attempt: shown.attempt ?? null },
          }),
        );
      } catch (error) {
        s.note = failed(error);
      } finally {
        s.action = null;
        render();
      }
    },
    // Shown at once as a local pending send (ADR 26); nothing is queued on
    // the phone, so an unreachable server sends nothing (Q20).
    send: () => {
      if (!canSend) return Promise.resolve();
      const shown = s.server!;
      s.pending = {
        id: "local",
        state: "queued",
        text: shown.selected_media?.kind === "sticker" ? "" : s.text,
        images: [...shown.attachments],
        media: shown.selected_media ?? null,
        reply_to: s.reply,
        created: Date.now() / 1000,
        native_ids: [],
        submitted: false,
        retryable: false,
        cancellable: false,
        checkable: false,
      };
      return act(
        "send",
        "The draft changed before Send. Review it and send again.",
        async () => {
          let sent: { operation: Operation; draft: Draft };
          try {
            sent = await api<{ operation: Operation; draft: Draft }>(
              `${path}/send`,
              { body: { revision: s.server!.revision } },
            );
          } catch (error) {
            // The request may have arrived before the connection failed. A
            // second Send of the same revision returns that send, never a
            // new one, so the draft stays for it.
            if (!(error instanceof Unreachable)) throw error;
            s.note = `${origin} can't be reached. Your draft is kept; check the chat before sending again.`;
            return;
          }
          s.pending = sent.operation;
          s.sentAt = Date.now();
          adopt(sent.draft);
          removeCached(keep);
          void reload();
        },
      ).finally(() => {
        if (s.pending?.id === "local") s.pending = null;
        render();
      });
    },
    // Inserts one response option at the caret, with Undo (ADR 25).
    insert: (index: number) => {
      const attempt = snapshot?.suggestions?.attempt;
      if (!canInsert || !attempt) return Promise.resolve();
      return act(
        "insert",
        "The draft changed before inserting the response. Review it and choose again.",
        async () => {
          const result = await api<{ draft: Draft; caret: number }>(
            `${path}/suggestions/insert`,
            {
              body: {
                revision: s.server!.revision,
                attempt,
                index,
                cursor: s.caret,
              },
            },
          );
          s.caret = result.caret;
          adopt(result.draft);
        },
      );
    },
    // The shown set stays folded on every client until the next one (ADR 56
    // Q12 B). A tap shows at once; a refusal shows the server's state again.
    folded,
    fold: async (next: boolean) => {
      const attempt = suggestions?.attempt;
      if (!attempt) return;
      const mine = { attempt, folded: next };
      s.fold = mine;
      s.foldedAt = 0;
      render();
      try {
        const answer = await api<Schemas["SuggestionsView"]>(
          `${path}/suggestions/fold`,
          { body: { attempt, folded: next } },
        );
        if (s.fold !== mine) return;
        if (answer.attempt === attempt) mine.folded = answer.folded;
        s.foldedAt = Date.now();
      } catch {
        if (s.fold !== mine) return;
        s.fold = null;
        void reload();
      } finally {
        render();
      }
    },
    reply: (identity: number | null) => edit({ reply: identity }),
    // Photos from the library or the camera, each prepared within the
    // service's bounds and attached one at a time, as the web does.
    attach: async (source: "library" | "camera") => {
      const shown = s.server;
      if (!shown || s.action || locked) return;
      const room = MAX_IMAGES - shown.attachments.length;
      s.note = shown.selected_media
        ? "Remove the selected GIF or sticker before attaching images."
        : room <= 0
          ? "Use up to ten images, at most 5 MB each."
          : "";
      if (s.note) return render();
      let assets;
      try {
        assets = await pickPhotos(source, room);
      } catch (error) {
        s.note = failed(error);
        return render();
      }
      if (!assets?.length) return;
      await act(
        "attach",
        "The draft changed before the photo was attached. Review it and attach again.",
        async () => {
          for (const [index, asset] of assets.entries()) {
            const photo = await preparePhoto(asset, index);
            adopt(
              await api<Draft>(`${path}/images`, {
                body: {
                  revision: s.server!.revision,
                  name: photo.name,
                  data: photo.data,
                },
              }),
            );
          }
        },
      );
    },
    removeImage: (id: string) =>
      act("attach", "", async () => {
        adopt(
          await api<Draft>(`${path}/images/${encodeURIComponent(id)}/remove`, {
            body: { revision: s.server!.revision },
          }),
        );
      }),
    // A tile stages its GIF or sticker; the service prepares it (CU5).
    selectMedia: (item: CatalogItem) =>
      act(
        "media",
        "The draft changed before the choice. Choose again.",
        async () => {
          adopt(
            await api<Draft>(`${path}/selected-media`, {
              body: {
                revision: s.server!.revision,
                token: item.token,
                catalog_revision: item.revision,
              },
            }),
          );
        },
      ),
    retryMedia: () => {
      const media = s.server?.selected_media;
      if (!media || s.action || media.status !== "failed" || !media.retryable)
        return Promise.resolve();
      return act("media", "", async () => {
        adopt(
          await api<Draft>(`${path}/selected-media/retry`, {
            body: { revision: s.server!.revision, id: media.id },
          }),
        );
      });
    },
    discardMedia: () => {
      const media = s.server?.selected_media;
      if (!media || s.action) return Promise.resolve();
      return act("media", "", async () => {
        adopt(
          await api<Draft>(`${path}/selected-media/discard`, {
            body: { revision: s.server!.revision, id: media.id },
          }),
        );
      });
    },
    caret: () => s.caret,
  };
}

export type Composer = ReturnType<typeof useComposer>;

// The sends to list after the messages, oldest first (the web's rule): the
// service's operations without a confirmed one whose native messages are
// already loaded, plus the phone's own send until the service lists it.
export function visibleOperations(
  snapshot: Snapshot,
  local: Operation | null,
): Operation[] {
  const loaded = new Set(snapshot.messages.map((m) => m.message_id));
  const listed = (snapshot.operations ?? []).filter(
    (operation) =>
      !(
        operation.state === "confirmed" &&
        operation.native_ids.length > 0 &&
        operation.native_ids.every((id) => loaded.has(id))
      ),
  );
  if (local && !listed.some((operation) => operation.id === local.id))
    listed.push(local);
  return listed.sort((a, b) => a.created - b.created);
}
