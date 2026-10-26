import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import type { DeletionOperation } from "../../shared/deletion";

export type MediaKind = NonNullable<components["schemas"]["Message"]["media"]>;
export type MediaInfo = components["schemas"]["MediaInfo"];
export type SelectedMedia = components["schemas"]["SelectedMediaView"];
export type ViewedContent = components["schemas"]["ViewedContent"];
export type ReactionView = components["schemas"]["ReactionView"];
export type NativeMessage = {
  message_id: number;
  revision: number;
  deletion_fingerprint?: string;
  date: number;
  text: string;
  author?: string;
  author_peer?: string | null;
  // A group author's current photo, for their avatar (ADR 51 Q13).
  author_photo_id?: string | null;
  // Telegram's album (`grouped_id`), drawn as one grid (ADR 51 Q12).
  album?: string | null;
  author_signature?: string | null;
  sender_is_bot?: boolean | null;
  group_filter?: { ad: boolean; ignored_bot: boolean; allowed_bot?: boolean };
  links?: { offset: number; length: number; url: string }[];
  history_count?: number;
  viewed_content?: ViewedContent | null;
  native_edited?: boolean;
  forwarded?: boolean | null;
  forwarded_from?: string | null;
  forward_date?: number | null;
  forward_signature?: string | null;
  imported?: boolean | null;
  forward_read?: boolean;
  attachment_id?: string | null;
  outgoing: boolean;
  deleted: boolean;
  // A deleted message's content is what it last said (ADR 52 Q7): the
  // version captured at its deletion, and whether Enloq still holds its media.
  deleted_version?: number | null;
  media_available?: boolean | null;
  media: MediaKind | null;
  media_info: MediaInfo | null;
  reply_to: number | null;
  // ADR 41: the reply header's own details (see shared/replyQuote.ts).
  reply_quote?: string | null;
  reply_elsewhere?: components["schemas"]["ReplyElsewhere"] | null;
  reply_story?: boolean;
  reply_read?: boolean;
  service?: boolean;
  reactions?: ReactionView | null;
};
export type Unanalysed = { id: number; kind: string; reason?: string };
// A saved submission as the service reports it (ADR 26); no local file paths.
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
  partial?: boolean;
  error?: string | null;
};
export type ReplyHold = "pending" | "unresolved" | "deleted";
// Deletion operations as the service reports them (ADR 31, ADR 46 CU4).
export type { DeletionOperation, DeletionTarget } from "../../shared/deletion";
export type Chat = {
  key: string;
  peer: string;
  kind?: components["schemas"]["Chat"]["kind"];
  title: string;
  muted: boolean;
  archived: boolean;
  ai_eligible: boolean;
  photo_id: string | null;
  read_outbox?: number;
  processing?: number;
  unseen?: boolean;
  opening_source?: number | null;
  preview?: components["schemas"]["ConversationPreview"];
};
export type GroupProcessing = {
  processing_enabled: boolean;
  account_processing_enabled: boolean;
  effective: boolean;
  access: "unknown" | "available" | "lost";
  supported: boolean;
  reason: string;
};
export type RetainedGroup = {
  chat: Chat;
  reasons: (
    "draft" | "selected_media" | "send" | "deletion" | "cancelled_send"
  )[];
  draft: boolean;
  selected_media: boolean;
  send_count: number;
  deletion_count: number;
  cancelled_count: number;
};
export type DailyBriefing = {
  day: string;
  start: number;
  end: number;
  source_ids: number[];
  covered_ids: number[];
  uncovered_ids: number[];
  partial: boolean;
  gap: boolean;
  cached: boolean;
  omissions: { message_id: number; reason: string }[];
  intake_coverage: { native_omitted: number; range_complete: boolean };
  final: {
    id: string;
    text: string;
    outcome: "briefing" | "no_substance";
    local_outcome?: "ads_skipped" | "bots_skipped" | null;
    topics: {
      text: string;
      citations: number[];
      title?: string | null;
      participants?: { peer: string; name: string }[];
      participant_source_ids?: number[];
    }[];
    excluded_ids?: number[];
    ad_ids?: number[];
    covered_ids: number[];
    covered_start: number;
    covered_end: number;
  } | null;
  attempt: { id: string; state: string; error: string | null } | null;
  stream: { attempt: string; text: string } | null;
  analyzing: boolean;
  retry: { attempt: string; enabled: boolean; reason: string | null } | null;
};
export type ConversationData = {
  // The Reminder Bar's pending occurrences (ADR 58 Q13).
  reminders?: components["schemas"]["PendingReminder"][];
  briefing_update?: {
    enabled: boolean;
    status: string;
    reason: string | null;
    day: string;
    attempt?: string;
    transient?: boolean;
  } | null;
  timezone: string;
  retained?: boolean;
  opening_source?: number | null;
  // Ready unseen messages, earliest first; reactions place no separator.
  unread_sources?: number[];
  emotion?: components["schemas"]["EmotionView"] | null;
  suggestions?: components["schemas"]["SuggestionsView"] | null;
  group_processing?: GroupProcessing | null;
  // ADR 53: whether this private chat calms frustrated or angry messages.
  calming?: boolean | null;
  briefings?: DailyBriefing[];
  chat: Chat;
  messages: NativeMessage[];
  draft: Draft;
  operations: Operation[];
  deletions?: DeletionOperation[];
  reply_holds?: Record<number, ReplyHold>;
  blocks: {
    id: string;
    revision: string;
    source_revisions?: Record<number, number> | null;
    sources: number[];
    rendered: string | null;
    rendered_revision: string | null;
    stream?: { attempt: string; text: string; revision: string } | null;
    state: string;
    viewed_content?: ViewedContent | null;
    error: string | null;
    unanalysed: Unanalysed[];
    stage: string | null;
    // ADR 53: its rewrite was calmed (Q5).
    calmed?: boolean;
  }[];
  // A chat's current run; explicit while it includes the older-message run.
  progress: { done: number; total: number; explicit: boolean } | null;
  older: {
    available: number;
    notice: { remaining: number; reason: string } | null;
  };
};
export function chatApi<T>(
  account: number,
  peer: string,
  suffix = "",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return workspaceApi<T>(
    `accounts/${account}/chats/${encodeURIComponent(peer)}${suffix}`,
    body,
    signal,
  );
}
