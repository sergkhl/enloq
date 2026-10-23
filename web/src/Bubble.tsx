import React, { useEffect, useRef, useState } from "react";
import { Check, CheckCheck, CheckCircle2, Circle, X } from "lucide-react";
import { chatApi, type NativeMessage } from "./conversation-api";
import { useVersions, type ObservedVersion } from "./detailData";
import {
  AttachmentCard,
  ForwardedLines,
  ReactionChips,
  VersionControls,
} from "./Details";
import { mediaLabel } from "../../shared/deletion";
import { LinkedText } from "./LinkedText";
import { ViewedEnd } from "./LocalViewing";
import {
  FileCard,
  isVoice,
  mediaActions,
  mediaUrl,
  spoken,
  type MediaAction,
} from "./MediaFiles";
import { useMessageDeletion } from "./MessageDeletion";
import { GifView, StickerView } from "./Moving";
import { ReplyQuote } from "./ReplyQuote";
import { combined, useSwipeToReply } from "./SwipeToReply";
import { useGallery } from "./MediaCarousel";
import { Avatar } from "./Avatar";
import { peerIndex, type RunPlace } from "../../shared/runs";
import { albumLayout } from "../../shared/gallery";
import { VideoCard, VideoMessage } from "./VideoMedia";
import { VoiceNote } from "./VoiceNote";
import { messageTime } from "../../shared/messageTime";
import {
  forwardedGroups,
  forwardedLine,
  forwardOf,
} from "../../shared/forwarding";
import { participantLabel } from "../../shared/participantLabel";
import { editedName } from "../../shared/versions";
import { isReply, outsideReplies } from "../../shared/replyQuote";
import {
  showTranscript,
  type TranscriptStatus,
  type TranscriptView,
} from "../../shared/transcripts";
import "./bubble.css";
import "./media.css";

// What a message's sheet offers (ADR 26): the message a Reply answers, the
// visible wording Copy takes, a group bot to stop ignoring (ADR 42 Q3), and
// the row's transcripts and files (ADR 46 CU1).
export type MenuTarget = {
  reply: number | null;
  copy: string;
  bot?: string | null;
  transcripts?: MediaAction[];
  downloads?: MediaAction[];
  // A private chat's current sources the owner may react to (CU2).
  react?: NativeMessage[];
  // A failed block's Retry, and showing one block's AI text or originals
  // against the reading view (CU3).
  retry?: () => void;
  toggleView?: { label: string; run: () => void };
  // Starts Message Selection with this original (CU4).
  select?: () => void;
  // Hides a deleted message for good (ADR 52 Q11).
  hide?: () => void;
};

// A bubble's place in its Message Run (ADR 51 Q13): later bubbles sit closer,
// and the last has a tail.
export const SOLO: RunPlace = { first: true, last: true };
export function runClasses(run: RunPlace) {
  return `${run.first ? "" : " run-follow"}${run.last ? " run-tail" : ""}`;
}

// A long press, a right-click or the menu key opens a message's sheet, as in
// the app (ADR 44's derived design). Android delivers its long press as the
// same contextmenu event; the touch timer covers browsers that don't.
export function useMenuGesture(open: () => void) {
  const timer = useRef<number | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  const clear = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    start.current = null;
  };
  useEffect(() => clear, []);
  return {
    onPointerDown: (event: React.PointerEvent) => {
      if (event.pointerType === "mouse") return;
      clear();
      start.current = { x: event.clientX, y: event.clientY };
      timer.current = window.setTimeout(() => {
        timer.current = null;
        open();
      }, 500);
    },
    onPointerMove: (event: React.PointerEvent) => {
      const from = start.current;
      if (
        from &&
        Math.hypot(event.clientX - from.x, event.clientY - from.y) > 10
      )
        clear();
    },
    onPointerUp: clear,
    onPointerCancel: clear,
    onPointerLeave: clear,
    onContextMenu: (event: React.MouseEvent) => {
      event.preventDefault();
      clear();
      open();
    },
    onKeyDown: (event: React.KeyboardEvent) => {
      if (
        event.key === "ContextMenu" ||
        (event.shiftKey && event.key === "F10")
      ) {
        event.preventDefault();
        open();
      }
    },
  };
}

