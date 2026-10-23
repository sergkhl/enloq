// Chat navigation belongs to this browser-tab session (ADR 34). The durable
// Remembered Account remains independent of these tab and chat selections.
import type { ChatTab } from "../../shared/chatRoute";

export type { ChatTab };

function read(key: string) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string) {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    // Navigation still works when this browser cannot retain session state.
  }
}

export function rememberedTab(account: number): ChatTab {
  const tab = read(`rewriting-chat-tab:${account}`);
  return tab === "group" || tab === "saved" ? tab : "private";
}

export function rememberTab(account: number, kind: ChatTab) {
  write(`rewriting-chat-tab:${account}`, kind);
}

export function rememberedChat(account: number, kind: ChatTab): string | null {
  return (
    read(`rewriting-chat:${account}:${kind}`) ||
    (kind === "private" ? read(`rewriting-chat:${account}`) : null)
  );
}

export function rememberChat(account: number, kind: ChatTab, peer: string) {
  write(`rewriting-chat:${account}:${kind}`, peer);
}
