// Processing Guidance's owner-editable fields (ADR 15, ADR 25, ADR 34, ADR 49,
// ADR 53, ADR 55, ADR 56), as both clients' Settings group them (ADR 45):
// each with a line saying what it shapes, since neither client has Help (Q7).
export type GuidanceField = {
  name: string;
  label: string;
  explanation: string;
};

export const GUIDANCE_LIMIT = 4000;

export const GUIDANCE_GROUPS: { title: string; fields: GuidanceField[] }[] = [
  {
    title: "Rewriting",
    fields: [
      {
        name: "faithfulness",
        label: "Faithfulness rules",
        explanation:
          "Keeps facts, requests, refusals and conditions in incoming rewrites.",
      },
      {
        name: "incoming",
        label: "Incoming rewriting",
        explanation: "How incoming messages are rewritten for reading.",
      },
      {
        name: "calming",
        label: "Soften hostile messages",
        explanation:
          "How hostile wording is softened, keeping facts and threats. Anger alone is left unchanged.",
      },
    ],
  },
  {
    title: "Suggestion styles",
    fields: [
      {
        name: "tone_professional",
        label: "Professional",
        explanation:
          "Professional wording for manual and automatic suggestions.",
      },
      {
        name: "tone_friendly",
        label: "Friendly",
        explanation: "Friendly wording for manual and automatic suggestions.",
      },
    ],
  },
  {
    title: "Other work",
    fields: [
      {
        name: "emotion",
        label: "Emotion extraction",
        explanation: "How the emotion behind incoming messages is estimated.",
      },
      {
        name: "memory",
        label: "Person memory",
        explanation: "What Enloq notes about what each person cares about.",
      },
      {
        name: "account_memory",
        label: "Account memory",
        explanation: "What Enloq notes about what you care about.",
      },
      {
        name: "suggestions",
        label: "Response suggestions",
        explanation: "How the short reply suggestions are written.",
      },
      {
        name: "idioms",
        label: "Idioms",
        explanation: "How suggestions are written in chats set to Idioms.",
      },
      {
        name: "usefulness",
        label: "When to suggest",
        explanation: "When short reply suggestions are worth offering.",
      },
      {
        name: "group_briefing",
        label: "Group briefings",
        explanation: "How group briefings summarize a day, with sources.",
      },
    ],
  },
];

export const GUIDANCE_FIELDS = GUIDANCE_GROUPS.flatMap((group) => group.fields);

// A paused role (ADR 30): its name, and the fields its Reset guidance restores.
// Emotion is a separate decision, so a rewrite role's reset keeps it.
export const PAUSED_ROLES: Record<string, { label: string; fields: string[] }> =
  {
    incoming_rewrite: {
      label: "Incoming rewrites",
      fields: ["faithfulness", "incoming", "calming"],
    },
    chat_memory: {
      label: "Memory updates",
      fields: ["memory", "account_memory"],
    },
  };