// The app's labels for what no player or card shows
// (mobile/src/conversation.ts).
const LABELS: Record<string, string> = {
  image: "Photo",
  audio: "Voice message",
  video_note: "Video message",
  video: "Video",
  gif: "GIF",
  sticker: "Sticker",
  document: "File",
  other: "Unsupported message",
  unsupported: "Unsupported message",
};
const CARDS = new Set(["poll", "location", "contact", "link"]);

// A message's own content, as the app shows it: a photo, a voice note, a
// video message or video, a GIF or sticker, a file card or a label (ADR 46
// CU1), the transcript of a voice or video message, and the text with links.
export function MessageContent({
  message,
  account,
  peer,
  hideText = false,
  version = null,
}: {
  message: NativeMessage;
  account: number;
  peer: string;
  // Under an AI rewrite only the attachment shows; the text is rewritten.
  hideText?: boolean;
  // An earlier version shown in its place (ADR 46 CU2).
  version?: ObservedVersion | null;
}) {
  if (version)
    return <VersionContent version={version} account={account} peer={peer} />;
  const label = message.media ? LABELS[message.media] : null;
  const media = { account, peer, message };
  // A deleted message shows what it last said (ADR 52 Q7); media Enloq no
  // longer holds says so, since Telegram serves it no more.
  const gone = message.deleted && !!message.media && !message.media_available;
  return (
    <>
      {gone ? (
        <small className="bubble-media">{`${label || "Attachment"} · no longer available`}</small>
      ) : message.media === "image" ? (
        message.deleted ? (
          <Photo
            url={`${mediaUrl(account, peer, message)}&version=${message.deleted_version}`}
          />
        ) : (
          <Photo url={mediaUrl(account, peer, message)} message={message} />
        )
      ) : isVoice(message) ? (
        <VoiceNote {...media} />
      ) : message.media === "video_note" ? (
        <VideoMessage {...media} />
      ) : message.media === "video" ? (
        <VideoCard {...media} />
      ) : message.media === "gif" ? (
        <GifView {...media} />
      ) : message.media === "sticker" ? (
        <StickerView {...media} />
      ) : message.media === "document" || message.media === "audio" ? (
        <FileCard {...media} />
      ) : CARDS.has(message.media ?? "") ? (
        <AttachmentCard message={message} />
      ) : label ? (
        <small className="bubble-media">{label}</small>
      ) : null}
      {spoken(message) && (
        <TranscriptButton account={account} peer={peer} message={message} />
      )}
      {message.text && !hideText && (
        <p className="bubble-text">
          <LinkedText text={message.text} links={message.links} />
        </p>
      )}
    </>
  );
}

// An earlier version: its text, and its photo while the cache still has it;
// other media say whether they are still kept, with a captured transcript.
function VersionContent({
  version,
  account,
  peer,
}: {
  version: ObservedVersion;
  account: number;
  peer: string;
}) {
  const message = version.message;
  const label = message.media ? LABELS[message.media] : null;
  return (
    <>
      {message.media === "image" && version.media_available ? (
        <Photo
          url={`${mediaUrl(account, peer, message)}&version=${version.id}`}
        />
      ) : label ? (
        <small className="bubble-media">
          {version.media_available
            ? label
            : `${label} · no longer available locally`}
        </small>
      ) : null}
      {version.transcript?.text && (
        <small className="bubble-media">{`Transcript: ${version.transcript.text}`}</small>
      )}
      {message.deleted ? (
        <p className="bubble-deleted">Message deleted</p>
      ) : (
        message.text && (
          <p className="bubble-text">
            <LinkedText text={message.text} links={message.links} />
          </p>
        )
      )}
    </>
  );
}

// A photo opens full screen, as in the app (ADR 38 Q6).
// A photo opens the chat's Media Carousel (ADR 51 Q10); an earlier
// version's photo opens alone.
function Photo({ url, message }: { url: string; message?: NativeMessage }) {
  const gallery = useGallery();
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  if (failed) return <small className="bubble-media">Photo unavailable</small>;
  return (
    <>
      <button
        type="button"
        className="bubble-photo"
        aria-label="Photo, opens full screen"
        onClick={() =>
          gallery && message ? gallery.open(message) : setOpen(true)
        }
      >
        <img src={url} alt="" loading="lazy" onError={() => setFailed(true)} />
      </button>
      {open && <PhotoViewer url={url} onClose={() => setOpen(false)} />}
    </>
  );
}

