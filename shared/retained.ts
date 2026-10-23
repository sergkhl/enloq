// Group recovery (ADR 34, ADR 46 CU6): the rules both clients share. A
// group past the list's seven days keeps its unfinished work reachable,
// read-only, and a send proven unsent after access was lost resumes only
// after an exact review.

export type RetainedGroup = {
  chat: { key: string; peer: string; title: string };
  draft: boolean;
  selected_media: boolean;
  send_count: number;
  deletion_count: number;
  cancelled_count: number;
};

const count = (value: number, one: string, many: string) =>
  `${value} ${value === 1 ? one : many}`;

// A retained group's reason line, such as "Saved draft · 1 unresolved send".
export function retainedLine(entry: RetainedGroup) {
  return [
    entry.draft || entry.selected_media ? "Saved draft" : "",
    entry.send_count
      ? count(entry.send_count, "unresolved send", "unresolved sends")
      : "",
    entry.deletion_count
      ? count(
          entry.deletion_count,
          "unresolved deletion",
          "unresolved deletions",
        )
      : "",
    entry.cancelled_count
      ? count(
          entry.cancelled_count,
          "saved cancelled send",
          "saved cancelled sends",
        )
      : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export type ResumeReview<Operation extends { id: string }> = {
  token: string;
  expires: number;
  account_id: number;
  peer: string;
  title: string;
  operation: Operation;
};

// The review must be this paused send's, with time left on its token.
export function reviewFits<Operation extends { id: string }>(
  review: ResumeReview<Operation>,
  account: number,
  peer: string,
  operation: string,
  now = Date.now(),
) {
  return (
    review.account_id === account &&
    review.peer === peer &&
    review.operation.id === operation &&
    /^[a-f0-9]{64}$/.test(review.token) &&
    review.expires * 1000 > now
  );
}

// "Resume within 1:58" while the token lasts.
export function resumeWithin(expires: number, now = Date.now()) {
  const left = Math.max(0, Math.ceil(expires - now / 1000));
  return `Resume within ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}
