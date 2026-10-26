import React, { useEffect, useRef } from "react";
import {
  BellOff,
  Bookmark,
  Check,
  ChevronDown,
  Clock,
  Settings,
  SquarePen,
  Trash2,
  User,
  Users,
} from "lucide-react";
import { Avatar } from "./Avatar";
import type { Account, Overview } from "./accounts";
import type { Chat } from "./conversation-api";
import type { ChatTab } from "../../shared/chatRoute";
import { previewTime } from "../../shared/messageTime";
import "./list.css";
import { NotificationAsk } from "./NotificationAsk";
import { RetainedGroups } from "./RetainedGroups";

const EMPTY: Record<ChatTab, string> = {
  private: "No private conversations in the last seven days.",
  group: "No groups in the last seven days.",
  saved: "Saved Messages is unavailable.",
  reminders: "No reminders.",
};

// The three tabs, as icons in the header (ADR 50 Q1, Q9, Q10).
const TABS = [
  { tab: "private", label: "Private", Icon: User },
  { tab: "group", label: "Groups", Icon: Users },
  { tab: "saved", label: "Saved", Icon: Bookmark },
  { tab: "reminders", label: "Reminders", Icon: Clock },
] as const;

// The list screen in the Enloq app's design (ADR 44 Q1, MW1): one header row
// with the account switcher and its sheet, the Private | Groups | Saved icons
// and the gear (ADR 50), then each account's seven-day rows, or its own Saved
// Messages in the third tab.
export function ListScreen({
  overview,
  account,
  tab,
  chats,
  loaded,
  view,
  error,
  sheet,
  onSheet,
  onSelectAccount,
  onTab,
  onOpen,
  onManage,
  onAdd,
  onSettings,
  onNewMessage,
  saved,
  reminders,
}: {
  overview: Overview | null;
  account?: Account;
  tab: ChatTab;
  chats: Chat[];
  loaded: boolean;
  view: "ai" | "originals";
  error: string;
  sheet: boolean;
  onSheet: (open: boolean) => void;
  onSelectAccount: (id: number) => void;
  onTab: (tab: ChatTab) => void;
  onOpen: (peer: string) => void;
  onManage: () => void;
  onAdd: () => void;
  onSettings: () => void;
  // The pencil on the Private tab opens New Message (ADR 51 Q15, Q19).
  onNewMessage: () => void;
  // The account's Saved Messages, shown in place of a list (ADR 50 Q2).
  saved?: React.ReactNode;
  reminders?: React.ReactNode;
}) {
  const signedIn = (overview?.accounts || []).filter(
    (a) => a.state === "authorized",
  );
  const shown = account?.state === "authorized" ? account : undefined;
  return (
    <section className="list-screen" aria-label="Conversations">
      {overview && <NotificationAsk />}
      <header className="list-header">
        <button
          type="button"
          className="account-switcher"
          aria-label={
            shown ? `Account: ${shown.name}. Switch account` : "Accounts"
          }
          aria-haspopup="dialog"
          disabled={!overview}
          onClick={() => onSheet(true)}
        >
          {/* Reminders are the member's, across accounts (ADR 58 Q7). */}
          {shown && tab !== "reminders" && (
            <Avatar
              account={shown.account_id}
              peer={`user:${shown.account_id}`}
              name={shown.name}
              photoId={shown.photo_id}
              className="switcher-avatar"
            />
          )}
          <span className="switcher-name">
            {tab === "reminders" ? "All accounts" : shown?.name || "Enloq"}
          </span>
          {/* The sheet holds Settings too, so it always has more to show
              (ADR 51 Q21). */}
          <ChevronDown size={18} aria-hidden />
        </button>
        {!(overview && signedIn.length === 0) && (
          <div className="list-switch" role="tablist" aria-label="Lists">
            {TABS.map(({ tab: option, label, Icon }) => (
              <button
                key={option}
                type="button"
                role="tab"
                aria-label={label}
                title={label}
                aria-selected={tab === option}
                disabled={!shown && option !== "reminders"}
                onClick={() => tab !== option && onTab(option)}
              >
                <Icon
                  size={18}
                  aria-hidden
                  fill={
                    tab === option && option !== "reminders"
                      ? "currentColor"
                      : "none"
                  }
                />
              </button>
            ))}
          </div>
        )}
        {/* New Message takes the gear's place on Private; Groups and Saved
            keep the place empty, so the icons never move (ADR 51 Q15, Q19,
            Q20). Settings is in the account sheet. */}
        {tab === "private" && shown ? (
          <button
            type="button"
            className="header-icon"
            aria-label="New message"
            onClick={onNewMessage}
          >
            <SquarePen size={24} aria-hidden />
          </button>
        ) : (
          <span className="header-icon" aria-hidden />
        )}
      </header>
      {error && (
        <p className="list-error" role="alert">
          {error}
        </p>
      )}
      {tab === "reminders" ? (
        reminders
      ) : overview && signedIn.length === 0 ? (
        <div className="list-empty">
          <p>No Telegram account is signed in on this server.</p>
          <button
            type="button"
            className="primary"
            disabled={!overview.login_available}
            onClick={onAdd}
          >
            Add account
          </button>
        </div>
      ) : tab === "saved" ? (
        (saved ?? (
          <p className="list-note">
            {loaded ? EMPTY.saved : "Loading Saved Messages…"}
          </p>
        ))
      ) : (
        <>
          <nav className="chat-list" aria-label="Recent chats">
            {chats.map((chat) => (
              <ChatRow
                key={chat.key}
                chat={chat}
                account={shown!.account_id}
                view={view}
                timezone={overview?.timezone || "UTC"}
                onOpen={onOpen}
              />
            ))}
            {!chats.length && (
              <p className="list-note">
                {loaded ? EMPTY[tab] : "Loading conversations…"}
              </p>
            )}
          </nav>
          {/* Retained group work at the foot of Groups (ADR 46 CU6). */}
          {tab === "group" && shown && (
            <RetainedGroups
              account={shown.account_id}
              version={chats}
              onOpen={onOpen}
            />
          )}
        </>
      )}
      {sheet && overview && (
        <AccountSheet
          accounts={signedIn}
          current={shown?.account_id ?? 0}
          onSelect={(id) => {
            onSheet(false);
            onSelectAccount(id);
          }}
          onManage={() => {
            onSheet(false);
            onManage();
          }}
          onSettings={() => {
            onSheet(false);
            onSettings();
          }}
          onClose={() => onSheet(false)}
        />
      )}
    </section>
  );
}