function PhotoViewer({ url, onClose }: { url: string; onClose: () => void }) {
  const [note, setNote] = useState("");
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  // Share where the browser can share files, as the app's sheet does.
  async function share() {
    setNote("Preparing…");
    try {
      const blob = await (await fetch(url)).blob();
      const file = new File([blob], "photo.jpg", { type: blob.type });
      await navigator.share({ files: [file] });
      setNote("");
    } catch {
      setNote("The photo couldn't be shared.");
    }
  }
  const sharing =
    typeof navigator.canShare === "function" &&
    navigator.canShare({ files: [new File([], "photo.jpg")] });
  return (
    <div
      className="photo-viewer"
      role="dialog"
      aria-modal="true"
      aria-label="Photo"
    >
      <img src={url} alt="" />
      {note && <p role="status">{note}</p>}
      <div className="photo-viewer-actions">
        {sharing && (
          <button
            type="button"
            className="secondary"
            onClick={() => void share()}
          >
            Share
          </button>
        )}
        <button
          ref={close}
          type="button"
          className="secondary"
          onClick={onClose}
        >
          <X size={16} aria-hidden /> Close
        </button>
      </div>
    </div>
  );
}

// A Telegram album (ADR 51 Q12): its photos and videos as one grid laid out
// as Telegram does, each opening the Media Carousel there, then its caption.
function AlbumContent({
  album,
  account,
  peer,
}: {
  album: NativeMessage[];
  account: number;
  peer: string;
}) {
  const gallery = useGallery();
  let next = 0;
  const caption = album.find((item) => item.text);
  return (
    <>
      <div className="album-grid" aria-label={`Album, ${album.length} items`}>
        {albumLayout(album.length).map((size, row) => (
          <div key={row} className="album-row">
            {album.slice(next, (next += size)).map((item) => (
              <button
                key={item.message_id}
                type="button"
                className="album-item"
                aria-label={`${item.media === "video" ? "Video" : "Photo"}, opens full screen`}
                onClick={() => gallery?.open(item)}
              >
                <img
                  src={mediaUrl(account, peer, item, {
                    variant: item.media === "video" ? "poster" : undefined,
                  })}
                  alt=""
                  loading="lazy"
                />
                {item.media === "video" && (
                  <span className="album-play" aria-hidden>
                    ▶
                  </span>
                )}
              </button>
            ))}
          </div>
        ))}
      </div>
      {caption && (
        <p className="bubble-text">
          <LinkedText text={caption.text} links={caption.links} />
        </p>
      )}
    </>
  );
}

// The machine transcript of a voice or video message, on request; a missing
// one starts on the tap and shows while it runs (ADR 51 Q9).
function TranscriptButton({
  account,
  peer,
  message,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
}) {
  const [view, setView] = useState<TranscriptView | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const path = `/originals/${message.message_id}/transcript`;
  function show() {
    void showTranscript({
      read: () => chatApi<TranscriptStatus>(account, peer, `${path}/status`),
      start: () => chatApi<TranscriptStatus>(account, peer, path, {}),
      saved: peer === `user:${account}`,
      show: setView,
      alive: () => alive.current,
      wait: (ms) => new Promise((done) => setTimeout(done, ms)),
    });
  }
  if (view === null)
    return (
      <button type="button" className="bubble-link" onClick={show}>
        Show transcript
      </button>
    );
  if (view.kind === "loading")
    return <small className="bubble-media">Loading…</small>;
  if (view.kind === "running")
    return <small className="bubble-media">Transcribing…</small>;
  if (view.kind === "text")
    return <p className="bubble-transcript">{view.text}</p>;
  return (
    <p className="bubble-media">
      {view.note}
      {view.retry && (
        <>
          {" "}
          <button type="button" className="bubble-link" onClick={show}>
            Try again
          </button>
        </>
      )}
    </p>
  );
}

