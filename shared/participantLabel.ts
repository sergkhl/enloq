// Who wrote a message, as its header names them. An outgoing flag alone does
// not identify a group author: Telegram can mark anonymous and channel-authored
// posts outgoing. Only an exact own-user peer earns You. Native names and
// signatures retain the evidence for everyone else.
export type Participant = {
  outgoing: boolean;
  author?: string | null;
  author_peer?: string | null;
  author_signature?: string | null;
};

export function participantLabel(
  message: Participant,
  name: string,
  account: number,
  group = false,
) {
  if (!group) return message.outgoing ? "You" : name;
  const author =
    message.author_peer === `user:${account}`
      ? "You"
      : message.author?.trim() || "Unknown participant";
  return message.author_signature?.trim()
    ? `${author} · ${message.author_signature.trim()}`
    : author;
}