function ChatRow({
  chat,
  account,
  view,
  timezone,
  onOpen,
}: {
  chat: Chat;
  account: number;
  view: "ai" | "originals";
  timezone: string;
  onOpen: (peer: string) => void;
}) {
  const preview =
    (view === "ai" ? chat.preview?.ai : chat.preview?.original) || "";
  return (
    <button
      type="button"
      className="list-row"
      data-chat-peer={chat.peer}
      aria-label={`${chat.title}${chat.unseen ? ", new messages" : ""}${chat.muted ? ", muted" : ""}`}
      onClick={() => onOpen(chat.peer)}
    >
      <Avatar
        account={account}
        peer={chat.peer}
        name={chat.title}
        photoId={chat.photo_id}
        className="row-avatar"
      />
      <span className="row-body">
        <span className="row-line">
          <strong>{chat.title}</strong>
          {chat.muted && <BellOff size={14} aria-hidden />}
          <time
            dateTime={
              chat.preview?.date
                ? new Date(chat.preview.date * 1000).toISOString()
                : undefined
            }
          >
            {previewTime(chat.preview?.date, timezone)}
          </time>
        </span>
        <span className="row-line">
          {/* A deleted newest message, by what it last said (ADR 52 Q10). */}
          {chat.preview?.deleted && (
            <Trash2
              className="row-deleted"
              size={14}
              aria-label="Deleted message"
            />
          )}
          <span className="row-preview">{preview}</span>
          {chat.unseen && <span className="row-dot" aria-hidden />}
        </span>
      </span>
    </button>
  );
}

// The accounts to switch between and Manage accounts, as a sheet over the
// list (ADR 45 Q4 keeps the trusted list and Disconnect in Settings).
function AccountSheet({
  accounts,
  current,
  onSelect,
  onManage,
  onSettings,
  onClose,
}: {
  accounts: Account[];
  current: number;
  onSelect: (id: number) => void;
  onManage: () => void;
  onSettings: () => void;
  onClose: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    panel.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="sheet-scrim" onClick={onClose}>
      <div
        ref={panel}
        className="sheet account-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="Accounts"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <h2>Accounts</h2>
        {accounts.map((a) => (
          <button
            key={a.account_id}
            type="button"
            className="sheet-row"
            data-account={a.account_id}
            aria-label={`${a.name || `Account ${a.account_id}`}${a.ai_enabled ? "" : ", AI off"}`}
            aria-current={a.account_id === current ? "true" : undefined}
            onClick={() => onSelect(a.account_id)}
          >
            <Avatar
              account={a.account_id}
              peer={`user:${a.account_id}`}
              name={a.name}
              photoId={a.photo_id}
              className="sheet-avatar"
            />
            <span className="sheet-row-body">
              <strong>{a.name || `Account ${a.account_id}`}</strong>
              {!a.ai_enabled && <small>AI off</small>}
            </span>
            {a.account_id === current && <Check size={20} aria-hidden />}
          </button>
        ))}
        <button type="button" className="sheet-row action" onClick={onManage}>
          <Users size={20} aria-hidden />
          <span className="sheet-row-body">
            <strong>Manage accounts</strong>
          </span>
        </button>
        {/* Settings left the header for the sheet's last row (ADR 51 Q19,
            Q21). */}
        <hr className="sheet-divider" />
        <button type="button" className="sheet-row action" onClick={onSettings}>
          <Settings size={20} aria-hidden />
          <span className="sheet-row-body">
            <strong>Settings</strong>
          </span>
        </button>
      </div>
    </div>
  );
}
