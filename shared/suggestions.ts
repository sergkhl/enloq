// A private chat's Suggestions choice in both clients' Correspondent Profile
// (ADR 56 Q12 A): Off · On · Idioms, On by default, with a line saying what
// the chosen one does, since neither client has Help (ADR 45 Q7).
export type SuggestionChoice = "off" | "on" | "idioms";

export const SUGGESTION_CHOICES: {
  value: SuggestionChoice;
  label: string;
  explanation: string;
}[] = [
  {
    value: "off",
    label: "Off",
    explanation: "No reply suggestions in this chat.",
  },
  {
    value: "on",
    label: "On",
    explanation:
      "Short reply suggestions appear above the composer when a message is emotional or calls for a reply.",
  },
  {
    value: "idioms",
    label: "Idioms",
    explanation:
      "Suggestions use idioms, proverbs, set expressions and phrasal verbs in this chat's language.",
  },
];

// The choices a chat offers: Idioms only while the service allows it, as it
// has since the owner launched it (Q17, Q20).
export function suggestionChoices(idioms: boolean) {
  return SUGGESTION_CHOICES.filter(
    (choice) => idioms || choice.value !== "idioms",
  );
}
