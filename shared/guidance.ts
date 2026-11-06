// Processing Guidance's owner-editable fields (ADR 15, ADR 25, ADR 34, ADR 49,
// ADR 53, ADR 55, ADR 56), each with a line saying what it shapes, since
// neither client has Help (ADR 45 Q7). They stay server-wide, and each sits
// on the AI Menu page of the control it shapes, marked for all chats (ADR 62
// Q11, Q17).
import type { AiControl } from "./aiMenu";

export type GuidanceField = {
  name: string;
  label: string;
  explanation: string;
};

export const GUIDANCE_LIMIT = 4000;

export const GUIDANCE_FIELDS: GuidanceField[] = [
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
  {
    name: "tone_professional",
    label: "Professional",
    explanation: "Professional wording for manual and automatic suggestions.",
  },
  {
    name: "tone_friendly",
    label: "Friendly",
    explanation: "Friendly wording for manual and automatic suggestions.",
  },
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
];

// Each control's fields, in the order its page lists them (ADR 62 Q17).
// Faithfulness rules governs both rewrites, so two pages hold it.
export const CONTROL_GUIDANCE: Partial<Record<AiControl, string[]>> = {
  suggestions: ["usefulness"],
  style: ["suggestions", "tone_friendly", "tone_professional", "idioms"],
  emotion: ["emotion"],
  calming: ["calming", "faithfulness"],
  media: ["incoming", "faithfulness"],
  remember: ["memory", "account_memory"],
  group: ["group_briefing"],
};

export function controlGuidance(control: AiControl): GuidanceField[] {
  return (CONTROL_GUIDANCE[control] ?? []).map((name) =>
    GUIDANCE_FIELDS.find((field) => field.name === name)!,
  );
}

// A field on a page: whether it was edited, marked for all chats.
export function guidanceDetail(edited: boolean) {
  return edited ? "Edited, for all chats" : "For all chats";
}

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
