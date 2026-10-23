// The Unread Separator's message (ADR 28): the earliest captured message that
// is still unseen. A deleted capture no longer is, so the next one takes its
// place; a capture that is not loaded shows no separator rather than marking a
// later message, unless a group's briefing stands for it.
export function unreadAnchor(
  captured: number[],
  messages: { message_id: number; deleted: boolean }[],
  covered: number[] = [],
) {
  for (const identity of captured) {
    const message = messages.find((m) => m.message_id === identity);
    if (!message) return covered.includes(identity) ? identity : null;
    if (!message.deleted) return identity;
  }
  return null;
}
