import { useEffect } from "react";
import { useConnection } from "./connection";

// One presence identity per app run, in the attention route's tab format.
const TAB = Array.from({ length: 32 }, () =>
  Math.floor(Math.random() * 16).toString(16),
).join("");

// ADR 38 Q5: while this phone shows a chat, pushes for that chat stay quiet
// on every destination. Presence lasts 12 seconds on the server, so it is
// renewed every five while focused and withdrawn at once when not.
export function useAttention(account: number, peer: string, focused: boolean) {
  const { api } = useConnection();
  useEffect(() => {
    const report = (on: boolean) =>
      api("notifications/attention", {
        body: {
          tab: TAB,
          account_id: account,
          peer,
          focused: on,
          audio: false,
        },
      }).catch(() => undefined);
    if (!focused) return;
    void report(true);
    const timer = setInterval(() => void report(true), 5000);
    return () => {
      clearInterval(timer);
      void report(false);
    };
  }, [account, api, focused, peer]);
}