// One original as its own bubble: Originals reading, and every group
// original with its author line (ADR 42 AG3).
export function OriginalBubble({
  message,
  account,
  peer,
  name,
  group,
  timezone,
  readOutbox = 0,
  highlighted = false,
  originals = false,
  toggleView,
  onMenu,
  run = SOLO,
  onMember,
  album,
}: {
  message: NativeMessage;
  account: number;
  peer: string;
  name: string;
  group: boolean;
  // A Telegram album this message begins, drawn as one grid (ADR 51 Q12).
  album?: NativeMessage[];
  // Its place in its Message Run (ADR 51 Q13).
  run?: RunPlace;
  // A group member's avatar or name opens their profile (Q14).
  onMember?: (message: NativeMessage) => void;
  timezone: string;
  readOutbox?: number;
  highlighted?: boolean;
  // In Originals reading an edited original steps through its versions.
  originals?: boolean;
  // Its block's AI text, against the reading view (ADR 46 CU3).
  toggleView?: MenuTarget["toggleView"];
  onMenu: (target: MenuTarget) => void;
}) {
  const versions = useVersions(account, peer, message.service ? null : message);
  const version = versions.shown;
  const current = version?.message ?? message;
  const forward = forwardedLine(forwardOf(current, message), timezone);
  // Only a native user bot that is not exempt yet (ADR 42 Q3, the app's rule).
  const bot =
    group &&
    message.sender_is_bot === true &&
    !message.group_filter?.allowed_bot &&
    /^user:[1-9][0-9]*$/.test(message.author_peer ?? "")
      ? message.author_peer!
      : null;
  // Message Selection (ADR 46 CU4): while it runs, a tap adds or removes
  // an eligible original instead of opening links or media.
  const deletion = useMessageDeletion();
  const selecting = !!deletion?.active && !message.service;
  // An album is selected as a whole: each of its items it may delete.
  const members = album ?? [message];
  const selectable =
    selecting && !version && members.some((item) => deletion!.eligible(item));
  const chosen =
    selecting &&
    members
      .filter((item) => deletion!.eligible(item))
      .every((item) => deletion!.isSelected(item.message_id));
  const gesture = useMenuGesture(() =>
    onMenu(
      // While an earlier version shows, its sheet offers only Copy.
      version
        ? { reply: null, copy: current.deleted ? "" : current.text }
        : {
            reply: message.deleted ? null : message.message_id,
            // A deleted message's last words still copy (ADR 52 Q7).
            copy: message.text,
            bot,
            ...mediaActions(message.deleted ? [] : [message], (source) =>
              messageTime(source.date, timezone),
            ),
            // Groups show reactions but take none (ADR 46 CU2).
            react: group || message.deleted ? undefined : [message],
            toggleView,
            select:
              deletion && !deletion.active && deletion.eligible(message)
                ? () => deletion.select(message)
                : undefined,
            hide:
              deletion && message.deleted
                ? () => deletion.hide([message])
                : undefined,
          },
    ),
  );
  // The sheet's Reply, by swiping (ADR 51 Q5); none while selecting.
  const swipe = useSwipeToReply(
    version || message.deleted ? null : message.message_id,
    selecting,
  );
  if (message.service)
    return (
      <p
        className="bubble-service"
        data-source-id={message.message_id}
        data-source-ids={message.message_id}
      >
        {message.text}
      </p>
    );
  // Telegram's own marks: Sent once accepted, Read once it reports so; a
  // group shows Sent, never Read (ADR 34).
  const read = !group && message.message_id <= readOutbox;
  // Telegram's phone look (ADR 51 Q13): in a group another person's run has
  // their name, in their colour, on its first bubble and their avatar beside
  // its last; the owner's own messages carry neither. An ad keeps its mark.
  const own = message.author_peer === `user:${account}`;
  const label = `${participantLabel(message, name, account, true)}${
    message.group_filter?.ad ? " · (ad)" : ""
  }`;
  const named = group && !own && (run.first || !!message.group_filter?.ad);
  // Only a person has a profile: not a channel or an anonymous admin.
  const member =
    onMember && /^user:[1-9][0-9]*$/.test(message.author_peer ?? "")
      ? () => onMember(message)
      : undefined;
  const colour = `peer-colour-${peerIndex(message.author_peer)}`;
  const bubble = (
    <article
      className={`message-bubble app-bubble ${message.outgoing ? "outgoing" : "incoming"}${highlighted ? " highlighted" : ""}${runClasses(run)}`}
      data-source-ids={
        album
          ? album.map((item) => item.message_id).join(" ")
          : message.message_id
      }
      tabIndex={0}
      style={swipe.style}
      {...combined(gesture, swipe.handlers)}
    >
      {swipe.arrow}
      <section className="native-source" data-source-id={message.message_id}>
        {named &&
          (member ? (
            <button
              type="button"
              className={`bubble-author ${colour}`}
              onClick={member}
            >
              {label}
            </button>
          ) : (
            <span className={`bubble-author ${colour}`}>{label}</span>
          ))}
        <ForwardedLines lines={forward ? [forward] : []} />
        {/* A deleted message keeps its quote (ADR 52 Q7). */}
        {isReply(message) && <ReplyQuote reply={message} />}
        {album ? (
          <AlbumContent album={album} account={account} peer={peer} />
        ) : (
          <MessageContent
            message={message}
            account={account}
            peer={peer}
            version={version}
          />
        )}
        {!version && <ReactionChips sources={[message]} />}
        {versions.error && <small role="alert">{versions.error}</small>}
        <span className="bubble-meta">
          <VersionControls
            message={message}
            versions={versions}
            originals={originals}
          />
          <time dateTime={new Date(current.date * 1000).toISOString()}>
            {messageTime(current.date, timezone)}
          </time>
          {message.outgoing &&
            !message.deleted &&
            (read ? (
              <CheckCheck size={14} aria-label="Read" />
            ) : (
              <Check size={14} aria-label="Sent" />
            ))}
        </span>
        {deletion?.pending(message.message_id) && (
          <small className="deleting-original">Deleting…</small>
        )}
        <ViewedEnd content={message.viewed_content} />
      </section>
    </article>
  );
  // Another person's group bubble sits beside its avatar column.
  const placed =
    group && !message.outgoing ? (
      <div className="member-row">
        <span className="member-avatar">
          {run.last &&
            (member ? (
              <button
                type="button"
                aria-label={`Profile: ${label}`}
                onClick={member}
              >
                <Avatar
                  account={account}
                  peer={message.author_peer ?? peer}
                  name={message.author || label}
                  photoId={message.author_photo_id}
                  className={`small-avatar ${colour}`}
                />
              </button>
            ) : (
              <Avatar
                account={account}
                peer={message.author_peer ?? peer}
                name={message.author || label}
                photoId={message.author_photo_id}
                className={`small-avatar ${colour}`}
              />
            ))}
        </span>
        {bubble}
      </div>
    ) : (
      bubble
    );
  if (!selecting) return placed;
  const excerpt = message.text.trim() || mediaLabel(message) || "Message";
  return (
    <div
      className={`selecting-row ${message.outgoing ? "outgoing" : "incoming"}${chosen ? " chosen" : ""}${selectable ? "" : " ineligible"}`}
      onClickCapture={(event) => {
        if (!selectable) return;
        event.preventDefault();
        event.stopPropagation();
        for (const item of members)
          if (
            deletion!.eligible(item) &&
            deletion!.isSelected(item.message_id) === chosen
          )
            deletion!.toggle(item);
      }}
    >
      <button
        type="button"
        role="checkbox"
        className="select-mark"
        aria-checked={chosen}
        aria-label={`Select: ${excerpt.length > 60 ? `${excerpt.slice(0, 59)}…` : excerpt}`}
        disabled={!selectable}
      >
        {chosen ? (
          <CheckCircle2 size={24} aria-hidden />
        ) : (
          <Circle size={24} aria-hidden />
        )}
      </button>
      <div className="selecting-bubble">{placed}</div>
    </div>
  );
}

