// Native reactions under a message and the sheet's reaction row (ADR 46
// CU2), the same in the web and the Enloq app.

export type Reaction = {
  emoji: string | null;
  custom: string | null;
  paid: boolean;
};

export type ReactionChip = {
  reaction: Reaction;
  count: number;
  // Whether the owner gave it, when Telegram says.
  mine: boolean | null;
  peer: boolean | null;
};

export type ReactionView = { chips: ReactionChip[]; own_known: boolean };

// The account's selectable ordinary emoji and how many one message may carry.
export type ReactionOptions = { emoji: string[]; limit: number };

// The row shows this many of the palette before + opens the rest.
export const QUICK_REACTIONS = 6;

// ⭐ stands for a paid reaction and ◇ for a custom emoji.
export function chipLabel(chip: ReactionChip) {
  return chip.reaction.emoji || (chip.reaction.paid ? "⭐" : "◇");
}

export function chipName(chip: ReactionChip) {
  const kind =
    chip.reaction.emoji ||
    (chip.reaction.paid ? "Paid reaction" : "Custom reaction");
  return `${kind} ${chip.count}${chip.mine ? ", including yours" : ""}`;
}

// The ordinary emoji the owner gave a message.
export function ownReactions(view: ReactionView | null | undefined) {
  return (view?.chips ?? [])
    .filter((chip) => chip.mine && chip.reaction.emoji)
    .map((chip) => chip.reaction.emoji!);
}

// The owner's own first, then the palette's first six, or all of it once +
// was chosen.
export function quickReactions(
  palette: string[],
  own: string[],
  expanded: boolean,
) {
  return Array.from(
    new Set([
      ...own,
      ...palette.slice(0, expanded ? palette.length : QUICK_REACTIONS),
    ]),
  );
}

// Choosing an emoji the owner already gave removes it.
export function reactionChoice(
  view: ReactionView | null | undefined,
  emoji: string,
) {
  return { emoji, selected: !ownReactions(view).includes(emoji) };
}
