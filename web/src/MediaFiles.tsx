import React from "react";
import { FileText, Music } from "lucide-react";
import { chatApi, type NativeMessage } from "./conversation-api";

// The service keeps files up to 50 MiB for playback and Download.
export const FILE_LIMIT = 50 * 1024 * 1024;

export function originalPath(
  account: number,
  peer: string,
  message: NativeMessage,
) {
  return `/api/accounts/${account}/chats/${encodeURIComponent(peer)}/originals/${message.message_id}`;
}

// A message's media bytes: the file, its poster frame, or a GIF's or
// sticker's checked preview.
export function mediaUrl(
  account: number,
  peer: string,
  message: NativeMessage,
  { variant, path = "media" }: { variant?: "poster"; path?: string } = {},
) {
  return `${originalPath(account, peer, message)}/${path}?revision=${message.revision}${variant ? `&variant=${variant}` : ""}`;
}

// Telegram's voice notes: audio marked as voice, or audio with no details.
export const isVoice = (message: NativeMessage) =>
  message.media === "audio" &&
  (!message.media_info || !!message.media_info.voice);

const DOWNLOADS = new Set([
  "image",
  "audio",
  "video_note",
  "video",
  "document",
]);
const KINDS: Record<string, string> = {
  image: "photo",
  video_note: "video message",
  video: "video",
  document: "file",
};

export const downloadable = (message: NativeMessage) =>
  !message.deleted &&
  DOWNLOADS.has(message.media ?? "") &&
  (message.media_info?.size ?? 0) <= FILE_LIMIT;

export const spoken = (message: NativeMessage) =>
  !message.deleted && (isVoice(message) || message.media === "video_note");

const kind = (message: NativeMessage) =>
  message.media === "audio"
    ? isVoice(message)
      ? "voice message"
      : "music file"
    : (KINDS[message.media ?? ""] ?? "file");

export type MediaAction = { label: string; message: NativeMessage };

// The sheet's Copy transcript and Download items for a row's sources (ADR 46
// CU1: the app shares, the web keeps Download). With several, each names its
// kind and time.
export function mediaActions(
  messages: NativeMessage[],
  time: (message: NativeMessage) => string,
) {
  const sources = messages.filter(
    (message) => !message.deleted && message.media,
  );
  const label = (verb: string, list: NativeMessage[], item: NativeMessage) =>
    list.length === 1 ? verb : `${verb} · ${kind(item)} ${time(item)}`;
  const files = sources.filter(downloadable);
  const voices = sources.filter(spoken);
  return {
    transcripts: voices.map((message) => ({
      label: label("Copy transcript", voices, message),
      message,
    })),
    downloads: files.map((message) => ({
      label: label("Download", files, message),
      message,
    })),
  };
}

// Download names the file as Telegram Desktop does (the service's header).
export function download(
  account: number,
  peer: string,
  message: NativeMessage,
) {
  const link = document.createElement("a");
  link.href = `${mediaUrl(account, peer, message)}&download=1`;
  link.download = "";
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
}

export async function transcriptText(
  account: number,
  peer: string,
  message: NativeMessage,
) {
  const answer = await chatApi<{ text: string } | null>(
    account,
    peer,
    `/originals/${message.message_id}/transcript`,
  );
  return answer?.text || null;
}

// A size as Telegram shows it: 48 KB, 3.1 MB.
export function fileSize(bytes: number | null | undefined) {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// A document or a music file: its name and size, downloading it (ADR 46 Q9).
export function FileCard({
  account,
  peer,
  message,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
}) {
  const info = message.media_info;
  const music = message.media === "audio";
  const name =
    (music && info?.title
      ? [info.performer, info.title].filter(Boolean).join(" – ")
      : info?.file_name) || (music ? "Music file" : "File");
  const size = fileSize(info?.size);
  const tooLarge = (info?.size ?? 0) > FILE_LIMIT;
  const Icon = music ? Music : FileText;
  const content = (
    <>
      <Icon size={26} aria-hidden />
      <span className="file-text">
        <strong>{name}</strong>
        <small>
          {[size, tooLarge ? "Open in Telegram" : "Download"]
            .filter(Boolean)
            .join(" · ")}
        </small>
      </span>
    </>
  );
  return tooLarge ? (
    <div className="file-card" aria-label={`${name}${size ? `, ${size}` : ""}`}>
      {content}
    </div>
  ) : (
    <a
      className="file-card"
      href={`${mediaUrl(account, peer, message)}&download=1`}
      download=""
      aria-label={`${name}${size ? `, ${size}` : ""}, download`}
    >
      {content}
    </a>
  );
}
