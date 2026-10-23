import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Paperclip,
  Image as ImageIcon,
  Clapperboard,
  Sticker,
  LoaderCircle,
  Pencil,
  Send,
  ChevronDown,
  Undo2,
  Square,
  X,
  Copy,
  Check,
} from "lucide-react";
import {
  chatApi,
  type Chat,
  type Draft,
  type ReplyHold,
  type Operation,
} from "./conversation-api";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { SuggestionArea, type SuggestionsView } from "./Suggestions";
import { ReplyQuote } from "./ReplyQuote";
import { catalogApi, type CatalogItem } from "./catalog-api";
import { MediaPicker, type PickerKind } from "./MediaPicker";
import { SelectedMediaPreview } from "./SelectedMediaPreview";
import "./composer.css";

type Preferences = {
  tone: components["schemas"]["Tone"]["tone"];
  revision: number;
};
export function Composer({
  account,
  visible,
  processing,
  nativeAvailable = true,
  retained = false,
  replyHolds,
  deletedTargets,
  chat,
  incoming,
  suggestions,
  reply,
  refresh,
  onPending,
  onSend,
  onBudget,
  setNavigationGuard,
}: {
  account: number;
  visible: boolean;
  // The account's AI processing; null until it is known.
  processing: boolean | null;
  // Confirmed native group access; opt-in Off does not affect this guard.
  nativeAvailable?: boolean;
  retained?: boolean;
  replyHolds: Record<number, ReplyHold>;
  deletedTargets: number[];
  chat: Chat;
  incoming: Draft;
  suggestions: SuggestionsView;
  reply: { identity: number | null; nonce: number } | null;
  refresh: () => Promise<void>;
  // Immediate presentation of a submission, replaced by its saved operation;
  // clearing names the presentation it ends, so it never removes a newer one.
  onPending: (operation: Operation | null, clear?: string) => void;
  onSend: () => void;
  onBudget?: () => void;
  setNavigationGuard: (guard: (() => Promise<boolean>) | null) => void;
}) {
  const recoveryKey = `draft:${account}/${chat.peer}`;
  const [draft, setDraft] = useState(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(recoveryKey) || "null");
      if (incoming.selected_media?.kind === "sticker") {
        // Only a reply edit bound to this exact kept draft may be recovered.
        if (
          saved?.selection === incoming.selected_media.id &&
          saved.revision === incoming.revision &&
          saved.text === incoming.text &&
          (saved.reply_to === null || Number.isInteger(saved.reply_to))
        )
          return saved.reply_to === incoming.reply_to
            ? incoming
            : { ...incoming, reply_to: saved.reply_to };
        sessionStorage.removeItem(recoveryKey);
        return incoming;
      }
      if (saved?.selection) {
        sessionStorage.removeItem(recoveryKey);
        return incoming;
      }
      if (
        saved &&
        typeof saved.text === "string" &&
        (saved.reply_to === null || Number.isInteger(saved.reply_to))
      ) {
        if (
          saved.text === incoming.text &&
          saved.reply_to === incoming.reply_to
        )
          return incoming;
        return {
          ...incoming,
          text: saved.text,
          reply_to: saved.reply_to,
          undo: null,
          state: "idle",
        };
      }
    } catch {
      /* A damaged local recovery entry must not replace the saved draft. */
    }
    return incoming;
  });
  const current = useRef(draft),
    server = useRef(incoming),
    dirty = useRef(draft !== incoming),
    edits = useRef(0);
  const ignoredAttempt = useRef<string | null>(null);
  const action = useRef<string | null>(null);
  const composing = useRef(false);
  const queue = useRef(Promise.resolve());
  const mediaRequest = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const visibleNow = useRef(visible);
  visibleNow.current = visible;
  const [preferences, setPreferences] = useState<Preferences>({
    tone: "neutral",
    revision: 0,
  });
  const fileInput = useRef<HTMLInputElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const selectedField = useRef<HTMLDivElement>(null);
  // The owner's last caret in UTF-16 code units; null inserts at the end.
  const caret = useRef<number | null>(null);
  const [focusAt, setFocusAt] = useState<number | null>(null);
  const [focusSelection, setFocusSelection] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [inserting, setInserting] = useState(false);
  const [working, setWorking] = useState(false),
    [sending, setSending] = useState(false),
    [error, setError] = useState("");
  // The tone sheet, as in the app (ADR 44 MW3).
  const [choosingTone, setChoosingTone] = useState(false);
  const [mediaBusy, setMediaBusy] = useState(false);
  // The paperclip's sheet and the GIF and sticker picker (ADR 46 CU5).
  const [attachSheet, setAttachSheet] = useState(false);
  const [picker, setPicker] = useState<PickerKind | null>(null);
  useEffect(() => {
    if (!attachSheet) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAttachSheet(false);
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [attachSheet]);
  const [undoing, setUndoing] = useState(false);
  const replyStatus = draft.reply_to
    ? replyHolds[draft.reply_to] ||
      (deletedTargets.includes(draft.reply_to) ? "deleted" : null)
    : null;
  const replyStatusText =
    replyStatus === "deleted"
      ? "Reply target deleted"
      : replyStatus === "pending"
        ? "Reply target deletion pending"
        : replyStatus === "unresolved"
          ? "Reply target deletion unconfirmed"
          : null;
  const streaming = draft.state === "rewriting";
  const locked = working || streaming || undoing;
  const sticker = draft.selected_media?.kind === "sticker";
  useEffect(() => {
    mounted.current = true;
    const leave = () => {
      mediaRequest.current?.abort();
      setChoosingTone(false);
      setFocusSelection(false);
      setFocusAt(null);
      composing.current = false;
    };
    window.addEventListener("pagehide", leave);
    window.addEventListener("browser-auth-lost", leave);
    return () => {
      mounted.current = false;
      mediaRequest.current?.abort();
      window.removeEventListener("pagehide", leave);
      window.removeEventListener("browser-auth-lost", leave);
    };
  }, []);
  useEffect(() => {
    if (!visible) {
      mediaRequest.current?.abort();
      setChoosingTone(false);
      setFocusSelection(false);
      setFocusAt(null);
      composing.current = false;
    }
  }, [visible]);
  useEffect(() => {
    if (dirty.current) setError("Unsaved draft restored in this browser.");
  }, []);
  const replyNonce = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (incoming.attempt && incoming.attempt === ignoredAttempt.current) return;
    if (incoming.revision >= server.current.revision) {
      server.current = incoming;
      const selectionChanged =
        incoming.selected_media?.id !== current.current.selected_media?.id ||
        incoming.selected_media?.kind !== current.current.selected_media?.kind;
      const heldChanged =
        selectionChanged ||
        (incoming.selected_media?.kind === "sticker" &&
          (incoming.text !== current.current.text ||
            incoming.undo !== current.current.undo ||
            incoming.state !== current.current.state ||
            incoming.attempt !== current.current.attempt));
      if (!dirty.current || heldChanged) {
        if (heldChanged) {
          dirty.current = false;
          sessionStorage.removeItem(recoveryKey);
        }
        if (incoming.text !== current.current.text) caret.current = null;
        current.current = incoming;
        setDraft(incoming);
      }
    }
  }, [incoming]);
  useLayoutEffect(() => {
    if (focusAt === null || !field.current) return;
    field.current.focus();
    field.current.setSelectionRange(focusAt, focusAt);
    caret.current = focusAt;
    setFocusAt(null);
  }, [focusAt, draft.text]);
  useLayoutEffect(() => {
    if (!focusSelection || !visible) return;
    if (draft.selected_media?.kind === "sticker")
      selectedField.current?.focus();
    else field.current?.focus();
    setFocusSelection(false);
  }, [focusSelection, draft.selected_media?.id, visible]);
  useEffect(() => {
    void workspaceApi<Preferences>("preferences")
      .then(setPreferences)
      .catch((e) => setError(e.message));
  }, []);
  function apply(value: Draft, version?: number) {
    if (value.revision < server.current.revision) return;
    if (value.revision >= server.current.revision) server.current = value;
    if (version === undefined || version === edits.current) {
      current.current = value;
      setDraft(value);
      dirty.current = false;
      sessionStorage.removeItem(recoveryKey);
    }
  }
  function sameDraftContent(value: Draft, clicked: Draft) {
    return (
      value.text === clicked.text &&
      value.reply_to === clicked.reply_to &&
      value.selected_media?.id === clicked.selected_media?.id &&
      value.selected_media?.kind === clicked.selected_media?.kind &&
      value.attachments.length === clicked.attachments.length &&
      value.attachments.every(
        (identity, index) => identity === clicked.attachments[index],
      )
    );
  }
  function save(): Promise<void> {
    queue.current = queue.current
      .catch(() => {})
      .then(async () => {
        while (dirty.current) {
          const version = edits.current;
          const value = await chatApi<Draft>(account, chat.peer, "/draft", {
            revision: server.current.revision,
            text: current.current.text,
            reply_to: current.current.reply_to,
          });
          apply(value, version);
        }
      });
    return queue.current;
  }
  function edit(change: Partial<Draft>) {
    // An in-flight submission owns the draft until it is saved or refused;
    // an edit now could resurrect the submitted text as a new draft.
    if (
      locked ||
      mediaBusy ||
      action.current === "media" ||
      action.current === "send" ||
      action.current === "undo"
    )
      return;
    const kept = current.current.selected_media?.kind === "sticker";
    if (
      kept &&
      change.text !== undefined &&
      change.text !== current.current.text
    )
      return;
    edits.current++;
    dirty.current = true;
    current.current = {
      ...current.current,
      ...change,
      ...(kept ? {} : { undo: null, state: "idle" }),
    };
    setDraft(current.current);
    sessionStorage.setItem(
      recoveryKey,
      JSON.stringify({
        text: current.current.text,
        reply_to: current.current.reply_to,
        ...(kept
          ? {
              selection: current.current.selected_media?.id,
              revision: server.current.revision,
            }
          : {}),
      }),
    );
    void save().catch((e) => setError(e.message));
  }
  useEffect(() => {
    // A Reply chosen during a submission applies to the next draft.
    if (
      !locked &&
      !sending &&
      !mediaBusy &&
      reply &&
      reply.nonce !== replyNonce.current
    ) {
      replyNonce.current = reply.nonce;
      edit({ reply_to: reply.identity });
    }
  }, [reply, sending, locked, mediaBusy]);
  async function rewrite() {
    if (action.current || !canRewrite()) return;
    const clicked = current.current;
    action.current = "rewrite";
    setWorking(true);
    setError("");
    try {
      await save();
      if (action.current !== "rewrite") return;
      if (!sameDraftContent(current.current, clicked))
        throw new Error(
          "The draft changed before Rewrite. Review it and rewrite again.",
        );
      const version = edits.current;
      const result = await chatApi<Draft>(account, chat.peer, "/rewrite", {
        revision: server.current.revision,
      });
      apply(result, version);
      if (dirty.current) await save();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      if (action.current === "rewrite") action.current = null;
      setWorking(false);
      void refresh();
    }
  }
  async function choose(index: number) {
    if (action.current || !canInsert() || !suggestions.attempt) return;
    const clicked = current.current;
    const cursor = caret.current;
    action.current = "insert";
    setInserting(true);
    setError("");
    try {
      await save();
      if (!sameDraftContent(current.current, clicked))
        throw new Error(
          "The draft changed before inserting the response. Review it and choose again.",
        );
      const version = edits.current;
      const result = await chatApi<{ draft: Draft; caret: number }>(
        account,
        chat.peer,
        "/suggestions/insert",
        {
          revision: server.current.revision,
          attempt: suggestions.attempt,
          index,
          cursor,
        },
      );
      apply(result.draft, version);
      if (version === edits.current) setFocusAt(result.caret);
    } catch (e) {
      setError((e as Error).message);
      void refresh();
    } finally {
      action.current = null;
      setInserting(false);
    }
  }
  async function undo() {
    if (!canUndo() || (action.current && action.current !== "rewrite")) return;
    const clicked = current.current;
    action.current = "undo";
    setUndoing(true);
    setError("");
    try {
      await save();
      const savedDraft = current.current;
      if (
        savedDraft.undo !== clicked.undo ||
        savedDraft.attempt !== clicked.attempt ||
        savedDraft.selected_media?.id !== clicked.selected_media?.id ||
        savedDraft.selected_media?.kind !== clicked.selected_media?.kind ||
        (!clicked.attempt && savedDraft.text !== clicked.text)
      )
        throw new Error(
          "The draft changed before Undo. Review it and try again.",
        );
      const version = ++edits.current;
      ignoredAttempt.current = current.current.attempt || null;
      apply(
        await chatApi<Draft>(account, chat.peer, "/undo", {
          revision: server.current.revision,
          attempt: current.current.attempt || null,
        }),
        version,
      );
    } catch (e) {
      ignoredAttempt.current = null;
      setError((e as Error).message);
      void refresh();
    } finally {
      if (action.current === "undo") action.current = null;
      setUndoing(false);
    }
  }
  // A tile stages its GIF or sticker in the draft, which the service then
  // prepares (ADR 32, ADR 46 CU5).
  async function selectMedia(item: CatalogItem, stillCurrent: () => boolean) {
    if (
      action.current ||
      locked ||
      !visibleNow.current ||
      !stillCurrent() ||
      current.current.attachments.length
    )
      return;
    const previous = current.current.selected_media;
    action.current = "media";
    setMediaBusy(true);
    setError("");
    const request = new AbortController();
    mediaRequest.current = request;
    try {
      await save();
      if (
        request.signal.aborted ||
        !visibleNow.current ||
        !stillCurrent() ||
        current.current.state === "rewriting" ||
        current.current.attachments.length
      )
        return;
      if (
        current.current.selected_media?.id !== previous?.id ||
        current.current.selected_media?.kind !== previous?.kind
      )
        throw new Error(
          "The selected media changed. Review it and choose again.",
        );
      const version = edits.current;
      setPicker(null);
      const result = await catalogApi.select(
        account,
        chat.peer,
        {
          revision: server.current.revision,
          token: item.token,
          catalog_revision: item.revision,
        },
        request.signal,
      );
      if (
        request.signal.aborted ||
        !mounted.current ||
        !visibleNow.current ||
        result.revision < server.current.revision
      )
        return;
      apply(result, version);
      setPicker(null);
      setFocusSelection(true);
    } catch (e) {
      if (!request.signal.aborted && mounted.current && visibleNow.current)
        setError((e as Error).message);
    } finally {
      if (mediaRequest.current === request) mediaRequest.current = null;
      if (action.current === "media") action.current = null;
      if (mounted.current) {
        setMediaBusy(false);
        if (visibleNow.current) void refresh();
      }
    }
  }
  async function mediaAction(kind: "retry" | "discard") {
    const selected = current.current.selected_media;
    if (
      !selected ||
      action.current ||
      locked ||
      sending ||
      attaching ||
      selecting ||
      inserting ||
      !visibleNow.current
    )
      return;
    if (
      kind === "retry" &&
      (selected.status !== "failed" || !selected.retryable)
    )
      return;
    action.current = "media";
    setMediaBusy(true);
    setError("");
    const request = new AbortController();
    mediaRequest.current = request;
    try {
      await save();
      if (
        request.signal.aborted ||
        !visibleNow.current ||
        current.current.state === "rewriting" ||
        current.current.selected_media?.id !== selected.id
      )
        return;
      const version = edits.current;
      const result = await catalogApi.act(
        account,
        chat.peer,
        kind,
        { revision: server.current.revision, id: selected.id },
        request.signal,
      );
      if (
        request.signal.aborted ||
        !mounted.current ||
        !visibleNow.current ||
        result.revision < server.current.revision
      )
        return;
      apply(result, version);
      setFocusSelection(true);
    } catch (e) {
      if (!request.signal.aborted && mounted.current && visibleNow.current)
        setError((e as Error).message);
    } finally {
      if (mediaRequest.current === request) mediaRequest.current = null;
      if (action.current === "media") action.current = null;
      if (mounted.current) {
        setMediaBusy(false);
        if (visibleNow.current) void refresh();
      }
    }
  }
  async function stop() {
    const shown = current.current;
    ignoredAttempt.current = shown.attempt || null;
    const version = ++edits.current;
    setError("");
    try {
      apply(
        await chatApi<Draft>(account, chat.peer, "/rewrite/stop", {
          revision: shown.revision,
          attempt: shown.attempt,
          text: shown.partial ? shown.text : null,
        }),
        version,
      );
      setWorking(false);
    } catch (e) {
      ignoredAttempt.current = null;
      setError((e as Error).message);
      void refresh();
    }
  }
  async function send() {
    if (action.current || !canSend()) return;
    action.current = "send";
    setSending(true);
    setError("");
    const shown = current.current;
    // The submitted content shows at once; saving it frees the composer and
    // the service sends it in order (ADR 26). Presentation proves nothing.
    onPending({
      id: "local",
      state: "queued",
      text: shown.selected_media?.kind === "sticker" ? "" : shown.text,
      images: [...shown.attachments],
      media: shown.selected_media || null,
      reply_to: shown.reply_to,
      created: Date.now() / 1000,
      native_ids: [],
      submitted: true,
      retryable: false,
      cancellable: false,
      checkable: false,
    });
    let saved: Operation;
    try {
      await save();
      if (!sameDraftContent(current.current, shown))
        throw new Error(
          "The draft changed before Send. Review it and send again.",
        );
      const version = edits.current;
      const result = await chatApi<{ operation: Operation; draft: Draft }>(
        account,
        chat.peer,
        "/send",
        { revision: server.current.revision },
      );
      apply(result.draft, version);
      saved = result.operation;
    } catch (e) {
      // Not saved: the draft and its images stay in the composer.
      onPending(null, "local");
      setError((e as Error).message);
      return;
    } finally {
      // Saved or not, the composer and navigation are free again at once.
      action.current = null;
      setSending(false);
    }
    // Bridge until the conversation snapshot carries the saved submission
    // (or, once confirmed, its native message); then drop the local copy.
    onPending(saved);
    onSend();
    try {
      await refresh();
    } finally {
      onPending(null, saved.id);
    }
  }
  async function attach(files: FileList | null) {
    if (!files?.length || action.current || locked || sending || mediaBusy)
      return;
    if (current.current.selected_media) {
      setError("Remove the selected GIF or sticker before attaching images.");
      if (fileInput.current) fileInput.current.value = "";
      return;
    }
    action.current = "attach";
    setAttaching(true);
    setError("");
    try {
      const selected = Array.from(files);
      if (
        selected.length + current.current.attachments.length > 10 ||
        selected.some((f) => f.size > 5 * 1024 * 1024)
      )
        throw new Error("Use up to ten images, at most 5 MB each.");
      for (const file of selected) {
        const data = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(",")[1]);
          reader.onerror = () => reject(new Error("Image could not be read"));
          reader.readAsDataURL(file);
        });
        await save();
        const version = edits.current;
        const result = await chatApi<Draft>(account, chat.peer, "/images", {
          revision: server.current.revision,
          name: file.name,
          data,
        });
        apply(result, version);
        if (dirty.current) await save();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      action.current = null;
      setAttaching(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }
  async function removeImage(identity: string) {
    if (action.current || locked || sending || mediaBusy) return;
    action.current = "attach";
    setAttaching(true);
    try {
      await save();
      const version = edits.current;
      apply(
        await chatApi<Draft>(account, chat.peer, `/images/${identity}/remove`, {
          revision: server.current.revision,
        }),
        version,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      action.current = null;
      setAttaching(false);
    }
  }
  useEffect(() => {
    setNavigationGuard(async () => {
      if (sending || attaching || undoing) {
        setError("Wait for the current action to finish.");
        return false;
      }
      try {
        await save();
        return true;
      } catch (e) {
        setError((e as Error).message);
        return false;
      }
    });
    const closing = (event: BeforeUnloadEvent) => {
      if (dirty.current || sending || attaching || undoing) {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", closing);
    return () => {
      setNavigationGuard(null);
      window.removeEventListener("beforeunload", closing);
    };
  }, [sending, attaching, undoing]);
  function canRewrite() {
    return (
      processing &&
      nativeAvailable &&
      current.current.selected_media?.kind !== "sticker" &&
      current.current.selected_media?.status !== "preparing" &&
      !!current.current.text.trim() &&
      current.current.state !== "rewriting" &&
      !locked &&
      !attaching &&
      !sending &&
      !selecting &&
      !inserting &&
      !mediaBusy
    );
  }
  function canSend() {
    const media = current.current.selected_media;
    return (
      nativeAvailable &&
      !replyStatus &&
      // A staged GIF or sticker sends once ready, a GIF with the text as
      // its caption (ADR 32).
      (media
        ? media.status === "ready"
        : !!current.current.text.trim() ||
          !!current.current.attachments.length) &&
      current.current.state !== "rewriting" &&
      !locked &&
      !attaching &&
      !sending &&
      !selecting &&
      !inserting &&
      !mediaBusy
    );
  }
  function canInsert() {
    return (
      current.current.selected_media?.kind !== "sticker" &&
      current.current.state !== "rewriting" &&
      !locked &&
      !attaching &&
      !sending &&
      !selecting &&
      !inserting &&
      !mediaBusy
    );
  }
  function canUndo() {
    return (
      !retained &&
      current.current.undo !== null &&
      !dirty.current &&
      current.current.selected_media?.kind !== "sticker" &&
      !inserting &&
      !sending &&
      !attaching &&
      !selecting &&
      !mediaBusy &&
      !undoing
    );
  }
  function remember(event: React.SyntheticEvent<HTMLTextAreaElement>) {
    caret.current = event.currentTarget.selectionEnd;
  }
  const group = chat.kind === "group";
  // Saved Messages has no AI for now (ADR 50 Q3, Q17): no Rewrite, tone,
  // Undo or suggestions.
  const saved = chat.kind === "saved";
  const tone = preferences.tone;
  const toneName = {
    neutral: "Neutral",
    professional: "Professional",
    friendly: "Friendly casual",
  }[tone];
  const rewriteStatus =
    processing === false
      ? "AI processing is Off for this account"
      : !nativeAvailable
        ? "Native group access is unavailable"
        : `Rewrite · ${toneName}`;
  return (
    <footer className={`composer-wrap${streaming ? " is-rewriting" : ""}`}>
      {retained && (
        <div className="retained-draft-note">
          <span>Saved draft · Retained group work</span>
          <button
            aria-label="Copy saved draft"
            disabled={!draft.text}
            onClick={() =>
              void navigator.clipboard
                .writeText(draft.text)
                .catch(() => setError("Copy unavailable in this browser"))
            }
          >
            <Copy size={14} aria-hidden="true" /> Copy draft
          </button>
        </div>
      )}
      <input
        ref={fileInput}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        hidden
        aria-label="Attach images"
        disabled={
          retained || locked || sending || mediaBusy || !!draft.selected_media
        }
        onChange={(e) => void attach(e.target.files)}
      />
      {!group && !saved && (
        <SuggestionArea
          account={account}
          peer={chat.peer}
          view={suggestions}
          disabled={!canInsert()}
          onChoose={(index) => void choose(index)}
          onBudget={onBudget}
          refresh={refresh}
        />
      )}
      {!!draft.attachments.length && (
        <div className="image-tray">
          {draft.attachments.map((id, index) => (
            <div key={id} className="draft-image">
              <img
                src={`/api/accounts/${account}/chats/${encodeURIComponent(chat.peer)}/images/${id}`}
                alt={`Attached image ${index + 1}`}
              />
              <button
                aria-label={`Remove image ${index + 1}`}
                title={`Remove image ${index + 1}`}
                disabled={retained || locked || sending || mediaBusy}
                onClick={() => void removeImage(id)}
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
      {draft.reply_to && (
        <div className="reply-quote">
          <span>
            {/* The chosen message's sender and first line (ADR 41 Q4). */}
            <ReplyQuote
              reply={{
                message_id: 0,
                date: Date.now() / 1000,
                reply_to: draft.reply_to,
              }}
              variant="target"
            />
            {replyStatusText && <small role="status">{replyStatusText}</small>}
          </span>
          <button
            aria-label="Remove reply target"
            title="Remove reply target"
            disabled={retained || locked || sending || mediaBusy}
            onClick={() => edit({ reply_to: null })}
          >
            <X size={14} />
          </button>
        </div>
      )}
      {draft.selected_media && (
        <SelectedMediaPreview
          media={draft.selected_media}
          keptText={!!draft.text.trim()}
          disabled={retained || locked || sending || mediaBusy}
          onRemove={() => void mediaAction("discard")}
          onRetry={() => void mediaAction("retry")}
        />
      )}
      {/* Enter adds a new line; Rewrite and Send act from their buttons
          only (ADR 44 Q4). */}
      <textarea
        ref={field}
        aria-label="Message"
        placeholder="Write your message…"
        value={draft.text}
        readOnly={retained || locked || sending || mediaBusy || sticker}
        // A sticker sends on its own; the text waits as Draft kept.
        hidden={sticker}
        onSelect={remember}
        onKeyUp={remember}
        onClick={remember}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
        }}
        onChange={(e) => {
          caret.current = e.target.selectionEnd;
          edit({ text: e.target.value });
        }}
      />
      <div className="composer-actions">
        <button
          type="button"
          className="attach-button"
          aria-label="Attach"
          title="Attach"
          disabled={
            retained ||
            attaching ||
            locked ||
            sending ||
            selecting ||
            inserting ||
            mediaBusy ||
            !!draft.selected_media ||
            !nativeAvailable
          }
          onClick={() => setAttachSheet(true)}
        >
          <Paperclip size={18} />
        </button>
        {saved ? (
          <span className="composer-spacer" />
        ) : (
          <div className="rewrite-controls">
            <button
              className="rewrite-button"
              aria-label="Rewrite"
              disabled={!canRewrite()}
              title={rewriteStatus}
              aria-description={rewriteStatus}
              onClick={() => void rewrite()}
            >
              {locked ? (
                <LoaderCircle
                  size={16}
                  className="work-spinner"
                  aria-hidden="true"
                />
              ) : (
                <Pencil size={16} aria-hidden="true" />
              )}
              <span>Rewrite · {toneName}</span>
            </button>
            <button
              type="button"
              className="tone-switch"
              aria-label="Rewrite tone"
              aria-haspopup="dialog"
              disabled={
                retained ||
                locked ||
                selecting ||
                sending ||
                attaching ||
                inserting ||
                mediaBusy
              }
              onClick={() => setChoosingTone(true)}
            >
              <ChevronDown size={16} aria-hidden="true" />
            </button>
            {streaming && (
              <button
                className="stop-rewrite"
                aria-label="Stop rewrite"
                title="Stop rewrite"
                onClick={() => void stop()}
              >
                <Square size={15} />
              </button>
            )}
            <button
              className="undo-button"
              aria-label={
                draft.state === "inserted" ? "Undo suggestion" : "Undo rewrite"
              }
              title={
                draft.state === "inserted" ? "Undo suggestion" : "Undo rewrite"
              }
              disabled={!canUndo()}
              onClick={() => void undo()}
            >
              <Undo2 size={16} />
            </button>
          </div>
        )}
        <button
          className="send-button"
          aria-label="Send"
          title={
            nativeAvailable ? "Send" : "Native group access is unavailable"
          }
          disabled={!canSend()}
          onClick={() => void send()}
        >
          <Send size={18} />
        </button>
      </div>
      {(streaming || draft.state === "stopped") && (
        <small className="rewrite-state" role="status">
          {streaming ? "Processing…" : "Stopped · incomplete draft"}
        </small>
      )}
      {(error || draft.error) && (
        <p className="composer-error" role="alert">
          {error || draft.error}
        </p>
      )}
      {choosingTone && (
        <ToneSheet
          tone={tone}
          onChoose={async (next) => {
            setChoosingTone(false);
            if (action.current || next === tone) return;
            action.current = "tone";
            setSelecting(true);
            try {
              setPreferences(
                await workspaceApi<Preferences>("preferences", { tone: next }),
              );
            } catch (err) {
              setError((err as Error).message);
            } finally {
              action.current = null;
              setSelecting(false);
            }
          }}
          onClose={() => setChoosingTone(false)}
        />
      )}
      {/* The paperclip offers Photos, GIFs and Stickers (ADR 46 CU5). */}
      {attachSheet && (
        <div className="sheet-scrim" onClick={() => setAttachSheet(false)}>
          <div
            className="sheet-bottom attach-sheet"
            role="menu"
            aria-label="Attach"
            onClick={(event) => event.stopPropagation()}
          >
            {(
              [
                ["Photos", <ImageIcon size={20} aria-hidden />, null],
                ["GIFs", <Clapperboard size={20} aria-hidden />, "gif"],
                ["Stickers", <Sticker size={20} aria-hidden />, "sticker"],
              ] as const
            ).map(([label, icon, kind]) => (
              <button
                key={label}
                type="button"
                role="menuitem"
                className="sheet-item"
                onClick={() => {
                  setAttachSheet(false);
                  if (kind) setPicker(kind);
                  else fileInput.current?.click();
                }}
              >
                {icon}
                <span>{label}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <MediaPicker
        account={account}
        peer={chat.peer}
        open={picker}
        visible={visible}
        disabled={retained || locked || sending || mediaBusy || attaching}
        onTab={setPicker}
        onClose={() => setPicker(null)}
        onSelect={selectMedia}
      />
    </footer>
  );
}

const TONES = [
  ["neutral", "Neutral"],
  ["professional", "Professional"],
  ["friendly", "Friendly casual"],
] as const;

// The rewrite tones as a sheet of choices, as in the app (Adaptive left with
// ADR 53).
function ToneSheet({
  tone,
  onChoose,
  onClose,
}: {
  tone: Preferences["tone"];
  onChoose: (tone: Preferences["tone"]) => void;
  onClose: () => void;
}) {
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    first.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div
        className="sheet-bottom"
        role="radiogroup"
        aria-label="Rewrite tone"
        onClick={(event) => event.stopPropagation()}
      >
        {TONES.map(([value, label], index) => (
          <button
            key={value}
            ref={index === 0 ? first : undefined}
            type="button"
            role="radio"
            aria-checked={value === tone}
            className="sheet-item"
            onClick={() => onChoose(value)}
          >
            <span className="sheet-check">
              {value === tone && <Check size={20} aria-hidden="true" />}
            </span>
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
