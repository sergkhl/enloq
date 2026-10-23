import * as Notifications from "expo-notifications";
import { Stack, router } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AgreementGate } from "../src/AgreementGate";
import { ConnectionProvider } from "../src/connection";
import { usePushUpkeep } from "../src/push";
import { phonePush } from "../src/phonePush";

function PushUpkeep() {
  usePushUpkeep();
  return phonePush ? <PushTaps /> : null;
}

// Tapping a notice opens its chat, People for a join request (ADR 48 Q19),
// Settings → AI for a decision-model outage (ADR 49 Q14), or the list for a
// test or generic notice.
function PushTaps() {
  const response = Notifications.useLastNotificationResponse();
  useEffect(() => {
    if (!response) return;
    const data = response.notification.request.content.data ?? {};
    void Notifications.clearLastNotificationResponseAsync();
    if (typeof data.account === "number" && typeof data.peer === "string")
      router.push({
        pathname: "/chat/[account]/[peer]",
        params: { account: String(data.account), peer: data.peer },
      });
    else if (data.target === "people") router.push("/settings/people");
    else if (data.target === "ai") router.push("/settings/ai");
    else router.navigate("/chats");
  }, [response]);
  return null;
}

export default function Layout() {
  return (
    <SafeAreaProvider>
      <ConnectionProvider>
        <PushUpkeep />
        <StatusBar style="auto" />
        <Stack screenOptions={{ headerShown: false }} />
        <AgreementGate />
      </ConnectionProvider>
    </SafeAreaProvider>
  );
}
