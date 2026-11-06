// A chat's AI Menu (ADR 62), the same in the web and the Enloq app: its rows
// with their one-line explanations (Q15), each control's page (Q16), the notes
// saying why nothing runs (Q14) and the robot's accessible name.

import type { SuggestionStyle } from "./suggestions";

// The conversation snapshot's `ai` object (the service's ChatAi).
export type ChatAi = {
  account: boolean;
  running: boolean;
  suggestions?: boolean | null;
  style?: SuggestionStyle | null;
  emotion?: boolean | null;
  calming?: boolean | null;
  media?: boolean | null;
  remember?: boolean | null;
  server_suggestions?: boolean | null;
  server_memory?: boolean | null;
};

// The controls with a page: AI view and a private chat's controls, in the
// menu's order, and a group's Group AI processing.
export type AiControl =
  | "view"
  | "suggestions"
  | "style"
  | "emotion"
  | "calming"
  | "media"
  | "remember"
  | "group";

// A private chat's rows after AI view (Q15): five switches, all Off by
// default, and Style.
export const PRIVATE_CONTROLS: AiControl[] = [
  "suggestions",
  "style",
  "emotion",
  "calming",
  "media",
  "remember",
];

// Each control's name, its line in the menu and its page's explanation.
export const AI_CONTROLS: Record<
  AiControl,
  { label: string; line: string; explanation: string }
> = {
  view: {
    label: "AI view",
    line: "Show AI text where it exists, in every chat.",
    explanation:
      "On, chats show combined messages and AI text where it exists, with every original one tap away. Off, each message shows as sent. It is one choice for every chat and starts no AI work.",
  },
  suggestions: {
    label: "Automatic suggestions",
    line: "Prepare replies on their own when a message calls for one. The Suggestions button works either way.",
    explanation:
      "Prepare replies on their own when a message calls for one, in this chat's Style. The Suggestions button works either way.",
  },
  style: {
    label: "Style",
    line: "How suggestions in this chat are worded.",
    explanation:
      "How automatic suggestions and the Suggestions button word their replies in this chat.",
  },
  emotion: {
    label: "Emotion",
    line: "Estimate how their latest messages feel, shown after their name.",
    explanation:
      "On, Enloq estimates how their latest messages feel and shows it after their name in the header. Off, nothing is estimated, and automatic suggestions follow usefulness alone.",
  },
  calming: {
    label: "Soften hostile messages",
    line: "Soften hostile wording, keeping every fact, request, deadline and threat.",
    explanation:
      "Hostile wording is softened while keeping every fact, request, deadline and threat. Anger or frustration alone stays unchanged.",
  },
  media: {
    label: "Voice and images",
    line: "Transcribe voice and video messages, read images, and write them out as text.",
    explanation:
      "On, voice and video messages are transcribed and images read, and they're written out as text, with every original one tap away. Off, nothing is transcribed or read on its own; Show transcript still works.",
  },
  remember: {
    label: "Remember this chat",
    line: "Note what they and you care about here. What's noted stays when Off.",
    explanation:
      "On, Enloq notes what this person cares about and their traits, and what you care about from this chat. Off, this chat teaches and uses neither; what's noted stays until you clear it.",
  },
  group: {
    label: "Group AI processing",
    line: "Brief this group's days and answer Ask this group.",
    explanation:
      "Brief this group's days from its messages, and answer Ask this group from them. Hide bots decides what briefings read.",
  },
};

export const HIDE_BOTS = {
  label: "Hide bots",
  line: "Leave bots' messages out of every group and its briefings.",
};

export const ASK_THIS_GROUP = "Ask this group";

// The robot opens the AI Menu: filled while AI work can run in the chat,
// an outline otherwise, and its name says which (Q14).
export function robotLabel(ai: ChatAi | null | undefined) {
  if (!ai) return "AI Menu";
  return ai.running
    ? "AI Menu, AI on in this chat"
    : "AI Menu, AI off in this chat";
}

export const ACCOUNT_OFF = "AI processing is Off for this account.";

// Under a private chat's switch, the owner's server-wide switch that
// overrides it while Off (Q14).
export function serverNote(control: AiControl, ai: ChatAi | null | undefined) {
  if (!ai) return null;
  if (control === "suggestions" && ai.server_suggestions === false)
    return "Automatic suggestions is Off for all chats.";
  if (control === "remember" && ai.server_memory === false)
    return "Memory is Off for all chats.";
  return null;
}

// Why Group AI processing can't brief now, under its switch.
export function groupNote(
  value:
    | {
        supported: boolean;
        access: string;
      }
    | null
    | undefined,
  accountEnabled: boolean | null | undefined,
) {
  if (value && !value.supported)
    return "This group is unavailable for AI processing.";
  if (value?.access === "lost")
    return "Group access was lost. Cached originals remain available.";
  if (value?.access === "unknown") return "Group access has not been verified.";
  if (accountEnabled === false) return ACCOUNT_OFF;
  return null;
}

// A switch's state in the menu, or null for a row without one yet.
export function switchState(
  control: AiControl,
  ai: ChatAi | null | undefined,
): boolean | null {
  if (!ai) return null;
  switch (control) {
    case "suggestions":
      return ai.suggestions ?? null;
    case "emotion":
      return ai.emotion ?? null;
    case "calming":
      return ai.calming ?? null;
    case "media":
      return ai.media ?? null;
    case "remember":
      return ai.remember ?? null;
    default:
      return null;
  }
}

// The controls whose row holds a switch: all but Style.
export function hasSwitch(control: AiControl) {
  return control !== "style";
}

// The route a private chat control's change writes, under the chat's own
// path, with its body; the conversation snapshot then reads it back.
export function chatWrite(
  control: AiControl,
  ai: ChatAi,
  value: boolean | SuggestionStyle,
): { suffix: string; body: Record<string, unknown> } | null {
  switch (control) {
    case "suggestions":
      return {
        suffix: "/suggestions/settings",
        body: { automatic: value, style: ai.style ?? "friendly" },
      };
    case "style":
      return {
        suffix: "/suggestions/settings",
        body: { automatic: ai.suggestions ?? false, style: value },
      };
    case "emotion":
      return { suffix: "/emotion", body: { enabled: value } };
    case "calming":
      return { suffix: "/calming", body: { enabled: value } };
    case "media":
      return { suffix: "/voice-and-images", body: { enabled: value } };
    case "remember":
      return { suffix: "/remember", body: { enabled: value } };
    default:
      return null;
  }
}

// A write's answer, shown until the snapshot reads it back.
export function answered(
  ai: ChatAi,
  control: AiControl,
  answer: { automatic?: boolean; style?: SuggestionStyle; enabled?: boolean },
): ChatAi {
  switch (control) {
    case "suggestions":
    case "style":
      return {
        ...ai,
        suggestions: answer.automatic ?? ai.suggestions,
        style: answer.style ?? ai.style,
      };
    case "emotion":
      return { ...ai, emotion: answer.enabled ?? ai.emotion };
    case "calming":
      return { ...ai, calming: answer.enabled ?? ai.calming };
    case "media":
      return { ...ai, media: answer.enabled ?? ai.media };
    case "remember":
      return { ...ai, remember: answer.enabled ?? ai.remember };
    default:
      return ai;
  }
}

// The shared scopes whose change can alter a chat's `ai` object: the account's
// AI processing and the owner's server-wide switches.
export const AI_SCOPES = ["accounts", "memory_enabled", "suggestions_enabled"];
