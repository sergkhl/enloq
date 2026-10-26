import { Redirect, useLocalSearchParams } from "expo-router";
import React from "react";
import { useConnection } from "../../../src/connection";
import { ReminderEditor } from "../../../src/Reminders";

// The reminder editor (ADR 58 Q12), its own screen: a new schedule for the
// person, or the one `schedule` names.
export default function ReminderRoute() {
  const params = useLocalSearchParams<{
    account: string;
    peer: string;
    title?: string;
    schedule?: string;
  }>();
  const account = Number(params.account);
  const peer = String(params.peer ?? "");
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (
    !Number.isSafeInteger(account) ||
    account <= 0 ||
    !peer.startsWith("user:")
  )
    return <Redirect href="/chats" />;
  if (connection.status !== "paired") return null;
  return (
    <ReminderEditor
      key={`${account}/${peer}/${params.schedule ?? ""}`}
      account={account}
      peer={peer}
      title={params.title ?? ""}
      schedule={params.schedule || undefined}
    />
  );
}
