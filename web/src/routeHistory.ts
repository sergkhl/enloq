// Browser-history bookkeeping for account and chat routes (ADR 36). Every entry
// this document reaches carries its position, so a refused Back, Forward or
// hash edit returns to the committed entry without rewriting another one.
import { routeHref, type Destination } from "../../shared/chatRoute";

const field = "replyRoute";

// The browser moved to another entry; the application has not followed yet.
export type Arrival = { href: string; index: number };

function here() {
  return location.pathname + location.search + location.hash;
}

function position(state: unknown) {
  const value =
    state && typeof state === "object"
      ? (state as Record<string, unknown>)[field]
      : null;
  return typeof value === "number" && Number.isSafeInteger(value)
    ? value
    : null;
}

function mark(index: number, href?: string) {
  const state = history.state;
  history.replaceState(
    {
      ...(state && typeof state === "object" ? state : {}),
      [field]: index,
    },
    "",
    href,
  );
}

// The entry the browser shows, and the entry the application's state matches.
let shown = position(history.state) ?? 0;
if (position(history.state) === null) mark(shown);
let committed = { href: here(), index: shown };
let pending: Arrival | null = null;
// The arrival just refused, until the browser is back on the committed entry:
// the second event of that same move must not send the browser back twice.
let refused: Arrival | null = null;
// An address that must wait until the browser is back on the committed entry.
let deferred: { destination: Destination; mode: "push" | "replace" } | null =
  null;
// The mounted application. Without one there is no state to disagree with.
let arrive: ((arrival: Arrival) => void) | null = null;

function settle() {
  committed = { href: here(), index: shown };
  pending = null;
  refused = null;
  deferred = null;
}

function write(destination: Destination) {
  const href = routeHref(location.pathname, location.search, destination);
  if (href !== here()) mark(committed.index, href);
  committed.href = href;
}

function enter(destination: Destination) {
  const href = routeHref(location.pathname, location.search, destination);
  if (href === here()) return;
  history.pushState({ [field]: ++shown }, "", href);
  settle();
}

// Publishes the application's destination. Push adds one entry for a
// deliberate change; replace canonicalizes the committed entry.
export function publish(destination: Destination, mode: "push" | "replace") {
  // Away also covers the moment between a refusal and the browser's return.
  if (pending || shown !== committed.index) {
    // Never spell the committed destination into the entry merely arrived at,
    // and never add an entry while the browser is still returning from a
    // refusal: whether it would land before or after that return is timing.
    if (mode === "replace" || refused) {
      // An entry still owed stays owed when its address is then corrected.
      deferred = {
        destination,
        mode: deferred?.mode === "push" ? "push" : mode,
      };
      return;
    }
    // A deliberate destination leaves from the entry the browser is on.
    settle();
  }
  if (mode === "replace") return write(destination);
  enter(destination);
}

// The application follows the browser to this entry.
export function accept(arrival: Arrival) {
  if (pending !== arrival) return false;
  settle();
  return true;
}

// The application stays; the browser returns to the committed entry.
export function refuse(arrival: Arrival) {
  if (pending !== arrival) return;
  pending = null;
  if (shown !== committed.index) {
    refused = arrival;
    history.go(committed.index - shown);
  } else if (here() !== committed.href) mark(committed.index, committed.href);
}

// An owner removes its consumed query input; the entry keeps its position.
export function rewriteQuery(change: (query: URLSearchParams) => void) {
  const url = new URL(location.href);
  change(url.searchParams);
  const href = url.pathname + url.search + url.hash;
  if (href === here()) return;
  const home = shown === committed.index && here() === committed.href;
  history.replaceState(history.state, "", href);
  if (home) committed.href = href;
  else if (pending?.index === shown) pending.href = href;
}

// Back, Forward and in-document hash edits. One arrival covers the popstate
// and hashchange events of the same move. The position is followed for the
// whole life of the document, so it stays true while the connection screen
// has replaced the application.
function moved() {
  const known = position(history.state);
  // A fragment edit makes a new, unmarked entry after the shown one.
  if (known === null) mark(shown + 1);
  shown = known ?? shown + 1;
  // The application, once mounted again, starts from the entry it finds.
  if (!arrive) return settle();
  const href = here();
  if (shown === committed.index && href === committed.href) {
    // The committed entry again: a restoration, or the owner returned.
    pending = null;
    refused = null;
    if (deferred) {
      const { destination, mode } = deferred;
      deferred = null;
      if (mode === "push") enter(destination);
      else write(destination);
    }
    return;
  }
  if (pending?.index === shown && pending.href === href) return;
  if (refused?.index === shown && refused.href === href) return;
  refused = null;
  pending = { href, index: shown };
  arrive(pending);
}
window.addEventListener("popstate", moved);
window.addEventListener("hashchange", moved);

export function watchHistory(consumer: (arrival: Arrival) => void) {
  arrive = consumer;
  return () => {
    if (arrive !== consumer) return;
    arrive = null;
    settle();
  };
}
