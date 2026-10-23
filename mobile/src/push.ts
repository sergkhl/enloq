import * as Notifications from "expo-notifications";
import { useCallback, useEffect, useState } from "react";
import type { Schemas } from "./api";
import { useConnection } from "./connection";
import { phonePush } from "./phonePush";
import { PUSH_TASK, dropRegistration, ensureChannels } from "./pushTask";

export type DeviceStatus = Schemas["DeviceNotificationStatus"];

const message = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

// The phone's FCM registration token, with the task that turns its pushes
// into notices. Asking for it is the first contact with Google (Q42).
async function register() {
  await ensureChannels();
  await Notifications.registerTaskAsync(PUSH_TASK);
  const token = await Notifications.getDevicePushTokenAsync();
  return String(token.data);
}

// ADR 38: while this phone is paired with push on, it
// registers at each start and gives the server any token FCM rotates.
export function usePushUpkeep() {
  const { api, connection } = useConnection();
  const paired = connection.status === "paired";
  useEffect(() => {
    if (!phonePush || !paired) return;
    let live = true;
    let subscription: { remove: () => void } | null = null;
    void api<DeviceStatus>("notifications/device")
      .then(async (status) => {
        if (!live || !status.push) return;
        subscription = Notifications.addPushTokenListener(({ data }) => {
          void api("notifications/device", {
            body: { token: String(data) },
          }).catch(() => undefined);
        });
        const token = await register();
        if (live) await api("notifications/device", { body: { token } });
      })
      .catch(() => undefined);
    return () => {
      live = false;
      subscription?.remove();
    };
  }, [api, paired]);
}

// The phone's notification settings: on or off, sound, and a test.
export function usePhonePush() {
  const { api, connection } = useConnection();
  const [status, setStatus] = useState<DeviceStatus | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      setStatus(await api<DeviceStatus>("notifications/device"));
    } catch (error) {
      setNote(message(error));
    }
  }, [api]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function act(run: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setNote("");
    try {
      await run();
    } catch (error) {
      setNote(message(error));
    } finally {
      setBusy(false);
    }
  }

  return {
    available: phonePush,
    status,
    note,
    busy,
    reload,
    turnOn: () =>
      act(async () => {
        if (!phonePush || connection.status !== "paired") return;
        // Android 13 asks for the permission once a channel exists.
        await ensureChannels();
        const permission = await Notifications.requestPermissionsAsync();
        if (!permission.granted) {
          setNote("Allow notifications for Enloq in Android Settings.");
          return;
        }
        let token: string;
        try {
          token = await register();
        } catch {
          setNote(
            "This phone couldn't register for notifications with Google Play services. Try again later.",
          );
          return;
        }
        setStatus(
          await api<DeviceStatus>("notifications/device", { body: { token } }),
        );
      }),
    turnOff: () =>
      act(async () => {
        await dropRegistration();
        setStatus(
          await api<DeviceStatus>("notifications/device/preferences", {
            body: { push: false },
          }),
        );
      }),
    setSound: (sound: boolean) =>
      act(async () => {
        setStatus(
          await api<DeviceStatus>("notifications/device/preferences", {
            body: { sound },
          }),
        );
      }),
    test: () =>
      act(async () => {
        const result = await api<{ state: string }>(
          "notifications/device/test",
          { body: {} },
        );
        setNote(
          result.state === "sent"
            ? "Test sent."
            : "The test couldn't be delivered.",
        );
      }),
  };
}
