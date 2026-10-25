// Account and chat URLs (ADR 36). The `account` query is the stable Telegram
// user ID; the fragment is Telegram Web A's signed numeric peer. This module
// only parses and serializes: storage, API reads and history belong elsewhere.

// The account's three tabs: private chats, Group Chats (ADR 34) and its own
// Saved Messages (ADR 50).
export type ChatTab = "private" | "group" | "saved" | "reminders";

export type RouteIntent =
  | { kind: "reminders" }
  // The plain application URL: ordinary startup restoration.
  | { kind: "restore" }
  // Ordinary tab/chat restoration within one exact account.
  | { kind: "account"; account: number }
  | { kind: "list"; account: number; tab: ChatTab }
  | { kind: "chat"; account: number; peer: string; occurrence?: string }
  // A chat fragment without its account: the owner chooses one.
  | { kind: "choose"; peer: string }
  | { kind: "invalid" };

export type Destination =
  | { kind: "reminders" }
  | { kind: "list"; account: number; tab: ChatTab }
  | { kind: "chat"; account: number; peer: string; occurrence?: string };

// Supergroups are spelled -(10^12 + id); basic groups stay below that base.
const supergroupBase = 10n ** 12n;
const largestIdentity = 2n ** 63n - 1n;
const canonical = /^[1-9]\d{0,18}$/;
const tabs: Record<string, ChatTab> = {
  private: "private",
  groups: "group",
  saved: "saved",
  reminders: "reminders",
};

function identity(digits: string) {
  if (!canonical.test(digits)) return null;
  const value = BigInt(digits);
  return value <= largestIdentity ? value : null;
}

// The signed fragment for a typed native peer, without the leading "#".
export function peerFragment(peer: string): string | null {
  const [type, digits, extra] = peer.split(":");
  const value = extra === undefined ? identity(digits ?? "") : null;
  if (value === null) return null;
  if (type === "user") return String(value);
  // A basic-group ID at or above the base would read back as a supergroup.
  if (type === "chat") return value < supergroupBase ? `-${value}` : null;
  if (type === "channel")
    return value <= largestIdentity - supergroupBase
      ? `-${supergroupBase + value}`
      : null;
  return null;
}

export function fragmentPeer(fragment: string): string | null {
  const negative = fragment.startsWith("-");
  const value = identity(negative ? fragment.slice(1) : fragment);
  if (value === null) return null;
  if (!negative) return `user:${value}`;
  if (value < supergroupBase) return `chat:${value}`;
  // Exactly the base would be supergroup zero, which no conversation has.
  return value === supergroupBase ? null : `channel:${value - supergroupBase}`;
}

// A chat fragment determines its tab.
export function peerTab(peer: string): ChatTab {
  return peer.startsWith("user:") ? "private" : "group";
}

function accountIdentity(value: string) {
  const account = canonical.test(value) ? Number(value) : 0;
  return Number.isSafeInteger(account) && account > 0 ? account : null;
}

// `search` and `hash` as the browser reports them. Other query input, such as
// the pairing and notification locators, belongs to its own owner.
export function parseRoute(search: string, hash: string): RouteIntent {
  const query = new URLSearchParams(search);
  const accounts = query.getAll("account");
  const requestedTabs = query.getAll("tab");
  if (accounts.length > 1 || requestedTabs.length > 1)
    return { kind: "invalid" };
  const [accountText] = accounts;
  const [tabText] = requestedTabs;
  const occurrences = query.getAll("reminder");
  if (
    occurrences.length > 1 ||
    (occurrences[0] && !/^[a-f0-9]{32}$/.test(occurrences[0]))
  )
    return { kind: "invalid" };
  const account =
    accountText === undefined ? undefined : accountIdentity(accountText);
  // Own names only: "constructor" is not a tab.
  const tab =
    tabText === undefined
      ? undefined
      : Object.hasOwn(tabs, tabText)
        ? (tabs[tabText] ?? null)
        : null;
  if (account === null || tab === null) return { kind: "invalid" };
  const fragment = hash.startsWith("#") ? hash.slice(1) : hash;
  if (fragment) {
    const peer = fragmentPeer(fragment);
    if (!peer) return { kind: "invalid" };
    // A valid tab beside a fragment is redundant and cannot redirect the peer.
    if (occurrences[0] && !account) return { kind: "invalid" };
    return account
      ? {
          kind: "chat",
          account,
          peer,
          ...(occurrences[0] ? { occurrence: occurrences[0] } : {}),
        }
      : { kind: "choose", peer };
  }
  if (occurrences.length) return { kind: "invalid" };
  if (tab === "reminders") return { kind: "reminders" };
  if (!account) return tab ? { kind: "invalid" } : { kind: "restore" };
  return tab ? { kind: "list", account, tab } : { kind: "account", account };
}

function chatHash(peer: string) {
  const fragment = peerFragment(peer);
  if (!fragment) throw new Error("This conversation has no link.");
  return `#${fragment}`;
}

// The address-bar spelling of a destination: canonical route fields first,
// then any separately owned query input exactly as it was.
export function routeHref(
  pathname: string,
  search: string,
  destination: Destination,
): string {
  const query = new URLSearchParams();
  if (destination.kind !== "reminders")
    query.set("account", String(destination.account));
  else query.set("tab", "reminders");
  if (destination.kind === "list")
    query.set("tab", destination.tab === "group" ? "groups" : destination.tab);
  if (destination.kind === "chat" && destination.occurrence)
    query.set("reminder", destination.occurrence);
  for (const [name, value] of new URLSearchParams(search))
    if (name !== "account" && name !== "tab" && name !== "reminder")
      query.append(name, value);
  return `${pathname}?${query}${destination.kind === "chat" ? chatHash(destination.peer) : ""}`;
}

// Copy chat link: only the exact account and chat, from this app's origin.
export function chatLink(
  origin: string,
  pathname: string,
  account: number,
  peer: string,
): string | null {
  const fragment = peerFragment(peer);
  return fragment && Number.isSafeInteger(account) && account > 0
    ? `${origin}${pathname}?account=${account}#${fragment}`
    : null;
}
