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

// Square-bracketed draft text instructs Suggestions and is never message text
// (ADR 25); the service's rule (`domain/suggestions.py`) is the same.
const BRACKETED = /\[[^[\]]*[^\s[\]][^[\]]*\]/;

export function hasBracketed(text: string) {
  return BRACKETED.test(text);
}

// Send asks first while the draft still holds bracketed instructions.
export const SEND_BRACKETED = {
  title: "Send with brackets?",
  message:
    "This draft still has [bracketed] text, which Suggestions reads as instructions. Send it as written?",
  cancel: "Keep editing",
  confirm: "Send",
};
