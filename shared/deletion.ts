// Message Selection and deletion (ADR 31, ADR 34, ADR 46 CU4): the rules
// both clients share. The service owns accepted intent and progress; a
// client keeps only what the owner reviewed and the identities it submitted.

// The service deletes only originals from the last 30 days.
export const RETENTION = 30 * 24 * 60 * 60;

export type DeletionOutcome =
  "acknowledged" | "failed" | "uncertain" | "unstarted" | "dispatching";

export type DeletionTarget = {
  message_id: number;
  revision: number;
  preview: string;
  media: string | null;
  outgoing: boolean;
  date: number;
  outcome: DeletionOutcome;
  observation: "present" | "absent" | "unavailable" | null;
  checked?: number | null;
  absent_at?: number | null;
  resolved_by?: string | null;
};

export type DeletionOperation = {
  id: string;
  account_id: number;
  peer: string;
  state:
    "queued" | "deleting" | "completed" | "partial" | "uncertain" | "failed";
  created: number;
  retry_of?: string | null;
  reason?: string | null;
  check?: string | null;
  targets: DeletionTarget[];
  counts: {
    total: number;
    acknowledged: number;
    failed: number;
    uncertain: number;
    unstarted: number;
    dispatching: number;
    absent: number;
    resolved: number;
  };
  checkable: boolean;
  retryable: boolean;
  retry_targets: {
    message_id: number;
    revision: number;
    source_fingerprint?: string;
  }[];
};

// What a rule needs of a message.
export type Original = {
  message_id: number;
  revision: number;
  date: number;
  deleted?: boolean;
  service?: boolean;
  outgoing?: boolean;
  text?: string;
  media?: string | null;
  media_info?: { file_name?: string | null; voice?: boolean | null } | null;
  author_peer?: string | null;
  deletion_fingerprint?: string | null;
};

// A current original the service can delete: not deleted, not a service
// message, a Telegram identity, and within the retention window.
export function ordinary(message: Original, now = Date.now() / 1000) {
  return (
    !message.deleted &&
    !message.service &&
    Number.isSafeInteger(message.message_id) &&
    message.message_id > 0 &&
    message.message_id < 2 ** 31 &&
    message.date >= now - RETENTION
  );
}

// Groups delete only originals verified as the owner's (ADR 34).
export function deletable(
  message: Original,
  account: number,
  ownOnly: boolean,
  now = Date.now() / 1000,
) {
  return (
    ordinary(message, now) &&
    (!ownOnly ||
      (message.author_peer === `user:${account}` &&
        /^[a-f0-9]{64}$/.test(message.deletion_fingerprint || "")))
  );
}

export function operationActive(operation: DeletionOperation) {
  return operation.state === "queued" || operation.state === "deleting";
}

// An identity still being deleted, which no new selection may take.
export function targetPending(operation: DeletionOperation, identity: number) {
  return (
    operationActive(operation) &&
    operation.targets.some(
      (target) =>
        target.message_id === identity &&
        target.outcome !== "acknowledged" &&
        !target.absent_at &&
        target.observation !== "absent" &&
        !target.resolved_by,
    )
  );
}

const MEDIA: Record<string, string> = {
  image: "Image",
  audio: "Audio",
  video: "Video",
  video_note: "Video message",
  file: "File",
  document: "File",
  sticker: "Sticker",
  gif: "Animation",
  contact: "Contact",
  location: "Location",
  poll: "Poll",
  link: "Link",
  other: "Attachment",
  unsupported: "Attachment",
};

// A review line's attachment: the file's name, or what it is.
export function mediaLabel(message: Original) {
  if (!message.media) return "";
  if (message.media_info?.file_name) return message.media_info.file_name;
  if (message.media === "audio" && message.media_info?.voice)
    return "Voice note";
  return MEDIA[message.media] ?? "Attachment";
}

export function deletionStatus(operation: DeletionOperation) {
  const counts = operation.counts;
  if (operationActive(operation))
    return `Deleting · ${counts.acknowledged} of ${counts.total} confirmed`;
  if (operation.state === "completed")
    return `Deleted · ${counts.acknowledged} ${counts.acknowledged === 1 ? "message" : "messages"}`;
  if (operation.state === "uncertain") return "Deletion result unconfirmed";
  if (operation.state === "partial") return "Deletion partially completed";
  return "Deletion stopped";
}

// The lines under a card's status, in order.
export function deletionDetails(
  operation: DeletionOperation,
  ownOnly: boolean,
): string[] {
  const counts = operation.counts;
  const lines: string[] = [];
  if (!operationActive(operation) && operation.state !== "completed")
    lines.push(
      `${counts.acknowledged} confirmed · ${counts.uncertain} unconfirmed · ${counts.failed} failed · ${counts.unstarted} not started`,
    );
  if (counts.absent)
    lines.push(
      `${counts.absent} absent in this account; deletion for ${ownOnly ? "everyone" : "both"} unconfirmed.`,
    );
  if (counts.resolved)
    lines.push(`${counts.resolved} resolved by a later confirmed deletion.`);
  if (operation.reason && operation.state !== "completed")
    lines.push(operation.reason);
  if (operation.check) lines.push(operation.check);
  return lines;
}

// Cards show every unfinished operation and the newest completed one,
// newest first.
export function shownOperations(operations: DeletionOperation[]) {
  const newestComplete = operations
    .filter((operation) => operation.state === "completed")
    .sort((a, b) => b.created - a.created)[0]?.id;
  return operations
    .filter(
      (operation) =>
        operation.state !== "completed" || operation.id === newestComplete,
    )
    .sort((a, b) => b.created - a.created);
}

// The request body for the reviewed originals: their exact revisions and,
// in a group, the fingerprint the owner's originals were verified by.
export function deletionRequest(
  id: string,
  messages: Original[],
  ownOnly: boolean,
  retryOf?: string,
) {
  return {
    id,
    targets: messages.map((message) => ({
      message_id: message.message_id,
      revision: message.revision,
      ...(ownOnly ? { source_fingerprint: message.deletion_fingerprint } : {}),
    })),
    ...(retryOf ? { retry_of: retryOf } : {}),
  };
}

// The service's answer must be this conversation's operation for exactly the
// reviewed originals.
export function matchesReview(
  operation: DeletionOperation,
  id: string,
  account: number,
  peer: string,
  messages: Original[],
) {
  if (
    operation.id !== id ||
    operation.account_id !== account ||
    operation.peer !== peer
  )
    return false;
  const membership = new Map(
    operation.targets.map((target) => [target.message_id, target.revision]),
  );
  return (
    membership.size === messages.length &&
    messages.every(
      (message) => membership.get(message.message_id) === message.revision,
    )
  );
}

export const DELETE_LABEL = (ownOnly: boolean) =>
  ownOnly ? "Delete for everyone" : "Delete for both participants";

// A new deletion intent's identity: 32 hex characters, from the platform's
// cryptographic source where it has one.
export function intentIdentity() {
  const bytes = new Uint8Array(16);
  const source = globalThis.crypto;
  if (source?.getRandomValues) source.getRandomValues(bytes);
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
}
