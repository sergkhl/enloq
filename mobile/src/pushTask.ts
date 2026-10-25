import * as Notifications from "expo-notifications";
import * as TaskManager from "expo-task-manager";
import { Refused, Revoked, request } from "./api";
import { readCached, writeCached } from "./cache";
import { loadCredential } from "./credential";
import { phonePush } from "./phonePush";

export const PUSH_TASK = "enloq-push";

const SOUND = "messages";
const QUIET = "messages-quiet";
const EVENT = /^[a-f0-9]{32}$/;
let claims = Promise.resolve();

// FCM can redeliver one opaque event. Serialize its durable claim across
// concurrent task callbacks; keep only IDs within the notification lifetime.
async function claim(id: string) {
  let fresh = false;
  const next = claims.then(async () => {
    const now = Date.now();
    const seen = Object.fromEntries(
      Object.entries(
        (await readCached<Record<string, number>>("push-events")) || {},
      ).filter(([, until]) => until > now),
    );
    if (seen[id]) return;
    fresh = true;
    writeCached("push-events", { ...seen, [id]: now + 300_000 });
  });
  claims = next.catch(() => undefined);
  await next;
  return fresh;
}

export type Push = { id: string; silent: boolean };

type Details = {
  // A Join Request's push (ADR 48 Q19) opens People.
  kind?: string;
  account_id: number | null;
  peer: string | null;
  sender: string;
  text: string;
  silent: boolean;
};

// The opaque event (ADR 20) that FCM carried as one JSON string; nothing
// expired or malformed is shown.
export function readPush(data: unknown): Push | null {
  const fields = (data as { data?: Record<string, unknown> } | null)?.data;
  if (typeof fields?.payload !== "string") return null;
  try {
    const value = JSON.parse(fields.payload) as Record<string, unknown>;
    if (typeof value.id !== "string" || !EVENT.test(value.id)) return null;
    if (typeof value.expires !== "number") return null;
    if (value.expires * 1000 <= Date.now()) return null;
    return { id: value.id, silent: value.silent === true };
  } catch {
    return null;
  }
}

export async function ensureChannels() {
  await Notifications.setNotificationChannelAsync(SOUND, {
    name: "Messages",
    description: "New private messages",
    importance: Notifications.AndroidImportance.HIGH,
  });
  await Notifications.setNotificationChannelAsync(QUIET, {
    name: "Messages without sound",
    description: "New messages, when sound is off for this phone",
    importance: Notifications.AndroidImportance.HIGH,
    sound: null,
    enableVibrate: false,
  });
}

async function details(id: string): Promise<Details | null | false> {
  const credential = await loadCredential();
  if (!credential) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4500);
  try {
    return await request<Details>(credential.origin, "notifications/validate", {
      token: credential.token,
      body: { id },
      signal: controller.signal,
    });
  } catch (error) {
    // A known invalidated, superseded or revoked locator must not produce
    // a second generic banner. Network loss keeps the generic fallback.
    if (error instanceof Refused || error instanceof Revoked) return false;
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// The phone asks its server for sender and preview with its own credential;
// when the server can't answer in time, a generic notice still opens the app.
// A chat's newer notice replaces its older one.
export async function presentPush(push: Push) {
  if (!(await claim(push.id))) return;
  const found = await details(push.id);
  if (found === false) return;
  const chat = found && found.account_id !== null && found.peer ? found : null;
  const join = found?.kind === "join";
  // A decision-model outage opens Settings → AI (ADR 49 Q14).
  const decider = found?.kind === "decider";
  await ensureChannels();
  await Notifications.scheduleNotificationAsync({
    identifier: chat
      ? `chat:${chat.account_id}:${chat.peer}`
      : join
        ? "people"
        : decider
          ? "decider"
          : push.id,
    content: {
      title: found?.sender?.trim() || "Enloq",
      body: found?.text?.trim() || "New messages are ready",
      data:
        found?.kind === "reminders"
          ? { reminder: push.id }
          : chat
            ? { account: chat.account_id, peer: chat.peer }
            : join
              ? { target: "people" }
              : decider
                ? { target: "ai" }
                : {},
      sound: push.silent || found?.silent ? false : "default",
      priority: Notifications.AndroidNotificationPriority.HIGH,
    },
    trigger: { channelId: push.silent || found?.silent ? QUIET : SOUND },
  });
}

// Turning notifications off, being forgotten or pairing again deletes the
// phone's FCM token (Q42).
export async function dropRegistration() {
  if (!phonePush) return;
  await Notifications.unregisterForNotificationsAsync().catch(() => undefined);
}

// Defined at load, before the router, so a push that wakes a closed app
// finds the task (see index.ts).
TaskManager.defineTask(PUSH_TASK, async ({ data, error }) => {
  if (error || !phonePush) return;
  const push = readPush(data);
  if (push) await presentPush(push).catch(() => undefined);
});

// The data push itself has no title or text and stays hidden; the notice
// built from it shows while the app is open too.
Notifications.setNotificationHandler({
  handleNotification: async (notification) => {
    const { title, body } = notification.request.content;
    const shown = Boolean(title || body);
    return {
      shouldShowBanner: shown,
      shouldShowList: shown,
      shouldPlaySound: shown && notification.request.content.sound !== null,
      shouldSetBadge: false,
    };
  },
});
