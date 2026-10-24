// Shared styles for manual and automatic suggestions (ADR 56).
export type SuggestionStyle = "friendly" | "professional" | "idioms";
export const SUGGESTION_STYLES: {
  value: SuggestionStyle;
  label: string;
  explanation: string;
}[] = [
  {
    value: "friendly",
    label: "Friendly",
    explanation: "Warm, natural wording without forced slang or emojis.",
  },
  {
    value: "professional",
    label: "Professional",
    explanation: "Clear, respectful and businesslike wording.",
  },
  {
    value: "idioms",
    label: "Idioms",
    explanation:
      "Idioms, proverbs, set expressions and phrasal verbs in this chat's language.",
  },
];
