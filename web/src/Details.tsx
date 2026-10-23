import React from "react";
import {
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Forward,
  Import,
  Link2,
  MapPin,
  PencilLine,
  Trash2,
  UserRound,
} from "lucide-react";
import type { NativeMessage } from "./conversation-api";
import type { useVersions } from "./detailData";
import { attachmentCard } from "../../shared/attachments";
import type { ForwardedLine } from "../../shared/forwarding";
import { chipLabel, chipName } from "../../shared/reactions";
import {
  canGoEarlier,
  canGoLater,
  DELETED_NAME,
  editedName,
  versionCounter,
} from "../../shared/versions";
import "./details.css";

// A message's details in the Enloq app's design (ADR 46 CU2).

// Chips under a message: not tappable; adding or removing the owner's own
// starts from the sheet. With several sources each names its message.
export function ReactionChips({ sources }: { sources: NativeMessage[] }) {
  const shown = sources.flatMap((message) =>
    (message.reactions?.chips ?? []).map((chip) => ({ chip, message })),
  );
  if (!shown.length) return null;
  const several = sources.length > 1;
  return (
    <div className="reaction-chips" role="list" aria-label="Reactions">
      {shown.map(({ chip, message }, index) => (
        <span
          key={`${message.message_id}-${index}`}
          role="listitem"
          className={`reaction-chip${chip.mine ? " mine" : ""}`}
          aria-label={`${chipName(chip)}${several ? ` on message ${message.message_id}` : ""}`}
        >
          {chipLabel(chip)}
          {chip.count > 1 && <small>{chip.count}</small>}
        </span>
      ))}
    </div>
  );
}

export function ForwardedLines({ lines }: { lines: ForwardedLine[] }) {
  if (!lines.length) return null;
  return (
    <div className="forwarded-lines">
      {lines.map((line, index) => (
        <small key={index}>
          {line.imported ? (
            <Import size={13} aria-hidden />
          ) : (
            <Forward size={13} aria-hidden />
          )}
          {line.text}
        </small>
      ))}
    </div>
  );
}

const ICONS = {
  poll: BarChart3,
  location: MapPin,
  contact: UserRound,
  link: Link2,
};
const KINDS = {
  poll: "Poll",
  location: "Location",
  contact: "Contact",
  link: "Link",
};

// A poll, a location, a contact or a link preview (ADR 46 Q9). A venue opens
// OpenStreetMap's search for it; a preview opens the message's link.
export function AttachmentCard({ message }: { message: NativeMessage }) {
  const card = attachmentCard(message);
  if (!card) return null;
  const Icon = ICONS[card.kind];
  const target =
    card.kind === "location" && card.place
      ? `https://www.openstreetmap.org/search?query=${encodeURIComponent(card.place)}`
      : card.kind === "link"
        ? card.url
        : null;
  const content = (
    <>
      <Icon size={24} aria-hidden />
      <span className="card-text">
        <small>
          {card.kind === "link" && card.site ? card.site : KINDS[card.kind]}
        </small>
        <strong>{card.title}</strong>
        {card.kind === "poll" &&
          card.options.map((option, index) => (
            <span key={index}>{`• ${option}`}</span>
          ))}
      </span>
    </>
  );
  return target ? (
    <a
      className="attachment-card"
      href={target}
      target="_blank"
      rel="noopener noreferrer"
    >
      {content}
    </a>
  ) : (
    <div className="attachment-card">{content}</div>
  );
}

// In Originals, Telegram's earlier versions of an edited original: Previous,
// "2/3" and Next. Elsewhere, or with none captured, a pencil says it was
// edited (ADR 46 CU2). A deleted message adds a trash (ADR 52 Q7).
export function VersionControls({
  message,
  versions,
  originals,
}: {
  message: NativeMessage;
  versions: ReturnType<typeof useVersions>;
  originals: boolean;
}) {
  const state = { count: versions.count, index: versions.index };
  const trash = message.deleted && (
    <span
      className="edited-mark"
      role="img"
      aria-label={DELETED_NAME}
      title={DELETED_NAME}
    >
      <Trash2 size={13} aria-hidden />
    </span>
  );
  if (originals && versions.count > 0)
    return (
      <>
        <span
          className="version-arrows"
          role="group"
          aria-label={`Versions of message ${message.message_id}`}
        >
          <button
            type="button"
            aria-label="Previous version"
            disabled={versions.busy || !canGoEarlier(state)}
            onClick={versions.earlier}
          >
            <ChevronLeft size={16} aria-hidden />
          </button>
          <small aria-live="polite">{versionCounter(state)}</small>
          <button
            type="button"
            aria-label="Next version"
            disabled={!canGoLater(state)}
            onClick={versions.later}
          >
            <ChevronRight size={16} aria-hidden />
          </button>
        </span>
        {trash}
      </>
    );
  if (!message.native_edited && versions.count === 0) return trash || null;
  const name = editedName(originals ? 0 : versions.count);
  return (
    <>
      <span className="edited-mark" role="img" aria-label={name} title={name}>
        <PencilLine size={13} aria-hidden />
      </span>
      {trash}
    </>
  );
}