// An incoming block in AI reading: "AI · N messages · time", the quotes of
// messages outside it that it answers, the AI text with every source's
// attachment, or the originals and why there is no AI text yet. Text that
// isn't rewritten (ADR 54) shows as its originals together, unlabelled.
export function AiBubble({
  originals,
  sourceCount,
  text,
  streaming,
  status,
  viewedContent,
  account,
  peer,
  timezone,
  highlighted = false,
  calmed = false,
  originalsOnly = false,
  note = null,
  retry,
  toggleView,
  onMenu,
  run = SOLO,
}: {
  originals: NativeMessage[];
  sourceCount: number;
  text: string | null;
  // The shown rewrite was calmed (ADR 53 Q5).
  calmed?: boolean;
  // Originals shown together, not rewritten (ADR 54): no "AI" label.
  originalsOnly?: boolean;
  streaming: boolean;
  status: React.ReactNode;
  viewedContent?: NativeMessage["viewed_content"];
  account: number;
  peer: string;
  timezone: string;
  highlighted?: boolean;
  // "Not analysed: …" for what the AI skipped (ADR 46 CU3).
  note?: string | null;
  retry?: () => void;
  toggleView?: MenuTarget["toggleView"];
  onMenu: (target: MenuTarget) => void;
  // Its place in its Message Run (ADR 51 Q13).
  run?: RunPlace;
}) {
  const live = originals.filter((message) => !message.deleted);
  const gesture = useMenuGesture(() =>
    onMenu({
      reply: live.at(-1)?.message_id ?? null,
      copy:
        text ??
        live
          .map((message) => message.text)
          .filter(Boolean)
          .join("\n"),
      ...mediaActions(live, (source) => messageTime(source.date, timezone)),
      react: live,
      retry,
      toggleView,
    }),
  );
  const deletion = useMessageDeletion();
  const swipe = useSwipeToReply(
    live.at(-1)?.message_id ?? null,
    !!deletion?.active,
  );
  const first = originals[0];
  const quoted = outsideReplies(live);
  // AI reading points to Originals for an edited source's versions.
  const edited = live.filter(
    (message) => message.native_edited || message.history_count,
  );
  return (
    <article
      className={`message-bubble app-bubble incoming${highlighted ? " highlighted" : ""}${runClasses(run)}`}
      data-source-ids={originals.map((message) => message.message_id).join(" ")}
      tabIndex={0}
      style={swipe.style}
      {...combined(gesture, swipe.handlers)}
    >
      {swipe.arrow}
      <small className="bubble-ai-head">
        {originalsOnly ? "" : "AI · "}
        {sourceCount} {sourceCount === 1 ? "message" : "messages"}
        {first ? ` · ${messageTime(first.date, timezone)}` : ""}
        {edited.length > 0 && (
          <span
            className="edited-mark"
            role="img"
            aria-label={editedName(
              Math.max(...edited.map((message) => message.history_count ?? 0)),
            )}
          >
            {" ✎"}
          </span>
        )}
        {calmed && text !== null && (
          <span className="calmed-mark"> · Calmed</span>
        )}
      </small>
      <ForwardedLines lines={forwardedGroups(live, timezone)} />
      {quoted.map((message) => (
        <ReplyQuote key={message.message_id} reply={message} />
      ))}
      {text !== null ? (
        <>
          <p className="bubble-text">{text}</p>
          {/* ADR 24: every source attachment stays in AI reading too. */}
          {originals
            .filter((message) => message.media && message.media !== "link")
            .map((message) => (
              <MessageContent
                key={message.message_id}
                message={message}
                account={account}
                peer={peer}
                hideText
              />
            ))}
        </>
      ) : (
        originals.map((message) => (
          <MessageContent
            key={message.message_id}
            message={message}
            account={account}
            peer={peer}
          />
        ))
      )}
      {note && <small className="bubble-status">{note}</small>}
      <ReactionChips sources={live} />
      {streaming ? (
        <small className="bubble-status">Streaming · incomplete</small>
      ) : text === null && (status || retry) ? (
        <span className="bubble-status-line">
          <small className="bubble-status">{status}</small>
          {retry && (
            <button type="button" className="bubble-link" onClick={retry}>
              Retry
            </button>
          )}
        </span>
      ) : null}
      <ViewedEnd content={streaming ? null : viewedContent} />
    </article>
  );
}
