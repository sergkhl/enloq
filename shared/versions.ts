// Telegram's earlier versions of one original (ADR 19, ADR 46 CU2), the
// same in the web and the Enloq app. Versions come newest first; the current
// message is the last of `count + 1`.

export type VersionState = {
  // Captured earlier versions in the last 30 days.
  count: number;
  // The shown version's place in the newest-first list, or -1 for the
  // current message.
  index: number;
};

// "3/4": which version shows, oldest first.
export function versionCounter({ count, index }: VersionState) {
  const shown = index < 0 ? count + 1 : Math.max(1, count - index);
  return `${shown}/${count + 1}`;
}

export function canGoEarlier({ count, index }: VersionState) {
  return count > 0 && index < count - 1;
}

export function canGoLater({ index }: VersionState) {
  return index >= 0;
}

// The pencil's name: Originals shows captured versions with arrows instead,
// and AI reading points there.
export function editedName(count: number) {
  return count > 0
    ? "Edited in Telegram; versions are available in Originals"
    : "Edited in Telegram; no earlier version captured";
}

// A deleted message's trash, where the pencil goes; its bubble shows what it
// last said (ADR 52 Q7).
export const DELETED_NAME = "Deleted in Telegram";
