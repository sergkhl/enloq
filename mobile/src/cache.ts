import { Directory, File, Paths } from "expo-file-system";
import { buildCommit } from "./build";

// ADR 38 Q20: what this phone last loaded stays readable when its server
// can't be reached, across restarts. It lives in the cache directory, which
// neither iOS nor Android backs up, ages out under the service's 30-day rule
// and is deleted whenever the phone is paired, forgotten or revoked, with the
// photos saved there for sharing.
const MAX_AGE = 30 * 86_400_000;
// Opened conversations kept; accounts, lists and the reading choice are few.
const MAX_CHATS = 20;
const CHATS = "chats-opened";
// The phone's own words and records, which every build reads. The rest are the
// server's answers, whose shape belongs to the build that kept them (ADR 61):
// a copy from another build is not read, or a screen would draw it, and fail,
// before the server's fresh answer could replace it.
const OWN = /^(draft:|deletion:|push-events$|logs$)/;

type Saved<T> = { saved: number; build?: string; value: T };

const readable = <T>(key: string, saved: Saved<T>) =>
  Date.now() - saved.saved <= MAX_AGE &&
  (OWN.test(key) || (saved.build ?? "") === buildCommit());

const folder = () => new Directory(Paths.cache, "enloq");
export const cacheFolder = (name: string) => new Directory(folder(), name);
const entry = (key: string) =>
  new File(folder(), `${encodeURIComponent(key)}.json`);

export const chatKey = (account: number, peer: string) =>
  `chat:${account}/${peer}`;
// The replied-to messages a chat's quotes resolved (ADR 41), kept and
// dropped with the chat's own copy.
export const repliesKey = (account: number, peer: string) =>
  `replies:${account}/${peer}`;

export async function readCached<T>(key: string): Promise<T | null> {
  try {
    const file = entry(key);
    if (!file.exists) return null;
    const saved = JSON.parse(await file.text()) as Saved<T>;
    if (readable(key, saved)) return saved.value;
    file.delete();
  } catch {
    // An unreadable copy is no copy.
  }
  return null;
}

// For the log, which a crash's handler must extend before anything can wait.
export function readCachedSync<T>(key: string): T | null {
  try {
    const file = entry(key);
    if (!file.exists) return null;
    const saved = JSON.parse(file.textSync()) as Saved<T>;
    if (readable(key, saved)) return saved.value;
  } catch {
    // An unreadable copy is no copy.
  }
  return null;
}

export function writeCached(key: string, value: unknown) {
  try {
    const directory = folder();
    if (!directory.exists)
      directory.create({ intermediates: true, idempotent: true });
    entry(key).write(
      JSON.stringify({ saved: Date.now(), build: buildCommit(), value }),
    );
    if (key.startsWith("chat:")) keepRecent(key);
  } catch {
    // A full disk costs only the offline copy.
  }
}

export function removeCached(key: string) {
  try {
    const file = entry(key);
    if (file.exists) file.delete();
  } catch {
    // Gone already.
  }
}

// The most recently opened conversations stay; older ones are deleted.
function keepRecent(key: string) {
  const index = entry(CHATS);
  let order: string[] = [];
  try {
    if (index.exists) order = JSON.parse(index.textSync()) as string[];
  } catch {
    order = [];
  }
  order = [key, ...order.filter((kept) => kept !== key)];
  for (const old of order.splice(MAX_CHATS)) {
    for (const file of [entry(old), entry(old.replace(/^chat:/, "replies:"))])
      if (file.exists) file.delete();
  }
  index.write(JSON.stringify(order));
}

// Remove local data (ADR 43 Q10): the account's lists, opened chats and
// their Reply Quotes leave the offline copy too.
export function clearAccountCache(account: number) {
  try {
    const directory = folder();
    if (!directory.exists) return;
    for (const item of directory.list()) {
      const key = decodeURIComponent(item.name.replace(/\.json$/, ""));
      if (
        key === `list:${account}` ||
        key === `groups:${account}` ||
        key.startsWith(`chat:${account}/`) ||
        key.startsWith(`replies:${account}/`) ||
        key.startsWith(`draft:${account}/`) ||
        key.startsWith(`deletion:${account}/`)
      )
        item.delete();
    }
  } catch {
    // What could not be read stays until the copy ages out.
  }
}

export function clearCached() {
  try {
    const directory = folder();
    if (directory.exists) directory.delete();
  } catch {
    // Nothing was kept.
  }
}
